"""OPENWORKER_BASE_DIR — one directory the box never looks beyond.

Owner ruling 2026-09-02 (spec §Fly sandboxes, "Base directory"): when the
variable is set, the state dir, every scratch workspace, and every folder a
user adds — typed remote paths, save-as-project targets, extra roots — must
resolve UNDER it; anything outside is refused with a plain error. On a
managed sandbox it is the volume mount (/data). On a machine the user
brought it is optional; unset means today's behaviour, no restriction.

This is the path discipline. The tool-level sandbox that later makes
"nothing beyond base" mechanical (sandbox-refactor-design.md) then changes a
mechanism, not what users see.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class OutsideBaseDir(ValueError):
    """A path outside OPENWORKER_BASE_DIR on a box that has one."""


def base_dir() -> Optional[Path]:
    raw = os.environ.get("OPENWORKER_BASE_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


def ensure_under_base(path: str | os.PathLike, what: str = "folder") -> Path:
    """Expand and resolve `path`; raise OutsideBaseDir when a base is set and
    the path is not inside it. Symlinks are resolved first so a link out of
    the base does not slip through. Returns the resolved path either way."""
    resolved = Path(path).expanduser().resolve()
    base = base_dir()
    if base is None:
        return resolved
    root = base.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise OutsideBaseDir(
            f"this machine only works under {root} — pick a {what} inside it"
        )
    return resolved
