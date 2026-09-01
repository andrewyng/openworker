"""Project identity (twentieth pass): key derivation, naming, one-time migration."""

import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from coworker.memory.base import Scope
from coworker.memory.sqlite_store import SQLiteMemoryStore
from coworker.projects import (
    ProjectNames,
    project_key,
    project_label,
    project_presence,
    resolve_board_space,
    resolve_memory_key,
)
from coworker.teams.store import GENESIS, TeamStore
from coworker.teams.model import Actor, Role


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "a.txt").write_text("x")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-qm", "init")
    return r


class TestProjectKey:
    def test_plain_dir_is_resolved_path(self, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        assert project_key(d) == str(d.resolve())

    def test_missing_dir_falls_back_to_path(self, tmp_path):
        d = tmp_path / "nope"
        assert project_key(d) == str(d.resolve())

    def test_repo_root_keys_to_repo(self, repo):
        assert project_key(repo) == str(repo.resolve())

    def test_subdir_keys_to_repo(self, repo):
        sub = repo / "src" / "deep"
        sub.mkdir(parents=True)
        assert project_key(sub) == str(repo.resolve())

    def test_worktrees_share_one_key(self, repo, tmp_path):
        wt = tmp_path / "wt"
        _git(repo, "worktree", "add", "-q", str(wt))
        assert project_key(wt) == project_key(repo) == str(repo.resolve())

    def test_labels(self, repo, tmp_path):
        lab = project_label(str(repo.resolve()))
        assert lab["kind"] == "git" and lab["label"] == "repo"
        plain = tmp_path / "plain"
        plain.mkdir()
        lab2 = project_label(str(plain.resolve()))
        assert lab2["kind"] == "folder"

    def test_label_home_collapse(self, tmp_path):
        lab = project_label(str(tmp_path / "x" / "y"), home=str(tmp_path))
        assert lab["label"] == "~/x/y"


class TestProjectNames:
    @pytest.fixture()
    def names(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return ProjectNames(conn, threading.RLock())

    def test_name_resolve_roundtrip(self, names):
        names.name_current("memory", "openworker", "/k1")
        assert names.resolve("memory", "openworker") == "/k1"
        assert names.resolve("board", "openworker") is None  # kinds are separate

    def test_rename_repoints(self, names):
        names.name_current("memory", "ops", "/k1")
        names.name_current("memory", "ops", "/k2")
        assert names.resolve("memory", "ops") == "/k2"

    def test_mru_order_and_limit(self, names):
        import time
        for i, n in enumerate(["a", "b", "c"]):
            names.name_current("memory", n, f"/k{i}")
        names._conn.execute(
            "UPDATE project_names SET last_used_at = datetime('now', '-1 hour') "
            "WHERE name != 'a'"
        )
        listed = names.list("memory", limit=2)
        assert [x["name"] for x in listed] == ["a", "b"]

    def test_rejects_bad_input(self, names):
        with pytest.raises(ValueError):
            names.name_current("memory", "  ", "/k")
        with pytest.raises(ValueError):
            names.name_current("nope", "x", "/k")

    def test_forget(self, names):
        names.name_current("board", "x", "/k")
        assert names.forget("board", "x") is True
        assert names.resolve("board", "x") is None


class TestMigration:
    def test_memory_rekey_unions(self, tmp_path):
        store = SQLiteMemoryStore(tmp_path / "m.db")
        store.add("old fact", scope=Scope.WORKSPACE, workspace="/old")
        store.add("git fact", scope=Scope.WORKSPACE, workspace="/new")
        moved = store.rekey_workspace("/old", "/new")
        assert moved == 1
        assert {m.content for m in store.list(workspace="/new")} == {"old fact", "git fact"}
        assert store.list(workspace="/old") == []

    def test_board_rekey_recomputes_chain(self, tmp_path):
        store = TeamStore(tmp_path / "b.db")
        lead = Actor(id="lead", role=Role.LEAD)
        store.create_item("/old", lead, title="one", criteria="c1")
        store.create_item("/old", lead, title="two", criteria="c2")
        assert store.rekey_space("/old", "/new") is True
        assert store.event_count("/old") == 0
        assert store.event_count("/new") == 2
        assert store.verify_chain("/new") == 2  # chain honestly recomputed

    def test_board_rekey_refuses_occupied_target(self, tmp_path):
        store = TeamStore(tmp_path / "b.db")
        lead = Actor(id="lead", role=Role.LEAD)
        store.create_item("/old", lead, title="old item", criteria="c")
        store.create_item("/new", lead, title="new item", criteria="c")
        assert store.rekey_space("/old", "/new") is False
        assert store.event_count("/old") == 1  # dormant, untouched


class TestResolvers:
    def test_binding_beats_derivation(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        names = ProjectNames(conn, threading.RLock())
        names.name_current("memory", "openworker", "/the/real/project")
        key = resolve_memory_key(str(tmp_path), binding="openworker", names=names)
        assert key == "/the/real/project"

    def test_derivation_migrates_memory(self, repo, tmp_path):
        store = SQLiteMemoryStore(tmp_path / "m.db")
        sub = repo / "src"
        sub.mkdir()
        store.add("learned here", scope=Scope.WORKSPACE, workspace=str(sub.resolve()))
        key = resolve_memory_key(str(sub), memory_store=store)
        assert key == str(repo.resolve())
        assert [m.content for m in store.list(workspace=key)] == ["learned here"]

    def test_presence_counts(self, tmp_path):
        mstore = SQLiteMemoryStore(tmp_path / "m.db")
        tstore = TeamStore(tmp_path / "b.db")
        mstore.add("f", scope=Scope.WORKSPACE, workspace="/p")
        tstore.create_item("/p", Actor(id="l", role=Role.LEAD), title="t", criteria="c")
        pres = project_presence("/p", memory_store=mstore, team_store=tstore)
        assert pres == {"memories": 1, "board_items": 1}
