"""Async subagents (OPE-253/254): background extra hands of the same coworker.

`spawn_agent` returns a task id immediately — the mirror of `run_shell`'s background
contract — and the child runs with its own fresh context while the parent keeps
working. The child is the SAME identity, not a new actor:

- `read_only` (default): the explorer toolset in plan mode — can never write or
  shell, so it never needs an approval and runs anywhere (own thread + loop, like
  the synchronous `explore`).
- `inherit`: the parent's tools (minus the spawn tools — no recursion), the parent's
  PermissionEngine (same mode, same session grants) and the parent's approver. The
  human stays the only approver (OPE-255): a child's ask parks in the parent
  session's one approval stream, labeled with the subagent's name, and its
  tool-call ids are prefixed with the task id so the Inbox's
  (session_id, tool_call_id) idempotency key can't collide with the parent's.
  Inherit children run on the server loop (`registry.loop`), where those approver
  closures are safe to await.

Approval deadline (OPE-255 ruling): no timeout by default — a child waits on its
card forever, like a main session. A parent may pass `approval_timeout` at spawn
(floor 60s); expiry resolves the ask as DECLINED, never approved, the card flips to
an expired state instead of vanishing, and the child's tool result says the request
expired so the model continues honestly without the step.

Delivery (OPE-256): on completion the registry's `deliver` hook (the manager wires
it to `deliver_to_session`) steers a busy parent at the next iteration boundary or
wakes an idle one with the report; without a manager the report is queued as
steering on the parent engine directly. Completion also appends an `agent_task`
notice carrying the child's per-model token usage, which `sessions.usage_totals`
folds into the session's totals (OPE-258).
"""

from __future__ import annotations

import asyncio
import functools
import json
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai

from ..engine import ApprovalOutcome, EventType, PermissionRequest, TurnEngine
from ..observability.engine_hooks import HelperRecording, current_tool_run
from ..observability.provider_view import tag as tag_provider
from .subagent import build_explorer_engine

_MAX_ACTIVE = 3
_CHILD_MAX_ITERATIONS = 25
_MIN_APPROVAL_TIMEOUT = 60.0
# Supplemental record (owner ruling 2026-11-07): last N finished runs per session,
# reports truncated — the full text already reached the parent transcript at delivery.
_KEEP_FINISHED = 50
_REPORT_STORE_MAX = 8192


class AgentTaskStore:
    """`agent_tasks.json` — the supplemental session record for subagent runs, beside
    the wake store. Written on every task transition (a subagent finishes BETWEEN turn
    checkpoints, so riding the session record would leave it undurable until the next
    save). One file, keyed by session id; atomic tmp-and-replace like WakeStore."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        if self.path and self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self._sessions = {
                    str(k): [dict(r) for r in v]
                    for k, v in (saved.get("sessions") or {}).items()
                }
            except Exception:
                self._sessions = {}

    def _save(self) -> None:  # caller holds the lock
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"sessions": self._sessions}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def load(self, session_id: str) -> list[dict[str, Any]]:
        """This session's persisted runs. A run persisted as live is honestly lost —
        its engine died with the app — and is flipped to an error, not a phantom."""
        with self._lock:
            rows = [dict(r) for r in self._sessions.get(session_id, [])]
        changed = False
        for r in rows:
            if r.get("status") in ("running", "waiting"):
                r["status"] = "error"
                r["note"] = "lost when the app restarted"
                r.setdefault("finished", time.time())
                changed = True
        if changed:
            self.put(session_id, rows)
        return rows

    def put(self, session_id: str, rows: list[dict[str, Any]]) -> None:
        live = [r for r in rows if r.get("status") in ("running", "waiting")]
        done = [r for r in rows if r.get("status") not in ("running", "waiting")]
        done.sort(key=lambda r: r.get("finished") or r.get("created") or 0, reverse=True)
        keep = live + done[:_KEEP_FINISHED]
        for r in keep:
            rep = r.get("report") or ""
            if len(rep) > _REPORT_STORE_MAX:
                r["report"] = (
                    rep[:_REPORT_STORE_MAX]
                    + "\n… (truncated in the store — the full report was delivered in the session)"
                )
        with self._lock:
            self._sessions[session_id] = keep
            self._save()

    def drop(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._save()

INHERIT_INSTRUCTIONS = """You are a background subagent of the main coworker, working inside the same \
workspace with the same tools and the same permissions. The user sees your approval requests in the \
main session, labeled with your name — ask only for what the task truly needs. If an approval is \
declined or expires, do not retry it: continue without that step and note the gap.

