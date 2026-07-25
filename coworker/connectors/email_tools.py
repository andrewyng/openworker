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

from .email_support import *  # noqa: F403 - compatibility surface for tool closures

# -- the tools ----------------------------------------------------------------------
def make_email_tools(
    secrets: SecretStore,
    *,
    roots: Optional[list[RootDir]] = None,
    imap_factory: Callable[[str, int], imaplib.IMAP4] = _default_imap_factory,
    smtp_factory: Callable[[str, int], smtplib.SMTP] = _default_smtp_factory,
) -> list[Callable[..., Any]]:
    def _connect_imap():
        """(imap, profile, servers, error) — error is a tool-result dict."""
        profile = secrets.get("email:default") or {}
        if not profile.get("address") or not profile.get("app_password"):
            return (
                None,
                None,
                None,
                {"error": "email is not connected; add it in Manage → Integrations"},
            )
        servers, err = resolve_servers(profile)
        if servers is None:
            return None, None, None, {"error": err}
        try:
            imap = _imap_login(profile, servers, imap_factory)
        except Exception as exc:
            return (
                None,
                None,
                None,
                {"error": f"IMAP login failed: {exc}.{_auth_hint(servers)}"},
            )
        return imap, profile, servers, None

    def _logout(imap) -> None:
        try:
            imap.logout()
        except Exception:
            pass

    def email_list_folders() -> dict[str, Any]:
        imap, _, _, err = _connect_imap()
        if err:
            return err
        try:
            status, lines = imap.list()
            if status != "OK":
                return {"error": "could not list folders"}
            folders = []
            for line in lines[:_MAX_FOLDERS]:
                name = _parse_list_line(line) if isinstance(line, bytes) else None
                if name is None:
                    continue
                entry: dict[str, Any] = {"name": name}
                try:
                    st, data = imap.status(_quote(name), "(MESSAGES)")
                    if st == "OK" and data and data[0]:
                        m = re.search(rb"MESSAGES\s+(\d+)", data[0])
                        if m:
                            entry["messages"] = int(m.group(1))
                except Exception:
                    pass
                folders.append(entry)
            return {"ok": True, "folders": folders}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            _logout(imap)

    def email_search(
        folder: str = "INBOX",
        from_address: str = "",
        to_address: str = "",
        subject: str = "",
        text: str = "",
        since: str = "",
        before: str = "",
        unread_only: bool = False,
        max_results: int = 10,
    ) -> dict[str, Any]:
        criteria, crit_err = build_search_criteria(
            from_address=from_address,
            to_address=to_address,
            subject=subject,
            text=text,
            since=since,
            before=before,
            unread_only=bool(unread_only),
        )
        if criteria is None:
            return {"error": crit_err}
        imap, _, _, err = _connect_imap()
        if err:
            return err
        try:
            sel_err = _select_readonly(imap, folder)
            if sel_err:
                return {"error": sel_err}
            status, data = imap.uid("SEARCH", criteria)
            if status != "OK":
                return {"error": "search failed"}
            uids = (data[0] or b"").split()
            limit = max(1, min(int(max_results or 10), _MAX_SEARCH_RESULTS))
            newest = list(reversed(uids[-limit:]))  # UIDs ascend → newest last
            messages = []
            for uid in newest:
                status, fetched = imap.uid(
                    "FETCH",
                    uid.decode(),
                    "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] FLAGS BODYSTRUCTURE)",
                )
                if status != "OK" or not fetched:
                    continue
                header_bytes = b""
                meta_bytes = b""
                for item in fetched:
                    if isinstance(item, tuple):
                        meta_bytes += item[0]
                        header_bytes += item[1]
                    elif isinstance(item, bytes):
                        meta_bytes += item
                headers = email_lib.message_from_bytes(header_bytes)
                messages.append(
                    {
                        "uid": uid.decode(),
                        "date": decode_mime_header(headers.get("Date", "")),
                        "from": decode_mime_header(headers.get("From", "")),
                        "to": decode_mime_header(headers.get("To", "")),
                        "subject": decode_mime_header(headers.get("Subject", "")),
                        "unread": b"\\Seen" not in meta_bytes,
                        "has_attachments": b'"ATTACHMENT"' in meta_bytes.upper(),
                    }
                )
            return {
                "ok": True,
                "folder": folder,
                "total_matches": len(uids),
                "messages": messages,
            }
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            _logout(imap)

    def email_read(uid: str, folder: str = "INBOX") -> dict[str, Any]:
        imap, _, _, err = _connect_imap()
        if err:
            return err
        try:
            sel_err = _select_readonly(imap, folder)
            if sel_err:
                return {"error": sel_err}
            msg = _fetch_message(imap, str(uid))
            if msg is None:
                return {"error": f"message {uid} not found in {folder}"}
            attachments = [
                {
                    "filename": name,
                    "content_type": part.get_content_type(),
                    "size": len(part.get_payload(decode=True) or b""),
                }
                for name, part in list_attachment_parts(msg)
            ]
            return {
                "ok": True,
                "uid": str(uid),
                "folder": folder,
                "from": decode_mime_header(msg.get("From", "")),
                "to": decode_mime_header(msg.get("To", "")),
                "cc": decode_mime_header(msg.get("Cc", "")),
                "date": decode_mime_header(msg.get("Date", "")),
                "subject": decode_mime_header(msg.get("Subject", "")),
                "body": extract_text_body(msg),
                "attachments": attachments,
            }
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            _logout(imap)

    def email_download_attachment(
        uid: str, filename: str, folder: str = "INBOX"
    ) -> dict[str, Any]:
        scratch = roots[0] if roots else None
        if scratch is None or not scratch.writable:
            return {
                "error": "no writable session directory to save the attachment into"
            }
        imap, _, _, err = _connect_imap()
        if err:
            return err
        try:
            sel_err = _select_readonly(imap, folder)
            if sel_err:
                return {"error": sel_err}
            msg = _fetch_message(imap, str(uid))
            if msg is None:
                return {"error": f"message {uid} not found in {folder}"}
            for name, part in list_attachment_parts(msg):
                if name == filename:
                    payload = part.get_payload(decode=True) or b""
                    target = scratch.path / _safe_filename(name)
                    counter = 1
                    while target.exists():
                        target = (
                            scratch.path
                            / f"{target.stem.rstrip('-0123456789') or 'attachment'}-{counter}{target.suffix}"
                        )
                        counter += 1
                    target.write_bytes(payload)
                    return {"ok": True, "path": str(target), "size": len(payload)}
            available = [n for n, _ in list_attachment_parts(msg)]
            return {
                "error": f"no attachment named {filename!r}; message has {available}"
            }
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            _logout(imap)

    def email_send(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        reply_to_uid: str = "",
        reply_to_folder: str = "INBOX",
        attachments: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        profile = secrets.get("email:default") or {}
        if not profile.get("address") or not profile.get("app_password"):
            return {"error": "email is not connected; add it in Manage → Integrations"}
        servers, res_err = resolve_servers(profile)
        if servers is None:
            return {"error": res_err}

        msg = EmailMessage()
        display = str(profile.get("display_name") or "").strip()
        msg["From"] = (
            formataddr((display, profile["address"])) if display else profile["address"]
        )
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc
        msg["Message-ID"] = make_msgid(domain=profile["address"].rsplit("@", 1)[-1])

        # Reply threading: pull Message-ID/References/Subject from the original first.
        final_subject = subject
        if reply_to_uid:
            imap, _, _, err = _connect_imap()
            if err:
                return err
            try:
                sel_err = _select_readonly(imap, reply_to_folder)
                if sel_err:
                    return {"error": sel_err}
                status, data = imap.uid(
                    "FETCH",
                    str(reply_to_uid),
                    "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES SUBJECT)])",
                )
                if status != "OK" or not data or not isinstance(data[0], tuple):
                    return {
                        "error": f"reply target {reply_to_uid} not found in {reply_to_folder}"
                    }
                orig = email_lib.message_from_bytes(data[0][1])
                orig_id = str(orig.get("Message-ID", "")).strip()
                if orig_id:
                    msg["In-Reply-To"] = orig_id
                    refs = str(orig.get("References", "")).strip()
                    msg["References"] = f"{refs} {orig_id}".strip()
                if not subject:
                    orig_subject = decode_mime_header(orig.get("Subject", ""))
                    final_subject = (
                        orig_subject
                        if orig_subject.lower().startswith("re:")
                        else f"Re: {orig_subject}"
                    )
            except Exception as exc:
                return {"error": str(exc)}
            finally:
                _logout(imap)
        msg["Subject"] = final_subject
        msg.set_content(body)

        allowed_roots = [r.path for r in (roots or [])]
        for raw_path in attachments or []:
            path = Path(str(raw_path)).expanduser().resolve()
            if not any(path.is_relative_to(root) for root in allowed_roots):
                return {
                    "error": f"attachment {raw_path} is outside the session's directories"
                }
            if not path.is_file():
                return {"error": f"attachment not found: {raw_path}"}
            import mimetypes

            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )

        try:
            smtp = _smtp_login(profile, servers, smtp_factory)
        except Exception as exc:
            return {"error": f"SMTP login failed: {exc}.{_auth_hint(servers)}"}
        try:
            smtp.send_message(msg)
        except Exception as exc:
            return {"error": f"send failed: {exc}"}
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        return {"ok": True, "message_id": msg["Message-ID"], "subject": final_subject}

    return [
        _attach(
            email_list_folders,
            _schema(
                "email_list_folders",
                "List the connected mailbox's folders and message counts.",
                {},
                [],
            ),
            approval=False,
            caps=["email", "read"],
        ),
        _attach(
            email_search,
            _schema(
                "email_search",
                "Search the connected mailbox. Returns newest-first envelopes (uid, date, "
                "from, to, subject, unread, has_attachments). Never marks messages read.",
                {
                    "folder": {
                        "type": "string",
                        "description": "Mailbox folder, default INBOX.",
                    },
                    "from_address": {"type": "string", "description": "Match sender."},
                    "to_address": {"type": "string", "description": "Match recipient."},
                    "subject": {
                        "type": "string",
                        "description": "Match subject substring.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Match anywhere in the message.",
                    },
                    "since": {
                        "type": "string",
                        "description": "On/after this date, YYYY-MM-DD.",
                    },
                    "before": {
                        "type": "string",
                        "description": "Before this date, YYYY-MM-DD.",
                    },
                    "unread_only": {"type": "boolean"},
                    "max_results": {
                        "type": "integer",
                        "description": "Default 10, max 25.",
                    },
                },
                [],
            ),
            approval=False,
            caps=["email", "read"],
        ),
        _attach(
            email_read,
            _schema(
                "email_read",
                "Read one email by uid: headers, text body, and attachment names/sizes "
                "(use email_download_attachment to save one). Never marks messages read.",
                {
                    "uid": {"type": "string", "description": "UID from email_search."},
                    "folder": {
                        "type": "string",
                        "description": "Folder the uid lives in, default INBOX.",
                    },
                },
                ["uid"],
            ),
            approval=False,
            caps=["email", "read"],
        ),
        _attach(
            email_download_attachment,
            _schema(
                "email_download_attachment",
                "Save one attachment from an email into the session's primary directory "
                "and return the saved path. Requires user approval.",
                {
                    "uid": {"type": "string", "description": "UID from email_search."},
                    "filename": {
                        "type": "string",
                        "description": "Attachment filename as listed by email_read.",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Folder the uid lives in, default INBOX.",
                    },
                },
                ["uid", "filename"],
            ),
            approval=True,
            caps=["email", "read"],
        ),
        _attach(
            email_send,
            _schema(
                "email_send",
                "Send an email from the connected account. Requires user approval. To reply "
                "to a message pass reply_to_uid (threading headers and Re: subject are set "
                "automatically; leave subject empty to reuse the original).",
                {
                    "to": {
                        "type": "string",
                        "description": "Recipient address(es), comma-separated.",
                    },
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Plain-text body."},
                    "cc": {"type": "string"},
                    "bcc": {"type": "string"},
                    "reply_to_uid": {
                        "type": "string",
                        "description": "UID of the message being replied to.",
                    },
                    "reply_to_folder": {
                        "type": "string",
                        "description": "Folder of reply_to_uid, default INBOX.",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paths within the session's directories to attach.",
                    },
                },
                ["to", "subject", "body"],
            ),
            approval=True,
            caps=["email", "write"],
        ),
    ]


