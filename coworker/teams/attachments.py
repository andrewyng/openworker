"""Content-addressed attachments for board items — screenshots first.

Review artifacts don't belong in the repo (they aren't source, and they die with
checkouts) and don't belong in the board log (events carry refs, never blobs — no
megabytes under the hash chain). They live here: files named by their sha256 in the
state dir, bridged into the board as a normal comment event carrying an
`attachment://<hash>.<ext>#<name>` ref.

Content addressing buys three things: dedupe for free (the same screenshot attached
twice stores once), immutability by construction (the ref can never dangle onto
changed bytes), and location independence — on a hosted board the same ref resolves
to object storage instead of this directory.

Scope is images-only and ~10MB to start; the allowlist is the policy choke point
when that widens.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from .model import BoardError, BoardNotFoundError

ATTACHMENT_SCHEME = "attachment://"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

# Extension → mime for the types we accept. Sniffed magic must agree with the
# claimed extension — a .png that isn't a PNG is refused, not renamed.
_IMAGE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

_MAGIC = {
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "gif": b"GIF8",
    "webp": b"RIFF",  # RIFF….WEBP — checked with the fourcc below
}

_STORED_NAME = re.compile(r"[0-9a-f]{64}\.[a-z0-9]{1,5}")


class AttachmentStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def put(self, data: bytes, filename: str) -> str:
        """Store one attachment; returns its `attachment://` ref. Idempotent —
        identical bytes land on the same file."""
        ext = _validate(data, filename)
        stored = f"{hashlib.sha256(data).hexdigest()}.{ext}"
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / stored
        if not target.exists():
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
        safe_name = Path(filename).name.replace("#", "_")
        return f"{ATTACHMENT_SCHEME}{stored}#{safe_name}"

    def path_for(self, stored: str) -> Path:
        """Resolve a stored name (`<sha256>.<ext>`) to its file. The strict name
        check is the traversal guard — nothing else reaches the filesystem."""
        stored = validate_stored_name(stored)
        path = self.root / stored
        if not path.exists():
            raise BoardNotFoundError("attachment not found")
        return path

    def mime_for(self, stored: str) -> str:
        return _IMAGE_TYPES.get(stored.rsplit(".", 1)[-1], "application/octet-stream")


def stored_name(ref: str) -> Optional[str]:
    """`attachment://<hash>.<ext>#<name>` → `<hash>.<ext>`; None for other refs."""
    if not ref.startswith(ATTACHMENT_SCHEME):
        return None
    return ref[len(ATTACHMENT_SCHEME):].split("#", 1)[0]


def validate_stored_name(stored: str) -> str:
    """Return one normalized stored name, rejecting malformed input."""
    stored = stored.strip()
    if not _STORED_NAME.fullmatch(stored):
        raise BoardError(f"not an attachment name: {stored!r}")
    return stored


def _validate(data: bytes, filename: str) -> str:
    if not data:
        raise BoardError("attachment is empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise BoardError(
            f"attachment exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB"
        )
    ext = Path(filename).suffix.lstrip(".").lower()
    if ext not in _IMAGE_TYPES:
        raise BoardError(
            f"unsupported attachment type .{ext or '?'} — images only for now"
            f" ({', '.join(sorted(set(_IMAGE_TYPES)))})"
        )
    if not data.startswith(_MAGIC[ext]) or (
        ext == "webp" and data[8:12] != b"WEBP"
    ):
        raise BoardError(f"file content does not look like .{ext}")
    return "jpg" if ext == "jpeg" else ext