Your final message is your report — it goes back to the main agent, not to the user. Make it \
self-contained: answer the task directly, reference code as path:line, list anything you changed, \
and note what you could not do and why."""


@dataclass
class AgentTask:
    id: str
    label: str
    task: str
    mode: str  # "read_only" | "inherit"
    status: str = "running"  # running | waiting | done | error | canceled
    report: str = ""
    note: str = ""
    created: float = field(default_factory=time.time)
    finished: Optional[float] = None
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    icon_seed: int = 0
    approval_timeout: Optional[float] = None
    engine: Optional[TurnEngine] = None
    # The loop the child runs on (its own in read_only, the server's in inherit) —
    # how cancel and follow-ups reach it thread-safely.
    loop: Optional[asyncio.AbstractEventLoop] = None
    # Observability (OPE-244 integration): the child's steps and model calls recorded
    # on the parent's turn, nested under the spawn_agent step — the same HelperRecording
    # contract as explore, so the Steps panel and the Usage ledger see background
    # children too. None when recording was off at spawn time.
    obs: Optional[Any] = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "task": self.task,
            "mode": self.mode,
            "status": self.status,
            "note": self.note,
            "created": self.created,
            "finished": self.finished,
            "usage": self.usage,
            "icon_seed": self.icon_seed,
        }


class AgentTaskRegistry:
    """Per-session registry of background subagent runs (in-memory v1, like the
    background shells: it dies with the app, never with the turn)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, AgentTask] = {}
        self._counter = 0
        self.parent: Optional[TurnEngine] = None
        # Manager-wired hooks; every one is optional so the CLI still works.
        self.loop: Optional[asyncio.AbstractEventLoop] = None  # the server loop
        self.deliver: Optional[Callable[[str, dict[str, Any]], None]] = None
        self.on_event: Optional[Callable[[dict[str, Any]], None]] = None
        # Resolves a parked Inbox card whose wait expired (prefixed tool_call_id).
        self.resolve_expired: Optional[Callable[[str], None]] = None
        # The supplemental record (AgentTaskStore), manager-attached.
        self.store: Optional[AgentTaskStore] = None
        self.session_id: str = ""

    def bind(self, parent: TurnEngine) -> None:
        self.parent = parent

    def attach_store(self, store: AgentTaskStore, session_id: str) -> None:
        """Rehydrate this session's history (done rows only — a live run's engine
        never survives a restart, and load() already marked those lost)."""
        if self.store is store and self.session_id == session_id:
            return
        self.store, self.session_id = store, session_id
        with self._lock:
            known = set(self._tasks)
            for row in store.load(session_id):
                tid = str(row.get("id") or "")
                if not tid or tid in known:
                    continue
                self._tasks[tid] = AgentTask(
                    id=tid,
                    label=str(row.get("label") or tid),
                    task=str(row.get("task") or ""),
                    mode=str(row.get("mode") or "read_only"),
                    status=str(row.get("status") or "error"),
                    report=str(row.get("report") or ""),
                    note=str(row.get("note") or ""),
                    created=float(row.get("created") or 0),
                    finished=row.get("finished"),
                    usage=dict(row.get("usage") or {}),
                    icon_seed=int(row.get("icon_seed") or 0),
                )
                # Fresh ids must never collide with rehydrated ones.
                try:
                    self._counter = max(self._counter, int(tid.split("-")[-1]))
                except ValueError:
                    pass

    def _persist(self) -> None:
        if self.store is None or not self.session_id:
            return
        with self._lock:
            rows = [{**t.snapshot(), "report": t.report} for t in self._tasks.values()]
        try:
            self.store.put(self.session_id, rows)
        except Exception:
            pass  # a persistence hiccup must never take a child run down

    def new_task(self, label: str, task: str, mode: str, timeout: Optional[float]) -> AgentTask:
        with self._lock:
            self._counter += 1
            t = AgentTask(
                id=f"agent-{self._counter}",
                label=label,
                task=task,
                mode=mode,
                # Raw counter: the GUI derives shape (mod 10) and color family from it,
                # so the full 10×8 identity space is usable and stable per run.
                icon_seed=self._counter,
                approval_timeout=timeout,
            )
            self._tasks[t.id] = t
            return t

    def get(self, task_id: str) -> Optional[AgentTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status in ("running", "waiting"))

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [t.snapshot() for t in sorted(self._tasks.values(), key=lambda t: t.created, reverse=True)]

    # -- internals ---------------------------------------------------------------

    def _emit(self, kind: str, task: AgentTask) -> None:
        # Every transition goes through here: persist first, then tell the GUI.
        self._persist()
        if self.on_event:
            try:
                self.on_event({"kind": kind, **task.snapshot()})
            except Exception:
                pass

    async def _consume(self, task: AgentTask, user_input: str) -> None:
        """Drive one child turn, folding usage and watching for approval waits."""
        engine = task.engine
        assert engine is not None
        task.loop = asyncio.get_running_loop()
        report, status = "", "unknown"
        try:
            async for event in engine.run(user_input):
                if event.type == EventType.PERMISSION_REQUIRED:
                    task.status = "waiting"
                    self._emit("agent_task_waiting", task)
                elif event.type == EventType.TOOL_FINISHED and task.status == "waiting":
                    task.status = "running"
                    self._emit("agent_task_running", task)
                elif event.type == EventType.ASSISTANT_MESSAGE:
                    if event.data.get("text"):
                        report = event.data["text"]
                    u = event.data.get("usage")
                    if isinstance(u, dict):
                        key = str(u.get("model") or "unknown")
                        row = task.usage.setdefault(
                            key, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "turns": 0}
                        )
                        for f in ("input", "output", "cache_read", "cache_write"):
                            row[f] += int(u.get(f) or 0)
                        row["turns"] += 1
                elif event.type == EventType.TURN_END:
                    status = event.data.get("status", "unknown")
                elif event.type == EventType.INTERRUPTED:
                    status = "canceled"
                elif event.type == EventType.ERROR:
                    status = f"error: {event.data.get('error', '')}"
        except Exception as exc:  # the child must never take the parent surface down
            status = f"error: {exc}"
        self._finish(task, report, status)

    def _finish(self, task: AgentTask, report: str, status: str) -> None:
        # The spawn step's helper pill (attrs.helper, same shape as explore's): model
        # calls, tool calls and tokens the child spent, stamped when it ends. Display
        # only — the Usage ledger already counted its steps as they happened.
        if task.obs is not None:
            try:
                task.obs.parent.update_step(
                    task.obs.parent_step_id,
                    attrs={"helper": task.obs.summary({"canceled": "interrupted"}.get(status, status))},
                )
            except Exception:
                pass  # a recording hiccup must never take the run result down
        task.finished = time.time()
        task.report = report
        if status == "completed":
            task.status = "done"
        elif status == "canceled":
            task.status, task.note = "canceled", "stopped by request"
        elif status.startswith("error"):
            task.status, task.note = "error", status
        else:
            task.status, task.note = "done", f"stopped early ({status}); the report may be partial"
        self._emit("agent_task_finished", task)
        self._deliver_report(task)

    def _deliver_report(self, task: AgentTask) -> None:
        label = task.label or task.id
        if task.status == "canceled":
            return  # the canceller knows; no wake for a run the user/parent stopped
        body = task.report or "(no report)"
        note = f" ({task.note})" if task.note else ""
        text = f"[Subagent '{label}' {task.status}{note}] {body}"
        # The usage notice first: it must be in the transcript before the delivery
        # turn saves, so usage_totals folds it exactly once (OPE-258).
        parent = self.parent
        if parent is not None:
            append = functools.partial(
                parent._append_notice,
                "agent_task",
                f"Subagent '{label}' {task.status}",
                agent_task=task.id,
                agent_usage=dict(task.usage),
            )
            try:
                # call_soon_threadsafe takes no callback kwargs — hence the partial.
                if self.loop is not None:
                    self.loop.call_soon_threadsafe(append)
                else:
                    append()
            except Exception:
                pass  # the usage notice must never block the report delivery below
        source = {"connector": "subagent", "title": label, "task_id": task.id}
        if self.deliver is not None:
            try:
                self.deliver(text, source)
                return
            except Exception:
                pass
        # No manager (CLI/tests): queue as steering; the next parent turn picks it up.
        if parent is not None:
            parent.queue_steering(text, source=source)

    def _wrap_approver(self, task: AgentTask, child: TurnEngine):
        async def approver(req: PermissionRequest) -> ApprovalOutcome:
            # Resolved at CALL time: the manager overwrites the parent's approver on
            # every reconnect, and the child must always ask through the live one.
            parent_approver = getattr(self.parent, "approver", None)
            if parent_approver is None:
                return ApprovalOutcome.DENY
            # Prefix the id (Inbox idempotency is (session_id, tool_call_id)) and name
            # the asker so the card says which subagent wants what.
            prefixed = f"{task.id}:{req.tool_call_id or ''}"
            labeled = replace(
                req,
                tool_call_id=prefixed,
                reason=f"[subagent: {task.label}] {req.reason or ''}".strip(),
            )
            if not task.approval_timeout:
                return await parent_approver(labeled)
            try:
                return await asyncio.wait_for(parent_approver(labeled), task.approval_timeout)
            except asyncio.TimeoutError:
                secs = int(task.approval_timeout)
                if req.tool_call_id:
                    child._deny_notes[req.tool_call_id] = (
                        f"approval request expired after {secs}s and was treated as NOT "
                        "approved — continue without this step and note the gap in your report"
                    )
                if self.resolve_expired:
                    try:
                        self.resolve_expired(prefixed)
                    except Exception:
                        pass
                return ApprovalOutcome.DENY

        return approver