def validate_email_account(creds: dict[str, Any]) -> tuple[bool, str, str]:
    """Connect-time check: IMAP login + INBOX open and SMTP login must both pass.

    Returns (ok, identity, error). Used by the connector descriptor so a mailbox with
    IMAP disabled (common on org-managed accounts) fails in the wizard with an
    actionable message instead of at first tool call.
    """
    servers, err = resolve_servers(creds)
    if servers is None:
        return False, "", err
    address = str(creds.get("address") or "")
    inbox_count = ""
    try:
        imap = _default_imap_factory(servers.imap_host, servers.imap_port)
        try:
            imap.login(address, creds.get("app_password", ""))
            status, data = imap.select('"INBOX"', readonly=True)
            if status == "OK" and data and data[0]:
                inbox_count = data[0].decode(errors="replace")
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except Exception as exc:
        return False, "", f"IMAP check failed: {exc}.{_auth_hint(servers)}"
    try:
        smtp = _default_smtp_factory(servers.smtp_host, servers.smtp_port)
        try:
            smtp.login(address, creds.get("app_password", ""))
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
    except Exception as exc:
        return False, "", f"SMTP check failed: {exc}.{_auth_hint(servers)}"
    identity = address + (f" · INBOX: {inbox_count} messages" if inbox_count else "")
    return True, identity, ""
