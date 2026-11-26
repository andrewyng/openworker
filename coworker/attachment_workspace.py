"""Expose user-supplied PDF and image bytes to tools in the conversation's scratch folder."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


_PDF_PREFIX = "data:application/pdf;base64,"
_IMAGE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("data:image/png;base64,", "png"),
    ("data:image/jpeg;base64,", "jpg"),
    ("data:image/jpg;base64,", "jpg"),
    ("data:image/gif;base64,", "gif"),
    ("data:image/webp;base64,", "webp"),
)


def _matches_image_magic(raw: bytes, ext: str) -> bool:
    if ext == "png":
        return len(raw) >= 24 and raw.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "jpg":
        return len(raw) >= 4 and raw.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return len(raw) >= 10 and raw.startswith((b"GIF87a", b"GIF89a"))
    if ext == "webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def materialize_pdf_attachments(content: Any, scratch: str) -> Any:
    """Add tool-readable paths alongside PDF and image parts without changing original bytes."""
    if not isinstance(content, list):
        return content

    result = []
    created: list[Path] = []
    try:
        for part in content:
            result.append(part)
            _append_pdf_path(part, scratch, result, created)
            _append_image_path(part, scratch, result, created)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return result


def _write_scratch_attachment(
    raw: bytes,
    *,
    scratch: str,
    prefix: str,
    suffix: str,
    created: list[Path],
) -> Path:
    root = Path(scratch).resolve()
    folder = root / "attachments"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.resolve() != folder or not folder.resolve().is_relative_to(root):
        raise ValueError("attachment folder leaves the session scratch directory")
    path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=prefix, suffix=suffix, dir=folder, delete=False
        ) as output:
            path = Path(output.name)
            os.chmod(path, 0o600)
            output.write(raw)
    except Exception:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    created.append(path)
    return path


def _append_pdf_path(part: Any, scratch: str, result: list, created: list[Path]) -> None:
    if not isinstance(part, dict) or part.get("type") != "file":
        return
    file = part.get("file")
    if not isinstance(file, dict):
        return
    data_url = file.get("file_data")
    if not isinstance(data_url, str) or not data_url.startswith(_PDF_PREFIX):
        return
    try:
        raw = base64.b64decode(data_url[len(_PDF_PREFIX) :], validate=True)
    except (binascii.Error, ValueError):
        return
    if not raw.startswith(b"%PDF-"):
        return

    path = _write_scratch_attachment(
        raw, scratch=scratch, prefix="pdf-", suffix=".pdf", created=created
    )

    name = str(file.get("filename") or "attachment.pdf")
    digest = hashlib.sha256(raw).hexdigest()
    result.append({
        "type": "text",
        "text": (
            f"[Attached PDF {json.dumps(name)}: original bytes are available "
            f"to local tools at {path}; SHA-256 {digest}. Use this path for "
            "byte-oriented file or MCP workflows.]"
        ),
    })


def _append_image_path(part: Any, scratch: str, result: list, created: list[Path]) -> None:
    if not isinstance(part, dict) or part.get("type") != "image_url":
        return
    image_url = part.get("image_url")
    if not isinstance(image_url, dict):
        return
    data_url = image_url.get("url")
    if not isinstance(data_url, str):
        return
    ext: Optional[str] = None
    b64_payload: Optional[str] = None
    for prefix, candidate_ext in _IMAGE_PREFIXES:
        if data_url.startswith(prefix):
            ext = candidate_ext
            b64_payload = data_url[len(prefix) :]
            break
    if ext is None or b64_payload is None:
        return
    try:
        raw = base64.b64decode(b64_payload, validate=True)
    except (binascii.Error, ValueError):
        return
    if not _matches_image_magic(raw, ext):
        return

    path = _write_scratch_attachment(
        raw, scratch=scratch, prefix="img-", suffix=f".{ext}", created=created
    )

    name = str(image_url.get("filename") or f"attachment.{ext}")
    digest = hashlib.sha256(raw).hexdigest()
    result.append({
        "type": "text",
        "text": (
            f"[Attached image {json.dumps(name)}: original bytes are available "
            f"to local tools at {path}; SHA-256 {digest}. Use this path for "
            "byte-oriented file or MCP workflows.]"
        ),
    })
