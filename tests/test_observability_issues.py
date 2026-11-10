"""Observability Phase 2, OB-3: the Issues checks (E8b) and the 1.2 fields they read (E8a).

The scenarios in `tests/corpora/observability/golden-phase2/` are the shared test of the checks:
a copy of ocw-context docs/observability/golden/phase2 (signals.yaml 0.7, recording format 1.2).
Every recording must give exactly its expected issues, and the hash vectors must hash the same,
in this engine and in the Rust harness."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from coworker.engine import TurnEngine
from coworker.observability import Recorder, signals
from coworker.observability.canonical import jcs, short_hash, text_hash
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry

GOLDEN = Path(__file__).parent / "corpora" / "observability" / "golden-phase2"
INDEX = json.loads((GOLDEN / "index.json").read_text(encoding="utf-8"))


def _expected(rec: dict) -> dict:
    res = signals.check_turn(rec["turns"][0], rec["steps"])
    fired: dict[str, list[int]] = {}
    for i in res["issues"]:
        fired.setdefault(i["check"], []).append(i["step_seq"])
    return {"signals_version": res["signals_version"], "warn": res["warn"],
            "fired": {k: sorted(x for x in v if x is not None) for k, v in sorted(fired.items())},
            "fine": sorted(f["check"] for f in res["fine"]),
            "not_checked": sorted(n["check"] for n in res["not_checked"])}


def test_the_shipped_signals_file_is_the_golden_tests_version():
    assert signals.version() == INDEX["signals_version"]
    from coworker.observability import scopes

    known = set(signals.CHECKS) | set(scopes.SERIES_CHECKS) | set(scopes.TASK_CHECKS)
    assert set(signals.signals()["checks"]) <= known
    for cid, check in signals.signals()["checks"].items():
        assert (signals.scope_of(check) == "turn") == (cid in signals.CHECKS), cid


@pytest.mark.parametrize("sid", [s["id"] for s in INDEX["scenarios"]])
def test_golden_scenario_gives_exactly_its_issues(sid):
    rec = json.loads((GOLDEN / sid / "recording.json").read_text(encoding="utf-8"))
    want = json.loads((GOLDEN / sid / "expected-issues.json").read_text(encoding="utf-8"))
    assert _expected(rec) == want


def test_hash_vectors():
    v = json.loads((GOLDEN / "vectors" / "hashes.json").read_text(encoding="utf-8"))
    for case in v["args"]:
        assert jcs(case["value"]) == case["canonical"], case["name"]
        assert short_hash(case["value"]) == case["args_hash"], case["name"]
    for item in v["plan_items"]:
        assert text_hash(item["text"]) == item["h"]


# -- E8a: what the engine records for the checks ---------------------------------------------

class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


class _Meta:
    category: str = ""
    risk_level: str = "low"
    requires_approval: bool = False


def read_file(path: str) -> dict:
    """Read a file.

    Args:
        path: the file
    """
    return {"error": "file not found"} if path == "missing.txt" else {"text": "hi"}


def todo_write(todos: list = None) -> dict:
    """Replace the task list.

    Args:
        todos: the items
    """
    return {"count": len(todos or [])}


def _call(cid, name, args):
    return AssistantTurn(tool_calls=[ToolCall(id=cid, name=name, arguments=args)], finish_reason="tool_calls")


def _run(tmp_path, turns, *, max_iterations=12):
    registry = ToolRegistry()
    registry.register(read_file, metadata=_Meta())
    registry.register(todo_write, metadata=_Meta())
    engine = TurnEngine(
        provider=_Scripted(turns), registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS),
        model="openai:model-a", instructions="You are a test coworker.", max_iterations=max_iterations,
    )
    engine.audit_context = {"session_id": "sess_i"}
    rec = Recorder()
    engine.recorder = rec

    async def go():
        return [ev async for ev in engine.run("go", trigger="user")]

    asyncio.run(go())
    return rec.as_dict()


def test_tool_steps_carry_the_12_fields(tmp_path):
    d = _run(tmp_path, [
        _call("c1", "todo_write", {"todos": [{"content": "read the notes", "status": "in_progress"},
                                             {"content": "answer", "status": "completed"}]}),
        _call("c2", "read_file", {"path": "missing.txt"}),
        _call("c3", "nope_tool", {"x": 1}),
        AssistantTurn(text="done"),
    ])
    assert d["turns"][0]["max_iterations"] == 12
    tools = {s["call_id"]: s for s in d["steps"] if s["kind"] == "tool"}
    plan = tools["c1"]["attrs"]
    assert plan["args_hash"] == short_hash({"todos": [{"content": "read the notes", "status": "in_progress"},
                                                      {"content": "answer", "status": "completed"}]})
    assert plan["plan"] == {"items": [{"h": text_hash("read the notes"), "s": "in_progress"},
                                      {"h": text_hash("answer"), "s": "done"}]}
    assert plan["reaches_network"] is False and plan["repeat_ok"] is False and plan["error_kind"] is None
    missing = tools["c2"]["attrs"]
    assert tools["c2"]["status"] == "ok" and missing["result_error"] is True
    unknown = tools["c3"]
    assert unknown["status"] == "error" and unknown["attrs"]["error_kind"] == "unknown_tool"
    assert unknown["attrs"]["reaches_network"] is None  # it never ran


def test_a_recorded_turn_gives_its_issues(tmp_path):
    d = _run(tmp_path, [
        _call("c1", "read_file", {"path": "a"}), _call("c2", "read_file", {"path": "a"}),
        _call("c3", "read_file", {"path": "a"}), _call("c4", "nope_tool", {}),
        AssistantTurn(text="done"),
    ])
    res = signals.check_turn(d["turns"][0], d["steps"])
    fired = {i["check"] for i in res["issues"]}
    assert {"repeated_step", "made_up_tool"} <= fired
    assert "tool_failed" not in fired  # the unknown tool is made_up_tool's, not a tool failure
    assert any(f["check"] == "step_limit" and "of 12" in f["text"] for f in res["fine"])


def test_arguments_that_do_not_fit_the_tool_are_bad_arguments(tmp_path):
    """Labelling (T11): a model gave replace_in_file a name it does not take. The call fails at
    the call itself: the model's mistake, so made_up_tool, not "A tool failed"."""
    d = _run(tmp_path, [
        _call("c1", "read_file", {"pth": "a"}),             # a name the tool does not take
        _call("c2", "read_file", {}),                       # the required one missing
        _call("c3", "read_file", {"path": "a"}),            # fits
        AssistantTurn(text="done"),
    ])
    tools = {s["call_id"]: s for s in d["steps"] if s["kind"] == "tool"}
    assert tools["c1"]["status"] == "error" and tools["c1"]["attrs"]["error_kind"] == "bad_arguments"
    assert tools["c2"]["attrs"]["error_kind"] == "bad_arguments"
    assert tools["c3"]["attrs"]["error_kind"] is None
    res = signals.check_turn(d["turns"][0], d["steps"])
    found = [i for i in res["issues"] if i["check"] == "made_up_tool"]
    assert len(found) == 2 and found[0]["text"] == "The model sent arguments that do not fit read_file."
    assert "tool_failed" not in {i["check"] for i in res["issues"]}


