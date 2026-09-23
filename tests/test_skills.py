"""Agents (Code/Chat) + SKILL.md loader (catalog + load_skill)."""

from __future__ import annotations

from coworker.agent import build_engine
from coworker.agents import AgentContext, chat_agent, code_agent, get_agent
from coworker.providers import ModelCapabilities
from coworker.skills import SkillLoader, skill_catalog_text, skill_tools
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
    assert chat_agent().requires_folder is False
    assert code_agent().requires_folder is True


def test_get_agent_fallback():
    # Chat is removed (owner 2026-08-21): its id, like any unknown id, falls back to
    # the default persona per the registry.
    assert get_agent("chat").name == "cowork"
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


# -- engine assembly per agent --------------------------------------------------


def test_build_engine_chat(tmp_path):
    engine = build_engine(agent=chat_agent(), provider=_Stub())
    assert "load_skill" in engine.registry.names()
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


def test_unreadable_skill_dir_does_not_abort_the_scan(tmp_path, monkeypatch):
    """An unreadable skills dir degrades to "no skills", it never raises.

    `rescan()` runs every turn to rebuild the live skill menu, so an EACCES from
    `is_dir()` on the workspace's optional `.coworker/skills` propagated out of the turn
    and killed long headless sessions outright, some ~100 turns deep.
    """
    from pathlib import Path

    from coworker.skills.base import SkillLoader

    good = tmp_path / "good"
    (good / "helper").mkdir(parents=True)
    (good / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: works\n---\nbody\n", encoding="utf-8"
    )
    denied = tmp_path / "denied"
    denied.mkdir()

    real_is_dir = Path.is_dir

    def fake_is_dir(self):
        if self == denied:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    # The unreadable dir is skipped; the readable one is still loaded.
    loader = SkillLoader([denied, good])
    assert loader.names() == ["helper"]
    loader.rescan()  # the per-turn call must stay quiet too
    assert loader.get("helper") is not None


def test_unreadable_single_skill_folder_is_skipped(tmp_path, monkeypatch):
    """One bad folder costs only that skill, not the whole dir."""
    from pathlib import Path

    from coworker.skills.base import SkillLoader

    root = tmp_path / "skills"
    for name in ("alpha", "beta"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(
            "---\nname: %s\ndescription: d\n---\nbody\n" % name, encoding="utf-8"
        )

    real_read_text = Path.read_text

    def fake_read_text(self, *a, **kw):
        if self.parent.name == "alpha":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    loader = SkillLoader([root])
    assert loader.names() == ["beta"]
