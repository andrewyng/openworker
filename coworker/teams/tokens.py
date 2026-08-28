"""Board join tokens — identity for external board clients.

A token binds an ACTOR, ROLE, and exactly one board SPACE server-side: an external
harness (another agent CLI, a headless OpenWorker, the `ocw` CLI from a second
machine) presents the token and the server resolves both who it is and which board
it may address. The client never states its own identity or authority. Space scope
is the transport gate; actor visibility and verb authority remain store gates.

Storage is hash-only (sha256): the plaintext is shown once at mint and never
persisted, so the registry file leaking doesn't leak the credentials. Revocation is
per-token, keyed by the display prefix.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..secrets import write_private_text
from .model import Actor, Role

_TOKEN_PREFIX = "owb_"  # OpenWorker board — greppable in configs, meaningless to guess
_SPACE_REKEYS_KEY = "__space_rekeys__"
_INVALID_REGISTRY_KEY = "__invalid_registry__"


@dataclass(frozen=True)
class BoardPrincipal:
    """The identity and one board-space capability carried by a token."""

    actor: Actor
    space: str


class BoardTokens:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()

    def mint(
        self, actor: str, role: str = "worker", *, space: str, label: str = ""
    ) -> str:
        """Create a token for one actor in one board space; return plaintext once."""
        actor = (actor or "").strip()
        if not actor:
            raise ValueError("actor is required")
        resolved_role = Role(role)
        if resolved_role == Role.SYSTEM:
            raise ValueError("system actors cannot use board tokens")
        if not isinstance(space, str):
            raise ValueError("one exact board space is required")
        checked_space = space.strip()
        if not checked_space or checked_space == "*":
            raise ValueError("one exact board space is required")
        token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self._locked():
            entries = self._load()
            self._require_valid(entries)
            if space in self._space_rekeys(entries):
                raise ValueError(f"board space {space!r} is retired after rekey")
            entries[_digest(token)] = {
                "actor": actor,
                "role": resolved_role.value,
                "space": space,
                "label": label,
                "prefix": token[:12],
                "created_ts": datetime.now(timezone.utc).isoformat(),
            }
            self._save(entries)
        return token

    def resolve(self, token: str) -> Optional[BoardPrincipal]:
        if not token:
            return None
        with self._locked():
            entries = self._load()
            valid = self._registry_valid(entries)
            entry = entries.get(_digest(token))
            retired = self._space_rekeys(entries)
        if not valid:
            return None
        if not isinstance(entry, dict):
            return None
        raw_space = entry.get("space")
        if not isinstance(raw_space, str):
            return None
        checked_space = raw_space.strip()
        if not checked_space or checked_space == "*":
            return None
        if raw_space in retired:
            return None
        try:
            raw_actor = entry["actor"]
            if not isinstance(raw_actor, str):
                return None
            actor_id = raw_actor.strip()
            if not actor_id:
                return None
            role = Role(entry["role"])
            if role == Role.SYSTEM:
                return None
            actor = Actor(id=actor_id, role=role)
        except (KeyError, TypeError, ValueError):
            return None
        return BoardPrincipal(actor=actor, space=raw_space)

    def entries(self) -> list[dict[str, Any]]:
        with self._locked():
            loaded = self._load()
            if not self._registry_valid(loaded):
                return []
            entries = [
                entry
                for key, entry in loaded.items()
                if key != _SPACE_REKEYS_KEY and isinstance(entry, dict)
            ]
            return sorted(entries, key=lambda entry: str(entry.get("created_ts", "")))

    def revoke(self, prefix: str) -> int:
        """Revoke every token whose display prefix matches; returns the count."""
        prefix = (prefix or "").strip()
        if not prefix:
            return 0
        with self._locked():
            entries = self._load()
            if not self._registry_valid(entries):
                return 0
            keep = {
                key: entry
                for key, entry in entries.items()
                if key == _SPACE_REKEYS_KEY
                or not isinstance(entry, dict)
                or not str(entry.get("prefix", "")).startswith(prefix)
            }
            removed = len(entries) - len(keep)
            if removed:
                self._save(keep)
        return removed

    def begin_space_rekey(self, old: str, new: str) -> bool:
        """Persist a fail-closed rekey intent while leaving tokens recoverable."""
        with self._locked():
            entries = self._load()
            self._require_valid(entries)
            rekeys = self._space_rekeys(entries)
            existing = rekeys.get(old)
            if existing is not None and existing != new:
                raise ValueError(
                    f"board space {old!r} is already retired to {existing!r}"
                )
            if existing == new:
                return False
            rekeys[old] = new
            entries[_SPACE_REKEYS_KEY] = rekeys
            self._save(entries)
            return True

    def cancel_space_rekey(self, old: str, new: str) -> None:
        """Reactivate tokens when the coordinated database transaction rolls back."""
        with self._locked():
            entries = self._load()
            self._require_valid(entries)
            rekeys = self._space_rekeys(entries)
            if rekeys.get(old) != new:
                return
            del rekeys[old]
            if rekeys:
                entries[_SPACE_REKEYS_KEY] = rekeys
            else:
                entries.pop(_SPACE_REKEYS_KEY, None)
            self._save(entries)

    def finish_space_rekey(self, old: str, new: str) -> int:
        """Revoke old credentials but retain the tombstone against future minting."""
        with self._locked():
            entries = self._load()
            self._require_valid(entries)
            rekeys = self._space_rekeys(entries)
            if rekeys.get(old) != new:
                raise ValueError(f"no pending board-space rekey {old!r} to {new!r}")
            keep = {
                key: entry
                for key, entry in entries.items()
                if key == _SPACE_REKEYS_KEY
                or not isinstance(entry, dict)
                or entry.get("space") != old
            }
            removed = len(entries) - len(keep)
            self._save(keep)
        return removed

    def space_rekeys(self) -> dict[str, str]:
        """Return persisted rekey intents/tombstones for startup recovery."""
        with self._locked():
            entries = self._load()
            if not self._registry_valid(entries):
                return {}
            return dict(self._space_rekeys(entries))

    def is_space_active(self, space: str) -> bool:
        with self._locked():
            entries = self._load()
            return self._registry_valid(entries) and space not in self._space_rekeys(
                entries
            )

    @contextmanager
    def _locked(self):
        """Serialize registry read/modify/write across threads and processes."""
        with self._lock:
            with _interprocess_lock(self.path.with_name(self.path.name + ".lock")):
                yield

    def _load(self) -> dict[str, Any]:
        try:
            entries = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {_INVALID_REGISTRY_KEY: True}
        return entries if isinstance(entries, dict) else {_INVALID_REGISTRY_KEY: True}

    @staticmethod
    def _registry_valid(entries: dict[str, Any]) -> bool:
        if _INVALID_REGISTRY_KEY in entries:
            return False
        raw = entries.get(_SPACE_REKEYS_KEY, {})
        return isinstance(raw, dict) and all(
            isinstance(old, str) and isinstance(new, str)
            for old, new in raw.items()
        )

    @classmethod
    def _require_valid(cls, entries: dict[str, Any]) -> None:
        if not cls._registry_valid(entries):
            raise ValueError("board token registry is malformed; repair it before use")

    @staticmethod
    def _space_rekeys(entries: dict[str, Any]) -> dict[str, str]:
        raw = entries.get(_SPACE_REKEYS_KEY)
        if not isinstance(raw, dict):
            return {}
        return {
            old: new
            for old, new in raw.items()
            if isinstance(old, str) and isinstance(new, str)
        }

    def _save(self, entries: dict[str, Any]) -> None:
        write_private_text(self.path, json.dumps(entries, indent=2))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@contextmanager
def _interprocess_lock(path: Path):
    """Exclusive advisory lock shared by the server and administrative CLIs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
