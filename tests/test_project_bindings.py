"""Pass 20 — project bindings over the manager/API surface (UX-044 backend)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from coworker.memory.base import Scope
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord


class _StubProvider(ProviderClient):
    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()


def _fixture(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_StubProvider())
    return TestClient(create_app(manager)), manager


def _seed_session(manager, sid: str, workspace: Path) -> None:
    manager.session_store.save(
        SessionRecord(
            session_id=sid,
            workspace=str(workspace),
            model="m",
            mode="auto",
            messages=[{"role": "user", "content": "hi"}],
        )
    )


def test_project_menu_derived_and_named(tmp_path):
    client, manager = _fixture(tmp_path)
    ws = tmp_path / "proj"
    ws.mkdir()
    _seed_session(manager, "s1", ws)

    menu = client.get("/v1/sessions/s1/project-menu", params={"kind": "memory"}).json()
    assert menu["bound"] is None
    assert menu["derived"]["key"] == str(ws.resolve())
    assert menu["derived"]["kind"] == "folder"
    assert menu["named"] == []

    named = client.post(
        "/v1/sessions/s1/project-name", json={"kind": "memory", "name": "myproj"}
    ).json()
    assert named["ok"] and named["key"] == str(ws.resolve())
    menu = client.get("/v1/sessions/s1/project-menu", params={"kind": "memory"}).json()
    assert [n["name"] for n in menu["named"]] == ["myproj"]


def test_binding_swaps_memory_key(tmp_path):
    client, manager = _fixture(tmp_path)
    ws = tmp_path / "scratch"
    ws.mkdir()
    other = tmp_path / "real"
    other.mkdir()
    _seed_session(manager, "s1", ws)

    manager.session_store.names().name_current(
        "memory", "openworker", str(other.resolve())
    )
    manager.memory_store.add(
        "the real fact", scope=Scope.WORKSPACE, workspace=str(other.resolve())
    )

    put = client.put(
        "/v1/sessions/s1/bindings", json={"kind": "memory", "name": "openworker"}
    ).json()
    assert put["ok"] and put["bindings"] == {"memory": "openworker"}

    record = manager.session_store.load("s1")
    key = manager._memory_key_for(record, record.workspace)
    assert key == str(other.resolve())

    # unbind → back to derivation
    put = client.put("/v1/sessions/s1/bindings", json={"kind": "memory"}).json()
    assert put["ok"] and put["bindings"] == {}
    record = manager.session_store.load("s1")
    assert manager._memory_key_for(record, record.workspace) == str(ws.resolve())


def test_binding_rejects_unknown_name_and_kind(tmp_path):
    client, manager = _fixture(tmp_path)
    _seed_session(manager, "s1", tmp_path)
    assert not client.put(
        "/v1/sessions/s1/bindings", json={"kind": "memory", "name": "ghost"}
    ).json()["ok"]
    assert not client.put(
        "/v1/sessions/s1/bindings", json={"kind": "nope", "name": "x"}
    ).json()["ok"]


def test_bindings_survive_turn_save(tmp_path):
    """The per-turn upsert must not clobber bindings (same doctrine as team)."""
    client, manager = _fixture(tmp_path)
    ws = tmp_path / "p"
    ws.mkdir()
    _seed_session(manager, "s1", ws)
    manager.session_store.names().name_current("board", "b1", "/somewhere")
    assert client.put(
        "/v1/sessions/s1/bindings", json={"kind": "board", "name": "b1"}
    ).json()["ok"]

    # simulate a turn-save rebuilding the record WITHOUT bindings
    rec = manager.session_store.load("s1")
    manager.session_store.save(
        SessionRecord(
            session_id="s1",
            workspace=rec.workspace,
            model=rec.model,
            mode=rec.mode,
            messages=rec.messages,
        )
    )
    assert manager.session_store.load("s1").bindings == {"board": "b1"}


def test_board_space_follows_binding(tmp_path):
    client, manager = _fixture(tmp_path)
    ws = tmp_path / "p"
    ws.mkdir()
    _seed_session(manager, "s1", ws)
    manager.session_store.names().name_current("board", "shared", "/the/shared/space")
    client.put("/v1/sessions/s1/bindings", json={"kind": "board", "name": "shared"})
    assert manager._board_space("s1") == "/the/shared/space"


def test_worktree_sessions_share_memory_key(tmp_path):
    _, manager = _fixture(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"],):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "f").write_text("x")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "f"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        cwd=repo, check=True, capture_output=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(wt)],
        cwd=repo, check=True, capture_output=True,
    )
    _seed_session(manager, "a", repo)
    _seed_session(manager, "b", wt)
    ra, rb = manager.session_store.load("a"), manager.session_store.load("b")
    ka = manager._memory_key_for(ra, ra.workspace)
    kb = manager._memory_key_for(rb, rb.workspace)
    assert ka == kb == str(repo.resolve())


def test_grant_notice_fires_on_existing_memory(tmp_path):
    _, manager = _fixture(tmp_path)
    granted = tmp_path / "known"
    granted.mkdir()
    manager.memory_store.add(
        "fact", scope=Scope.WORKSPACE, workspace=str(granted.resolve())
    )
    notice = manager._project_notice(str(granted))
    assert notice is not None and "project memory (1 entries)" in notice

    empty = tmp_path / "fresh"
    empty.mkdir()
    assert manager._project_notice(str(empty)) is None
