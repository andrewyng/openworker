"""A socket connect that has to build a sandbox (OPE-206).

The engine build used to run on the event loop; with OpenShell that is a container being
created, so the whole server stopped answering and the GUI fell back to its startup
screen. Now the socket says `sandbox_preparing`, the build runs on a worker thread, and
the socket reports `sandbox_ready` (or an `error` with the reason) before `ready`.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from coworker.sandbox.providers.openshell import OpenShellUnavailable
from coworker.server import create_app
from tests.test_persona_connections import _mgr


class _FakeSandbox:
    def describe(self):
        return {"provider": "openshell", "enforcement": "full", "reason": "OpenShell 0.0.116: Landlock and seccomp", "sandbox": "ow-abc", "runner": {"os": "Linux"}}


def test_a_connect_that_builds_a_sandbox_says_so_and_builds_off_the_loop(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.setattr(mgr, "pending_sandbox_build", lambda session_id: "openshell")
    seen: dict = {}
    real = mgr.get_engine

    def build(session_id, **kwargs):
        seen["thread"] = threading.current_thread().name
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        engine = real(session_id, **kwargs)
        engine.sandbox_workspace = _FakeSandbox()
        return engine

    monkeypatch.setattr(mgr, "get_engine", build)
    client = TestClient(create_app(mgr))
    with client.websocket_connect("/ws/session/s-box?agent=cowork") as ws:
        first, second, third = ws.receive_json(), ws.receive_json(), ws.receive_json()
    assert first == {"type": "sandbox_preparing", "data": {"provider": "openshell"}}
    assert second["type"] == "sandbox_ready"
    assert second["data"] == {"provider": "openshell", "enforcement": "full", "reason": "OpenShell 0.0.116: Landlock and seccomp", "sandbox": "ow-abc"}
    assert third["type"] == "ready"
    assert seen["on_loop"] is False  # the build ran on a worker thread, the loop stayed free


def test_a_connect_that_needs_no_sandbox_is_unchanged(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)  # no sandbox_provider set: direct mode
    client = TestClient(create_app(mgr))
    with client.websocket_connect("/ws/session/s-plain?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"


def test_a_refused_sandbox_is_reported_on_the_socket_not_as_a_server_error(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.setattr(mgr, "pending_sandbox_build", lambda session_id: "openshell")
    refusal = "The sandbox base image is not downloaded yet (about 5 GB, one time). Run `openworker machine sandbox setup`."

    def refuse(session_id, **kwargs):
        raise OpenShellUnavailable(refusal)

    monkeypatch.setattr(mgr, "get_engine", refuse)
    client = TestClient(create_app(mgr))
    from starlette.websockets import WebSocketDisconnect

    from coworker.server.app import WS_CLOSE_SESSION_REFUSED

    with client.websocket_connect("/ws/session/s-refused?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "sandbox_preparing"
        assert ws.receive_json() == {"type": "error", "data": {"error": refusal}}
        with pytest.raises(WebSocketDisconnect) as closed:  # closed cleanly, with the "final" code
            ws.receive_json()
    assert closed.value.code == WS_CLOSE_SESSION_REFUSED  # so the client does not retry the refusal


def test_pending_sandbox_build_reads_the_config_and_knows_an_existing_engine(tmp_path, monkeypatch):
    from coworker import config

    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.delenv("OPENWORKER_SANDBOX_PROVIDER", raising=False)
    monkeypatch.delenv("OPENWORKER_HEADLESS", raising=False)
    monkeypatch.setattr(config, "global_config_path", lambda: tmp_path / "config.toml")
    assert mgr.pending_sandbox_build("s-new") == ""  # default rule on a desktop: direct
    monkeypatch.setenv("OPENWORKER_HEADLESS", "1")
    assert mgr.pending_sandbox_build("s-new") == "openshell"  # a headless machine's default
    monkeypatch.delenv("OPENWORKER_HEADLESS")
    mgr.get_engine("s-built", agent="cowork")  # direct mode here: no OpenShell on a test box
    config.set_global_value("sandbox_provider", "openshell")
    assert mgr.pending_sandbox_build("s-new") == "openshell"
    assert mgr.pending_sandbox_build("s-built") == ""  # built already: nothing pending
    monkeypatch.setenv("OPENWORKER_SANDBOX_PROVIDER", "direct")
    assert mgr.pending_sandbox_build("s-new") == ""  # the environment wins


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
