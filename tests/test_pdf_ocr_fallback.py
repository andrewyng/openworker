"""#667: the opt-in local OCR step behind pdf_support.adapt_content.

Every case runs without a real OCR dependency — a stub backend is injected into
ocr's registry, so the seam (routing, bounds, caching, failure handling) is what
these tests pin down. Fixtures are a blank-page PDF (the scanned case) and a
hand-built PDF with a real embedded text layer (pypdf's PdfWriter can only make
blank pages).
"""

from __future__ import annotations

import base64
import io
import time

import pytest

from coworker import ocr, pdf_support
from coworker.providers.base import ModelCapabilities

_NO_PDF_CAPS = ModelCapabilities(vision=False, pdf=False)


def _blank_pdf_url(pages: int = 3) -> str:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    return "data:application/pdf;base64," + base64.b64encode(buf.getvalue()).decode()


def _text_pdf_url(pages: int = 2, line: str = "Embedded text layer, no OCR needed.") -> str:
    """A PDF whose content stream actually shows text. Offsets are computed, not
    guessed, so pypdf reads the layer back."""
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(pages))
    font_ref = 3 + 2 * pages
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
    ]
    for i in range(pages):
        page = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 300] /Contents "
            f"{4 + 2 * i} 0 R /Resources << /Font << /F1 {font_ref} 0 R >> >> >>"
        ).encode()
        stream = f"BT /F1 12 Tf 20 150 Td ({line}) Tj ET".encode()
        content = (
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        objects.extend([page, content])
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return "data:application/pdf;base64," + base64.b64encode(bytes(out)).decode()


class _StubBackend(ocr.OCRExtractor):
    """Records every call; behaviour is settable per test."""

    name = "stub"

    def __init__(
        self,
        text: str = "Recovered page text",
        *,
        delay: float = 0.0,
        boom: Exception | None = None,
        result: object = None,
    ) -> None:
        self.text = text
        self.delay = delay
        self.boom = boom
        self.result = result
        self.calls: list[int] = []

    def extract_pages(self, pages: list[bytes], *, deadline: float) -> str:
        self.calls.append(len(pages))
        if self.delay:
            time.sleep(self.delay)
        if self.boom is not None:
            raise self.boom
        if self.result is not None:
            return self.result  # type: ignore[return-value]
        return self.text


@pytest.fixture(autouse=True)
def _reset_state():
    pdf_support.set_fallback_mode("text")
    yield
    pdf_support.set_fallback_mode("text")
    ocr.set_backend("")


@pytest.fixture
def stub(monkeypatch):
    """Install a stub backend under the name "stub" and select it."""

    def install(backend: _StubBackend | None = None, **kwargs) -> _StubBackend:
        backend = backend or _StubBackend(**kwargs)
        monkeypatch.setitem(ocr._BACKENDS, "stub", lambda: backend)
        ocr.set_backend("stub")
        return backend

    yield install


def _adapt(url: str) -> list[dict]:
    return pdf_support.adapt_content(
        [{"type": "file", "file": {"filename": "scan.pdf", "file_data": url}}],
        _NO_PDF_CAPS,
    )


def _text_of(parts: list[dict]) -> str:
    return "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")


# -- the fast path ----------------------------------------------------------------


def test_text_layer_pdf_never_invokes_ocr(stub):
    backend = stub()
    out = _adapt(_text_pdf_url(pages=2))
    assert backend.calls == []
    assert "text extracted locally" in _text_of(out)
    assert "Embedded text layer" in _text_of(out)


def test_thin_text_layer_with_ocr_off_keeps_the_extracted_text():
    # 1 page, 2 characters — below the per-page threshold, but with OCR off the
    # old behaviour (send what extraction found) must survive.
    out = _adapt(_text_pdf_url(pages=1, line="Hi"))
    assert "text extracted locally" in _text_of(out)
    assert "Hi" in _text_of(out)
    assert "no extractable text" not in _text_of(out)


# -- the OCR step -----------------------------------------------------------------


def test_scanned_pdf_invokes_the_configured_backend(stub):
    backend = stub()
    out = _adapt(_blank_pdf_url(pages=3))
    assert backend.calls == [3]
    text = _text_of(out)
    assert "local OCR" in text and "(stub)" in text
    assert "Recovered page text" in text
    assert "no extractable text" not in text


