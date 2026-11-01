"""Observability E1: a real engine turn, recorded.

The engine sends `turn_started` / `turn_ended` around run, retry and resume, a tool step for
every call that runs (from `_execute_sync` to the raw result), a zero-length tool step for a
call that never ran, and ids on its UI events. The fold rules are checked by the golden test;
this checks that the engine sends the right events.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from coworker.engine import TurnEngine
from coworker.events import EventType
from coworker.observability import Recorder
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tools(*calls):
    return AssistantTurn(
        tool_calls=[ToolCall(id=cid, name=name, arguments=args) for cid, name, args in calls],
        finish_reason="tool_calls",
    )


def _engine(tmp_path, turns):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=ScriptedProvider(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.audit_context = {"session_id": "sess_1"}
    rec = Recorder()
    events: list[dict] = []
    rec.add_listener(events.append)
    engine.recorder = rec
    return engine, rec, events


def _drain(gen):
    async def go():
        return [ev async for ev in gen]

    return asyncio.run(go())


def test_a_turn_with_tools_is_recorded(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "b.txt").write_text("world", encoding="utf-8")
    engine, rec, _ = _engine(tmp_path, [
        _tools(("c1", "read_file", {"path": "a.txt"}), ("c2", "read_file", {"path": "b.txt"})),
        _text("done"),
    ])
    ui = _drain(engine.run("read both", trigger="user"))
    d = rec.as_dict()

    [turn] = d["turns"]
    user = next(m for m in engine.messages if m["role"] == "user")
    assert (turn["kind"], turn["trigger"], turn["status"], turn["session_id"]) == (
        "run", "user", "completed", "sess_1")
    assert turn["first_message_id"] == user["mid"]
    assert turn["last_message_id"] == engine.messages[-1]["mid"]
    assert turn["approval_mode"] == engine.permissions.mode.value
    assert turn["reviewer"] == {"active": False, "shadow": False, "kind": None, "model": None}
    assert turn["duration_ms"] is not None

    tools = [s for s in d["steps"] if s["kind"] == "tool"]
    assert [(s["call_id"], s["call_index"], s["status"]) for s in tools] == [
        ("c1", 0, "ok"), ("c2", 1, "ok")]
    by_call = {m.get("tool_call_id"): m["mid"] for m in engine.messages if m["role"] == "tool"}
    for s in tools:
        assert s["message_id"] == by_call[s["call_id"]]
        assert s["attrs"]["risk_class"] == "read"
        assert s["attrs"]["result_bytes"] > 0
        assert s["duration_ms"] is not None and s["turn_id"] == turn["turn_id"]

    # UI events carry the turn id, a time, and a tool's call and step ids.
    assert all(e.data.get("turn_id") == turn["turn_id"] for e in ui)
    started = [e for e in ui if e.type is EventType.TOOL_STARTED]
    finished = [e for e in ui if e.type is EventType.TOOL_FINISHED]
    assert {e.data["tool_call_id"] for e in started} == {"c1", "c2"}
    assert {e.data["step_id"] for e in finished} == {s["step_id"] for s in tools}
    assert all(isinstance(e.data["ts_ms"], int) for e in ui)


def test_every_step_ended_carries_its_identity(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, _, events = _engine(tmp_path, [_tools(("c1", "read_file", {"path": "a.txt"})), _text("ok")])
    _drain(engine.run("go"))
    ends = [e for e in events if e["type"] == "step_ended"]
    assert ends
    for e in ends:
        assert {"v", "at_ms", "mono_ms", "kind", "name", "call_id", "started_at"} <= set(e)
    assert [e["type"] for e in events][0] == "turn_started"
    assert [e["type"] for e in events][-1] == "turn_ended"


def test_a_call_that_never_ran_is_a_zero_length_step(tmp_path):
    engine, rec, _ = _engine(tmp_path, [
        _tools(("c1", "read_file", {"_raw": "{not json"})),
        _text("sorry"),
    ])
    _drain(engine.run("go"))
    [step] = [s for s in rec.as_dict()["steps"] if s["kind"] == "tool"]
    assert (step["call_id"], step["status"], step["duration_ms"]) == ("c1", "error", 0)
    tool_msg = next(m for m in engine.messages if m.get("tool_call_id") == "c1")
    assert step["message_id"] == tool_msg["mid"]


def test_a_failed_model_call_ends_the_turn_as_error_and_retry_is_its_own_turn(tmp_path):
    engine, rec, _ = _engine(tmp_path, [RuntimeError("provider down"), _text("back")])
    _drain(engine.run("hello", trigger="user"))
    _drain(engine.retry(trigger="user"))
    first, second = rec.as_dict()["turns"]
    assert (first["kind"], first["status"]) == ("run", "error")
    assert (second["kind"], second["status"], second["seq"]) == ("retry", "completed", 2)
    # A retry opens with the first message it adds: the model's reply.
    reply = engine.messages[-1]
    assert second["first_message_id"] == reply["mid"] == second["last_message_id"]


def test_an_abandoned_turn_is_closed_as_unknown(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, rec, _ = _engine(tmp_path, [_tools(("c1", "read_file", {"path": "a.txt"})), _text("ok")])

    async def go():
        gen = engine.run("go")
        async for ev in gen:
            if ev.type is EventType.TURN_START:
                break
        await gen.aclose()  # the surface stopped reading

    asyncio.run(go())
    [turn] = rec.as_dict()["turns"]
    assert turn["status"] == "unknown" and turn["duration_ms"] is not None


def test_no_recorder_means_no_ids_on_events(tmp_path):
    engine, _, _ = _engine(tmp_path, [_text("hi")])
    engine.recorder = None
    ui = _drain(engine.run("hello", trigger="user"))
    assert all("turn_id" not in e.data for e in ui)


def test_a_turn_through_the_server_lands_in_recordings_db(tmp_path):
    from fastapi.testclient import TestClient

    from coworker.server import SessionManager, create_app

    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi there")]))
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/obs1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello"})
        while ws.receive_json()["type"] != "turn_done":
            pass
    [turn] = manager.recordings_store.load_recording("obs1")["turns"]
    assert (turn["kind"], turn["trigger"], turn["status"]) == ("run", "user", "completed")
    assert manager.recordings_store.path.parent == manager.audit_store.db_path.parent

    manager.delete_session("obs1")
    assert manager.recordings_store.load_recording("obs1") == {"turns": [], "steps": []}
