"""Board join tokens — identity for external board clients.

A token binds an ACTOR and a ROLE server-side: an external harness (another agent
CLI, a headless OpenWorker, the `ocw` CLI from a second machine) presents the token
and the server resolves who it is — the client never states its own identity, and a
worker token cannot claim to be the lead. Authority then falls to the store, same
as for in-app agents: the token is identity, the store is the gate.

Storage is hash-only (sha256): the plaintext is shown once at mint and never
persisted, so the registry file leaking doesn't leak the credentials. Revocation is
per-token, keyed by the display prefix.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .model import Actor, Role

_TOKEN_PREFIX = "owb_"  # OpenWorker board — greppable in configs, meaningless to guess


class BoardTokens:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()

    def mint(self, actor: str, role: str = "worker", *, label: str = "") -> str:
        """Create a token for one actor identity; returns the plaintext ONCE."""
        actor = (actor or "").strip()
        if not actor:
            raise ValueError("actor is required")
        Role(role)  # validate early — a bad role should fail at mint, not at use
        token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            entries = self._load()
            entries[_digest(token)] = {
                "actor": actor,
                "role": role,
                "label": label,
                "prefix": token[:12],
                "created_ts": datetime.now(timezone.utc).isoformat(),
            }
            self._save(entries)
        return token

    def resolve(self, token: str) -> Optional[Actor]:
        if not token:
            return None
        with self._lock:
            entry = self._load().get(_digest(token))
        if entry is None:
            return None
        return Actor(id=entry["actor"], role=Role(entry["role"]))

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self._load().values(), key=lambda e: e["created_ts"])

    def revoke(self, prefix: str) -> int:
        """Revoke every token whose display prefix matches; returns the count."""
        prefix = (prefix or "").strip()
        if not prefix:
            return 0
        with self._lock:
            entries = self._load()
            keep = {
                key: entry
                for key, entry in entries.items()
                if not entry["prefix"].startswith(prefix)
            }
            removed = len(entries) - len(keep)
            if removed:
                self._save(keep)
        return removed

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, entries: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=2))
        tmp.replace(self.path)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
