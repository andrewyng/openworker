import base64
import hashlib
from pathlib import Path

import pytest

from coworker.attachment_workspace import materialize_pdf_attachments
from coworker.attachments import build_user_content


def _pdf_part(name: str, raw: bytes) -> dict:
    return {
        "type": "file",
        "file": {
            "filename": name,
            "file_data": "data:application/pdf;base64," + base64.b64encode(raw).decode(),
        },
    }


def test_pdf_bytes_reach_session_scratch_without_changing_original(tmp_path: Path):
    raw = b"%PDF-1.4\n1 0 obj <<>> endobj\n%%EOF\n"
    original = [_pdf_part("../form.pdf", raw), _pdf_part("notes.pdf", raw)]
    output = materialize_pdf_attachments(original, str(tmp_path))

    assert output[0] is original[0]
    assert output[2] is original[1]
    assert len(output) == 4
    paths = list((tmp_path / "attachments").glob("*.pdf"))
    assert len(paths) == 2
    assert all(path.read_bytes() == raw for path in paths)
    assert all(str(tmp_path / "attachments") in output[i]["text"] for i in (1, 3))
    assert hashlib.sha256(raw).hexdigest() in output[1]["text"]
    assert not (tmp_path / "form.pdf").exists()


def test_invalid_payload_has_no_tool_path(tmp_path: Path):
    content = [{"type": "file", "file": {"filename": "broken.pdf", "file_data": "data:application/pdf;base64,%%%"}}]
    assert materialize_pdf_attachments(content, str(tmp_path)) == content
    assert not (tmp_path / "attachments").exists()


def test_symlinked_attachment_directory_cannot_escape_session(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    scratch = tmp_path / "session"
    scratch.mkdir()
    (scratch / "attachments").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="leaves the session scratch"):
        materialize_pdf_attachments([_pdf_part("form.pdf", b"%PDF-1.4\n")], str(scratch))
    assert not list(outside.iterdir())


def test_composer_pdf_content_exposes_exact_original_bytes(tmp_path: Path):
    raw = b"%PDF-1.4\nsynthetic form\n%%EOF\n"
    data_url = "data:application/pdf;base64," + base64.b64encode(raw).decode()
    content = build_user_content("Use the form", [
        {"kind": "pdf", "name": "form.pdf", "data_url": data_url},
    ])
    output = materialize_pdf_attachments(content, str(tmp_path))
    assert output[1] is content[1]
    assert output[1]["file"]["file_data"] == data_url
    assert len(output) == 3
    path = next((tmp_path / "attachments").glob("*.pdf"))
    assert path.read_bytes() == raw
    assert str(path) in output[2]["text"]
