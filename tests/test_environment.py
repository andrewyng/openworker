"""Tests for the session environment blocks.

Split in two (see coworker/environment.py): `environment_context` is the stable half that
goes in the cached system prompt, `environment_live` is the volatile half delivered per
turn. These tests pin which facts live on which side — putting git state back in the
stable half would silently cost the prefix cache, and nothing else would notice.
"""

from __future__ import annotations

import subprocess
import sys

from coworker.environment import environment_context, environment_live


def _git_repo(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    run = lambda *a: subprocess.run(
        ["git", "-C", str(ws), *a], capture_output=True, check=True
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@t.io")
    run("config", "user.name", "T")
    (ws / "f.txt").write_text("1", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "first commit")
    return ws


def test_stable_block_has_workspace_and_platform(tmp_path):
    block = environment_context(tmp_path)
    assert str(tmp_path.resolve()) in block
    assert sys.platform in block
    assert "<environment>" in block and "</environment>" in block


def test_stable_block_carries_no_volatile_facts(tmp_path):
    """The cached prefix must not move when the clock or the repo does."""
    block = environment_context(_git_repo(tmp_path))
    assert "Today's date:" not in block
    assert "Git branch" not in block and "Git status" not in block


def test_live_block_outside_git_repo(tmp_path):
    assert "not a git repository" in environment_live(tmp_path)


def test_live_block_with_git_repo(tmp_path):
    ws = _git_repo(tmp_path)
    block = environment_live(ws)
    assert "Today's date:" in block
    assert "Git branch: main" in block
    assert "Git status: clean" in block
    assert "first commit" in block


def test_live_block_shows_dirty_status(tmp_path):
    ws = _git_repo(tmp_path)
    (ws / "f.txt").write_text("2", encoding="utf-8")
    (ws / "new.txt").write_text("x", encoding="utf-8")
    block = environment_live(ws)
    assert "Git status (2 changed):" in block
    assert "f.txt" in block and "new.txt" in block


class _Stub:
    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        from coworker.providers import ModelCapabilities

        return ModelCapabilities()


def test_build_engine_injects_environment(tmp_path):
    from coworker.agent import build_engine
    from coworker.agents import code_agent

    engine = build_engine(agent=code_agent(), workspace=tmp_path, provider=_Stub())
    try:
        system = engine.messages[0]
        assert system["role"] == "system"
        assert "<environment>" in system["content"]
        assert str(tmp_path.resolve()) in system["content"]
    finally:
        engine.executor.close()
