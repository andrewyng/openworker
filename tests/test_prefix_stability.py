"""The system prompt is the cached prefix — it must not move when the repo does.

vLLM on this machine serves a 92% prefix-cache hit rate. That only pays if the bytes ahead
of the conversation stay identical between turns. The environment block used to carry
`git status --porcelain` and the last five commits inside the system prompt, so every file
the agent wrote invalidated the prefix — and a build persona writes constantly.
"""

import subprocess
from pathlib import Path

import pytest

from coworker.environment import environment_context, environment_live


def git(ws: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "first")
    return tmp_path


def test_stable_block_survives_a_write_and_a_commit(repo: Path):
    before_stable, before_live = environment_context(repo), environment_live(repo)

    (repo / "b.txt").write_text("two\n")
    after_write_stable, after_write_live = environment_context(repo), environment_live(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "second")
    after_commit_stable, after_commit_live = environment_context(repo), environment_live(repo)

    # The control: if the live block never moved, this test proves nothing and must say so
    # rather than passing on a repo whose state never actually changed.
    assert before_live != after_write_live, "repo state did not change — test is invalid"
    assert after_write_live != after_commit_live, "commit did not change state — invalid"

    # The claim.
    assert before_stable == after_write_stable == after_commit_stable


def test_volatile_facts_are_not_in_the_prefix(repo: Path):
    (repo / "dirty.txt").write_text("x\n")
    stable = environment_context(repo)
    for leaked in ("Git branch", "Git status", "Recent commits", "Today's date", "dirty.txt"):
        assert leaked not in stable, f"{leaked!r} is in the cached prefix"


def test_volatile_facts_are_still_delivered(repo: Path):
    """Moved, not dropped. The agent must still get them, just later in the prompt."""
    (repo / "dirty.txt").write_text("x\n")
    live = environment_live(repo)
    for wanted in ("Git branch", "Git status", "Recent commits", "Today's date", "dirty.txt"):
        assert wanted in live, f"{wanted!r} was lost, not relocated"


def test_stable_block_keeps_the_folder_scope_policy(repo: Path):
    assert "Folder scope" in environment_context(repo)


def test_non_git_directory_does_not_break_either_half(tmp_path: Path):
    assert "Workspace:" in environment_context(tmp_path)
    assert "not a git repository" in environment_live(tmp_path)
