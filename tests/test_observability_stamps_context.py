"""Observability E15a, E16, E5, E13: stamps, the coworker fingerprint, the context step,
notices and per-call sandbox facts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from coworker.engine import TurnEngine
from coworker.observability import Recorder
from coworker.observability import stamps
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    def capabilities(self, model):
        return ModelCapabilities()


class _ReadMeta:
    category: str = ""
    risk_level: str = "low"
    requires_approval: bool = False


_BLOCK = {"text": ""}


def look(path: str) -> dict:
    """Read something.

    Args:
        path: what to read
    """
    if _BLOCK["text"]:
        _BLOCK["text"] += ", b"  # a skill became available mid-turn
    return {"path": path}


def _engine(tmp_path, turns, *, blocks=None, max_iterations=12):
    registry = ToolRegistry()
    registry.register(look, metadata=_ReadMeta())
    _BLOCK["text"] = blocks[0] if blocks else ""
    engine = TurnEngine(
        provider=_Scripted(turns), registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS),
        model="openai:model-a", instructions="You are a test coworker.",
        context_provider=(lambda: _BLOCK["text"]) if blocks else None,
        max_iterations=max_iterations,
    )
    engine.audit_context = {"session_id": "sess_c"}
    engine.persona_fp = "9f3a21"
    engine.context_facts = {"memories": [12, 15], "memory_mode": "full", "rules": 2, "agents_md": 1}
    rec = Recorder()
    engine.recorder = rec
    return engine, rec


def _run(engine, text="go"):
    async def go():
        return [ev async for ev in engine.run(text, trigger="user")]

    return asyncio.run(go())


def _call(cid, name, args):
    return AssistantTurn(tool_calls=[ToolCall(id=cid, name=name, arguments=args)], finish_reason="tool_calls")


def test_the_turn_is_stamped(tmp_path):
    engine, rec = _engine(tmp_path, [AssistantTurn(text="hi")])
    _run(engine)
    stamp = rec.as_dict()["turns"][0]["stamp"]
    assert stamp["machine"]["kind"] in ("this_machine", "own_machine", "cloud")
    assert stamp["machine"]["app_version"] != "0.0.0"
    assert stamp["model"]["id"] == "openai:model-a"
    assert stamp["persona_fp"] == "9f3a21"


def test_machine_kind():
    assert stamps.machine_kind(None) == "this_machine"
    assert stamps.machine_kind("https://machines.openworker.com/") == "cloud"
    assert stamps.machine_kind("https://my-box.example:8443") == "own_machine"


def test_fingerprint_changes_only_with_the_coworkers_own_files(tmp_path):
    skills = tmp_path / "skills"
    (skills / "a").mkdir(parents=True)
    (skills / "a" / "SKILL.md").write_text("one", encoding="utf-8")
    fp = stamps.persona_fingerprint("helper", "manifest text", [skills])
    assert len(fp) == 6
    assert stamps.persona_fingerprint("helper", "manifest text", [skills]) == fp
    (skills / "a" / "SKILL.md").write_text("two", encoding="utf-8")
    assert stamps.persona_fingerprint("helper", "manifest text", [skills]) != fp
    assert stamps.persona_fingerprint("other", "manifest text", [skills]) != stamps.persona_fingerprint(
        "helper", "manifest text", [skills])


def test_a_context_step_at_the_first_model_call_and_a_notice_when_it_changes(tmp_path):
    engine, rec = _engine(tmp_path, [_call("c1", "look", {"path": "x"}), AssistantTurn(text="ok")],
                          blocks=["Folders: /w\nSkills: a"])
    _run(engine)
    steps = rec.as_dict()["steps"]
    [context] = [s for s in steps if s["kind"] == "context"]
    a = context["attrs"]
    assert context["duration_ms"] == 0
    assert a["size_chars"] == len("Folders: /w\nSkills: a") and len(a["block_hash"]) == 6
    assert len(a["session_prompt_hash"]) == 6
    assert a["session_start"] == {"memories": [12, 15], "memory_mode": "full", "rules": 2, "agents_md": 1}
    [changed] = [s for s in steps if s["kind"] == "notice"]
    assert changed["name"] == "context_changed"
    assert changed["attrs"]["block_hash"] != a["block_hash"]
    assert steps[0]["kind"] == "context"


def test_session_start_facts_only_on_the_first_turn(tmp_path):
    engine, rec = _engine(tmp_path, [AssistantTurn(text="a"), AssistantTurn(text="b")])
    _run(engine, "one")
    _run(engine, "two")
    contexts = [s for s in rec.as_dict()["steps"] if s["kind"] == "context"]
    assert len(contexts) == 2
    assert "session_start" in contexts[0]["attrs"] and "session_start" not in contexts[1]["attrs"]


def test_error_and_step_limit_are_notices(tmp_path):
    engine, rec = _engine(tmp_path, [RuntimeError("boom")])
    _run(engine)
    assert [s["name"] for s in rec.as_dict()["steps"] if s["kind"] == "notice"] == ["error"]

    engine, rec = _engine(tmp_path, [_call("c1", "look", {"path": "x"}), _call("c2", "look", {"path": "y"})],
                          max_iterations=1)
    _run(engine)
    assert "step_limit" in [s["name"] for s in rec.as_dict()["steps"] if s["kind"] == "notice"]


def test_blocked_sites_go_to_the_tool_step_not_the_transcript(tmp_path):
    class _Sandbox:
        reports_blocked = True

        def describe(self):
            return {"provider": "openshell", "enforcement": "full"}

    def fetch(url: str) -> dict:
        """Fetch a page.

        Args:
            url: the page
        """
        return {"exit_code": 1, "blocked_sites": ["semgrep.dev:443"], "network_note": "blocked",
                "_blocked": [{"site": "semgrep.dev:443", "count": 2, "first_at": 1, "late": False,
                              "uncertain": True}]}

    engine, rec = _engine(tmp_path, [_call("c1", "fetch", {"url": "https://semgrep.dev"}), AssistantTurn(text="ok")])
    engine.registry.register(fetch, metadata=_ReadMeta())
    engine.sandbox_workspace = _Sandbox()
    _run(engine)
    [tool] = [s for s in rec.as_dict()["steps"] if s["kind"] == "tool"]
    assert tool["attrs"]["sandbox"] == {"blocked_known": True, "blocked_count": 2, "notice": None}
    assert tool["content"]["blocked"][0]["site"] == "semgrep.dev:443"
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert "_blocked" not in str(result["content"])
    assert rec.as_dict()["turns"][0]["stamp"]["sandbox"] == {"provider": "openshell", "enforcement": "full"}


def test_network_note_keeps_the_counts(tmp_path, monkeypatch):
    from coworker.sandbox.workspace import RunnerWorkspace

    ws = RunnerWorkspace.__new__(RunnerWorkspace)
    ws._blocked_told = 0.0
    ws.provider = type("P", (), {"blocked_lag": 0.0})()
    monkeypatch.setattr(RunnerWorkspace, "reports_blocked", property(lambda self: True), raising=False)
    monkeypatch.setattr(ws, "blocked_since", lambda t: [(100.0, "semgrep.dev:443"), (101.0, "semgrep.dev:443")], raising=False)
    monkeypatch.setattr(ws, "_asking", lambda: False, raising=False)
    note = ws.network_note(started=100.0, result={"exit_code": 0})
    assert note["blocked_sites"] == ["semgrep.dev:443"]
    assert note["_blocked"] == [{"site": "semgrep.dev:443", "count": 2, "first_at": 100000, "late": False,
                                 "uncertain": True}]
