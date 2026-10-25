"""Expose user-supplied PDF bytes to tools in the conversation's scratch folder."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


_PDF_PREFIX = "data:application/pdf;base64,"


def materialize_pdf_attachments(content: Any, scratch: str) -> Any:
    """Add tool-readable paths alongside PDF parts without changing the original bytes."""
    if not isinstance(content, list):
        return content

    result = []
    created: list[Path] = []
    try:
        for part in content:
            result.append(part)
            _append_pdf_path(part, scratch, result, created)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return result


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

    root = Path(scratch).resolve()
    folder = root / "attachments"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.resolve() != folder or not folder.resolve().is_relative_to(root):
        raise ValueError("attachment folder leaves the session scratch directory")
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="pdf-", suffix=".pdf", dir=folder, delete=False
        ) as output:
            path = Path(output.name)
            os.chmod(path, 0o600)
            output.write(raw)
    except Exception:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    created.append(path)

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
