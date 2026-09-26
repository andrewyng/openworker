"""Prompt contract plus a real local-Git rehearsal of the documented hand-off.

These tests exercise the Git protocol, not model compliance or harness provisioning.
No remote, model, user checkout, or live team is touched.
"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from coworker.personas.manifest import parse_manifest


@pytest.mark.parametrize("persona", ["swe-lead", "swe-worker", "test-worker"])
def test_software_personas_require_verified_owner_handoff(persona):
    path = Path(__file__).resolve().parents[1] / "coworker/personas/builtin" / persona / "manifest.md"
    prompt = parse_manifest(path.read_text()).system_prompt
    assert "full verified SHA" in prompt
    assert "merge --ff-only <verified-sha>" in prompt
    assert "sibling" in prompt and "checkout" in prompt
    assert "repeat test run" in prompt
    assert "relevant test" in prompt and "environment" in prompt


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("git is required for the local worktree protocol test")
    # Do not inherit signing, hooks, repo selection, or credential configuration.
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)

    def git(path, *args, check=True):
        return subprocess.run(
            ["git", "-c", "user.name=Rohit C Prasad", "-c", "user.email=rohit.prasad15@gmail.com",
             "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={tmp_path / 'no-hooks'}",
             "-C", str(path), *args],
            check=check, capture_output=True, text=True,
        )

    def commit(path, filename, content):
        (path / filename).write_text(content)
        git(path, "add", filename)
        git(path, "commit", "-m", f"Update {filename}")
        return git(path, "rev-parse", "HEAD").stdout.strip()

    base = tmp_path / "shared"
    base.mkdir()
    git(base, "init", "--initial-branch=main")
    base_sha = commit(base, "app.txt", "baseline\n")
    sam = tmp_path / "sam" / "worktrees" / "app"
    maya = tmp_path / "maya" / "worktrees" / "app"
    sam.parent.mkdir(parents=True)
    maya.parent.mkdir(parents=True)
    git(base, "worktree", "add", "-b", "publish", str(sam), base_sha)
    feature_sha = commit(sam, "app.txt", "feature\n")
    git(base, "worktree", "add", "-b", "verify", str(maya), feature_sha)
    verified_sha = commit(maya, "regression.txt", "verification evidence\n")
    return git, commit, base, sam, maya, base_sha, feature_sha, verified_sha


def test_branch_owner_fast_forwards_to_exact_verified_commit(handoff):
    git, _, base, sam, maya, base_sha, _, verified_sha = handoff
    assert git(sam, "branch", "--show-current").stdout.strip() == "publish"
    assert not git(sam, "status", "--porcelain").stdout.strip()
    git(sam, "merge", "--ff-only", verified_sha)
    assert git(sam, "rev-parse", "HEAD").stdout.strip() == verified_sha
    assert not git(sam, "status", "--porcelain").stdout.strip()
    assert (sam / "regression.txt").read_text() == "verification evidence\n"
    assert git(maya, "rev-parse", "HEAD").stdout.strip() == verified_sha
    assert git(base, "rev-parse", "HEAD").stdout.strip() == base_sha
    assert not git(base, "status", "--porcelain").stdout.strip()
    assert git(base, "remote").stdout.strip() == ""


def test_sibling_cannot_force_move_checked_out_publish_branch(handoff):
    git, _, _, sam, maya, _, feature_sha, verified_sha = handoff
    result = git(maya, "branch", "-f", "publish", verified_sha, check=False)
    assert result.returncode != 0
    assert git(sam, "rev-parse", "HEAD").stdout.strip() == feature_sha
    assert git(maya, "rev-parse", "HEAD").stdout.strip() == verified_sha
    assert (sam / "app.txt").read_text() == "feature\n"


def test_divergence_stops_without_discarding_either_workers_commits(handoff):
    git, commit, base, sam, maya, base_sha, _, verified_sha = handoff
    extra_sha = commit(sam, "extra.txt", "new work after handoff\n")
    result = git(sam, "merge", "--ff-only", verified_sha, check=False)
    assert result.returncode != 0
    assert git(sam, "rev-parse", "HEAD").stdout.strip() == extra_sha
    assert (sam / "extra.txt").read_text() == "new work after handoff\n"
    assert not (sam / "regression.txt").exists()
    assert not git(sam, "status", "--porcelain").stdout.strip()
    assert git(maya, "rev-parse", "HEAD").stdout.strip() == verified_sha
    assert git(base, "rev-parse", "HEAD").stdout.strip() == base_sha


def test_conflicting_uncommitted_work_is_preserved(handoff):
    git, commit, _, sam, maya, _, feature_sha, _ = handoff
    verified_sha = commit(maya, "app.txt", "feature with verified correction\n")
    (sam / "app.txt").write_text("uncommitted owner changes\n")
    assert git(sam, "status", "--porcelain").stdout.strip()
    # The protocol requires a clean preflight. Git also refuses this conflict;
    # it must not be 'repaired' by resetting or stashing the owner's work.
    result = git(sam, "merge", "--ff-only", verified_sha, check=False)
    assert result.returncode != 0
    assert git(sam, "rev-parse", "HEAD").stdout.strip() == feature_sha
    assert (sam / "app.txt").read_text() == "uncommitted owner changes\n"
    assert git(maya, "rev-parse", "HEAD").stdout.strip() == verified_sha


def test_successful_git_exit_alone_does_not_prove_verified_head(handoff):
    git, commit, _, sam, _, _, _, verified_sha = handoff
    git(sam, "merge", "--ff-only", verified_sha)
    unverified_sha = commit(sam, "extra.txt", "unverified later change\n")
    # Already-up-to-date is successful even when HEAD is ahead of the verdict.
    # The documented exact-SHA comparison must reject reuse of the old PASS.
    result = git(sam, "merge", "--ff-only", verified_sha)
    assert result.returncode == 0
    assert git(sam, "rev-parse", "HEAD").stdout.strip() == unverified_sha
    assert unverified_sha != verified_sha
