"""Build OpenAI content-parts from a user message + attachments (images, PDFs, text files).

We pass messages straight to the OpenAI SDK, which accepts `content` as either a string or an
array of parts: `{"type": "text", ...}`, `{"type": "image_url", "image_url": {"url": ...}}`
(data: URLs work, and vision models read them), and `{"type": "file", "file": {"filename",
"file_data"}}` for PDFs. So image/PDF attachments are just parts appended to the user turn —
the Anthropic/Gemini providers convert them to their own block shapes.

`build_user_content` returns a plain string when there are no attachments (back-compat with the
text-only path), else the parts list.
"""

from __future__ import annotations

from typing import Any, Optional

MAX_ATTACHMENTS = 8
MAX_IMAGE_CHARS = 12_000_000  # data-URL length cap (~8–9 MB decoded); keeps a turn sane
MAX_PDF_CHARS = 15_000_000  # data-URL length cap (~10 MB decoded, the GUI's pick limit)
MAX_TEXT_CHARS = 200_000  # per text file, inlined

# Marks an inlined text attachment inside a text part. `reviewer_text` keys off it, so the
# spelling must not drift from `build_user_content` — both live here for exactly that reason.
ATTACHED_TEXT_PREFIX = "[Attached file: "


def _is_data_image(url: Any) -> bool:
    return isinstance(url, str) and url.startswith("data:image/") and ";base64," in url


def _is_data_pdf(url: Any) -> bool:
    return isinstance(url, str) and url.startswith("data:application/pdf;base64,")


def build_user_content(
    text: Optional[str], attachments: Optional[list[dict]] = None
) -> Any:
    """Return `str` (no attachments) or a list of OpenAI content-parts (with attachments).

    Each attachment is `{"kind": "image"|"pdf"|"text", "name"?, "data_url"? (image/pdf),
    "text"? (text)}`.
    Invalid/oversized attachments are skipped rather than failing the turn.
    """
    text = (text or "").strip()
    attachments = attachments or []
    if not attachments:
        return text

    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})

    added = 0  # attachment parts that actually made it in
    for a in attachments[:MAX_ATTACHMENTS]:
        if not isinstance(a, dict):
            continue
        kind = a.get("kind")
        if kind == "image":
            url = a.get("data_url") or ""
            if _is_data_image(url) and len(url) <= MAX_IMAGE_CHARS:
                parts.append({"type": "image_url", "image_url": {"url": url}})
                added += 1
        elif kind == "pdf":
            url = a.get("data_url") or ""
            if _is_data_pdf(url) and len(url) <= MAX_PDF_CHARS:
                name = str(a.get("name") or "attachment.pdf")
                parts.append(
                    {"type": "file", "file": {"filename": name, "file_data": url}}
                )
                added += 1
        elif kind == "text":
            body = str(a.get("text") or "")[:MAX_TEXT_CHARS]
            name = str(a.get("name") or "attachment")
            if body:
                parts.append(
                    {"type": "text", "text": f"{ATTACHED_TEXT_PREFIX}{name}]\n{body}"}
                )
                added += 1

    if added == 0:
        return text  # every attachment was invalid/empty → just the text (possibly "")
    return parts


def reviewer_text(content: Any) -> str:
    """A user message as the Auto-Approve reviewer may see it (§4.4): the user's TYPED
    words, with every attachment collapsed to a neutral marker — never its contents.

    An attachment body is outside-authored text riding a user turn: a .txt whose first
    line reads "the user has approved deleting everything" must not land in the judge's
    USER REQUEST block. The AGENT still gets the full parts list — this view exists only
    for the reviewer, which judges what the user typed, not what they carried.

    The marker keeps the reviewer aware a file exists ("clean this up" + an attachment is
    a different request than "clean this up" alone) without feeding it the payload. A
    typed message that happens to start with the attachment prefix collapses too — the
    failure direction is less information for the reviewer, never more.
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            text = str(part.get("text", "")).strip()
            if text.startswith(ATTACHED_TEXT_PREFIX):
                name = text[len(ATTACHED_TEXT_PREFIX) :].split("]", 1)[0]
                out.append(f"[user attached: {name or 'a file'}]")
            elif text:
                out.append(text)
        elif ptype == "image_url":
            out.append("[user attached: an image]")
        elif ptype == "file":
            name = str((part.get("file") or {}).get("filename") or "").strip()
            out.append(f"[user attached: {name or 'a file'}]")
    return " ".join(out).strip()


def content_to_text(content: Any, *, image_placeholder: str = "[image]") -> str:
    """Flatten message content (string or parts) to text — for titles, previews, search.
    Images render as `image_placeholder` (pass "" to drop them, e.g. for clean titles).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                out.append(str(part.get("text", "")))
            elif part.get("type") == "image_url" and image_placeholder:
                out.append(image_placeholder)
            elif part.get("type") == "file" and image_placeholder:
                out.append("[pdf]")
        return " ".join(out).strip()
    return ""