def test_a_tool_that_fails_by_itself_is_still_raised(tmp_path):
    def broken(path: str) -> dict:
        """Break.

        Args:
            path: anything
        """
        raise RuntimeError("disk on fire")

    from coworker.engine import _args_fit
    from coworker.tools import ToolRegistry as _R

    reg = _R()
    reg.register(broken, metadata=_Meta())
    assert _args_fit(reg.get("broken"), {"path": "x"}) is True
    assert _args_fit(reg.get("broken"), {"path": "x", "extra": 1}) is False
    assert _args_fit(None, {"anything": 1}) is True  # nothing to check against


# -- E8c: stored with the turn and served ------------------------------------------------------

def test_issues_are_stored_at_turn_end_and_served(tmp_path):
    from fastapi.testclient import TestClient

    from coworker.server import SessionManager, create_app

    (tmp_path / "notes.txt").write_text("Friday", encoding="utf-8")

    class _Provider(_Scripted):
        def complete(self, *, model, messages, tools=None, **settings):
            if str(messages[0].get("content") or "").startswith("You title"):
                return AssistantTurn(text="Notes")
            return super().complete(model=model, messages=messages, tools=tools, **settings)

    provider = _Provider([
        _call("c1", "read_file", {"path": "missing.txt"}),
        AssistantTurn(text="It is missing.", finish_reason="stop"),
    ])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/i1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "read missing.txt"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    [turn] = client.get("/v1/sessions/i1/turns").json()["turns"]
    assert turn["issues"] == {"warn": 1, "info": 0}
    assert turn["issue_checks"] == ["tool_failed"]  # T3-14: the turn list searches by issue title
    got = client.get(f"/v1/sessions/i1/turns/{turn['turn_id']}/issues").json()
    assert got["signals_version"] == signals.version() and got["seen"] is False
    [failed] = got["issues"]
    assert failed["check"] == "tool_failed" and failed["facts"]["how"] == "result_error"
    assert {f["check"] for f in got["fine"]} >= {"repeated_step", "cut_off", "step_limit"}
    row = next(s for s in client.get("/v1/sessions").json()["sessions"] if s["session_id"] == "i1")
    assert row["issues_unseen"] == 1
    assert client.post("/v1/sessions/i1/issues/seen").json() == {"ok": True}
    row = next(s for s in client.get("/v1/sessions").json()["sessions"] if s["session_id"] == "i1")
    assert row["issues_unseen"] == 0