def test_thin_text_layer_invokes_ocr(stub):
    backend = stub(text="OCR recovered")
    _adapt(_text_pdf_url(pages=1, line="Hi"))
    assert backend.calls == [1]


def test_off_by_default_keeps_the_visible_note():
    out = _adapt(_blank_pdf_url())
    assert "no extractable text (likely scanned)" in _text_of(out)


def test_unavailable_backend_keeps_the_visible_note():
    # "rapidocr" is registered but its extra isn't installed here.
    assert ocr.set_backend("rapidocr") == "rapidocr"
    assert ocr.ocr_text_for(_blank_pdf_url()) is None
    out = _adapt(_blank_pdf_url())
    assert "no extractable text (likely scanned)" in _text_of(out)


def test_backend_failure_keeps_the_visible_note(stub):
    backend = stub(boom=RuntimeError("engine exploded"))
    out = _adapt(_blank_pdf_url())
    assert backend.calls == [3]
    assert "no extractable text (likely scanned)" in _text_of(out)


def test_unusable_result_keeps_the_visible_note(stub):
    stub(result="")  # engine ran but produced nothing usable
    out = _adapt(_blank_pdf_url())
    assert "no extractable text (likely scanned)" in _text_of(out)


def test_unreadable_pdf_keeps_the_visible_note(stub):
    backend = stub()
    out = _adapt("data:application/pdf;base64,not-a-pdf")
    assert backend.calls == []
    assert "no extractable text" in _text_of(out)


# -- bounds ------------------------------------------------------------------------


def test_replay_reuses_the_digest_keyed_result(stub):
    backend = stub()
    _adapt(_blank_pdf_url())
    _adapt(_blank_pdf_url())
    assert backend.calls == [3]  # second replay hit the cache, not the engine


def test_char_cap_is_enforced(stub, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_MAX_CHARS", 50)
    stub(text="Q" * 500)
    out = _adapt(_blank_pdf_url())
    text = _text_of(out)
    assert text.endswith("Q" * 50)
    assert "Q" * 51 not in text


def test_page_cap_is_enforced(stub, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_MAX_PAGES", 5)
    backend = stub()
    _adapt(_blank_pdf_url(pages=30))
    assert backend.calls == [5]


def test_byte_cap_is_enforced(stub, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_MAX_PDF_BYTES", 10)
    backend = stub()
    out = _adapt(_blank_pdf_url())
    assert backend.calls == []
    assert "no extractable text" in _text_of(out)


def test_timeout_keeps_the_visible_note(stub, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_TIMEOUT_S", 0.05)
    backend = stub(delay=0.5)
    out = _adapt(_blank_pdf_url())
    assert "no extractable text (likely scanned)" in _text_of(out)


def test_deadline_is_passed_to_the_backend(stub, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_TIMEOUT_S", 30.0)
    seen: dict[str, float] = {}

    class _Probe(_StubBackend):
        def extract_pages(self, pages, *, deadline):
            seen["deadline"] = deadline
            return super().extract_pages(pages, deadline=deadline)

    stub(_Probe())
    _adapt(_blank_pdf_url())
    assert seen["deadline"] > time.monotonic()


# -- the outbound view only --------------------------------------------------------


def test_stored_content_is_not_mutated(stub):
    stub()
    content = [
        {"type": "file", "file": {"filename": "scan.pdf", "file_data": _blank_pdf_url()}}
    ]
    snapshot = [dict(p) for p in content]
    pdf_support.adapt_content(content, _NO_PDF_CAPS)
    assert content == snapshot


def test_backend_switch_is_not_shadowed_by_a_stale_cache(stub):
    stub(text="first engine")
    assert "first engine" in _text_of(_adapt(_blank_pdf_url()))
    ocr.set_backend("")
    stub(text="second engine")
    assert "second engine" in _text_of(_adapt(_blank_pdf_url()))


# -- backend selection -------------------------------------------------------------


def test_set_backend_validates_against_the_registry():
    assert ocr.set_backend("rapidocr") == "rapidocr"
    assert ocr.ocr_backend() == "rapidocr"
    assert ocr.set_backend("not-a-backend") == ""
    assert ocr.ocr_backend() == ""


def test_known_backends_include_the_reference_backend():
    assert "rapidocr" in ocr.known_backends()
