"""A socket connect and a session's sandbox (OPE-206, 2026-09-28).

The engine is built on a worker thread, so a slow build never stops the server. The
sandbox itself is not made at connect: the first turn makes it and says so
(`sandbox_preparing`, then `sandbox_ready`, or an error that ends the turn). Opening a
session, or picking its folder (a new session id each time), builds no sandbox.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from coworker.sandbox.providers.openshell import OpenShellUnavailable
from coworker.server import create_app
from tests.test_persona_connections import _mgr


def test_a_connect_builds_the_engine_off_the_loop_and_no_sandbox(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    seen: dict = {}
    real = mgr.get_engine

    def build(session_id, **kwargs):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return real(session_id, **kwargs)

    monkeypatch.setattr(mgr, "get_engine", build)
    client = TestClient(create_app(mgr))
    with client.websocket_connect("/ws/session/s-box?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"  # no sandbox_preparing at connect
    assert seen["on_loop"] is False  # the build ran on a worker thread, the loop stayed free


def test_a_connect_that_needs_no_sandbox_is_unchanged(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)  # no sandbox_provider set: direct mode
    client = TestClient(create_app(mgr))
    with client.websocket_connect("/ws/session/s-plain?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"


def test_a_refused_sandbox_is_reported_on_the_socket_not_as_a_server_error(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    refusal = "The sandbox base image is not downloaded yet (about 5 GB, one time). Run `openworker machine sandbox setup`."

    def refuse(session_id, **kwargs):
        raise OpenShellUnavailable(refusal)

    monkeypatch.setattr(mgr, "get_engine", refuse)
    client = TestClient(create_app(mgr))
    from starlette.websockets import WebSocketDisconnect

    from coworker.server.app import WS_CLOSE_SESSION_REFUSED

    with client.websocket_connect("/ws/session/s-refused?agent=cowork") as ws:
        assert ws.receive_json() == {"type": "error", "data": {"error": refusal}}
        with pytest.raises(WebSocketDisconnect) as closed:  # closed cleanly, with the "final" code
            ws.receive_json()
    assert closed.value.code == WS_CLOSE_SESSION_REFUSED  # so the client does not retry the refusal


class _LazySandbox:
    """A workspace whose sandbox is made on first use, like RunnerWorkspace(start=False)."""

    def __init__(self, fail: str = "") -> None:
        self.started, self.fail, self.starts = False, fail, 0
        self.provider = type("P", (), {"name": "seatbelt"})()

    def ensure_started(self):
        self.starts += 1
        if self.fail:
            raise RuntimeError(self.fail)
        self.started = True

    def describe(self):
        return {"provider": "seatbelt", "enforcement": "full", "reason": "the macOS sandbox", "runner": {}}


def _events(engine, text="hi"):
    async def collect():
        return [e async for e in engine._start_sandbox()]

    return asyncio.run(collect())


def test_the_first_turn_makes_the_sandbox_and_says_so(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    engine = mgr.get_engine("s-lazy", agent="cowork")
    engine.sandbox_workspace = _LazySandbox()
    events = _events(engine)
    assert [e.type.value for e in events] == ["sandbox_preparing", "sandbox_ready"]
    assert events[0].data == {"provider": "seatbelt"} and events[1].data["enforcement"] == "full"
    assert _events(engine) == []  # made once
    # A sandbox that cannot start ends the turn with the reason, kept in the transcript.
    engine.sandbox_workspace = _LazySandbox(fail="bind mounts are off")
    events = _events(engine)
    assert [e.type.value for e in events] == ["sandbox_preparing", "error"]
    assert "bind mounts are off" in events[1].data["error"]
    assert engine.messages[-1]["kind"] == "error"

def test_two_connects_to_one_session_build_one_engine(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    builds: list[str] = []
    real = mgr._build_or_get_engine

    def slow_build(session_id, **kwargs):
        if session_id not in mgr._engines:
            builds.append(session_id)
        return real(session_id, **kwargs)

    monkeypatch.setattr(mgr, "_build_or_get_engine", slow_build)
    threads = [threading.Thread(target=lambda: mgr.get_engine("s-twice", agent="cowork")) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert builds == ["s-twice"]  # serialized per session: the others found the engine
