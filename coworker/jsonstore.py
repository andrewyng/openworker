"""Durable JSON persistence helpers shared by the file-backed stores.

The tiny JSON-backed stores (Inbox, subscriptions, mention threads, connection
overrides, …) are all constructed in ``SessionManager.__init__``, and each one loads its
file eagerly there. That makes their read/write robustness a *startup* concern, and two
failure modes bite:

- **A corrupt/truncated file must never block startup.** An unguarded ``json.loads`` in a
  store's ``_load`` turns one unreadable file into a server that won't boot at all —
  recoverable only by finding and deleting the file by hand. ``read_json`` returns the
  caller's default instead, moving the bad file aside (``<name>.corrupt``) so it is neither
  lost nor silently re-hit on the next load.
- **A save must be atomic.** Writing in place leaves a half-written file if the process dies
  mid-write — which is exactly what produces the corrupt file above. ``write_json_atomic``
  writes a sibling temp file and ``os.replace``s it into place (atomic on POSIX and Windows).

This just generalizes the pattern already applied inline to ``ChannelBuffer`` and
``WorkspaceTrustStore``, so every store gets the same treatment instead of a few.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("coworker.jsonstore")


def read_json(path: str | Path | None, default: Any = None) -> Any:
    """Load JSON from ``path``, tolerating a missing or unreadable file.

    Returns ``default`` when the path is ``None``, absent, or unreadable/corrupt. A corrupt
    file is moved aside to ``<name>.corrupt`` (best effort) so the bad data is preserved for
    inspection and the next ``write_json_atomic`` starts from a clean slate rather than the
    store re-hitting the same parse error on every boot.
    """
    if path is None:
        return default
    p = Path(path)
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("ignoring unreadable JSON at %s (%s); moving it aside", p, exc)
        _quarantine(p)
        return default


def _quarantine(path: Path) -> None:
    try:
        path.replace(path.with_name(path.name + ".corrupt"))
    except OSError:
        pass


def write_json_atomic(
    path: str | Path | None, data: Any, *, indent: int | None = 2
) -> None:
    """Serialize ``data`` and write it to ``path`` atomically, creating parent dirs.

    A no-op when ``path`` is ``None`` (the in-memory-only store case). Writes to a sibling
    temp file first, then ``os.replace``s it over the target, so a crash mid-write can never
    leave a truncated file that would brick the next startup.
    """
    if path is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=indent), encoding="utf-8")
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
