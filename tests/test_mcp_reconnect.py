"""MCPManager recovers a connection that died under a live session.

Owner-hit 2026-10-22: a MetaMCP hub restart wiped its session table, every request
carrying the old session id got HTTP 404 ("Session terminated" from the SDK), and the
cached connection stayed dead until the app restarted. The MCP spec has the client
open a new session; `call` now does that once — and only on errors that prove the
request never ran, so a tool call is never repeated after it might have executed.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from coworker.mcp.client import MCPManager, _Conn
from coworker.mcp.config import MCPServerDef


def _server(name: str = "srv") -> MCPServerDef:
    return MCPServerDef(name=name, transport="http", url="https://hub.example/mcp")


def _terminated() -> McpError:
    return McpError(ErrorData(code=32600, message="Session terminated"))


class _Session:
    """Scripted call_tool: each entry is an exception to raise or a result to return."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool, arguments):
        self.calls.append((tool, arguments))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _ok(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)


def _fake_ensure(mgr: MCPManager, fresh: _Conn, calls: list):
    async def ensure(server, *, interactive=False):
        calls.append((server.name, interactive))
        await asyncio.sleep(0)  # let concurrent callers pile up on the reopen lock
        mgr._conns[server.name] = fresh
        mgr._lost.pop(server.name, None)
        return fresh

    return ensure


@pytest.mark.asyncio
async def test_session_terminated_reconnects_once_and_retries():
    mgr = MCPManager()
    server = _server()
    dead = _Conn(_Session(_terminated()), tools=[], server=server)
    mgr._conns["srv"] = dead
    fresh_session = _Session(_ok("found it"))
    fresh = _Conn(fresh_session, tools=[], server=server)
    ensured: list = []
    mgr.ensure = _fake_ensure(mgr, fresh, ensured)  # type: ignore[method-assign]

    out = await mgr.call("srv", "search_drive_files", {"query": "x"})

    assert out == "found it"
    assert ensured == [("srv", False)]  # one reconnect, never an interactive sign-in
    assert dead.shutdown.is_set()
    assert fresh_session.calls == [("search_drive_files", {"query": "x"})]
    assert mgr._conns["srv"] is fresh


@pytest.mark.asyncio
async def test_second_failure_after_reconnect_surfaces_without_looping():
    mgr = MCPManager()
    server = _server()
    mgr._conns["srv"] = _Conn(_Session(_terminated()), tools=[], server=server)
    fresh = _Conn(_Session(_terminated()), tools=[], server=server)
    ensured: list = []
    mgr.ensure = _fake_ensure(mgr, fresh, ensured)  # type: ignore[method-assign]

    with pytest.raises(McpError, match="Session terminated"):
        await mgr.call("srv", "t", {})
    assert len(ensured) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        # The request may already have run on the server: never send it twice.
        McpError(ErrorData(code=-32000, message="Connection closed")),
        asyncio.TimeoutError(),
        RuntimeError("boom"),
    ],
)
async def test_errors_that_may_have_executed_are_not_retried(exc):
    mgr = MCPManager()
    server = _server()
    session = _Session(exc)
    mgr._conns["srv"] = _Conn(session, tools=[], server=server)
    ensured: list = []
    mgr.ensure = _fake_ensure(mgr, _Conn(_Session(), [], server), ensured)  # type: ignore[method-assign]

    with pytest.raises(type(exc)):
        await mgr.call("srv", "send_gmail_message", {})
    assert ensured == []
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_closed_transport_reconnects():
    mgr = MCPManager()
    server = _server()
    mgr._conns["srv"] = _Conn(_Session(anyio.ClosedResourceError()), [], server)
    fresh = _Conn(_Session(_ok("ok")), [], server)
    ensured: list = []
    mgr.ensure = _fake_ensure(mgr, fresh, ensured)  # type: ignore[method-assign]

    assert await mgr.call("srv", "t", {}) == "ok"
    assert len(ensured) == 1


