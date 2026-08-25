"""Keep oversized tool output out of the model transcript while retaining local evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_INLINE_BYTES = 32 * 1024
PREVIEW_BYTES = 4 * 1024


@dataclass(frozen=True)
class Offload:
    path: str
    size: int
    sha256: str
    preview: str

    def to_reference(self) -> dict[str, Any]:
        return {
            "offloaded": True,
            "artifact": self.path,
            "bytes": self.size,
            "sha256": self.sha256,
            "preview": self.preview,
        }


class ArtifactOffloader:
    def __init__(self, root: str | Path | None) -> None:
        self.root = Path(root).resolve() if root else None

    def prepare(self, result: Any, *, tool_call_id: str) -> tuple[Any, Offload | None]:
        if self.root is None:
            return result, None
        encoded = json.dumps(result, ensure_ascii=False, default=str, indent=2).encode("utf-8")
        if len(encoded) <= MAX_INLINE_BYTES:
            return result, None
        digest = hashlib.sha256(encoded).hexdigest()
        folder = self.root / ".openwork-artifacts"
        folder.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(c for c in tool_call_id if c.isalnum() or c in "-_")[:80] or digest[:12]
        path = folder / f"tool-output-{safe_id}-{digest[:12]}.json"
        path.write_bytes(encoded)
        preview = encoded[:PREVIEW_BYTES].decode("utf-8", errors="replace")
        offload = Offload(str(path), len(encoded), digest, preview)
        return offload.to_reference(), offload
