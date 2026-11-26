"""Optional local OCR for scanned PDFs (#667).

The seam: `pdf_support.adapt_content` replaces a PDF `file` part for a model
without native PDF support. Its text path extracts the embedded layer with
pypdf, and a scanned (image-only) document comes back empty — the user gets the
"no extractable text (likely scanned)" note even though the page pixels are
sitting right there. This module recovers text from those pixels, **locally**:
no hosted OCR endpoint, no document upload, nothing leaves the machine.

It is strictly opt-in:
  - prefs `pdf_ocr` names a backend; "" (the default) keeps today's behaviour
    byte-for-byte — OCR code is never reached.
  - the reference backend (`rapidocr`) lives in the `ocr` extra, so the default
    package stays as light as it is; a missing backend degrades to the note.

Bounds (the issue's acceptance cases pin these down): page count, document
bytes, wall-clock duration and output characters all have hard ceilings, and a
backend that overruns, fails or returns something unusable leaves the caller's
visible note in place — a PDF must never silently disappear from the turn.

OCR output is treated exactly like extracted text: untrusted document content,
framed by the same "[Attached PDF: ...]" wrapper, never merged into canonical
history (only the outbound view changes, so switching to a native-PDF model
still sends the real file).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Hard limits. Page count is the tight one: OCR runs seconds-per-page on CPU, so
# a longer document stays a vision-mode or native-PDF job rather than parking a
# turn for minutes.
OCR_MAX_PAGES = 20
OCR_MAX_PDF_BYTES = 20 * 1024 * 1024  # the attach-time pdf_max_mb ceiling (10MB) is usually stricter
OCR_MAX_CHARS = 50_000  # bounded output, tighter than MAX_EXTRACT_CHARS
OCR_TIMEOUT_S = 120.0  # wall clock for the whole run, enforced cooperatively


class OCRExtractor:
    """A local OCR engine. `extract_pages` receives the page PNGs that
    `pdf_support` already rasterizes for vision mode (same bytes, same cache) and
    returns plain text. `deadline` is a `time.monotonic()` value — an engine that
    can stop between pages should check it, so a cancelled run ends early
    instead of only being abandoned."""

    name = "base"

    def extract_pages(self, pages: list[bytes], *, deadline: float) -> str:
        raise NotImplementedError


# name → loader. Loaders do the optional import and return None when the
# backend isn't installed, so "configured but missing" is a normal state.
_BACKENDS: dict[str, Callable[[], Optional[OCRExtractor]]] = {}


def register_backend(name: str, loader: Callable[[], Optional[OCRExtractor]]) -> None:
    """Add a backend to the registry (the seam tests inject a stub through)."""
    _BACKENDS[name] = loader


def known_backends() -> list[str]:
    return sorted(_BACKENDS)


def _load_rapidocr() -> Optional[OCRExtractor]:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return None
    try:
        return _RapidOCRExtractor(RapidOCR())
    except Exception:
        logger.warning("rapidocr backend failed to initialise", exc_info=True)
        return None


class _RapidOCRExtractor(OCRExtractor):
    """Reference backend: rapidocr-onnxruntime (CPU ONNX runtime, models ride in
    the wheel — no system tesseract, no download at runtime). Its call takes the
    raw PNG bytes and yields `(lines, elapsed)` where each line is
    `[box, text, score]`."""

    name = "rapidocr"

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def extract_pages(self, pages: list[bytes], *, deadline: float) -> str:
        chunks: list[str] = []
        for png in pages:
            if time.monotonic() >= deadline:
                break
            lines, _elapsed = self._engine(png)
            for line in lines or []:
                try:
                    chunks.append(str(line[1]))
                except (IndexError, TypeError):
                    continue
        return "\n".join(chunks)


register_backend("rapidocr", _load_rapidocr)

# Prefs key pdf_ocr. "" = off, which is the default: without a backend name the
# OCR path is never entered.
_backend = ""


def set_backend(name: Any) -> str:
    global _backend
    if _backend != name:
        from . import pdf_support

        # A newly installed engine must not be shadowed by an earlier
        # "backend unavailable" result cached under the same name.
        pdf_support.forget_operation("ocr:")
    _backend = name if isinstance(name, str) and name in _BACKENDS else ""
    return _backend


def ocr_backend() -> str:
    return _backend


def _strip_data_url(url: str) -> bytes:
    import base64

    return base64.b64decode(url.split(",", 1)[1], validate=False)


def _run_with_deadline(extractor: OCRExtractor, pages: list[bytes]) -> str:
    """Run the engine off the turn's thread with a wall-clock bound. A run that
    overruns is abandoned (a cooperative engine has already stopped), and either
    way the caller hears nothing usable and keeps its visible note."""
    deadline = time.monotonic() + OCR_TIMEOUT_S
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["text"] = extractor.extract_pages(pages, deadline=deadline)
        except Exception:
            logger.warning(
                "pdf ocr backend %s failed", extractor.name, exc_info=True
            )

    worker = threading.Thread(target=work, daemon=True, name="pdf-ocr")
    worker.start()
    worker.join(OCR_TIMEOUT_S)
    if worker.is_alive():
        logger.warning("pdf ocr timed out after %.0fs", OCR_TIMEOUT_S)
        return ""
    value = box.get("text")
    return value if isinstance(value, str) else ""


def ocr_text_for(file_data: str) -> Optional[str]:
    """Bounded local OCR text for a PDF data URL, or None. Never raises.

    None means every ordinary outcome — OCR off, backend not installed, document
    over a hard limit, engine failed/timed out, no usable text — so callers keep
    their existing visible note.
    """
    from . import pdf_support

    name = _backend
    if not name:
        return None

    def compute() -> Optional[str]:
        info = pdf_support.inspect(file_data)
        if not info.get("ok") or int(info.get("bytes") or 0) > OCR_MAX_PDF_BYTES:
            return None
        loader = _BACKENDS.get(name)
        extractor = loader() if loader else None
        if extractor is None:
            logger.info("pdf ocr backend %r is not installed", name)
            return None
        # Reuses the vision path's rasterization (same digest-keyed cache entry
        # when the page cap matches), so the pixels are rendered at most once.
        pages = pdf_support.rasterize(file_data, max_pages=OCR_MAX_PAGES)
        if not pages:
            return None
        try:
            page_bytes = [_strip_data_url(url) for url in pages]
        except Exception:
            logger.warning("pdf ocr could not decode rendered pages", exc_info=True)
            return None
        text = _run_with_deadline(extractor, page_bytes).strip()
        return text[:OCR_MAX_CHARS] or None

    return pdf_support.cached_extract(
        pdf_support.content_digest(file_data), f"ocr:{name}", compute
    )
