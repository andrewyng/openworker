"""Durable execution records (#443): a side-effecting call whose adapter already ran is
never replayed by resume.

The crash window: approval resolved → adapter applies the side effect → process dies
before `_record_result` checkpoints the `role="tool"` message. Resume used to see an
unanswered call, find the resolved approval, and fire the adapter again — at-least-once
where the approval model promises once. These tests pin the three resume outcomes:

- no record (never dispatched) → execute, as before;
- `succeeded` → reconstruct the tool result from its snapshot, adapter does not run;
- `dispatched` only (outcome unknown) → honest error, adapter does not run.
"""

import asyncio
import json
import time
from pathlib import Path

import aisuite as ai

from coworker.engine import ApprovalOutcome, TurnEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.server.manager import SessionManager
from coworker.tool_exec import ToolExecutionStore


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool(name, args, call_id):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)])


def _make_deliver(counter: Path):
    """A side-effecting tool with an observable, non-idempotent effect: every run appends
    a line. `requires_approval=True` classifies it EXTERNAL, so the engine guards it."""

    def deliver_note(note: str) -> dict:
        with open(counter, "a", encoding="utf-8") as fh:
            fh.write(note + "\n")
        return {"ok": True, "delivered": note}

    return ai.tool(
        deliver_note,
        metadata=ai.ToolMetadata(
            category="external",
            risk_level="high",
            requires_approval=True,
        ),
    )


def _counter_lines(counter: Path) -> list[str]:
    return counter.read_text(encoding="utf-8").splitlines() if counter.exists() else []


def _assistant_call(call_id: str, note: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "deliver_note",
                    "arguments": json.dumps({"note": note}),
                },
            }
        ],
        "ts": time.time(),
    }


def _final_tool_messages(mgr, sid):
    rec = mgr.session_store.load(sid)
    return [m for m in rec.messages if m.get("role") == "tool"]


# -- store ---------------------------------------------------------------------


def test_store_roundtrip(tmp_path):
    store = ToolExecutionStore(tmp_path / "tool_executions.json")
    assert store.get("s1", "c1") is None
    store.dispatched("s1", "c1", "deliver_note")
    rec = store.get("s1", "c1")
    assert rec["state"] == "dispatched" and rec["tool"] == "deliver_note"
    store.succeeded("s1", "c1", "deliver_note", "ok", {"ok": True})
    rec = store.get("s1", "c1")
    assert rec["state"] == "succeeded" and rec["status"] == "ok"
    assert rec["result"] == {"ok": True}
    # A second session's call with the same id is a different key.
    assert store.get("s2", "c1") is None
    # Reload from disk.
    again = ToolExecutionStore(tmp_path / "tool_executions.json")
    assert again.get("s1", "c1")["state"] == "succeeded"


def test_execute_sync_marks_dispatched_then_succeeded(tmp_path):
    counter = tmp_path / "delivered.txt"
    mgr = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider([_text("done")]),
    )
    sid = "exec-marks"
    engine = mgr.get_engine(
        sid, agent="cowork", workspace=str(tmp_path), extra_tools=[_make_deliver(counter)]
    )
    call = ToolCall(id="call-mark", name="deliver_note", arguments={"note": "once"})
    engine._execute_sync(call)
    rec = mgr.tool_exec.get(sid, "call-mark")
    assert rec is not None and rec["state"] == "succeeded"
    assert rec["result"]["delivered"] == "once"
    assert _counter_lines(counter) == ["once"]


# -- resume outcomes -----------------------------------------------------------


def test_resume_reconstructs_a_succeeded_execution(tmp_path):
    """The #443 headline: adapter ran, result never checkpointed → resume restores the
    result from the snapshot and does NOT run the adapter again."""
    counter = tmp_path / "delivered.txt"
    mgr = SessionManager(
        workspace=tmp_path,
        # The tool call is seeded into the thread by hand; the model's only turn is the
        # wrap-up after the reconstructed result.
        provider=ScriptedProvider([_text("Delivered.")]),
    )
    sid = "recon"
    engine = mgr.get_engine(
        sid, agent="cowork", workspace=str(tmp_path), extra_tools=[_make_deliver(counter)]
    )
    # The persisted thread: user turn, then the assistant's call — no tool result
    # (the crash landed between the adapter and `_record_result`).
    engine.messages.append({"role": "user", "content": "send it", "ts": time.time()})
    engine.messages.append(_assistant_call("call_r", "once"))
    engine.tool_exec.dispatched(sid, "call_r", "deliver_note")
    engine.tool_exec.succeeded(
        sid, "call_r", "deliver_note", "ok", {"ok": True, "delivered": "once"}
    )

    async def go():
        async for _ in engine.resume():
            pass

    asyncio.run(go())
    mgr.save(sid, engine)

    assert _counter_lines(counter) == [], "the adapter must not run again"
    tools = [m for m in engine.messages if m.get("role") == "tool"]
    assert len(tools) == 1
    payload = json.loads(tools[0]["content"])
    assert payload["delivered"] == "once"
    assert tools[0]["tool_call_id"] == "call_r"


