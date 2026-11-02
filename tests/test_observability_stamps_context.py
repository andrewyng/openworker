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
                "_blocked": [{"host": "semgrep.dev", "port": 443, "count": 2, "first_at": 1, "late": False,
                              "uncertain": True}]}

    engine, rec = _engine(tmp_path, [_call("c1", "fetch", {"url": "https://semgrep.dev"}), AssistantTurn(text="ok")])
    engine.registry.register(fetch, metadata=_ReadMeta())
    engine.sandbox_workspace = _Sandbox()
    _run(engine)
    [tool] = [s for s in rec.as_dict()["steps"] if s["kind"] == "tool"]
    assert tool["attrs"]["sandbox"] == {"blocked_known": True, "blocked_count": 2, "notice": None}
    assert tool["content"]["blocked"][0]["host"] == "semgrep.dev"
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
    assert note["_blocked"] == [{"host": "semgrep.dev", "port": 443, "count": 2, "first_at": 100000,
                                 "late": False, "uncertain": True}]


def test_the_skills_menu_is_the_one_offered_this_turn(tmp_path):
    # Round-2 in-app check: a skill disabled mid-session left the menu, but the context step
    # still counted every skill on disk. The step counts what the turn's block offered.
    from coworker.agent import build_engine
    from coworker.agents.chat import chat_agent

    skills = tmp_path / "skills"
    for name in ("alpha", "beta"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: The {name} skill.\n---\nDo {name}.\n", encoding="utf-8")
    offered = {"alpha", "beta"}
    (tmp_path / "w").mkdir()
    engine = build_engine(agent=chat_agent(), model="openai:model-a", workspace=tmp_path / "w",
                          provider=_Scripted([AssistantTurn(text="a"), AssistantTurn(text="b")]),
                          extra_skill_dirs=[skills], skill_filter=lambda: set(offered))
    engine.recorder = rec = Recorder()
    _run(engine, "one")
    offered.discard("beta")  # turned off in Settings between the two messages
    _run(engine, "two")
    menus = [s["attrs"]["per_turn"] for s in rec.as_dict()["steps"] if s["kind"] == "context"]
    assert [m["skills_menu"] for m in menus] == [2, 1]
    assert menus[0]["skills_hash"] != menus[1]["skills_hash"]


def test_sites_count_the_profile_the_machine_list_and_the_logins_hosts():
    """V2-28: the stamp's `sites` read a list no provider has, so it was always None."""
    provider = type("P", (), {"profile": "allowlist", "extra_hosts": ["a.example:443", "b.example:443"],
                              "copied": type("C", (), {"hosts": ["a.example:443", "c.example:443"]})()})()
    ws = type("W", (), {"provider": provider})()
    assert stamps._site_count(ws) == 3
    provider.profile = "open"
    assert stamps._site_count(ws) is None  # any host: no count
    assert stamps._site_count(type("W", (), {"provider": object()})()) is None  # keeps no list


class _LateSandbox:
    """A sandbox the first turn makes after the turn's stamp was taken."""

    reports_blocked = False

    def __init__(self):
        self.started = False
        self.provider = type("P", (), {"name": "openshell", "profile": "allowlist", "extra_hosts": ["x.example:443"]})()

    def ensure_started(self):
        self.started = True

    def describe(self):
        info = {"provider": "openshell", "enforcement": "full"}
        if self.started:
            info["runner"] = {"runner_version": "0.4.1"}
        return info


def test_the_sandbox_made_by_the_first_turn_is_restamped(tmp_path):
    """V2-28: the runner's version is known only once the sandbox started, after the stamp."""
    engine, rec = _engine(tmp_path, [_call("c1", "look", {"path": "x"}), AssistantTurn(text="ok"),
                                     AssistantTurn(text="again")])
    engine.sandbox_workspace = _LateSandbox()
    _run(engine)
    d = rec.as_dict()
    assert "runner_version" not in d["turns"][0]["stamp"]["sandbox"]
    [notice] = [s for s in d["steps"] if s["kind"] == "notice"]
    assert notice["name"] == "stamp_changed"
    assert notice["attrs"] == {"stamp": {"sandbox": {"provider": "openshell", "enforcement": "full",
                                                     "profile": "allowlist", "sites": 1,
                                                     "runner_version": "0.4.1"}}}
    _run(engine, "again")  # the next turn's stamp has it, and nothing changes in it
    d = rec.as_dict()
    assert d["turns"][1]["stamp"]["sandbox"]["runner_version"] == "0.4.1"
    assert [s["name"] for s in d["steps"] if s["kind"] == "notice"] == ["stamp_changed"]


def test_attendance_changing_mid_turn_is_a_stamp_changed_notice(tmp_path):
    """V2-27: the Unattended toggle applies mid-turn; the turn keeps its start value and a
    notice carries the new one, with only the key that changed."""
    attended = {"now": True}
    engine, rec = _engine(tmp_path, [_call("c1", "look", {"path": "x"}), AssistantTurn(text="ok")])
    engine.is_attended = lambda: attended["now"]
    original = engine._execute_sync

    def run_tool(*a, **kw):
        attended["now"] = False  # you switched to Unattended while the tool ran
        return original(*a, **kw)

    engine._execute_sync = run_tool
    _run(engine)
    d = rec.as_dict()
    assert d["turns"][0]["attended"] is True
    [notice] = [s for s in d["steps"] if s["kind"] == "notice"]
    assert notice["name"] == "stamp_changed" and notice["attrs"] == {"attended": False}


def test_a_turn_where_nothing_changes_has_no_stamp_notice(tmp_path):
    engine, rec = _engine(tmp_path, [_call("c1", "look", {"path": "x"}), _call("c2", "look", {"path": "y"}),
                                     AssistantTurn(text="ok")])
    engine.is_attended = lambda: True
    _run(engine)
    assert not [s for s in rec.as_dict()["steps"] if s["kind"] == "notice"]


def test_a_leads_steering_wakes_the_worker_as_a_team(tmp_path, monkeypatch):
    """V2-30: `steer_worker` delivers through the `team` connector; it was recorded as `channel`."""
    from coworker.providers import AssistantTurn as _T
    from coworker.server.manager import SessionManager

    mgr = SessionManager(workspace=tmp_path, provider=_Scripted([_T(text="on it"), _T(text="hi")]))
    engine = mgr.get_engine("W", agent="chat")
    seen: list[str] = []
    real_run = engine.run

    def run(*a, trigger="user", **kw):
        seen.append(trigger)
        return real_run(*a, trigger=trigger, **kw)

    monkeypatch.setattr(engine, "run", run)
    asyncio.run(mgr.deliver_to_session("W", "[Lead] switch to the API docs",
                                       source={"connector": "team", "sender_name": "lead"}))
    asyncio.run(mgr.deliver_to_session("W", "hello", source={"connector": "slack", "sender_name": "pat"}))
    assert seen == ["team_wake", "channel"]
