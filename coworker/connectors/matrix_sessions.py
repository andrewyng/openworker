"""Optional persisted room-session map for Matrix (task 3.3).

Primary routing uses ``mention_sessions`` with keys from ``matrix_routing.mention_thread_target``.
This store holds auxiliary room→session mappings when ``session_scope=room``.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class MatrixRoomSession:
    room_id: str
    user_id: str  # empty = room-wide session
    session_id: str


class MatrixSessionStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: list[MatrixRoomSession] = []
        self._load()

    def _key(self, room_id: str, user_id: str) -> tuple[str, str]:
        return room_id, user_id or ""

    def _load(self) -> None:
        if self.path and self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._rows = [MatrixRoomSession(**raw) for raw in data.get("sessions", [])]
            except (OSError, ValueError, TypeError):
                self._rows = []

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"sessions": [asdict(r) for r in self._rows]}, indent=2),
            encoding="utf-8",
        )

    def set(self, room_id: str, session_id: str, *, user_id: str = "") -> None:
        uid = user_id or ""
        with self._lock:
            for row in self._rows:
                if self._key(row.room_id, row.user_id) == self._key(room_id, uid):
                    row.session_id = session_id
                    self._save()
                    return
            self._rows.append(MatrixRoomSession(room_id=room_id, user_id=uid, session_id=session_id))
            self._save()

    def get(self, room_id: str, *, user_id: str = "") -> Optional[str]:
        uid = user_id or ""
        for row in self._rows:
            if self._key(row.room_id, row.user_id) == self._key(room_id, uid):
                return row.session_id
        return None

    def remove_session(self, session_id: str) -> None:
        with self._lock:
            before = len(self._rows)
            self._rows = [r for r in self._rows if r.session_id != session_id]
            if len(self._rows) != before:
                self._save()
