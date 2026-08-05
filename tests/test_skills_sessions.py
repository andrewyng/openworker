"""SKILLS-SPEC §4.6 — the effective menu: merged scopes − Settings disables − session mutes.

One resolver (`effective_skills` / manager.effective_skill_names) feeds the engine catalog,
the rail list, and the composer popup — the parity test pins that they can never disagree.
Any-off-wins: a Settings disable can NOT be resurrected by a session override.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker.providers import ModelCapabilities, ProviderClient
from coworker.skills import (
    SessionSkillStore,
    SkillLoader,
    effective_skills,
    skill_catalog_text,
    skill_tools,
)
from coworker.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def __init__(self, turns=None):
        self._turns = list(turns or [])

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _skill(base: Path, name: str, description: str = "", body: str = "do it") -> None:
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def manager(tmp_path):
    return SessionManager(workspace=tmp_path / "ws", provider=ScriptedProvider())


# -- resolver ----------------------------------------------------------------------


def test_project_copy_wins_merge(tmp_path):
    _skill(tmp_path / "g", "report", body="generic steps")
    _skill(tmp_path / "p", "report", body="project steps")
    loader = SkillLoader([tmp_path / "g", tmp_path / "p"])  # global first, local last
    assert loader.get("report").instructions == "project steps"


def test_disabled_absent_everywhere():
    assert effective_skills(
        names={"a", "b"}, disabled={"a"}, session_overrides={}
    ) == {"b"}


def test_mute_hides_in_that_session_only(tmp_path):
    store = SessionSkillStore(tmp_path / "s.json")
    store.set("s1", "a", False)
    names = {"a", "b"}
    s1 = effective_skills(names=names, disabled=set(), session_overrides=store.get("s1"))
    s2 = effective_skills(names=names, disabled=set(), session_overrides=store.get("s2"))
    assert s1 == {"b"} and s2 == {"a", "b"}


def test_removed_session_inherits_clean(tmp_path):
    store = SessionSkillStore(tmp_path / "s.json")
    store.set("s1", "a", False)
    store.remove_session("s1")
    assert store.get("s1") == {}


def test_mute_of_unknown_skill_is_noop():
    out = effective_skills(
        names={"real"}, disabled=set(), session_overrides={"ghost": False}
    )
    assert out == {"real"}


def test_any_off_wins_both_directions():
    # enabled in Settings + muted in session → out
    assert effective_skills(
        names={"a"}, disabled=set(), session_overrides={"a": False}
    ) == set()
    # disabled in Settings + explicit session-on → STILL out (no resurrection)
    assert effective_skills(
        names={"a"}, disabled={"a"}, session_overrides={"a": True}
    ) == set()


def test_override_store_survives_reload(tmp_path):
    SessionSkillStore(tmp_path / "s.json").set("s1", "a", False)
    assert SessionSkillStore(tmp_path / "s.json").get("s1") == {"a": False}


def test_concurrent_sessions_same_workspace_independent(tmp_path):
    store = SessionSkillStore(tmp_path / "s.json")
    store.set("s1", "a", False)
    store.set("s2", "b", False)
    assert store.get("s1") == {"a": False}
    assert store.get("s2") == {"b": False}


# -- manager resolution ---------------------------------------------------------------


def test_no_workspace_means_global_only(manager, tmp_path):
    _skill(manager.skill_store.global_dir, "everywhere")
    ws = tmp_path / "elsewhere"
    (ws / ".coworker" / "skills").mkdir(parents=True)
    _skill(ws / ".coworker" / "skills", "local-only")
    assert manager.effective_skill_names("s1") == {"everywhere"}
    assert manager.effective_skill_names("s1", ws) == {"everywhere", "local-only"}


def test_workspace_without_skills_dir_is_fine(manager, tmp_path):
    ws = tmp_path / "bare-ws"
    ws.mkdir()
    assert manager.effective_skill_names("s1", ws) == set()


def test_empty_catalog_is_safe(tmp_path):
    from coworker.tools.registry import ToolRegistry

    loader = SkillLoader([tmp_path / "nowhere"])
    assert skill_catalog_text(loader) == ""
    reg = ToolRegistry()
    reg.register_all(skill_tools(loader))
    result = reg.execute("load_skill", {"name": "ghost"})
    assert result["error"].startswith("unknown skill")
    assert result["available"] == []


def test_live_load_skill_semantics(manager):
    """SKILLS-SPEC state table — EVERYTHING the model sees is live per turn:
    · the menu (context_provider) reflects installs/disables from the NEXT MESSAGE —
      no new session needed (mid-session import UX, decided 2026-07-27);
    · load_skill consults live state per call (create-after-build loadable; a Settings
      disable applies to RUNNING sessions; delete ≡ disable to the model);
    · the ONLY thing that persists is what a conversation already loaded (history)."""
    from coworker.agent import build_engine
    from coworker.agents.registry import get_agent

    _skill(manager.skill_store.global_dir, "early", body="early body")
    engine = build_engine(
        agent=get_agent("chat"),
        provider=ScriptedProvider(),
        skill_filter=lambda: manager.effective_skill_names("s1"),
    )
    # The menu lives in the per-turn context block, not the static system prompt.
    assert "early" in engine.context_provider()
    assert "Available skills" not in engine.messages[0]["content"]

    # created after build → in the menu from the next turn AND loadable
    manager.create_skill(
        {"name": "late", "description": "d", "instructions": "late body"}
    )
    assert "late" in engine.context_provider()
    loaded = engine.registry.execute("load_skill", {"name": "late"})
    assert loaded["instructions"] == "late body"

    # disable → gone from the menu next turn, refused on load, listed nowhere
    manager.skill_store.set_enabled("early", False)
    assert "early" not in engine.context_provider()
    refused = engine.registry.execute("load_skill", {"name": "early"})
    assert refused["error"].startswith("unknown skill")
    assert "early" not in refused["available"]

    # delete ≡ disable, from the model's side
    manager.delete_skill("late")
    assert "late" not in engine.context_provider()
    gone = engine.registry.execute("load_skill", {"name": "late"})
    assert gone["error"].startswith("unknown skill")

    # re-enable → back next turn, files untouched all along (OFF is parking, not deletion)
    manager.skill_store.set_enabled("early", True)
    assert "early" in engine.context_provider()
    assert (
        engine.registry.execute("load_skill", {"name": "early"})["instructions"]
        == "early body"
    )


def test_disable_countermand_for_loaded_skills(manager):
    """§3: a skill whose instructions already entered the conversation gets an explicit
    per-turn stop note once disabled/deleted — menus shrinking is passive, instructions in
    history are not. Recomputed fresh: re-enabling clears it; unloaded skills never get one."""
    import json as _json

    from coworker.agent import build_engine
    from coworker.agents.registry import get_agent

    _skill(manager.skill_store.global_dir, "used-one", body="used body")
    _skill(manager.skill_store.global_dir, "unused-one", body="never loaded")
    engine = build_engine(
        agent=get_agent("chat"),
        provider=ScriptedProvider(),
        skill_filter=lambda: manager.effective_skill_names("s1"),
    )
    # Simulate a successful load earlier in this conversation (OpenAI message shape).
    engine.messages.append(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "load_skill",
                        "arguments": _json.dumps({"name": "used-one"}),
                    },
                }
            ],
        }
    )
    engine.messages.append(
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": _json.dumps(
                {"name": "used-one", "instructions": "used body", "resources_path": "x"}
            ),
        }
    )
    assert "disabled by the user" not in engine.context_provider()

    manager.skill_store.set_enabled("used-one", False)
    ctx = engine.context_provider()
    assert 'skill "used-one" has been disabled' in ctx
    assert "- used-one:" not in ctx  # gone from the menu itself
    assert "- unused-one:" in ctx  # untouched skill still offered, no note for it

    manager.skill_store.set_enabled("used-one", True)
    assert "disabled by the user" not in engine.context_provider()  # self-healing

    manager.delete_skill("used-one")  # delete ≡ disable for the countermand too
    assert 'skill "used-one" has been disabled' in engine.context_provider()


def test_parity_catalog_vs_rail_view(manager):
    """The §3 invariant: the engine's menu and the rail payload come from one resolver."""
    _skill(manager.skill_store.global_dir, "alpha")
    _skill(manager.skill_store.global_dir, "beta")
    _skill(manager.skill_store.global_dir, "gamma")
    manager.skill_store.set_enabled("beta", False)  # Settings disable → gone from BOTH
    manager.session_skills.set("s1", "gamma", False)  # session mute → rail row off

    menu = manager.effective_skill_names("s1")
    view = manager.session_skills_view("s1")["skills"]
    view_names = {r["name"] for r in view}
    view_on = {r["name"] for r in view if r["enabled"]}

    assert menu == {"alpha"}
    assert view_names == {"alpha", "gamma"}  # disabled hidden; muted still listed (toggle)
    assert view_on == menu  # what's ON in the rail == what the model sees
