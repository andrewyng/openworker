"""Local PDF handling for models without native PDF support.

The canonical history always stores a PDF attachment as an OpenAI `file` content part
(attachments.py). At send time the engine checks the ACTIVE model's capabilities
(`ModelCapabilities.pdf`) and, when the model can't take PDFs natively, replaces the
file part right before the provider call — the stored history is never mutated, so
switching to a PDF-capable model mid-session sends the real document again.

Two fallback modes (user setting, Settings → Token savings):
  - "text"   — extract embedded text locally (pypdf; pure Python).
  - "images" — render each page to a PNG (pypdfium2) and send as image parts; only
               useful when the model has vision, else it degrades to text anyway.

An opt-in local OCR step (ocr.py, #667) sits behind the text path: a document
whose embedded text is too thin to be a real text layer gets its page pixels
OCR'd locally before the visible "no extractable text" note wins. Off by default
and never on the fast path of a text-bearing PDF.

Everything runs locally — the document never goes to any vendor "file extract"
endpoint. Results are cached by content hash because the history is replayed on every
turn.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from typing import Any, Optional

from . import ocr

logger = logging.getLogger(__name__)

MAX_EXTRACT_CHARS = 200_000  # match attachments.MAX_TEXT_CHARS
RASTER_SCALE = 2.0  # ~144 dpi; readable text without giant payloads
RASTER_MAX_PAGES = 100  # hard ceiling; the user's page threshold gates at attach time

FALLBACK_MODES = ("text", "images")

# An embedded layer thinner than this per page is not a text layer — it's a
# scanned document with a stray header or two. That is the case ocr.py exists
# for (#667); anything above it stays on the fast path.
SCANNED_CHARS_PER_PAGE = 16

# Global user preference, set by the server manager from prefs at startup and on
# settings change. CLI/library use keeps the "text" default.
_fallback_mode = "text"


def set_fallback_mode(mode: Any) -> str:
    global _fallback_mode
    _fallback_mode = mode if mode in FALLBACK_MODES else "text"
    return _fallback_mode


def fallback_mode() -> str:
    return _fallback_mode


# (sha256 of data URL, operation) → result. Tiny LRU-ish cache: history replays every
# turn, and extraction/rasterization of a 10MB PDF is the expensive part.
_cache: dict[tuple[str, str], Any] = {}
_CACHE_MAX = 8


def _cached(key: tuple[str, str], compute):
    if key in _cache:
        return _cache[key]
    value = compute()
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = value
    return value


def _digest(file_data: str) -> str:
    return hashlib.sha256(file_data.encode("ascii", "ignore")).hexdigest()


def content_digest(file_data: str) -> str:
    """Public form of the content hash so sibling extractors (ocr.py) can key
    into the same cache."""
    return _digest(file_data)


def cached_extract(digest: str, operation: str, compute):
    """Public form of the digest-keyed cache, so a sibling extractor extends the
    existing behaviour instead of growing a second one."""
    return _cached((digest, operation), compute)


def forget_operation(prefix: str) -> None:
    """Drop cache entries whose operation starts with `prefix` (e.g. "ocr:") —
    used when the backend setting changes, so a newly installed engine isn't
    shadowed by an earlier 'unavailable' result."""
    for key in [k for k in _cache if k[1].startswith(prefix)]:
        _cache.pop(key, None)


def _pdf_bytes(file_data: str) -> Optional[bytes]:
    prefix = "data:application/pdf;base64,"
    if not isinstance(file_data, str) or not file_data.startswith(prefix):
        return None
    try:
        return base64.b64decode(file_data[len(prefix) :], validate=False)
    except Exception:
        return None


def inspect(file_data: str) -> dict[str, Any]:
    """Page count + size for a PDF data URL — the attach-time threshold check.

    Never raises: `{"ok": False, "error": ...}` for anything unreadable.
    """
    raw = _pdf_bytes(file_data)
    if raw is None:
        return {"ok": False, "error": "not a PDF data URL"}
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")  # unencrypted-with-owner-password PDFs open this way
            except Exception:
                return {"ok": False, "error": "PDF is password-protected"}
        return {"ok": True, "pages": len(reader.pages), "bytes": len(raw)}
    except Exception as exc:
        return {"ok": False, "error": f"could not read PDF: {exc.__class__.__name__}"}


def extract_text(file_data: str) -> Optional[str]:
    """Embedded text of the whole document (capped), or None if unreadable.
    Scanned PDFs legitimately return "" — callers surface that distinctly."""

    def compute() -> Optional[str]:
        raw = _pdf_bytes(file_data)
        if raw is None:
            return None
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw), strict=False)
            chunks: list[str] = []
            total = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                if text:
                    chunks.append(text)
                    total += len(text)
                    if total >= MAX_EXTRACT_CHARS:
                        break
            return "\n\n".join(chunks)[:MAX_EXTRACT_CHARS]
        except Exception:
            logger.warning("pdf text extraction failed", exc_info=True)
            return None

    return _cached((_digest(file_data), "text"), compute)


def _encode_png(
    width: int, height: int, pixels: bytes, stride: int, channels: int
) -> bytes:
    """Minimal PNG writer (RGB/RGBA, 8-bit) so we don't ship Pillow just for this —
    the packaged sidecar deliberately excludes PIL (bundle size, signing surface)."""
    import struct
    import zlib

    color_type = 6 if channels == 4 else 2
    row_bytes = width * channels
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # filter: None
        start = y * stride
        scanlines.extend(pixels[start : start + row_bytes])

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(scanlines), 6))
        + chunk(b"IEND", b"")
    )


def rasterize(file_data: str, max_pages: int = RASTER_MAX_PAGES) -> Optional[list[str]]:
    """Each page as a PNG data URL, or None when rendering isn't possible
    (pypdfium2 missing or the document is broken) — callers fall back to text."""

    def compute() -> Optional[list[str]]:
        raw = _pdf_bytes(file_data)
        if raw is None:
            return None
        try:
            import pypdfium2

            doc = pypdfium2.PdfDocument(raw)
            pages: list[str] = []
            try:
                for index in range(min(len(doc), max_pages)):
                    # rev_byteorder flips pdfium's native BGR(A) to the RGB(A) PNG wants.
                    bitmap = doc[index].render(scale=RASTER_SCALE, rev_byteorder=True)
                    png = _encode_png(
                        bitmap.width,
                        bitmap.height,
                        bytes(bitmap.buffer),
                        bitmap.stride,
                        bitmap.n_channels,
                    )
                    encoded = base64.b64encode(png).decode("ascii")
                    pages.append(f"data:image/png;base64,{encoded}")
            finally:
                doc.close()
            return pages or None
        except Exception:
            logger.warning("pdf rasterization failed", exc_info=True)
            return None

    return _cached((_digest(file_data), f"images:{max_pages}"), compute)


def adapt_content(content: list[dict[str, Any]], caps: Any) -> list[dict[str, Any]]:
    """Replace `file` parts for a model without native PDF support.

    vision + "images" mode → page-image parts; otherwise extracted text. Both paths end
    in a VISIBLE text note when nothing usable comes out — a PDF must never silently
    vanish from the turn.
    """
    out: list[dict[str, Any]] = []
    for part in content:
        if not (isinstance(part, dict) and part.get("type") == "file"):
            out.append(part)
            continue
        file = part.get("file") or {}
        name = str(file.get("filename") or "attachment.pdf")
        file_data = file.get("file_data") or ""

        if fallback_mode() == "images" and getattr(caps, "vision", False):
            images = rasterize(file_data)
            if images:
                out.append(
                    {
                        "type": "text",
                        "text": f"[Attached PDF: {name} — {len(images)} page image(s), rendered locally]",
                    }
                )
                out.extend(
                    {"type": "image_url", "image_url": {"url": url}} for url in images
                )
                continue

        text = extract_text(file_data)
        if text and not _thin_text_layer(file_data, text):
            out.append(_extracted_note(name, text))
            continue
        # Unreadable or image-only: the opt-in local OCR step (#667) gets the page
        # pixels before the visible note wins. Returns None in a millisecond when
        # no backend is configured, so the default path is untouched.
        recovered = ocr.ocr_text_for(file_data)
        if recovered:
            out.append(
                {
                    "type": "text",
                    "text": (
                        f"[Attached PDF: {name} — text recovered by local OCR "
                        f"({ocr.ocr_backend()}); this model has no native PDF "
                        f"support]\n{recovered}"
                    ),
                }
            )
        elif text:
            # Thin but real text layer: send what extraction found rather than
            # dropping it to the note.
            out.append(_extracted_note(name, text))
        else:
            out.append(
                {
                    "type": "text",
                    "text": (
                        f"[Attached PDF: {name} — no extractable text (likely scanned). "
                        "A model with native PDF support (Claude, GPT, Gemini) can read it.]"
                    ),
                }
            )
    return out


def _extracted_note(name: str, text: str) -> dict[str, Any]:
    return {
        "type": "text",
        "text": (
            f"[Attached PDF: {name} — text extracted locally; "
            f"this model has no native PDF support]\n{text}"
        ),
    }


def _thin_text_layer(file_data: str, text: str) -> bool:
    """True when the embedded text is too thin to be a real text layer — the
    scanned-document case ocr.py (#667) exists for. Measured per page so a
    300-page document with a stray header doesn't read as image-only."""
    pages = 1
    info = inspect(file_data)
    if info.get("ok") and info.get("pages"):
        pages = int(info["pages"])
    return len(text.strip()) < SCANNED_CHARS_PER_PAGE * pages
