"""Agents (Code/Chat) + SKILL.md loader (catalog + load_skill)."""

from __future__ import annotations

from coworker.agent import build_engine
from coworker.agents import AgentContext, chat_agent, code_agent, get_agent
from coworker.providers import ModelCapabilities
from coworker.skills import (
    SkillLoader,
    builtin_skill_dir,
    skill_catalog_text,
    skill_tools,
)
from coworker.tools import ToolRegistry
from coworker.tools.shell import LocalExecutor
from coworker.tools.todo import TodoList


class _Stub:
    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()


# -- agents ---------------------------------------------------------------------


def test_code_agent_tools(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, default_timeout=5)
    try:
        ctx = AgentContext(workspace=tmp_path, executor=ex, todo=TodoList())
        names = {getattr(t, "__name__", "?") for t in code_agent().build_tools(ctx)}
        assert {
            "read_file",
            "write_file",
            "git_status",
            "run_shell",
            "todo_write",
        } <= names
    finally:
        ex.close()


def test_chat_agent_has_no_workspace_tools():
    assert chat_agent().build_tools(AgentContext()) == []
    assert chat_agent().needs_workspace is False
    assert code_agent().needs_workspace is True


def test_get_agent_fallback():
    assert get_agent("chat").name == "chat"
    # Unknown ids fall back to the default persona (Cowork), per the persona registry.
    assert get_agent("nope").name == "cowork"


# -- SKILL.md loader ------------------------------------------------------------


def _make_skill(skills_dir, name, desc, body):
    d = skills_dir / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n{body}", encoding="utf-8"
    )


def test_skill_loader_catalog_and_load(tmp_path):
    skills_dir = tmp_path / "skills"
    _make_skill(
        skills_dir, "pdf", "extract text from PDFs", "Use pdfplumber to extract text."
    )
    loader = SkillLoader([skills_dir])

    assert loader.catalog() == [
        {"name": "pdf", "description": "extract text from PDFs"}
    ]
    assert "pdf: extract text from PDFs" in skill_catalog_text(loader)

    reg = ToolRegistry()
    reg.register_all(skill_tools(loader))
    loaded = reg.execute("load_skill", {"name": "pdf"})
    assert "pdfplumber" in loaded["instructions"]
    assert reg.execute("load_skill", {"name": "missing"})["error"]


def test_builtin_browser_skill_is_packaged_and_self_contained():
    loader = SkillLoader([builtin_skill_dir()])
    skill = loader.get("control-in-app-browser")

    assert skill is not None
    assert "attended browser surface" in skill.description
    assert skill.allowed_tools == []
    assert "browser_surfaces" in skill.instructions
    assert "browser_documentation" in skill.instructions
    assert "once for the selected surface" in skill.instructions
    assert "ambient browser or UI state" in skill.instructions
    assert "`chrome`" in skill.instructions
    assert "`chrome`" in skill.instructions
    assert "`edge`" not in skill.instructions
    assert "hidden browser" in skill.instructions
    assert (builtin_skill_dir() / "control-in-app-browser" / "SKILL.md").is_file()


def test_skill_directory_order_allows_global_then_workspace_override(tmp_path):
    bundled = tmp_path / "bundled"
    global_skills = tmp_path / "global"
    workspace_skills = tmp_path / "workspace"
    _make_skill(bundled, "shared", "bundled description", "bundled body")
    _make_skill(global_skills, "shared", "global description", "global body")
    _make_skill(
        workspace_skills,
        "shared",
        "workspace description",
        "workspace body",
    )

    loader = SkillLoader([bundled, global_skills, workspace_skills])

    assert loader.catalog() == [
        {"name": "shared", "description": "workspace description"}
    ]
    assert loader.get("shared").instructions == "workspace body"


# -- engine assembly per agent --------------------------------------------------


def test_build_engine_chat(tmp_path):
    engine = build_engine(agent=chat_agent(), provider=_Stub())
    assert "load_skill" in engine.registry.names()
    assert "control-in-app-browser" in engine.context_provider()
    assert "read_file" not in engine.registry.names()
    assert engine.executor is None
    assert engine.agent_name == "chat"


def test_build_engine_code_has_agents_md_and_skills(tmp_path):
    (tmp_path / "AGENTS.md").write_text("PROJECT RULE: prefer pathlib.")
    engine = build_engine(agent=code_agent(), workspace=tmp_path, provider=_Stub())
    try:
        assert "prefer pathlib" in engine.messages[0]["content"]
        assert "todo_write" in engine.registry.names()
        assert "load_skill" in engine.registry.names()
        assert engine.agent_name == "code"
    finally:
        engine.executor.close()


def test_workspace_can_override_builtin_browser_skill(tmp_path):
    skills_dir = tmp_path / ".coworker" / "skills"
    _make_skill(
        skills_dir,
        "control-in-app-browser",
        "workspace browser workflow",
        "Use the workspace-specific browser rules.",
    )
    engine = build_engine(agent=code_agent(), workspace=tmp_path, provider=_Stub())
    try:
        loaded = engine.registry.execute(
            "load_skill", {"name": "control-in-app-browser"}
        )
        assert loaded["instructions"] == (
            "Use the workspace-specific browser rules."
        )
        assert "workspace browser workflow" in engine.context_provider()
    finally:
        engine.executor.close()


def test_user_skill_can_override_builtin_browser_skill(tmp_path, monkeypatch):
    user_state = tmp_path / "state"
    _make_skill(
        user_state / "skills",
        "control-in-app-browser",
        "user browser workflow",
        "Use the user-specific browser rules.",
    )
    monkeypatch.setattr("coworker.agent.state_dir", lambda: user_state)

    engine = build_engine(agent=chat_agent(), provider=_Stub())
    loaded = engine.registry.execute(
        "load_skill", {"name": "control-in-app-browser"}
    )

    assert loaded["instructions"] == "Use the user-specific browser rules."
    assert "user browser workflow" in engine.context_provider()


def test_builtin_browser_skill_survives_user_skill_filter():
    engine = build_engine(
        agent=chat_agent(),
        provider=_Stub(),
        skill_filter=lambda: set(),
    )

    assert "control-in-app-browser" in engine.context_provider()
    loaded = engine.registry.execute(
        "load_skill", {"name": "control-in-app-browser"}
    )
    assert "browser_surfaces" in loaded["instructions"]