@pytest.mark.asyncio
async def test_concurrent_failures_share_one_reconnect():
    mgr = MCPManager()
    server = _server()
    mgr._conns["srv"] = _Conn(_Session(_terminated(), _terminated()), [], server)
    fresh = _Conn(_Session(_ok("a"), _ok("b")), [], server)
    ensured: list = []
    mgr.ensure = _fake_ensure(mgr, fresh, ensured)  # type: ignore[method-assign]

    results = await asyncio.gather(mgr.call("srv", "t", {}), mgr.call("srv", "t", {}))
    assert sorted(results) == ["a", "b"]
    assert len(ensured) == 1


@pytest.mark.asyncio
async def test_connection_without_a_definition_is_not_reconnected():
    # Hand-built connections (no server def) keep the old behaviour.
    mgr = MCPManager()
    mgr._conns["srv"] = _Conn(_Session(_terminated()), tools=[])
    with pytest.raises(McpError):
        await mgr.call("srv", "t", {})


# -- the transport task dying under us (real _serve, fake transport) -------------


class _FakeClientSession:
    def __init__(self, read, write):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name="t")])

    async def call_tool(self, tool, arguments):
        self.calls += 1
        return _ok(f"{tool} ok")


@pytest.fixture
def fake_transport(monkeypatch):
    """Each opened transport gets a `drop` event; setting it kills that transport the
    way a real one dies — a failing task in its task group tears down the host."""
    drops: list[asyncio.Event] = []

    @asynccontextmanager
    async def transport(url, headers=None, auth=None):
        drop = asyncio.Event()
        drops.append(drop)

        async def die():
            await drop.wait()
            raise RuntimeError("transport dropped")

        async with anyio.create_task_group() as tg:
            tg.start_soon(die)
            yield (None, None, None)
            tg.cancel_scope.cancel()

    monkeypatch.setattr("coworker.mcp.client.streamablehttp_client", transport)
    monkeypatch.setattr("coworker.mcp.client.ClientSession", _FakeClientSession)
    return drops


async def _wait_gone(mgr: MCPManager, name: str) -> None:
    for _ in range(200):
        if name not in mgr._conns and name not in mgr._tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{name} never went away")


@pytest.mark.asyncio
async def test_transport_death_is_reopened_by_the_next_call(fake_transport):
    mgr = MCPManager()
    server = _server()
    await mgr.ensure(server)
    fake_transport[0].set()
    await _wait_gone(mgr, "srv")
    assert mgr._lost.get("srv") is server

    assert await mgr.call("srv", "t", {}) == "t ok"
    assert len(fake_transport) == 2  # a fresh transport, not the dead one
    assert "srv" not in mgr._lost
    await mgr.aclose()


@pytest.mark.asyncio
async def test_deliberate_shutdown_is_not_reopened(fake_transport):
    mgr = MCPManager()
    await mgr.ensure(_server())
    mgr._conns["srv"].shutdown.set()  # what signout/delete/disconnect do
    await _wait_gone(mgr, "srv")

    assert "srv" not in mgr._lost
    with pytest.raises(RuntimeError, match="not connected"):
        await mgr.call("srv", "t", {})
    assert len(fake_transport) == 1


@pytest.mark.asyncio
async def test_forget_stops_a_dead_server_from_reopening(fake_transport):
    mgr = MCPManager()
    await mgr.ensure(_server())
    fake_transport[0].set()
    await _wait_gone(mgr, "srv")

    mgr.forget("srv")  # the user removed it while it was down
    with pytest.raises(RuntimeError, match="not connected"):
        await mgr.call("srv", "t", {})


@pytest.mark.asyncio
async def test_server_still_down_stays_reconnectable(fake_transport, monkeypatch):
    mgr = MCPManager()
    server = _server()
    await mgr.ensure(server)
    fake_transport[0].set()
    await _wait_gone(mgr, "srv")

    real_ensure = mgr.ensure
    attempts = []

    async def flaky_ensure(s, *, interactive=False):
        attempts.append(s.name)
        if len(attempts) == 1:
            raise ConnectionError("hub unreachable")
        return await real_ensure(s, interactive=interactive)

    monkeypatch.setattr(mgr, "ensure", flaky_ensure)
    with pytest.raises(ConnectionError):
        await mgr.call("srv", "t", {})
    # Not "not connected" forever: the next call tries again and gets through.
    assert await mgr.call("srv", "t", {}) == "t ok"
    await mgr.aclose()