def agent_task_tools(
    *,
    workspace: str | Path,
    provider: Any,
    model: str,
    model_settings: Optional[dict[str, Any]] = None,
) -> tuple[list, AgentTaskRegistry]:
    """The four background-subagent tools plus the registry the server reads.
    `registry.bind(engine)` must be called once the parent engine exists."""
    registry = AgentTaskRegistry()
    ws = str(Path(workspace).resolve())

    def _build_child(task: AgentTask, child_provider: Any) -> TurnEngine:
        if task.mode == "inherit":
            parent = registry.parent
            assert parent is not None
            child_registry = parent.registry.clone(
                exclude={"spawn_agent", "agent_task_output", "agent_send", "agent_task_cancel", "explore"}
            )
            child = TurnEngine(
                provider=child_provider,
                registry=child_registry,
                permissions=parent.permissions,  # same mode, same session grants
                model=model,
                instructions=INHERIT_INSTRUCTIONS,
                max_iterations=_CHILD_MAX_ITERATIONS,
                model_settings=model_settings,
            )
            wrapped = registry._wrap_approver(task, child)
            if wrapped is not None:
                child.approver = wrapped
            # Attendance follows the parent: in auto mode the child auto-answers the
            # same way; in ask mode its cards reach the same person.
            for attr in ("is_attended", "attendance", "cards_reach_person"):
                val = getattr(parent, attr, None)
                if val is not None:
                    setattr(child, attr, val)
            return child
        return build_explorer_engine(
            workspace=ws,
            provider=child_provider,
            model=model,
            model_settings=model_settings,
            max_iterations=_CHILD_MAX_ITERATIONS,
        )

    def _launch(task: AgentTask, user_input: str) -> None:
        if task.mode == "inherit" and registry.loop is not None:
            asyncio.run_coroutine_threadsafe(registry._consume(task, user_input), registry.loop)
        else:
            threading.Thread(
                target=lambda: asyncio.run(registry._consume(task, user_input)),
                name=f"subagent-{task.id}",
                daemon=True,
            ).start()

    def spawn_agent(
        task: str,
        label: str = "",
        mode: str = "read_only",
        approval_timeout: Optional[float] = None,
    ) -> dict:
        """Spawn a background subagent with its own fresh context and keep working —
        you get the report automatically when it finishes, so do NOT poll for it.
        `mode="read_only"` (default) researches the workspace and can never write;
        `mode="inherit"` gets your tools and permissions, and its approval requests go
        to the user labeled with this subagent's name. Use read_only unless the task
        must change something. State the task precisely and say what the report
        should include.

        Args:
            task (str): The full task, with constraints and the expected report shape.
            label (str): A short human-readable name for the run (2-4 words), shown in
                the side panel and on approval cards.
            mode (str): "read_only" (default) or "inherit".
            approval_timeout (float): Optional, inherit mode only — seconds an approval
                request may wait before it expires as NOT approved (minimum 60). Omit
                to wait indefinitely, like the main session.
        """
        if mode not in ("read_only", "inherit"):
            return {"error": f"unknown mode: {mode!r} (use 'read_only' or 'inherit')"}
        if mode == "inherit" and registry.loop is None:
            return {"error": "inherit mode is only available under the desktop app session; use read_only"}
        if registry.active_count() >= _MAX_ACTIVE:
            return {"error": f"already {_MAX_ACTIVE} subagents running — wait for one to finish"}
        timeout = None
        if approval_timeout:
            timeout = max(float(approval_timeout), _MIN_APPROVAL_TIMEOUT)
        t = registry.new_task(label.strip() or task.strip()[:40], task, mode, timeout)
        # Observability (OPE-244 integration): the child records under THIS spawn step,
        # on the parent's turn, exactly like explore's helper — so background runs show
        # in the Steps panel and their tokens reach the Usage ledger. The recording
        # outlives this turn by design; late steps on an ended turn are already a
        # recorder concept (the after-turn title step).
        run = current_tool_run()
        child_provider: Any = provider
        if run is not None and run.step_id is not None:
            t.obs = HelperRecording(run.recording, run.step_id, run.turn_id, detached=True)
            child_provider = tag_provider(
                run.view, purpose="helper", parent=run.step_id, turn=run.turn_id, sink=t.obs
            )
        t.engine = _build_child(t, child_provider)
        if t.obs is not None:
            t.engine._obs = t.obs
        registry._emit("agent_task_started", t)
        _launch(t, task)
        return {
            "task_id": t.id,
            "label": t.label,
            "mode": mode,
            "note": "running in the background — the report arrives on its own when it finishes; continue your work",
        }

    def agent_task_output(task_id: str) -> dict:
        """Peek at a background subagent: status, token usage, and the report once it
        finished. The report is also delivered to you automatically — only use this to
        check on a run you suspect is stuck, not to poll.

        Args:
            task_id (str): The id spawn_agent returned (e.g. "agent-1").
        """
        t = registry.get(task_id)
        if t is None:
            return {"error": f"no such subagent task: {task_id}"}
        out = t.snapshot()
        if t.status in ("done", "error"):
            out["report"] = t.report
        return out

    def agent_send(task_id: str, message: str) -> dict:
        """Send a follow-up message to a subagent. A running one sees it at its next
        step; a finished one starts a new turn with its context intact and reports
        again when done.

        Args:
            task_id (str): The id spawn_agent returned.
            message (str): The follow-up or steering instruction.
        """
        t = registry.get(task_id)
        if t is None:
            return {"error": f"no such subagent task: {task_id}"}
        if t.engine is None:
            return {"error": "this subagent's context did not survive an app restart — spawn a new one"}
        framed = f"[Main agent] {message}"
        if t.status in ("running", "waiting"):
            if t.loop is not None:
                t.loop.call_soon_threadsafe(t.engine.queue_steering, framed)
            else:
                t.engine.queue_steering(framed)
            return {"task_id": t.id, "status": t.status, "note": "delivered — it is picked up at the next step"}
        if t.status == "canceled":
            return {"error": "this subagent was canceled; spawn a new one"}
        t.status, t.finished, t.note = "running", None, ""
        registry._emit("agent_task_started", t)
        _launch(t, framed)
        return {"task_id": t.id, "status": "running", "note": "continuing with its earlier context"}

    def agent_task_cancel(task_id: str) -> dict:
        """Stop a background subagent. Already-finished work is kept; its report (if
        any) stays readable via agent_task_output.

        Args:
            task_id (str): The id spawn_agent returned.
        """
        t = registry.get(task_id)
        if t is None:
            return {"error": f"no such subagent task: {task_id}"}
        if t.engine is None or t.status not in ("running", "waiting"):
            return {"task_id": t.id, "status": t.status, "note": "already finished"}
        if t.loop is not None:
            t.loop.call_soon_threadsafe(t.engine.request_interrupt)
        else:
            t.engine.request_interrupt()
        return {"task_id": t.id, "status": "canceling"}

    meta = lambda risk, caps, approval: ai.ToolMetadata(  # noqa: E731
        category="agent", risk_level=risk, capabilities=caps, requires_approval=approval
    )
    tools = [
        # spawn itself never writes; what the CHILD does is gated by the child's own
        # calls (read_only can't write at all; inherit asks through the parent's
        # approver). So the spawn family stays approval-free, like explore.
        ai.tool(spawn_agent, metadata=meta("low", ["search"], False)),
        ai.tool(agent_task_output, metadata=meta("low", ["search"], False)),
        ai.tool(agent_send, metadata=meta("low", ["search"], False)),
        ai.tool(agent_task_cancel, metadata=meta("low", ["search"], False)),
    ]
    return tools, registry
