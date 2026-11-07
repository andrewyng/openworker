"""`start_worktree` (Code coworker, 2026-11-07): the session moves into its feature
worktree — roots, shell cwd, persisted record — and cleanup on archive respects dirt.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from coworker.sessions import SessionRecord
from coworker.server.manager import SessionManager
from coworker.roots import RootDir


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "Test User", cwd=root)
    (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git("add", ".", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    return root


class _Shell:
    """The slice of an executor start_worktree touches: a cwd and a `cd` command."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.ran: list[str] = []

    def run(self, command: str, timeout: int = 15):
        self.ran.append(command)
        if command.startswith("cd "):
            self.cwd = command[3:].strip().strip("'")
        return {"exit_code": 0, "stdout": "", "stderr": ""}


def _manager_with_session(tmp_path, repo, monkeypatch) -> tuple[SessionManager, str, SimpleNamespace]:
    mgr = SessionManager(workspace=str(tmp_path), data_dir=tmp_path / "data")
    sid = "sess-wt"
    mgr.session_store.save(SessionRecord(session_id=sid, workspace=str(repo), model="m", mode="auto", agent="code"))
    engine = SimpleNamespace(
        executor=_Shell(str(repo)),
        roots=[RootDir(path=repo.resolve(), writable=True, label="workspace")],
    )
    mgr._engines[sid] = engine
    monkeypatch.setattr(mgr, "save", lambda *a, **k: None)  # the real save needs a full engine
    return mgr, sid, engine


def test_start_worktree_moves_the_session_in(tmp_path, repo, monkeypatch):
    mgr, sid, engine = _manager_with_session(tmp_path, repo, monkeypatch)
    res = mgr.start_worktree(sid, "add a login page")
    assert res["ok"], res
    path = Path(res["path"])
    assert path == repo / ".openworker" / "worktrees" / "add-a-login-page"
    assert res["branch"].endswith("/add-a-login-page")
    # The shell moved, the worktree is the writable primary, the checkout went read-only.
    assert Path(engine.executor.cwd).resolve() == path.resolve()
    assert engine.roots[0].path == path.resolve() and engine.roots[0].writable
    original = [r for r in engine.roots if Path(str(r.path)).resolve() == repo.resolve()]
    assert original and original[0].writable is False
    # Persisted on the record, and the engine is marked for the post-turn rebuild.
    assert mgr.session_worktree(sid)["branch"] == res["branch"]
    assert sid in mgr._promotion_rebuild
    # A second call is a no-op that says so.
    again = mgr.start_worktree(sid, "something else")
    assert again["ok"] and again.get("already")


def test_start_worktree_refuses_a_plain_folder(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    mgr, sid, engine = _manager_with_session(tmp_path, plain, monkeypatch)
    res = mgr.start_worktree(sid, "anything")
    assert not res["ok"] and "not a git repository" in res["error"]
    assert engine.executor.cwd == str(plain)  # nothing moved


def test_archive_removes_a_clean_worktree_but_keeps_a_dirty_one(tmp_path, repo, monkeypatch):
    mgr, sid, engine = _manager_with_session(tmp_path, repo, monkeypatch)
    res = mgr.start_worktree(sid, "cleanup check")
    path = Path(res["path"])
    (path / "wip.txt").write_text("unfinished", encoding="utf-8")
    mgr.set_session_flags(sid, archived=True)
    assert path.is_dir(), "a dirty worktree must survive archive"
    (path / "wip.txt").unlink()
    mgr.set_session_flags(sid, archived=False)
    mgr.set_session_flags(sid, archived=True)
    assert not path.exists()
    # The branch is still there.
    out = subprocess.run(["git", "branch", "--list", res["branch"]], cwd=repo, capture_output=True, text=True).stdout
    assert res["branch"] in out


def test_worktree_settings_round_trip_and_repo_override(tmp_path, repo):
    mgr = SessionManager(workspace=str(tmp_path), data_dir=tmp_path / "data")
    assert mgr.get_settings()["worktree_location"] == "in_repo"
    bad = mgr.set_worktree_settings({"location": "moon"})
    assert not bad["ok"]
    ok = mgr.set_worktree_settings({"location": "sibling", "branch_prefix": "rp/", "prune_days": 30})
    assert ok["ok"] and ok["worktree_location"] == "sibling" and ok["worktree_prune_days"] == 30
    over = mgr.set_worktree_override(str(repo), {"location": "in_repo"})
    assert over["ok"]
    assert mgr.worktree_settings(str(repo)).location == "in_repo"   # the repo's override wins
    assert mgr.worktree_settings(str(tmp_path)).location == "sibling"  # elsewhere, the global
    cleared = mgr.set_worktree_override(str(repo), {})
    assert cleared["ok"] and mgr.worktree_settings(str(repo)).location == "sibling"
