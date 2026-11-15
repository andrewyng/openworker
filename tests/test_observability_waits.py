"""Observability E20 and E4: wait steps, decision paths and summary steps.

- E20: an approval card is a wait step from the moment it is shown to the answer, linked to
  its tool call; its outcome and who answered are recorded.
- E4: every tool call that went through the permission flow carries its decision path
  (`attrs.decision = {path, origin}`); a summary or trim is a summary step with its model
  call nested inside it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from coworker import compaction as _compaction
from coworker.engine import ApprovalOutcome, TurnEngine
from coworker.observability import Recorder
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.providers.base import TokenUsage
from coworker.tools import ToolRegistry


class _Meta:
    category: str = ""
    risk_level: str = "high"
    requires_approval: bool = False


class _ReadMeta:
    category: str = ""
    risk_level: str = "low"
    requires_approval: bool = False


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def run_shell(command: str) -> dict:
    """Run a command.

    Args:
        command: the command line
    """
    return {"command": command, "exit_code": 0, "output": ""}


def look(path: str) -> dict:
    """Read something.

    Args:
        path: what to read
    """
    return {"path": path, "text": "ok"}


def _engine(tmp_path, turns, *, answer=ApprovalOutcome.ONCE, mode=Mode.INTERACTIVE):
    registry = ToolRegistry()
    registry.register(run_shell, metadata=_Meta())
    registry.register(look, metadata=_ReadMeta())

    async def approver(request):
        return answer

    engine = TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=mode),
        model="openai:model-a",
        approver=approver,
    )
    engine.audit_context = {"session_id": "sess_w"}
    engine.is_attended = lambda: True
    engine.resolver_of = lambda call_id: "devika"
    rec = Recorder()
    engine.recorder = rec
    return engine, rec


def _call(cid, name, args):
    return AssistantTurn(tool_calls=[ToolCall(id=cid, name=name, arguments=args)], finish_reason="tool_calls")


def _run(engine, text="go"):
    async def go():
        return [ev async for ev in engine.run(text, trigger="user")]

    return asyncio.run(go())


def _steps(rec, kind):
    return [s for s in rec.as_dict()["steps"] if s["kind"] == kind]


def test_an_approved_card_is_a_wait_step_and_the_call_records_its_path(tmp_path):
    engine, rec = _engine(tmp_path, [_call("c1", "run_shell", {"command": "make build"}),
                                     AssistantTurn(text="built")])
    _run(engine)
    [wait] = _steps(rec, "wait")
    assert (wait["name"], wait["call_id"], wait["status"]) == ("approval", "c1", "ok")
    assert wait["attrs"] == {"for_call_id": "c1", "kind": "approval", "attended": True,
                             "where": "session", "outcome": "approved", "inbox_at": None}
    assert wait["content"] == {"resolved_by": "devika"}
    [tool] = _steps(rec, "tool")
    assert tool["status"] == "ok"
    assert tool["attrs"]["decision"] == {
        "path": [{"stage": "permission", "outcome": "needs_user"},
                 {"stage": "card", "outcome": "shown_session"},
                 {"stage": "resolved", "outcome": "approved_once"}],
        "origin": "user",
    }
    # The wait comes before the tool in the canonical order.
    kinds = [s["kind"] for s in rec.as_dict()["steps"]]
    assert kinds.index("wait") < kinds.index("tool")


def test_a_denied_card_is_a_zero_length_denied_step_refused_by_the_user(tmp_path):
    engine, rec = _engine(tmp_path, [_call("c1", "run_shell", {"command": "rm -rf build"}),
                                     AssistantTurn(text="ok, not doing it")],
                          answer=ApprovalOutcome.DENY)
    _run(engine)
    [wait] = _steps(rec, "wait")
    assert wait["attrs"]["outcome"] == "denied"
    [tool] = _steps(rec, "tool")
    assert (tool["status"], tool["duration_ms"]) == ("denied", 0)
    assert tool["attrs"]["decision"]["origin"] == "user"
    assert tool["attrs"]["decision"]["path"][-1] == {"stage": "resolved", "outcome": "denied"}


def test_a_session_grant_is_recorded_as_its_origin(tmp_path):
    engine, rec = _engine(tmp_path, [_call("c1", "run_shell", {"command": "pytest"}),
                                     _call("c2", "run_shell", {"command": "pytest"}),
                                     AssistantTurn(text="done")],
                          answer=ApprovalOutcome.ALWAYS_COMMAND)
    _run(engine)
    first, second = _steps(rec, "tool")
    assert first["attrs"]["decision"]["path"][-1] == {
        "stage": "resolved", "outcome": "approved_session", "detail": "always_command"}
    assert second["attrs"]["decision"] == {
        "path": [{"stage": "permission", "outcome": "allowed"},
                 {"stage": "grant", "outcome": "session_grant"}],
        "origin": "session_grant",
    }
    assert len(_steps(rec, "wait")) == 1  # no card the second time


def test_a_low_risk_read_needs_no_one(tmp_path):
    engine, rec = _engine(tmp_path, [_call("c1", "look", {"path": "x"}), AssistantTurn(text="ok")])
    _run(engine)
    [tool] = _steps(rec, "tool")
    assert tool["attrs"]["decision"] == {
        "path": [{"stage": "permission", "outcome": "allowed", "detail": "low_risk"}],
        "origin": "low_risk",
    }
    assert _steps(rec, "wait") == []


def test_full_access_is_bypass(tmp_path):
    engine, rec = _engine(tmp_path, [_call("c1", "run_shell", {"command": "make"}), AssistantTurn(text="ok")],
                          mode=Mode.BYPASS_APPROVALS)
    _run(engine)
    [tool] = _steps(rec, "tool")
    assert tool["attrs"]["decision"]["origin"] == "bypass"


def test_a_summary_is_a_step_with_its_model_call_inside(tmp_path, monkeypatch):
    engine, rec = _engine(tmp_path, [
        AssistantTurn(text="a short summary", usage=TokenUsage(input=900, output=40)),
        AssistantTurn(text="next", usage=TokenUsage(input=300, output=5, cache_read=200)),
    ])
    engine._last_context_tokens = 47000

    def fake_build_state(messages, *, provider, model, **kw):
        provider.complete(model=model, messages=[{"role": "user", "content": "summarize"}])
        return SimpleNamespace(boundary_index=0, carries_outside=False, transcript_path=None)

    monkeypatch.setattr(_compaction, "build_state", fake_build_state)

    async def go():
        return await engine._compact_now()

    assert asyncio.run(go()) is not None
    engine.provider.complete(model=engine.model, messages=[])  # the next main call
    [summary] = _steps(rec, "summary")
    [model] = [s for s in rec.as_dict()["steps"] if s["kind"] == "model" and s["attrs"]["purpose"] == "summary"]
    assert (summary["name"], summary["status"]) == ("summary", "ok")
    assert model["parent_step_id"] == summary["step_id"]
    assert summary["attrs"]["model_step_id"] == model["step_id"]
    assert summary["attrs"]["reason"] == "threshold"
    assert summary["attrs"]["tokens_before"] == 47000
    assert summary["attrs"]["tokens_after"] == 500


def test_no_summary_step_when_nothing_was_summarised(tmp_path, monkeypatch):
    engine, rec = _engine(tmp_path, [])
    monkeypatch.setattr(_compaction, "build_state", lambda messages, **kw: None)

    async def go():
        return await engine._compact_now()

    assert asyncio.run(go()) is None
    assert _steps(rec, "summary") == []


def test_a_stop_while_a_plan_card_waits_is_interrupted_not_refused(tmp_path):
    # Round-2 in-app check: the plan handler answers "denied" when the turn is stopped, so
    # the propose_plan step read as refused. A Stop is recorded as interrupted.
    engine, rec = _engine(tmp_path, [_call("p1", "propose_plan", {"plan": "do it"}), AssistantTurn(text="x")],
                          mode=Mode.PLAN)

    async def never(args, call_id):
        await asyncio.sleep(30)

    engine.plan_approver = never

    async def go():
        async def stop_soon():
            await asyncio.sleep(0.3)
            engine.request_interrupt()
        task = asyncio.create_task(stop_soon())
        [ev async for ev in engine.run("plan", trigger="user")]
        await task

    asyncio.run(go())
    [wait] = _steps(rec, "wait")
    [plan] = [s for s in _steps(rec, "tool") if s["name"] == "propose_plan"]
    assert wait["attrs"]["outcome"] == "interrupted"
    assert plan["status"] == "interrupted"


def test_a_stop_while_an_approval_card_waits_is_interrupted_not_refused(tmp_path):
    # Round-2 in-app check (F3): the wait and the path said interrupted, the tool step denied.
    engine, rec = _engine(tmp_path, [_call("c1", "run_shell", {"command": "make"}), AssistantTurn(text="x")])

    async def never(request):
        await asyncio.sleep(30)

    engine.approver = never

    async def go():
        async def stop_soon():
            await asyncio.sleep(0.3)
            engine.request_interrupt()
        task = asyncio.create_task(stop_soon())
        [ev async for ev in engine.run("go", trigger="user")]
        await task

    asyncio.run(go())
    [tool] = _steps(rec, "tool")
    assert tool["status"] == "interrupted"
    assert tool["attrs"]["decision"]["path"][-1] == {"stage": "resolved", "outcome": "interrupted"}


def test_a_tool_killed_by_stop_is_interrupted(tmp_path):
    # Round-2 in-app check (F2): a shell command killed by Stop answers "interrupted by
    # user" and the step said ok.
    engine, rec = _engine(tmp_path, [_call("c1", "run_shell", {"command": "sleep 40"}), AssistantTurn(text="x")],
                          mode=Mode.BYPASS_APPROVALS)

    def run_shell(command: str) -> dict:
        """Run a command.

        Args:
            command: the command line
        """
        engine.request_interrupt()  # the Stop arrives while it runs; it is killed
        return {"command": command, "exit_code": None, "error": "interrupted by user"}

    registry = ToolRegistry()
    registry.register(run_shell, metadata=_Meta())
    engine.registry = registry
    _run(engine)
    [tool] = _steps(rec, "tool")
    assert tool["status"] == "interrupted"
