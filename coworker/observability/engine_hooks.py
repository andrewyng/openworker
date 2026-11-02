"""The engine's side of recording (observability E1): builds the recording events a
`TurnEngine` sends and hands them to its `Recorder`.

The engine calls a handful of methods here at its hook points (a turn starts or ends, a tool
starts or ends, a message is added). This module turns those calls into the five events of
recording-format.md section 2, with `v`, `at_ms`, `mono_ms`, and the copy of the step's
identity that every `step_ended` carries. With no recorder every method returns at once.

Two events wait for a fact that arrives later:
- A `retry` turn's `turn_started` waits for the first message the retry adds, whose `mid` it
  carries (`first_message_id`). Until then the turn's other events are held, in order; their
  times were taken when they happened, so holding them changes nothing in the recording.
- A tool step's `step_ended` is built when the tool returns (its time is then) and sent once
  the tool's result message is added, so it can carry that message's `mid`.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Callable, Optional

from .ids import new_ulid
from .recorder import EVENT_V, Recorder, now_ms

# `start_step(turn_id=CURRENT)`: the engine's current turn.
CURRENT = object()

# TOOL_FINISHED status -> tool step status (technical design §5.1).
_TOOL_STATUS = {"ok": "ok", "error": "error", "denied": "denied", "interrupted": "interrupted",
                "skipped": "skipped"}


class TurnRecording:
    """One per engine. Thread-safe: tool steps start and end in tool threads."""

    def __init__(self, recorder: Optional[Recorder], session_id: Callable[[], str]):
        self.recorder = recorder
        self._session_id = session_id
        self._lock = threading.RLock()
        self.turn_id: Optional[str] = None
        self._turn_session = ""
        self._ended = True
        self._held: Optional[list[dict[str, Any]]] = None  # events behind a waiting turn_started
        self._pending_start: Optional[dict[str, Any]] = None
        self._last_mid: Optional[str] = None
        self._ident: dict[str, dict[str, Any]] = {}  # step_id -> identity copy
        self._tool_steps: dict[str, str] = {}  # call_id -> step_id, for this turn's tools
        self._tool_ends: dict[str, dict[str, Any]] = {}  # call_id -> step_ended awaiting its mid
        self._reply_end: Optional[dict[str, Any]] = None  # a main model step awaiting its reply's mid
        self._waits: dict[str, str] = {}  # call_id -> its open wait step (E20)
        self._reserved: dict[str, dict[str, Any]] = {}  # summary steps written at their end (E4)
        self._tokens_after_for: Optional[str] = None  # a summary waiting for the next main call
        self._child_usage: dict[str, bool] = {}  # parent step -> its model call reported usage

    @property
    def enabled(self) -> bool:
        return self.recorder is not None

    # -- sending ------------------------------------------------------------------------------
    def _base(self, typ: str, turn_id: Optional[str], session_id: str) -> dict[str, Any]:
        at, mono = now_ms()
        # A step with no session (a proxy call) or no turn records null, as the format says.
        return {"type": typ, "v": EVENT_V, "at_ms": at, "mono_ms": mono,
                "session_id": session_id or None, "turn_id": turn_id or None}

    def _send(self, ev: dict[str, Any]) -> None:
        if self._held is not None and ev["type"] != "turn_started" and ev.get("turn_id") == self.turn_id:
            self._held.append(ev)
        else:
            self.recorder.emit(ev)

    def _release(self, first_message_id: Optional[str]) -> None:
        """Send the waiting turn_started with its first message id, then what it held."""
        if self._pending_start is None:
            return
        ev, held = self._pending_start, self._held or []
        self._pending_start, self._held = None, None
        ev["first_message_id"] = first_message_id
        self.recorder.emit(ev)
        for e in held:
            self.recorder.emit(e)

    # -- turns ----------------------------------------------------------------------------------
    def begin_turn(self, *, kind: str, trigger: str, attrs: dict[str, Any], stamp: dict[str, Any],
                   first_message_id: Optional[str], wait_for_first_message: bool = False) -> str:
        """`turn_started`, now. With `wait_for_first_message` it is held until `note_message`
        sees the turn's first message (a retry) or the turn ends."""
        with self._lock:
            self.turn_id = new_ulid()
            self._turn_session = self._session_id() or ""
            self._ended = False
            self._last_mid = None
            self._tool_steps.clear()
            self._tool_ends.clear()
            self._reply_end = None
            self._waits.clear()
            ev = self._base("turn_started", self.turn_id, self._turn_session)
            ev.update({"kind": kind, "trigger": trigger, "first_message_id": first_message_id,
                       "attrs": copy.deepcopy(attrs), "stamp": copy.deepcopy(stamp)})
            if wait_for_first_message:
                self._pending_start, self._held = ev, []
            else:
                self.recorder.emit(ev)
            return self.turn_id

    def end_turn(self, status: str) -> None:
        with self._lock:
            if self._ended or self.turn_id is None:
                return
            self._ended = True
            self._release(None)  # a retry that added no message at all
            for ev in self._tool_ends.values():  # a tool whose result message never came
                self.recorder.emit(ev)
            self._tool_ends.clear()
            self._flush_reply(None)
            ev = self._base("turn_ended", self.turn_id, self._turn_session)
            ev.update({"status": status, "last_message_id": self._last_mid})
            self.recorder.emit(ev)

    @property
    def in_turn(self) -> bool:
        return self.turn_id is not None and not self._ended

    def step_for_call(self, call_id: str) -> Optional[str]:
        """The tool step recorded for a call in this turn, if any."""
        with self._lock:
            return self._tool_steps.get(call_id) or None

    def note_message(self, message: dict[str, Any]) -> None:
        """Every message the engine adds (E21). Tracks the turn's last `mid`, releases a
        waiting `turn_started`, and sends a tool step's end once its result message exists."""
        if not self.enabled:
            return
        mid = message.get("mid")
        with self._lock:
            if not self.in_turn:
                return
            self._last_mid = mid
            if self._pending_start is not None:
                self._release(mid)
            if message.get("role") == "tool":
                ev = self._tool_ends.pop(str(message.get("tool_call_id") or ""), None)
                if ev is not None:
                    ev["message_id"] = mid
                    self._send(ev)
            elif message.get("role") == "assistant":
                self._flush_reply(mid)

    # -- steps ----------------------------------------------------------------------------------
    def start_step(self, *, kind: str, name: str, call_id: Optional[str] = None,
                   call_index: Optional[int] = None, parent_step_id: Optional[str] = None,
                   attrs: Optional[dict[str, Any]] = None,
                   content: Optional[dict[str, Any]] = None,
                   turn_id: Any = CURRENT, shadow: bool = False) -> Optional[str]:
        """`step_started`. The step belongs to the engine's current turn, or with `turn_id` to
        that turn: a step tagged with a turn that already ended is `after_turn` (§3.3, an
        after-turn title). A model step outside any turn is recorded with no turn; a tool
        step outside a turn is not recorded."""
        if not self.enabled:
            return None
        with self._lock:
            after = False
            if turn_id is CURRENT:
                if self.in_turn:
                    tid, sid = self.turn_id, self._turn_session
                elif kind == "tool":
                    return None
                else:
                    tid, sid = None, self._session_id() or ""
            else:
                tid = turn_id or None
                same = tid is not None and tid == self.turn_id
                sid = self._turn_session if same else (self._session_id() or "")
                # Turns of one engine run one after another: another turn of this engine has
                # ended, and so has this one once its end was sent.
                after = tid is not None and (not same or self._ended)
            step_id = new_ulid()
            ev = self._base("step_started", tid, sid)
            ev.update({"step_id": step_id, "parent_step_id": parent_step_id, "kind": kind,
                       "name": name, "call_id": call_id, "call_index": call_index,
                       "shadow": bool(shadow), "after_turn": after, "attrs": attrs or {},
                       "content": content or {}})
            self._ident[step_id] = {
                "kind": kind, "name": name, "parent_step_id": parent_step_id, "call_id": call_id,
                "call_index": call_index, "shadow": bool(shadow), "after_turn": after,
                "started_at": self._started_at(ev), "turn_id": tid, "session_id": sid,
            }
            if kind == "tool" and call_id:
                self._tool_steps[call_id] = step_id
            reserved = self._reserved.get(parent_step_id or "")
            if reserved is not None and kind == "model":
                reserved["children"].append(step_id)
            self._send(ev)
            return step_id

    def update_step(self, step_id: Optional[str], *, attrs: Optional[dict[str, Any]] = None,
                    content: Optional[dict[str, Any]] = None) -> None:
        """`step_updated`: a fact that arrives after the step started."""
        if not self.enabled or not step_id:
            return
        with self._lock:
            idn = self._ident.get(step_id) or {}
            ev = self._base("step_updated", idn.get("turn_id", self.turn_id), idn.get("session_id", self._turn_session))
            ev["step_id"] = step_id
            if attrs is not None:
                ev["attrs"] = attrs
            if content is not None:
                ev["content"] = content
            self._send(ev)

    def child_usage_reported(self, step_id: Optional[str]) -> bool:
        with self._lock:
            return self._child_usage.pop(step_id or "", False)

    # -- wait steps (E20) -----------------------------------------------------------------------
    def track_wait(self, call_id: str, step_id: Optional[str]) -> None:
        if step_id:
            with self._lock:
                self._waits[call_id] = step_id

    def end_wait(self, call_id: str) -> Optional[str]:
        with self._lock:
            return self._waits.pop(call_id, None)

    def wait_where(self, call_id: Optional[str], where: str) -> None:
        """Where the card for a waiting call is shown: `session` or `inbox` (§5.4). Reported
        by the Inbox when the card is added or moved there."""
        if not call_id:
            return
        with self._lock:
            step_id = self._waits.get(call_id)
        self.update_step(step_id, attrs={"where": where})

    # -- summary steps (E4) ---------------------------------------------------------------------
    def reserve_step(self) -> Optional[str]:
        """An id for a step written only at its end, from its identity copy (§3.2): a summary
        step exists only if a summary or trim happened, which is known only at the end. Model
        calls made inside it name it as their parent."""
        if not self.enabled:
            return None
        with self._lock:
            step_id = new_ulid()
            tid = self.turn_id if self.in_turn else None
            self._reserved[step_id] = {
                "at_ms": now_ms()[0], "turn_id": tid,
                "session_id": self._turn_session if tid else (self._session_id() or ""),
                "children": [],
            }
            return step_id

    def model_children(self, step_id: Optional[str]) -> list[str]:
        with self._lock:
            return list((self._reserved.get(step_id or "") or {}).get("children", []))

    def finish_reserved(self, step_id: Optional[str], *, kind: str, name: str, status: str,
                        attrs: dict[str, Any], wait_for_tokens_after: bool = False) -> None:
        """Write a reserved step whole, from its end alone; or drop it with `status=None`."""
        if not self.enabled or not step_id:
            return
        with self._lock:
            r = self._reserved.pop(step_id, None)
            if r is None:
                return
            ev = self._base("step_ended", r["turn_id"], r["session_id"])
            ev.update({"step_id": step_id, "status": status, "message_id": None, "attrs": attrs,
                       "content": {}, "kind": kind, "name": name, "parent_step_id": None,
                       "call_id": None, "call_index": None, "shadow": False, "after_turn": False,
                       "started_at": r["at_ms"]})
            self._send(ev)
            if wait_for_tokens_after:
                self._tokens_after_for = step_id

    def drop_reserved(self, step_id: Optional[str]) -> None:
        with self._lock:
            self._reserved.pop(step_id or "", None)

    def note_main_usage(self, usage: dict[str, Any]) -> None:
        """The first main call after a summary gives its `tokens_after` (§5.7)."""
        with self._lock:
            step_id, self._tokens_after_for = self._tokens_after_for, None
        if step_id and usage.get("reported"):
            after = (usage.get("input_fresh") or 0) + (usage.get("cache_read") or 0) + (usage.get("cache_write") or 0)
            self.update_step(step_id, attrs={"tokens_after": after})

    def _started_at(self, ev: dict[str, Any]) -> int:
        # The recorder anchors a step's start to its turn's start (§7.3); the identity copy
        # uses the wall clock, which is what a recorder without the turn would use.
        return ev["at_ms"]

    def end_step(self, step_id: Optional[str], *, status: str = "ok",
                 attrs: Optional[dict[str, Any]] = None, content: Optional[dict[str, Any]] = None,
                 message_id: Optional[str] = None, error: Optional[dict[str, Any]] = None,
                 wait_for_tool_message: bool = False,
                 wait_for_reply_message: bool = False) -> None:
        """`step_ended`. A tool step's end waits for its result message, a main model step's
        end for the reply message the engine adds next, so each carries that message's `mid`."""
        if not self.enabled or step_id is None:
            return
        with self._lock:
            idn = self._ident.pop(step_id, None)
            if idn is None:
                return
            ev = self._base("step_ended", idn["turn_id"], idn["session_id"])
            ev.update({"step_id": step_id, "status": status, "message_id": message_id,
                       "attrs": attrs or {}, "content": content or {}})
            if error is not None:
                ev["error"] = error
            ev.update({k: idn[k] for k in ("kind", "name", "parent_step_id", "call_id",
                                           "call_index", "shadow", "after_turn", "started_at")})
            if idn["kind"] == "model" and idn["parent_step_id"]:
                self._child_usage[idn["parent_step_id"]] = bool(((attrs or {}).get("usage") or {}).get("reported"))
            waiting_turn = idn["turn_id"] is not None and idn["turn_id"] == self.turn_id and not self._ended
            if wait_for_tool_message and idn["call_id"] and waiting_turn:
                self._tool_ends[idn["call_id"]] = ev
            elif wait_for_reply_message and waiting_turn:
                self._flush_reply(None)  # an earlier reply that never got a message
                self._reply_end = ev
            else:
                self._send(ev)

    def _flush_reply(self, mid: Optional[str]) -> None:
        ev, self._reply_end = self._reply_end, None
        if ev is not None:
            ev["message_id"] = mid
            self._send(ev)

    def instant_step(self, *, kind: str, name: str, status: str, call_id: Optional[str] = None,
                     call_index: Optional[int] = None, attrs: Optional[dict[str, Any]] = None,
                     message_id: Optional[str] = None) -> None:
        """A zero-length step at the moment of a decision: a call that never ran. Its start
        and end carry the same clocks."""
        if not self.enabled:
            return
        with self._lock:
            if not self.in_turn:
                return
            step_id = new_ulid()
            start = self._base("step_started", self.turn_id, self._turn_session)
            start.update({"step_id": step_id, "parent_step_id": None, "kind": kind, "name": name,
                          "call_id": call_id, "call_index": call_index, "shadow": False,
                          "after_turn": False, "attrs": {}, "content": {}})
            end = {k: start[k] for k in ("v", "at_ms", "mono_ms", "session_id", "turn_id")}
            end.update({"type": "step_ended", "step_id": step_id, "status": status,
                        "message_id": message_id, "attrs": attrs or {}, "content": {},
                        "kind": kind, "name": name, "parent_step_id": None, "call_id": call_id,
                        "call_index": call_index, "shadow": False, "after_turn": False,
                        "started_at": start["at_ms"]})
            self._send(start)
            self._send(end)

    def tool_finished(self, data: dict[str, Any], *, message_id: Optional[str],
                      call_index: Optional[int], attrs: dict[str, Any]) -> None:
        """A TOOL_FINISHED for a call that never reached `_execute_sync`: its zero-length step."""
        if not self.enabled:
            return
        call_id = str(data.get("tool_call_id") or "")
        with self._lock:
            if not self.in_turn or not call_id or call_id in self._tool_steps:
                return
            self._tool_steps[call_id] = ""
        status = _TOOL_STATUS.get(str(data.get("status") or ""), "error")
        if "superseded_worker_call" in data:
            status = "skipped"
        self.instant_step(kind="tool", name=str(data.get("name") or ""), status=status,
                          call_id=call_id, call_index=call_index, attrs=attrs,
                          message_id=message_id)


