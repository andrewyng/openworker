"""Email (IMAP/SMTP) connector tools — app-password auth, stdlib only.

One connector covers Gmail, iCloud, Fastmail, and custom IMAP servers: the user enters
an address + app password and servers are inferred from the address domain (advanced
fields override). Credentials are read from the SecretStore at execution time and never
enter prompts. All mailbox reads are non-destructive (read-only SELECT / PEEK fetches,
so the user's unread flags never flip) and v1 ships no delete/move/flag tools. Sending
and attachment download require approval. Sending is deliberately single-shot — SMTP
only, no APPEND-to-Sent afterwards — so a failure can never leave "delivered but looks
failed" state that tempts a retry into double-sending (Gmail saves to Sent server-side).
"""

from __future__ import annotations

import email as email_lib
import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai

from ..roots import RootDir
from ..secrets import SecretStore

_TIMEOUT = 30.0
_BODY_CHAR_LIMIT = 20_000
_MAX_SEARCH_RESULTS = 25
_MAX_FOLDERS = 50


# -- presets -------------------------------------------------------------------
@dataclass(frozen=True)
class EmailServers:
    imap_host: str
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587  # 587 → STARTTLS, 465 → implicit TLS


_PRESETS: dict[str, EmailServers] = {
    "gmail.com": EmailServers("imap.gmail.com", 993, "smtp.gmail.com", 587),
    "googlemail.com": EmailServers("imap.gmail.com", 993, "smtp.gmail.com", 587),
    "icloud.com": EmailServers("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "me.com": EmailServers("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "mac.com": EmailServers("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "fastmail.com": EmailServers("imap.fastmail.com", 993, "smtp.fastmail.com", 465),
}


def resolve_servers(profile: dict[str, Any]) -> tuple[Optional[EmailServers], str]:
    """Servers for a profile: explicit advanced fields win, then the domain preset."""
    address = str(profile.get("address") or "").strip()
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    preset = _PRESETS.get(domain)

    def _port(key: str, fallback: int) -> int:
        raw = str(profile.get(key) or "").strip()
        try:
            return int(raw) if raw else fallback
        except ValueError:
            return fallback

    imap_host = str(profile.get("imap_host") or "").strip() or (
        preset.imap_host if preset else ""
    )
    smtp_host = str(profile.get("smtp_host") or "").strip() or (
        preset.smtp_host if preset else ""
    )
    if not imap_host or not smtp_host:
        return None, (
            f"no server preset for '{domain or address}' — fill in the IMAP and SMTP "
            "host fields in the connector settings"
        )
    return (
        EmailServers(
            imap_host=imap_host,
            imap_port=_port("imap_port", preset.imap_port if preset else 993),
            smtp_host=smtp_host,
            smtp_port=_port("smtp_port", preset.smtp_port if preset else 587),
        ),
        "",
    )


def _is_gmail(servers: EmailServers) -> bool:
    return servers.imap_host.endswith(".gmail.com")


def _auth_hint(servers: EmailServers) -> str:
    if _is_gmail(servers):
        return (
            " For Gmail, check that 2-Step Verification is on and that this is an app "
            "password from myaccount.google.com/apppasswords — not your account password."
        )
    return " Check the address and app password in the connector settings."


# -- connections ----------------------------------------------------------------
def _default_imap_factory(host: str, port: int) -> imaplib.IMAP4_SSL:
    return imaplib.IMAP4_SSL(host, port, timeout=_TIMEOUT)


def _default_smtp_factory(host: str, port: int) -> smtplib.SMTP:
    if port == 465:
        return smtplib.SMTP_SSL(
            host, port, timeout=_TIMEOUT, context=ssl.create_default_context()
        )
    smtp = smtplib.SMTP(host, port, timeout=_TIMEOUT)
    smtp.starttls(context=ssl.create_default_context())
    return smtp


def _imap_login(profile, servers, factory) -> imaplib.IMAP4:
    imap = factory(servers.imap_host, servers.imap_port)
    imap.login(profile["address"], profile["app_password"])
    return imap


def _smtp_login(profile, servers, factory) -> smtplib.SMTP:
    smtp = factory(servers.smtp_host, servers.smtp_port)
    smtp.login(profile["address"], profile["app_password"])
    return smtp


# -- MIME helpers ----------------------------------------------------------------
def decode_mime_header(raw: Any) -> str:
    if not raw:
        return ""
    parts = []
    for part, charset in decode_header(str(raw)):
        if isinstance(part, bytes):
            try:
                parts.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError:  # bogus charset label in the wild
                parts.append(part.decode("utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


def _strip_html(html: str) -> str:
    text = re.sub(r"<(br|/p|/div|/tr)\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode_payload(part: email_lib.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_text_body(msg: email_lib.message.Message) -> str:
    """Best text rendering of a message: prefer text/plain, fall back to stripped HTML."""
    candidates = msg.walk() if msg.is_multipart() else [msg]
    plain, html = "", ""
    for part in candidates:
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and not plain:
            plain = _decode_payload(part)
        elif ctype == "text/html" and not html:
            html = _decode_payload(part)
    text = plain or _strip_html(html)
    if len(text) > _BODY_CHAR_LIMIT:
        text = text[:_BODY_CHAR_LIMIT] + "\n…[truncated]"
    return text


def list_attachment_parts(
    msg: email_lib.message.Message,
) -> list[tuple[str, email_lib.message.Message]]:
    out = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        filename = part.get_filename()
        if "attachment" not in disposition and not (
            filename and "inline" in disposition
        ):
            continue
        if filename:
            out.append((decode_mime_header(filename), part))
    return out


# -- IMAP query building -----------------------------------------------------------
def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _imap_date(value: str) -> Optional[str]:
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not 1 <= month <= 12:
        return None
    return f"{day:02d}-{_MONTHS[month - 1]}-{year}"


def build_search_criteria(
    *,
    from_address: str = "",
    to_address: str = "",
    subject: str = "",
    text: str = "",
    since: str = "",
    before: str = "",
    unread_only: bool = False,
) -> tuple[Optional[bytes], str]:
    """An IMAP SEARCH criteria string (as bytes, UTF-8) or an error message."""
    parts: list[str] = []
    for key, value in (
        ("FROM", from_address),
        ("TO", to_address),
        ("SUBJECT", subject),
        ("TEXT", text),
    ):
        if value and value.strip():
            parts.append(f"{key} {_quote(value.strip())}")
    for key, value in (("SINCE", since), ("BEFORE", before)):
        if value and value.strip():
            date = _imap_date(value)
            if date is None:
                return None, f"invalid {key.lower()} date {value!r}; use YYYY-MM-DD"
            parts.append(f"{key} {date}")
    if unread_only:
        parts.append("UNSEEN")
    criteria = " ".join(parts) if parts else "ALL"
    if criteria.isascii():
        return criteria.encode("ascii"), ""
    # Non-ASCII terms ride as UTF-8 with an explicit CHARSET (Gmail/iCloud accept this).
    return b"CHARSET UTF-8 " + criteria.encode("utf-8"), ""


_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$')


def _parse_list_line(line: bytes) -> Optional[str]:
    m = _LIST_RE.match(line)
    if not m:
        return None
    name = m.group("name").strip()
    if name.startswith(b'"') and name.endswith(b'"'):
        name = name[1:-1].replace(b'\\"', b'"')
    if rb"\Noselect" in m.group("flags"):
        return None
    try:
        return name.decode("utf-8")
    except UnicodeDecodeError:
        return name.decode("latin-1")


def _select_readonly(imap: imaplib.IMAP4, folder: str) -> Optional[str]:
    status, _ = imap.select(_quote(folder), readonly=True)
    if status != "OK":
        return f"cannot open folder {folder!r}"
    return None


def _fetch_message(
    imap: imaplib.IMAP4, uid: str
) -> Optional[email_lib.message.Message]:
    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK" or not data or not isinstance(data[0], tuple):
        return None
    return email_lib.message_from_bytes(data[0][1])


def _safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name  # strip any path components
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(". ")
    return name or "attachment"


# -- tool metadata plumbing (same shape as the sibling connector modules) -----------
def _meta(name: str, *, approval: bool, capabilities: list[str]):
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium" if approval else "low",
        capabilities=capabilities,
        requires_approval=approval,
    )


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _attach(
    fn: Callable[..., Any],
    schema: dict[str, Any],
    *,
    approval: bool,
    caps: list[str],
):
    from .tool_defs import approval_for_tool

    name = schema["function"]["name"]
    # §36: the tool registry's read/write kind wins for registered tools — reads never gate.
    approval = approval_for_tool(name, default=approval)
    fn.__name__ = name
    fn.__coworker_schema__ = schema
    fn.__aisuite_tool_metadata__ = _meta(name, approval=approval, capabilities=caps)
    fn.__doc__ = schema["function"]["description"]
    return fn

__all__ = [
    "EmailServers",
    "resolve_servers",
    "_is_gmail",
    "_auth_hint",
    "_default_imap_factory",
    "_default_smtp_factory",
    "_imap_login",
    "_smtp_login",
    "decode_mime_header",
    "_strip_html",
    "_decode_payload",
    "extract_text_body",
    "list_attachment_parts",
    "_quote",
    "_imap_date",
    "build_search_criteria",
    "_parse_list_line",
    "_select_readonly",
    "_fetch_message",
    "_safe_filename",
    "_meta",
    "_schema",
    "_attach",
    "_TIMEOUT",
    "_BODY_CHAR_LIMIT",
    "_MAX_SEARCH_RESULTS",
    "_MAX_FOLDERS",
    "_DATE_RE",
    "_MONTHS",
    "_LIST_RE",
]
