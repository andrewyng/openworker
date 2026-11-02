"""Observability §12: what the app's screens read, through the real server.

A turn runs over the session's websocket; the recorded turns, steps, one step in full and
the session's token numbers are then read back over HTTP, and the same recording events
arrive live on the socket as `{"type": "step", "events": [...]}`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.providers.base import TokenUsage
from coworker.server import SessionManager, create_app


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if str(messages[0].get("content") or "").startswith("You title"):
            return AssistantTurn(text="Notes", usage=TokenUsage(input=40, output=2))  # the session title
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def test_a_recorded_turn_reads_back_through_the_endpoints(tmp_path):
    (tmp_path / "notes.txt").write_text("Friday", encoding="utf-8")
    provider = _Scripted([
        AssistantTurn(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "notes.txt"})],
                      finish_reason="tool_calls", usage=TokenUsage(input=100, output=10, cache_read=900)),
        AssistantTurn(text="It says Friday.", finish_reason="stop", usage=TokenUsage(input=20, output=5, cache_read=1000)),
    ])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    client = TestClient(create_app(manager))
    live: list[dict] = []
    with client.websocket_connect("/ws/session/r1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "what does notes.txt say?"})
        while True:
            msg = ws.receive_json()
            if msg["type"] == "step":
                live.extend(msg["events"])
            if msg["type"] == "turn_done":
                break
        # The last batch may arrive just after the turn's end.
        import time
        deadline = time.time() + 2
        while time.time() < deadline and not any(e["type"] == "turn_ended" for e in live):
            msg = ws.receive_json()
            if msg["type"] == "step":
                live.extend(msg["events"])

    [turn] = client.get("/v1/sessions/r1/turns").json()["turns"]
    assert (turn["kind"], turn["trigger"], turn["status"]) == ("run", "user", "completed")
    # The two main calls, and the session title (40 + 2): every purpose with a session counts.
    assert turn["usage"]["pill"] == 100 + 900 + 10 + 20 + 1000 + 5 + 40 + 2
    assert turn["usage"]["by_purpose"] == {"main": 2035, "title": 42}
    assert turn["buckets"]["total"] == turn["duration_ms"]

    steps = client.get(f"/v1/sessions/r1/turns/{turn['turn_id']}/steps").json()["steps"]
    mains = [s for s in steps if s["kind"] != "context" and s["attrs"].get("purpose") != "title"]
    assert [s["kind"] for s in mains] == ["model", "tool", "model"]
    assert all("content" not in s for s in steps)

    tool = next(s for s in steps if s["kind"] == "tool")
    detail = client.get(f"/v1/sessions/r1/steps/{tool['step_id']}").json()["step"]
    assert detail["message"]["role"] == "tool" and "Friday" in detail["message"]["content"]
    assert detail["message"]["mid"] == tool["message_id"]
    assert any(row.get("call_id") == "c1" for row in detail["audit"])
    # The details card's arguments come from the transcript's tool call (UX-059 OB-2).
    assert detail["arguments"] == {"path": "notes.txt"}
    assert "wait" not in detail  # a low-risk read had no card

    usage = client.get("/v1/sessions/r1/usage").json()
    assert usage["pill"] == turn["usage"]["pill"] and usage["at_least"] is False
    assert usage["recorded"] is True
    assert usage["cache_share"] == round(1900 / (100 + 900 + 20 + 1000 + 40), 3)
    assert usage["context"]["tokens"] == 1020
    assert usage["totals"] == {"input": 100 + 900 + 20 + 1000 + 40, "cache": 1900, "output": 17}
    assert usage["turns"] == 1

    types = [e["type"] for e in live]
    assert types[0] == "turn_started" and "turn_ended" in types and "step_ended" in types


def test_a_session_recorded_before_phase_1_says_so(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_Scripted([]))
    client = TestClient(create_app(manager))
    assert client.get("/v1/sessions/old/turns").json() == {"turns": []}
    assert client.get("/v1/sessions/old/usage").json()["recorded"] is False


def test_a_tool_card_brings_its_wait_and_a_check_names_its_call():
    # UX-059 OB-2 / OB-11: the tool's card shows how long it waited on its approval card;
    # an auto-approver check names the call it judged.
    from coworker.observability import read_api

    def s(step_id, kind, name, **kw):
        return {"step_id": step_id, "turn_id": "t", "kind": kind, "name": name, "call_id": "c1",
                "parent_step_id": None, "attrs": kw.pop("attrs", {}), "content": {}, **kw}

    rec = {"turns": [{"turn_id": "t"}], "steps": [
        s("a", "auto_approver", "run_shell", attrs={"result": "unsure"}),
        s("w", "wait", "approval", duration_ms=2000, attrs={"for_call_id": "c1", "outcome": "approved"}),
        s("x", "tool", "run_shell", duration_ms=40),
    ]}
    messages = [{"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "run_shell", "arguments": '{"command": "make"}'}}]}]
    kw = dict(get_piece=lambda k: None, messages=messages, audit_rows=lambda c: [])
    tool = read_api.step(rec, "x", **kw)
    assert tool["arguments"] == {"command": "make"} and tool["wait"]["duration_ms"] == 2000
    check = read_api.step(rec, "a", **kw)
    assert check["for_step"]["step_id"] == "x" and check["arguments"] == {"command": "make"}