# -- the explore helper (E6) --------------------------------------------------------------------

_tool_run = threading.local()


class ToolRun:
    """What a running tool may know about itself: its step, its turn, and the engine's
    provider view and recording. Published for the tool's thread by `_execute_sync`."""

    def __init__(self, *, step_id: Optional[str], turn_id: Optional[str], view: Any,
                 recording: "TurnRecording") -> None:
        self.step_id, self.turn_id, self.view, self.recording = step_id, turn_id, view, recording


def current_tool_run() -> Optional[ToolRun]:
    return getattr(_tool_run, "run", None)


def set_tool_run(run: Optional[ToolRun]) -> Optional[ToolRun]:
    """Publish `run` for this thread; returns the one it replaces."""
    outer = getattr(_tool_run, "run", None)
    _tool_run.run = run
    return outer


class HelperRecording:
    """The recording of an explore helper's engine (§5.3, E6). Its steps go to the parent's
    recording, on the parent's turn, nested under the `explore` step. Its own turn events are
    not recorded as turns. Its tools run read-only in Plan mode, so their decision is
    `{helper: true}` instead of a full path."""

    # The helper's context is not the session's: no context step under the explore step.
    records_context = False

    def __init__(self, parent: TurnRecording, parent_step_id: str, turn_id: Optional[str]) -> None:
        self.parent, self.parent_step_id = parent, parent_step_id
        self.turn_id = turn_id

    @property
    def recorder(self) -> Any:
        return self.parent.recorder

    @property
    def enabled(self) -> bool:
        return self.parent.enabled

    @property
    def in_turn(self) -> bool:
        return True

    def begin_turn(self, **_kw: Any) -> Optional[str]:
        return self.turn_id

    def end_turn(self, status: str) -> None:
        pass

    def note_message(self, message: dict[str, Any]) -> None:
        pass

    def note_main_usage(self, usage: dict[str, Any]) -> None:
        pass

    def start_step(self, **kw: Any) -> Optional[str]:
        kw.setdefault("parent_step_id", None)
        kw["parent_step_id"] = kw["parent_step_id"] or self.parent_step_id
        kw["turn_id"] = self.turn_id
        return self.parent.start_step(**kw)

    def end_step(self, step_id: Optional[str], **kw: Any) -> None:
        kw.pop("wait_for_tool_message", None)
        kw.pop("wait_for_reply_message", None)
        attrs = kw.get("attrs")
        if attrs is not None and "decision" in attrs:
            attrs["decision"] = {"helper": True}
        self.parent.end_step(step_id, **kw)

    def instant_step(self, **kw: Any) -> None:
        attrs = kw.get("attrs")
        if attrs is not None and "decision" in attrs:
            attrs["decision"] = {"helper": True}
        step_id = self.start_step(kind=kw["kind"], name=kw["name"], call_id=kw.get("call_id"),
                                  call_index=kw.get("call_index"))
        self.parent.end_step(step_id, status=kw["status"], attrs=attrs,
                             message_id=kw.get("message_id"))

    def tool_finished(self, data: dict[str, Any], **kw: Any) -> None:
        pass  # a helper's refused calls stay in its own transcript

    def step_for_call(self, call_id: str) -> Optional[str]:
        return None

    def update_step(self, step_id: Optional[str], **kw: Any) -> None:
        self.parent.update_step(step_id, **kw)

    def track_wait(self, call_id: str, step_id: Optional[str]) -> None:
        pass

    def end_wait(self, call_id: str) -> Optional[str]:
        return None

    def wait_where(self, call_id: Optional[str], where: str) -> None:
        pass

    def reserve_step(self) -> Optional[str]:
        return None

    def model_children(self, step_id: Optional[str]) -> list[str]:
        return []

    def finish_reserved(self, *a: Any, **kw: Any) -> None:
        pass

    def drop_reserved(self, step_id: Optional[str]) -> None:
        pass

    def child_usage_reported(self, step_id: Optional[str]) -> bool:
        return self.parent.child_usage_reported(step_id)
