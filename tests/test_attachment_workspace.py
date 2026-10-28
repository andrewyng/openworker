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


def _tiny_png_bytes() -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)
    raw_rows = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(3))
    idat = zlib.compress(raw_rows)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_composer_image_content_exposes_exact_original_bytes(tmp_path: Path):
    raw = _tiny_png_bytes()
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    content = build_user_content("Inspect this screenshot", [
        {"kind": "image", "name": "screen.png", "data_url": data_url},
    ])
    output = materialize_pdf_attachments(content, str(tmp_path))
    assert output[1] is content[1]
    assert output[1]["image_url"]["url"] == data_url
    assert len(output) == 3
    path = next((tmp_path / "attachments").glob("img-*.png"))
    assert path.read_bytes() == raw
    assert str(path) in output[2]["text"]
    assert hashlib.sha256(raw).hexdigest() in output[2]["text"]


def test_mismatched_image_magic_has_no_tool_path(tmp_path: Path):
    fake_png_url = "data:image/png;base64," + base64.b64encode(b"not a real png payload" * 2).decode()
    content = [{"type": "image_url", "image_url": {"url": fake_png_url}}]
    assert materialize_pdf_attachments(content, str(tmp_path)) == content
    assert not (tmp_path / "attachments").exists()


def test_read_file_on_image_returns_structured_metadata_instead_of_mojibake(tmp_path: Path):
    from coworker.sandbox.runner.tools_read import read_file

    raw = _tiny_png_bytes()
    img_path = tmp_path / "chart.png"
    img_path.write_bytes(raw)

    res = read_file(str(tmp_path), "chart.png")
    assert res["path"] == "chart.png"
    assert res["binary"] is True
    assert res["mime"] == "image/png"
    assert (res["width"], res["height"]) == (4, 3)
    assert res["bytes"] == len(raw)
    assert "binary image (image/png, 4x3," in res["error"]
    assert "content" not in res


def test_read_file_and_materialize_support_gif_jpeg_webp(tmp_path: Path):
    import struct
    from coworker.sandbox.runner.tools_read import read_file

    # 1. GIF89a 5x2
    gif_raw = b"GIF89a" + struct.pack("<HH", 5, 2) + b"\x00\x00\x00;"
    # 2. Minimal JPEG with SOF0 (width=7, height=6)
    sof0_payload = struct.pack(">BHHB", 8, 6, 7, 1) + b"\x01\x11\x00"
    jpeg_raw = (
        b"\xff\xd8\xff\xc0"
        + struct.pack(">H", len(sof0_payload) + 2)
        + sof0_payload
        + b"\xff\xd9"
    )
    # 3. Minimal WebP VP8X (width=10, height=8)
    webp_vp8x = (
        b"RIFF"
        + struct.pack("<I", 22)
        + b"WEBP"
        + b"VP8X"
        + struct.pack("<I", 10)
        + b"\x00\x00\x00\x00"
        + (9).to_bytes(3, "little")
        + (7).to_bytes(3, "little")
    )

    content = build_user_content(
        "Check images",
        [
            {"kind": "image", "data_url": "data:image/gif;base64," + base64.b64encode(gif_raw).decode()},
            {"kind": "image", "data_url": "data:image/jpeg;base64," + base64.b64encode(jpeg_raw).decode()},
            {"kind": "image", "data_url": "data:image/webp;base64," + base64.b64encode(webp_vp8x).decode()},
        ],
    )
    output = materialize_pdf_attachments(content, str(tmp_path))
    assert len(output) == 7  # 1 prompt + 3 * (image_url + text pointer)

    gif_path = next((tmp_path / "attachments").glob("img-*.gif"))
    jpg_path = next((tmp_path / "attachments").glob("img-*.jpg"))
    webp_path = next((tmp_path / "attachments").glob("img-*.webp"))

    gif_res = read_file(str(tmp_path), str(gif_path.relative_to(tmp_path)))
    assert (gif_res["mime"], gif_res["width"], gif_res["height"]) == ("image/gif", 5, 2)

    jpg_res = read_file(str(tmp_path), str(jpg_path.relative_to(tmp_path)))
    assert (jpg_res["mime"], jpg_res["width"], jpg_res["height"]) == ("image/jpeg", 7, 6)

    webp_res = read_file(str(tmp_path), str(webp_path.relative_to(tmp_path)))
    assert (webp_res["mime"], webp_res["width"], webp_res["height"]) == ("image/webp", 10, 8)