def test_resume_refuses_to_replay_an_unknown_dispatch(tmp_path):
    """`dispatched` without `succeeded`: the side effect's outcome is unknown, so resume
    answers honestly instead of firing the adapter a second time."""
    counter = tmp_path / "delivered.txt"
    mgr = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider([_text("whatever")]),
    )
    sid = "unknown"
    engine = mgr.get_engine(
        sid, agent="cowork", workspace=str(tmp_path), extra_tools=[_make_deliver(counter)]
    )
    engine.messages.append({"role": "user", "content": "send it", "ts": time.time()})
    engine.messages.append(_assistant_call("call_u", "once"))
    engine.tool_exec.dispatched(sid, "call_u", "deliver_note")

    async def go():
        async for _ in engine.resume():
            pass

    asyncio.run(go())

    assert _counter_lines(counter) == [], "an unknown side effect must not be replayed"
    tools = [m for m in engine.messages if m.get("role") == "tool"]
    assert len(tools) == 1
    payload = json.loads(tools[0]["content"])
    assert "outcome is unknown" in payload["error"]
    assert "not run again" in payload["error"]


def test_resume_still_executes_when_no_record_exists(tmp_path):
    """Never dispatched (the approval parked and the process died before the adapter
    ran): resume executes normally — the existing durable-approval contract."""
    counter = tmp_path / "delivered.txt"
    mgr = SessionManager(
        workspace=tmp_path,
        # The seeded call executes without asking the model; the wrap-up is its only turn.
        provider=ScriptedProvider([_text("Delivered.")]),
    )
    sid = "fresh"
    engine = mgr.get_engine(
        sid,
        agent="cowork",
        workspace=str(tmp_path),
        extra_tools=[_make_deliver(counter)],
        # No card to wait on: this test is about the record check, not the approval flow.
        approver=lambda _request: asyncio.sleep(0, ApprovalOutcome.ONCE),
    )
    engine.messages.append({"role": "user", "content": "send it", "ts": time.time()})
    engine.messages.append(_assistant_call("call_f", "fresh"))
    assert mgr.tool_exec.get(sid, "call_f") is None

    async def go():
        async for _ in engine.resume():
            pass

    asyncio.run(go())

    assert _counter_lines(counter) == ["fresh"], "no record ⇒ the call still runs"


# -- the issue's crash window, end to end --------------------------------------


def test_crash_between_execute_and_record_result_does_not_double_deliver(tmp_path):
    """Issue #443's repro: approval resolved, adapter returns, the process dies before
    the conversation result is written. Resume must reconstruct, not re-deliver."""
    counter = tmp_path / "delivered.txt"
    mgr = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(
            [
                _tool("deliver_note", {"note": "once"}, "call_crash"),
                _text("Delivered exactly once."),
            ]
        ),
    )
    sid = "crash"

    async def scenario():
        engine = mgr.get_engine(
            sid,
            agent="cowork",
            workspace=str(tmp_path),
            extra_tools=[_make_deliver(counter)],
        )

        # Park at the approval card, then simulate the restart the issue describes:
        # cancel the suspended turn and drop the live engine.
        task = asyncio.create_task(_drive(engine))
        pend = []
        for _ in range(100):
            await asyncio.sleep(0.02)
            pend = mgr.inbox.pending(sid)
            if pend:
                break
        assert pend, "approval never became pending"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Simulate the restart: drop the live engine, then rebuild from the persisted
        # thread — the rebuild must carry the tool, or the resume could not call it.
        mgr._engines.pop(sid, None)
        mgr.mark_idle(sid)
        engine_mid = mgr.get_engine(
            sid,
            agent="cowork",
            workspace=str(tmp_path),
            extra_tools=[_make_deliver(counter)],
        )

        # Crash exactly between the adapter returning and `_record_result` appending
        # the tool message: let the adapter run, then raise on the result append.
        original = TurnEngine._record_result
        state = {"crashed": False}

        def boom(self, tool_call, result, status):
            if not state["crashed"]:
                state["crashed"] = True
                raise RuntimeError("simulated crash before checkpoint")
            return original(self, tool_call, result, status)

        TurnEngine._record_result = boom
        try:
            await mgr.resolve_inbox(pend[0].id, "allow")
        except RuntimeError as exc:
            assert "simulated crash" in str(exc)
        finally:
            TurnEngine._record_result = original
        assert state["crashed"], "the crash injection never fired"
        assert _counter_lines(counter) == ["once"], "the adapter ran exactly once"

        # Restart: rebuild from the persisted thread and resume.
        mgr._engines.pop(sid, None)
        mgr.mark_idle(sid)
        engine2 = mgr.get_engine(
            sid,
            agent="cowork",
            workspace=str(tmp_path),
            extra_tools=[_make_deliver(counter)],
        )
        async for _ in engine2.resume():
            pass
        mgr.save(sid, engine2)

    asyncio.run(scenario())

    assert _counter_lines(counter) == ["once"], "resume must not re-deliver"
    tools = _final_tool_messages(mgr, sid)
    assert len(tools) == 1, "the tool result was reconstructed into the thread"
    payload = json.loads(tools[0]["content"])
    assert payload["delivered"] == "once"


async def _drive(engine):
    async for _ in engine.run("send it"):
        pass
