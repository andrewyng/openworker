"""The recorder: the one sink for recording events (observability technical design §3.2).

Hook points in the engine and the tagged provider views never write records directly. They
send recording events — `turn_started`, `turn_ended`, `step_started`, `step_updated`,
`step_ended` — to `Recorder.emit()`. The recorder folds them into turn and step records by
the rules of recording-format.md 1.0 (section 2.3), keeps the canonical step order (section
4), and hands every changed record to its store.

Every event carries `v`, `at_ms` (wall clock) and `mono_ms` (monotonic clock). Durations come
from `mono_ms` only, and a step's `started_at` is anchored to its turn's start (§7.3), so a
wall-clock jump mid-turn cannot reorder steps. `ended_at` is never stored: it is always
`started_at + duration_ms`.

Thread-safe (one lock): events arrive from the event loop, tool threads, the stream thread
and the shadow-review task at once. The golden test (`tests/observability/`) checks this
module against the shared scenarios: the same events must fold to the same recording in the
Rust engine too.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from .ids import new_ulid

if TYPE_CHECKING:
    from .store import RecordingStore

log = logging.getLogger(__name__)

EVENT_V = 1  # the event contract version (§3.2)
SCHEMA_VERSION = 1  # turn.schema_version: the record layout of format 1.x (§4, §11)

# The canonical order (recording-format.md section 4): the kind order used after start time
# and the zero-length-first rule.
KIND_ORDER = {
    "context": 0,
    "summary": 1,
    "model": 2,
    "auto_approver": 3,
    "wait": 4,
    "tool": 5,
    "notice": 6,
}

TURN_FIELDS = (
    "turn_id", "session_id", "seq", "kind", "trigger", "started_at", "duration_ms", "status",
    "incomplete", "approval_mode", "attended", "reviewer", "stamp", "first_message_id",
    "last_message_id", "schema_version",
)
STEP_FIELDS = (
    "step_id", "turn_id", "session_id", "parent_step_id", "seq", "kind", "name", "started_at",
    "duration_ms", "status", "call_id", "call_index", "message_id", "shadow", "after_turn",
    "attrs", "content",
)

# An ended turn stays in memory this long, for the events that arrive after it ended (an
# after-turn title call, a step's own late end, §7.4). Its records are already in the store.
KEEP_ENDED_TURN_MS = 15 * 60 * 1000


@dataclass
class Turn:
    turn_id: str
    session_id: str
    seq: int
    kind: str  # run | resume | retry
    trigger: str  # user | channel | schedule | team_wake | unknown
    started_at: int
    approval_mode: Optional[str]
    attended: Optional[bool]
    reviewer: Optional[dict[str, Any]]
    stamp: dict[str, Any]
    first_message_id: Optional[str] = None
    last_message_id: Optional[str] = None
    duration_ms: Optional[int] = None
    status: str = "running"
    incomplete: bool = False
    schema_version: int = SCHEMA_VERSION
    mono_start: Optional[int] = field(default=None, repr=False)  # not part of the record
    mono_end: Optional[int] = field(default=None, repr=False)

    @property
    def ended_at(self) -> Optional[int]:
        return None if self.duration_ms is None else self.started_at + self.duration_ms

    def record(self) -> dict[str, Any]:
        return {k: copy.deepcopy(getattr(self, k)) for k in TURN_FIELDS}


@dataclass
class Step:
    step_id: str
    turn_id: Optional[str]
    session_id: Optional[str]
    kind: str
    name: str
    started_at: int
    parent_step_id: Optional[str] = None
    call_id: Optional[str] = None
    call_index: Optional[int] = None  # the call's position in the model's tool-call list
    message_id: Optional[str] = None
    duration_ms: Optional[int] = None
    status: str = "running"
    shadow: bool = False
    after_turn: bool = False
    attrs: dict[str, Any] = field(default_factory=dict)
    content: dict[str, Any] = field(default_factory=dict)  # content and personal data only
    seq: Optional[int] = None  # assigned when the turn ends
    mono_start: Optional[int] = field(default=None, repr=False)  # not part of the record
    closed_by_turn: bool = field(default=False, repr=False)

    @property
    def ended_at(self) -> Optional[int]:
        return None if self.duration_ms is None else self.started_at + self.duration_ms

    def record(self) -> dict[str, Any]:
        return {k: copy.deepcopy(getattr(self, k)) for k in STEP_FIELDS}


def _merge_top(into: dict[str, Any], new: Optional[dict[str, Any]]) -> None:
    """step_updated / step_ended merge: each top-level key replaces the same key."""
    for k, v in (new or {}).items():
        into[k] = copy.deepcopy(v)


def now_ms() -> tuple[int, int]:
    """(at_ms, mono_ms) for an event sent now.

    `mono_ms` uses `perf_counter`, which is monotonic too: on Windows `time.monotonic` only
    ticks every 15.6 ms, so a short tool step read 0 or 16 ms there."""
    return time.time_ns() // 1_000_000, time.perf_counter_ns() // 1_000_000


def new_turn_id() -> str:
    return new_ulid()


def new_step_id() -> str:
    return new_ulid()


class Recorder:
    """The sink. `emit()` is the only way in.

    With a `store`, every record an event changes is handed to it to be written (the store
    writes on its own thread, so `emit()` never waits on the disk). `listeners` get each
    event after it is folded: that is how the live view receives the same events (§12).
    """

    def __init__(self, store: Optional["RecordingStore"] = None):
        self.store = store
        self.turns: dict[str, Turn] = {}
        self.steps: dict[str, Step] = {}
        self._lock = threading.RLock()
        self._turn_count: dict[str, int] = {}  # session_id -> turns seen, for `seq`
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # -- listeners ---------------------------------------------------------------------------
    def add_listener(self, fn: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Call `fn(event)` after each event is folded. Returns a function that removes it."""
        with self._lock:
            self._listeners.append(fn)

        def remove() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)

        return remove

    # -- the event sink (recording-format.md section 2.3) ----------------------------------
    def emit(self, ev: dict[str, Any]) -> None:
        """Fold one recording event. Never raises: recording must not break a turn."""
        try:
            with self._lock:
                self._fold(ev)
                listeners = list(self._listeners)
        except Exception:
            log.exception("recorder: could not fold %s", ev.get("type"))
            return
        for fn in listeners:
            try:
                fn(ev)
            except Exception:
                log.exception("recorder: listener failed")

    def _fold(self, ev: dict[str, Any]) -> None:
        t = ev["type"]
        if t == "turn_started":
            self._turn_started(ev)
        elif t == "turn_ended":
            self._turn_ended(ev)
        elif t == "step_started":
            self._step_started(ev)
        elif t == "step_updated":
            s = self.steps.get(ev["step_id"])
            if s is not None:  # no identity on an update: an unknown step cannot be made
                _merge_top(s.attrs, ev.get("attrs"))
                _merge_top(s.content, ev.get("content"))
                self._write_step(s)
        elif t == "step_ended":
            self._step_ended(ev)

    def _turn_started(self, ev: dict[str, Any]) -> None:
        if ev["turn_id"] in self.turns:
            return
        a = ev.get("attrs") or {}
        session_id = ev["session_id"]
        self._turn_count[session_id] = self._turn_count.get(session_id, 0) + 1
        seq = self._turn_count[session_id]
        if self.store is not None and seq == 1:
            # After a restart the session's earlier turns are only in the store.
            seq += self.store.turn_count(session_id)
            self._turn_count[session_id] = seq
        turn = Turn(
            turn_id=ev["turn_id"], session_id=session_id, seq=seq, kind=ev["kind"],
            trigger=ev["trigger"], started_at=ev["at_ms"], approval_mode=a.get("approval_mode"),
            attended=a.get("attended"), reviewer=copy.deepcopy(a.get("reviewer")),
            stamp=copy.deepcopy(ev.get("stamp") or {}),
            first_message_id=ev.get("first_message_id"), mono_start=ev["mono_ms"],
        )
        self.turns[turn.turn_id] = turn
        self._write_turn(turn)

    def _turn_ended(self, ev: dict[str, Any]) -> None:
        turn = self.turns.get(ev["turn_id"])
        if turn is None or turn.mono_end is not None:
            return
        turn.status, turn.last_message_id = ev["status"], ev.get("last_message_id")
        turn.mono_end = ev["mono_ms"]
        turn.duration_ms = max(0, ev["mono_ms"] - turn.mono_start)
        # §7.4: anything of this turn still open is closed at the turn's end.
        for s in self.steps.values():
            if (s.turn_id == turn.turn_id and s.duration_ms is None and s.status == "running"
                    and not s.after_turn):
                s.duration_ms = max(0, turn.ended_at - s.started_at)
                s.status, s.closed_by_turn = "interrupted", True
        self._assign_seq()
        self._write_turn(turn)
        for s in self.steps.values():
            if s.turn_id == turn.turn_id:
                self._write_step(s)
        self._forget_old_turns(ev["mono_ms"])

    def _anchor(self, turn_id: Optional[str], at_ms: int, mono_ms: int) -> int:
        """§7.3: a step's start is its turn's start plus the monotonic time since then."""
        t = self.turns.get(turn_id) if turn_id else None
        if t is None or t.mono_start is None:
            return at_ms
        return t.started_at + (mono_ms - t.mono_start)

    def _step_started(self, ev: dict[str, Any]) -> None:
        if ev["step_id"] in self.steps:
            return
        s = Step(
            step_id=ev["step_id"], turn_id=ev.get("turn_id"), session_id=ev.get("session_id"),
            kind=ev["kind"], name=ev["name"],
            started_at=self._anchor(ev.get("turn_id"), ev["at_ms"], ev["mono_ms"]),
            parent_step_id=ev.get("parent_step_id"), call_id=ev.get("call_id"),
            call_index=ev.get("call_index"), shadow=bool(ev.get("shadow", False)),
            after_turn=bool(ev.get("after_turn", False)),
            attrs=copy.deepcopy(ev.get("attrs") or {}),
            content=copy.deepcopy(ev.get("content") or {}), mono_start=ev["mono_ms"],
        )
        self.steps[s.step_id] = s
        self._write_step(s)

    def _step_ended(self, ev: dict[str, Any]) -> None:
        s = self.steps.get(ev["step_id"])
        if s is None:
            # A step_ended with no matching start: the whole step from its identity copy.
            s = Step(
                step_id=ev["step_id"], turn_id=ev.get("turn_id"), session_id=ev.get("session_id"),
                kind=ev["kind"], name=ev["name"], started_at=ev["started_at"],
                parent_step_id=ev.get("parent_step_id"), call_id=ev.get("call_id"),
                call_index=ev.get("call_index"), shadow=bool(ev.get("shadow", False)),
                after_turn=bool(ev.get("after_turn", False)),
            )
            self.steps[s.step_id] = s
            s.duration_ms = max(0, ev["at_ms"] - s.started_at)
        elif s.closed_by_turn:
            # §7.4: the turn's end already closed it; only note how late its own end came.
            turn = self.turns.get(s.turn_id)
            if turn is not None and turn.mono_end is not None:
                s.attrs["ended_after_turn_ms"] = max(0, ev["mono_ms"] - turn.mono_end)
                self._write_step(s)
            return
        else:
            s.duration_ms = max(0, ev["mono_ms"] - s.mono_start)
        s.status = ev.get("status", "ok")
        if "message_id" in ev:
            s.message_id = ev["message_id"]
        _merge_top(s.attrs, ev.get("attrs"))
        _merge_top(s.content, ev.get("content"))
        if ev.get("error"):
            s.attrs["error"] = copy.deepcopy(ev["error"])
        turn = self.turns.get(s.turn_id) if s.turn_id else None
        if turn is not None and turn.mono_end is not None:
            # A step of a turn that already ended (an after-turn call): place it in order now.
            self._assign_seq()
            for x in self.steps.values():
                if x.turn_id == turn.turn_id:
                    self._write_step(x)
        else:
            self._write_step(s)
            if turn is None and self.store is not None:
                # No turn in memory (a proxy call, or a turn from before a restart): nothing
                # later changes this step, and no turn's end will release it, so let it go.
                del self.steps[s.step_id]

    # -- the canonical order (recording-format.md section 4) ---------------------------------
    def _key(self, s: Step):
        p = self.steps.get(s.parent_step_id) if s.parent_step_id else None
        return (
            s.started_at,
            0 if s.duration_ms == 0 else 1,  # a zero-length step first
            KIND_ORDER.get(s.kind, 9),
            s.call_index or 0,
            s.name,
            # Ties the key above leaves (two helpers starting at once, a live and a shadow
            # check of one call): the parent's call position, then call id, then shadow.
            ((p.call_index or 0), p.call_id or "") if p else (-1, ""),
            s.call_id or "",
            s.shadow,
        )

    def _assign_seq(self) -> list[Step]:
        """Turns by (started_at, turn_id), each turn's steps in canonical order; then steps
        with no known turn (proxy calls) by the same key. `seq` restarts at 1 in every turn."""
        by_turn: dict[Optional[str], list[Step]] = {}
        for s in self.steps.values():
            by_turn.setdefault(s.turn_id if s.turn_id in self.turns else None, []).append(s)
        out: list[Step] = []
        for t in sorted(self.turns.values(), key=lambda t: (t.started_at, t.turn_id)):
            out += sorted(by_turn.get(t.turn_id, []), key=self._key)
        out_none = sorted(by_turn.get(None, []), key=self._key)
        seqs: dict[Optional[str], int] = {}
        for s in out + out_none:
            k = s.turn_id if s.turn_id in self.turns else None
            seqs[k] = seqs.get(k, 0) + 1
            s.seq = seqs[k]
        return out + out_none

    # -- writing ------------------------------------------------------------------------------
    def _write_turn(self, turn: Turn) -> None:
        if self.store is not None:
            self.store.write_turn(turn.record())

    def _write_step(self, s: Step) -> None:
        if self.store is not None:
            self.store.write_step(s.record())

    def _forget_old_turns(self, mono_now: int) -> None:
        """Drop turns that ended long ago, and their steps, from memory (they are stored)."""
        if self.store is None:
            return  # nothing else holds them
        old = {
            t.turn_id for t in self.turns.values()
            if t.mono_end is not None and mono_now - t.mono_end > KEEP_ENDED_TURN_MS
        }
        if not old:
            return
        for tid in old:
            del self.turns[tid]
        for sid in [sid for sid, s in self.steps.items() if s.turn_id in old]:
            del self.steps[sid]

    def delete_session(self, session_id: str) -> None:
        """Forget a deleted session's turns and steps, in memory and in the store."""
        with self._lock:
            for tid in [tid for tid, t in self.turns.items() if t.session_id == session_id]:
                del self.turns[tid]
            for sid in [sid for sid, s in self.steps.items() if s.session_id == session_id]:
                del self.steps[sid]
            self._turn_count.pop(session_id, None)
            if self.store is not None:
                self.store.delete_session(session_id)

    # -- reading ------------------------------------------------------------------------------
    def ordered_steps(self) -> list[Step]:
        """Every step in memory in recording order (turn by turn, each in canonical order)."""
        with self._lock:
            return self._assign_seq()

    def as_dict(self) -> dict[str, Any]:
        """The recording in memory (format 1.0): {"turns": [...], "steps": [...]}."""
        with self._lock:
            steps = self._assign_seq()
            turns = sorted(self.turns.values(), key=lambda t: (t.started_at, t.turn_id))
            return {"turns": [t.record() for t in turns], "steps": [s.record() for s in steps]}
