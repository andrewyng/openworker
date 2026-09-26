"""Sandbox lifetime (OPE-200): a session's sandbox is closed when its engine is dropped and
when the server shuts down, and orphans are reaped once when a server starts."""

from __future__ import annotations

import asyncio
import time

from coworker.server.manager import SessionManager


class _Workspace:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _Engine:
    def __init__(self) -> None:
        self.sandbox_workspace = _Workspace()


def _manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(data_dir=tmp_path / "data")


def test_dropping_an_engine_closes_its_sandbox(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    engine = _Engine()
    manager._engines["s1"] = engine  # type: ignore[assignment]
    assert manager._drop_engine("s1") is engine
    assert engine.sandbox_workspace.closed == 1
    assert manager._drop_engine("s1") is None  # gone; nothing to close twice


def test_deleting_a_session_closes_its_sandbox(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    engine = _Engine()
    manager._engines["s2"] = engine  # type: ignore[assignment]
    manager.delete_session("s2")
    assert engine.sandbox_workspace.closed == 1


def test_shutdown_closes_every_sandbox_and_a_broken_one_does_not_stop_the_rest(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    good, bad = _Engine(), _Engine()
    bad.sandbox_workspace.close = lambda: (_ for _ in ()).throw(RuntimeError("container gone"))  # type: ignore[method-assign]
    manager._engines.update({"a": bad, "b": good})  # type: ignore[dict-item]
    asyncio.run(manager.aclose())
    assert good.sandbox_workspace.closed == 1


def test_a_server_reaps_orphans_once_at_start(tmp_path, monkeypatch):
    calls: list[str] = []
    from coworker.sandbox import registry

    monkeypatch.setattr(registry.SandboxRegistry, "reap", lambda self: calls.append("reap") or [])
    _manager(tmp_path, monkeypatch)
    for _ in range(50):
        if calls:
            break
        time.sleep(0.05)
    assert calls == ["reap"]
