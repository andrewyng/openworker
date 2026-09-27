"""Self-wake — tools that let a long-running agent suspend and be re-invoked on a trigger.

Converts an always-on agent into suspend/resume (event-driven, ~zero idle cost): the session
sleeps and the runtime re-invokes it when a wake is due. Two triggers here: a **timer**
(`sleep_for` relative, `sleep_until` absolute) and **on-completion** (`wake_on` a
backgrounded job). This module
owns the wake records + the due/complete logic; the scheduler tick consumes ``due()`` /
``complete_job()`` and resumes the session (shares the automation scheduler — see
``PERMISSIONS-AND-INBOX.md``).
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

KIND_TIMER = "timer"
KIND_COMPLETION = "completion"
KIND_EVENT = "event"  # wake when a named connector/webhook event fires (Phase 3)

STATE_PENDING = "pending"
STATE_DUE = "due"
STATE_FIRED = "fired"
STATE_CANCELLED = "cancelled"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Wake:
    id: str
    session_id: str
    kind: str
    state: str = STATE_PENDING
    fire_at: Optional[str] = None  # ISO, for timer wakes
    job_id: Optional[str] = None  # for completion wakes
    event_key: Optional[str] = None  # for on-event wakes
    note: str = ""
    created_at: str = field(default_factory=lambda: _now().isoformat())
    cancellation_reason: str = ""
    context_delivered: bool = False


class WakeStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._wakes: dict[str, Wake] = {}
        self.stopped_sessions: set[str] = set()
        if self.path and self.path.is_file():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.stopped_sessions = set(saved.get("stopped_sessions", []))
            for raw in saved.get("wakes", []):
                w = Wake(**raw)
                self._wakes[w.id] = w
            # Older versions accumulated timers. Keep only the newest pending
            # sleep per session while retaining the replaced records for audit.
            newest = {}
            migrated = False
            for w in sorted(self._wakes.values(), key=lambda w: w.created_at):
                if w.kind != KIND_TIMER or w.state not in (STATE_PENDING, STATE_DUE):
                    continue
                old = newest.get(w.session_id)
                if old:
                    old.state = STATE_CANCELLED
                    old.cancellation_reason = "replaced by a newer sleep"
                    old.context_delivered = True
                    migrated = True
                newest[w.session_id] = w
            if migrated:
                self._save()

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"wakes": [asdict(w) for w in self._wakes.values()],
                 "stopped_sessions": sorted(self.stopped_sessions)},
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def add_timer(self, session_id: str, fire_at: datetime, *, note: str = "") -> Wake:
        w = Wake(
            uuid.uuid4().hex,
            session_id,
            KIND_TIMER,
            fire_at=fire_at.isoformat(),
            note=note,
        )
        with self._lock:
            for old in self._wakes.values():
                if (old.session_id == session_id and old.kind == KIND_TIMER
                        and old.state in (STATE_PENDING, STATE_DUE)):
                    old.state = STATE_CANCELLED
                    old.cancellation_reason = "replaced by a newer sleep"
                    old.context_delivered = True
            self._wakes[w.id] = w
            self._save()
        return w

    def add_completion(self, session_id: str, job_id: str, *, note: str = "") -> Wake:
        w = Wake(
            uuid.uuid4().hex, session_id, KIND_COMPLETION, job_id=job_id, note=note
        )
        with self._lock:
            self._wakes[w.id] = w
            self._save()
        return w

    def add_event(self, session_id: str, event_key: str, *, note: str = "") -> Wake:
        w = Wake(
            uuid.uuid4().hex, session_id, KIND_EVENT, event_key=event_key, note=note
        )
        with self._lock:
            self._wakes[w.id] = w
            self._save()
        return w

    def due(self, now: Optional[datetime] = None) -> list[Wake]:
        """Timer wakes whose fire time has passed, plus completion/event wakes marked due."""
        now = now or _now()
        out = []
        with self._lock:
            snapshot = list(self._wakes.values())
        for w in snapshot:
            if w.state != STATE_PENDING and w.state != STATE_DUE:
                continue
            if (
                w.kind == KIND_TIMER
                and w.fire_at
                and datetime.fromisoformat(w.fire_at) <= now
            ):
                out.append(w)
            elif w.kind in (KIND_COMPLETION, KIND_EVENT) and w.state == STATE_DUE:
                out.append(w)
        return out

    def complete_job(self, job_id: str) -> list[Wake]:
        """Mark completion wakes for ``job_id`` as due (the job exited). Returns them."""
        return self._mark_due(
            lambda w: w.kind == KIND_COMPLETION and w.job_id == job_id
        )

    def fire_event(self, event_key: str) -> list[Wake]:
        """Mark on-event wakes for ``event_key`` as due (a connector/webhook fired). Returns them."""
        return self._mark_due(
            lambda w: w.kind == KIND_EVENT and w.event_key == event_key
        )

    def _mark_due(self, pred) -> list[Wake]:
        fired = []
        with self._lock:
            for w in self._wakes.values():
                if w.state == STATE_PENDING and pred(w):
                    w.state = STATE_DUE
                    fired.append(w)
            if fired:
                self._save()
        return fired

    def mark_fired(self, wake_id: str) -> None:
        with self._lock:
            w = self._wakes.get(wake_id)
            if w is not None and w.state in (STATE_PENDING, STATE_DUE):
                w.state = STATE_FIRED
                self._save()

    def cancel_sleep(self, session_id: str, reason: str) -> None:
        """Retain cancelled reminders durably until an incoming turn records them."""
        with self._lock:
            changed = False
            for w in self._wakes.values():
                if (w.session_id == session_id and w.kind == KIND_TIMER
                        and w.state in (STATE_PENDING, STATE_DUE)):
                    w.state = STATE_CANCELLED
                    w.cancellation_reason = reason
                    changed = True
            if changed:
                self._save()

    def set_stopped(self, session_id: str, stopped: bool) -> None:
        with self._lock:
            if stopped:
                self.stopped_sessions.add(session_id)
            else:
                self.stopped_sessions.discard(session_id)
            self._save()

    def cancelled_context(self, session_id: str) -> list[Wake]:
        with self._lock:
            return [
                w for w in self._wakes.values()
                if w.session_id == session_id and w.state == STATE_CANCELLED
                and not w.context_delivered
            ]

    def acknowledge(self, wake_ids: list[str]) -> None:
        """Called only after a durable incoming-message receipt exists."""
        with self._lock:
            changed = False
            for wake_id in wake_ids:
                w = self._wakes.get(wake_id)
                if w is not None and not w.context_delivered:
                    if w.state in (STATE_PENDING, STATE_DUE):
                        w.state = STATE_FIRED
                    w.context_delivered = True
                    changed = True
            if changed:
                self._save()

    def pending(self, session_id: Optional[str] = None) -> list[Wake]:
        with self._lock:
            return [
                w
                for w in self._wakes.values()
                if w.state in (STATE_PENDING, STATE_DUE)
                and (session_id is None or w.session_id == session_id)
            ]


def selfwake_tools(store: WakeStore, session_id: str) -> list:
    """Tools an agent calls to schedule its own resumption."""

    def sleep_for(seconds: int, note: str = "") -> dict:
        """Suspend and wake this session after `seconds` (a relative wait: "check again in
        5 minutes" is sleep_for(300)). Use for an explicit timed check, not routine
        team polling: board decisions already wake a lead that finishes its turn.
        Replaces the previous sleep. Earlier board/user activity cancels
        it and carries your optional reminder note forward. No clock arithmetic needed."""
        secs = int(seconds)
        if secs <= 0:
            raise ValueError("sleep_for needs a positive number of seconds")
        w = store.add_timer(session_id, _now() + timedelta(seconds=secs), note=note)
        return {"ok": True, "wake_id": w.id, "fire_at": w.fire_at}

    def sleep_until(when_iso: str, note: str = "") -> dict:
        """Suspend and wake this session at an ISO-8601 timestamp (timezone-aware; bare
        timestamps are read as UTC) — for an absolute time ("at 09:00 tomorrow"). Call
        `current_time` first if you need today's date or the timezone; for a relative wait
        use sleep_for instead. Replaces the previous sleep and cancels on earlier
        board/user activity, carrying the optional reminder note into that activity.
        This is an idle check-in deadline, not a persistent appointment."""
        when = datetime.fromisoformat(when_iso)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        w = store.add_timer(session_id, when, note=note)
        return {"ok": True, "wake_id": w.id, "fire_at": w.fire_at}

    def wake_on(job_id: str, note: str = "") -> dict:
        """Suspend and wake this session when a backgrounded job (`job_id`) completes."""
        w = store.add_completion(session_id, job_id, note=note)
        return {"ok": True, "wake_id": w.id, "job_id": job_id}

    def wake_on_event(event_key: str, note: str = "") -> dict:
        """Suspend and wake this session when a named event (`event_key`) fires — e.g. a
        connector/webhook signal an Ops agent watches for."""
        w = store.add_event(session_id, event_key, note=note)
        return {"ok": True, "wake_id": w.id, "event_key": event_key}

    tools = [sleep_for, sleep_until, wake_on, wake_on_event]
    for tool in tools:
        tool.__coworker_yields_turn__ = True
    return tools
