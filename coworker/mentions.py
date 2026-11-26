"""Mention-thread → session map for the Slack mention router (UX-DECISIONS §31).

When @OpenWorker is tagged in a channel with no subscribed session, the router spawns a
coworker session that OWNS that thread and replies into it. This store is the
dedupe map: one durable record per thread, keyed by the thread target string
(``"slack:C0123:1700….000100"``; relay: ``"slack:T…/C…:ts"``) — byte-identical to
what the session passes to ``send_message`` and to the standing-grant target, so
one string serves lookup, delivery, and permission.

The store is the durable source of truth for the thread grant: ``get_engine``
re-derives ``permissions.task_rules`` from it on every engine rebuild, so the
pre-approved in-thread reply survives server restarts. Deleting the session
clears its records (same contract as subscriptions).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MentionThread:
    thread_target: str  # "platform:chat_id:thread_ts" — the reply/grant target
    session_id: str
    channel: str  # thread-agnostic "platform:chat_id" (debugging/cleanup)


class MentionSessionStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._threads: list[MentionThread] = []
        self._load()

    def _load(self) -> None:
        if not (self.path and self.path.is_file()):
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw_threads = data.get("threads", [])
        except (OSError, ValueError, TypeError, AttributeError):
            # A corrupt file must never block startup. AttributeError covers a
            # file that is valid JSON but not an object (`[]`, `null`, a string).
            logger.warning(
                "mention_threads.json at %s is unreadable; starting empty", self.path
            )
            self._threads = []
            return
        # Skip only the records the dataclass refuses and keep the rest. A missing
        # field, an extra field a newer build wrote, or a non-object entry used to
        # abort the whole list — one bad row then took every other thread's grant
        # with it (get_engine re-derives task_rules from this map, and the next save
        # would have persisted the empty list over the readable records).
        threads: list[MentionThread] = []
        skipped = 0
        for raw in raw_threads if isinstance(raw_threads, list) else []:
            try:
                threads.append(MentionThread(**raw))
            except (TypeError, AttributeError):
                skipped += 1
        if skipped:
            logger.warning(
                "mention_threads.json at %s: %d unreadable record(s) skipped",
                self.path,
                skipped,
            )
        self._threads = threads

    def _save(self) -> None:
        if not self.path:
            return
        # Write a sibling .tmp and rename it over the real file, so a crash mid-write
        # leaves the previous complete file instead of a truncated one (#709).
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps({"threads": [asdict(t) for t in self._threads]}, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            # Persistence is best-effort; memory stays authoritative for this process.
            logger.warning("could not save %s: %s", self.path, exc)
            try:
                temporary.unlink()
            except OSError:
                pass

    # -- mutations --------------------------------------------------------------
    def set(self, thread_target: str, session_id: str, channel: str) -> MentionThread:
        """Upsert — a respawn over a deleted session overwrites the old mapping."""
        with self._lock:
            for t in self._threads:
                if t.thread_target == thread_target:
                    t.session_id = session_id
                    t.channel = channel
                    self._save()
                    return t
            rec = MentionThread(
                thread_target=thread_target, session_id=session_id, channel=channel
            )
            self._threads.append(rec)
            self._save()
            return rec

    def remove_session(self, session_id: str) -> None:
        """Drop all of a session's thread mappings (called when it is deleted)."""
        with self._lock:
            before = len(self._threads)
            self._threads = [t for t in self._threads if t.session_id != session_id]
            if len(self._threads) != before:
                self._save()

    # -- queries ----------------------------------------------------------------
    def get(self, thread_target: str) -> Optional[str]:
        for t in self._threads:
            if t.thread_target == thread_target:
                return t.session_id
        return None

    def targets_for(self, session_id: str) -> list[str]:
        """Every thread this session owns — the grant re-seed set."""
        return [t.thread_target for t in self._threads if t.session_id == session_id]

    def all(self) -> list[MentionThread]:
        return list(self._threads)
