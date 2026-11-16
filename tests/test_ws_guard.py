"""T3-3: a websocket whose client stopped reading must not hold the server up. A GUI websocket
send that cannot finish in time drops the connection, and at shutdown every open websocket is
aborted before uvicorn waits for its connections."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from coworker.server import ws_guard


class _Transport(asyncio.BaseTransport):
    def __init__(self) -> None:
        super().__init__()
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True

    def is_closing(self) -> bool:
        return self.aborted


class _Protocol:
    """Stands in for uvicorn's websocket protocol: its `send` is a bound method, and it holds
    the connection's transport."""

    def __init__(self, stuck: bool) -> None:
        self.transport = _Transport()
        self.stuck = stuck
        self.sent: list[dict] = []

    async def send(self, message: dict) -> None:
        if self.stuck and message.get("type") == "websocket.send":
            await asyncio.sleep(3600)  # the client stopped reading: the buffer never drains
        self.sent.append(message)


async def _receive() -> dict:
    return {"type": "websocket.connect"}


def _app(log: list):
    async def app(scope, receive, send):
        assert ws_guard.open_count() == 1  # tracked while it is open
        await send({"type": "websocket.accept"})
        try:
            await send({"type": "websocket.send", "text": "x"})
        except WebSocketDisconnect as exc:
            log.append(exc.code)
            return
        log.append("sent")

    return app


def test_a_gui_websocket_whose_client_stopped_reading_is_dropped():
    log: list = []
    proto = _Protocol(stuck=True)
    guard = ws_guard.WebsocketGuard(_app(log), send_timeout=0.05)
    asyncio.run(guard({"type": "websocket", "path": "/ws/session/s1"}, _receive, proto.send))
    assert proto.transport.aborted and log == [1011]
    assert ws_guard.open_count() == 0  # no longer tracked


def test_a_reading_client_is_untouched():
    log: list = []
    proto = _Protocol(stuck=False)
    guard = ws_guard.WebsocketGuard(_app(log), send_timeout=0.05)
    asyncio.run(guard({"type": "websocket", "path": "/ws/events"}, _receive, proto.send))
    assert not proto.transport.aborted and log == ["sent"]


def test_every_websocket_is_tracked_only_while_open():
    log: list = []
    proto = _Protocol(stuck=False)
    guard = ws_guard.WebsocketGuard(_app(log), send_timeout=0.05)
    asyncio.run(guard({"type": "websocket", "path": "/ws/machines/m1/p"}, _receive, proto.send))
    assert log == ["sent"] and ws_guard.open_count() == 0 and proto.transport not in ws_guard._open


def test_machine_links_have_no_send_timeout():
    """/ws/machines carries large frames over slow links: no timeout there, only the
    shutdown abort."""

    async def go():
        proto = _Protocol(stuck=True)
        guard = ws_guard.WebsocketGuard(_app([]), send_timeout=0.05)
        task = asyncio.create_task(guard({"type": "websocket", "path": "/ws/machines/m1/p"}, _receive, proto.send))
        await asyncio.sleep(0.3)
        assert not task.done() and not proto.transport.aborted  # still waiting, not dropped
        assert ws_guard.abort_open_websockets() == 1  # what shutdown does
        assert proto.transport.aborted
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())


def test_the_server_aborts_open_websockets_before_it_waits(monkeypatch):
    order: list[str] = []
    transport = _Transport()
    ws_guard._open.add(transport)

    async def base_shutdown(self, sockets=None):
        order.append("uvicorn waits" + (" (none open)" if ws_guard.open_count() == 0 else " (still open!)"))

    monkeypatch.setattr(ws_guard.uvicorn.Server, "shutdown", base_shutdown)
    server = ws_guard.GuardedServer.__new__(ws_guard.GuardedServer)
    asyncio.run(server.shutdown())
    assert transport.aborted and order == ["uvicorn waits (none open)"]


def test_the_send_timeout_can_be_set_and_falls_back(monkeypatch):
    monkeypatch.setenv("COWORKER_WS_SEND_TIMEOUT", "2.5")
    assert ws_guard._send_timeout() == 2.5
    for bad in ("0", "-1", "soon"):
        monkeypatch.setenv("COWORKER_WS_SEND_TIMEOUT", bad)
        assert ws_guard._send_timeout() == ws_guard.SEND_TIMEOUT_SECONDS


def test_the_app_still_serves_its_websockets_through_the_guard(tmp_path, monkeypatch):
    """Under the test client there is no transport to track; the session websocket works as
    before."""
    from coworker.server import SessionManager, create_app

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/events"):
        pass
    with client.websocket_connect("/ws/session/g1") as ws:
        assert ws.receive_json()["type"] in ("ready", "export_notice")
    if hasattr(manager, "observability"):
        manager.observability.close()
