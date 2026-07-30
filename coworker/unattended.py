"""Unattended mode — a per-session toggle for *where the human is reached*.

It does **not** change the autonomy ceiling (the permission mode does). When a session is
unattended, anything that would prompt inline (approval / question) is routed to the Inbox and
the agent suspends until answered; the composer is disabled. Turning it on is a one-tap confirm
(enforced at the API/GUI layer). This registry just persists the per-session flag.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .jsonstore import read_json, write_json_atomic


class UnattendedRegistry:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._flags: dict[str, bool] = dict(read_json(self.path, {}) or {})

    def _save(self) -> None:
        write_json_atomic(self.path, self._flags)

    def is_unattended(self, session_id: str) -> bool:
        return bool(self._flags.get(session_id, False))

    def set(self, session_id: str, unattended: bool) -> None:
        with self._lock:
            if unattended:
                self._flags[session_id] = True
            else:
                self._flags.pop(session_id, None)
            self._save()

    def sessions(self) -> list[str]:
        return [sid for sid, on in self._flags.items() if on]
