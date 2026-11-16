"""Feature worktrees (Code coworker, 2026-11-07): the policy module on real temp repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coworker import worktrees as wt


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "Rohit C Prasad", cwd=root)
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git("add", ".", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    return root


def test_settings_merge_prefs_and_repo_override():
    prefs = {"worktree_location": "sibling", "worktree_prune_days": "7",
             "worktree_overrides": {"/k": {"location": "custom", "custom_dir": "/tmp/x"}}}
    s = wt.settings_from_prefs(prefs)
    assert s.location == "sibling" and s.prune_days == 7 and s.fetch_first is True
    o = wt.settings_from_prefs(prefs, project_key="/k")
    assert o.location == "custom" and o.custom_dir == "/tmp/x" and o.prune_days == 7
    bad = wt.settings_from_prefs({"worktree_location": "moon", "worktree_prune_days": "x"})
    assert bad.location == "in_repo" and bad.prune_days == 14


def test_validate_settings_rejects_what_it_should():
    assert wt.validate_settings({"location": "sibling", "prune_days": "3", "branch_prefix": "rp/"}) == {
        "location": "sibling", "prune_days": 3, "branch_prefix": "rp/"}
    with pytest.raises(ValueError):
        wt.validate_settings({"location": "custom", "custom_dir": ""})
    with pytest.raises(ValueError):
        wt.validate_settings({"branch_prefix": "bad prefix!"})
    with pytest.raises(ValueError):
        wt.validate_settings({"prune_days": 0})


def test_prefix_comes_from_the_git_user_name(repo):
    assert wt.derive_prefix(repo) == "rp/"
    assert wt.slugify("Add the plan dock above the composer!") == "add-the-plan-dock-above-the-composer"


def test_create_in_repo_worktree_excludes_and_names_by_policy(repo):
    info = wt.create_worktree(repo, "Add a login page", wt.WorktreeSettings())
    path = Path(info["path"])
    assert path == repo / ".openworker" / "worktrees" / "add-a-login-page"
    assert path.is_dir() and (path / "README.md").is_file()
    assert info["branch"] == "rp/add-a-login-page" and info["base"] == "main"
    assert info["fetched"] is False  # no origin: branched from the local main, no warning
    assert info["warnings"] == []
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".openworker/" in exclude.splitlines()
    # The user's checkout is untouched and git status stays clean there.
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert status.strip() == ""
    # A second task with the same words gets a distinct branch and folder.
    again = wt.create_worktree(repo, "Add a login page", wt.WorktreeSettings())
    assert again["branch"] == "rp/add-a-login-page-2"
    assert Path(again["path"]).name == "add-a-login-page-2"


def test_sibling_and_custom_locations_and_setup_command(repo, tmp_path):
    info = wt.create_worktree(repo, "sibling task", wt.WorktreeSettings(location="sibling"))
    assert Path(info["path"]) == repo.parent / "proj-worktrees" / "sibling-task"
    custom = tmp_path / "elsewhere"
    info = wt.create_worktree(
        repo, "custom task",
        wt.WorktreeSettings(location="custom", custom_dir=str(custom), setup_command="echo ready > setup.txt"),
    )
    assert Path(info["path"]) == custom / "proj" / "custom-task"
    assert (Path(info["path"]) / "setup.txt").read_text().strip() == "ready"
    assert info["setup"]["exit_code"] == 0


def test_not_a_repo_is_reported_not_guessed(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert wt.repo_root(plain) is None


def test_removal_never_touches_a_dirty_worktree(repo):
    info = wt.create_worktree(repo, "clean me", wt.WorktreeSettings())
    path = Path(info["path"])
    (path / "wip.txt").write_text("unfinished", encoding="utf-8")
    assert wt.is_clean(path) is False
    assert wt.remove_worktree(repo, path) is False and path.is_dir()
    (path / "wip.txt").unlink()
    assert wt.remove_worktree(repo, path) is True and not path.exists()
    # The branch survives: unmerged work is never deleted with its folder.
    branches = subprocess.run(["git", "branch", "--list", info["branch"]], cwd=repo, capture_output=True, text=True).stdout
    assert info["branch"] in branches
