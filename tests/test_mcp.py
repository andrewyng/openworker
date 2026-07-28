"""Tests for MCP (C1): config loading/merge, tool wrapping + bridge, and REST.

No live MCP subprocess is needed — the connection layer is exercised by stubbing the call
coroutine; a live-server smoke test is documented in the plan instead.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from coworker.integrations.kordoc import (
    KORDOC_MCP_TOOL_ALLOWLIST,
    KordocRuntime,
    KordocRuntimeStatus,
)
from coworker.mcp import (
    MCPManager,
    build_callables,
    builtin_kordoc_server,
    load_mcp_servers,
    tool_name,
)
from coworker.mcp.config import MCPServerDef
from coworker.secrets import SecretStore
from coworker.server.app import create_app
from coworker.server.manager import SessionManager


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _fake_tool(name, schema=None, description="desc"):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema
        or {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )


# -- config --------------------------------------------------------------------
def test_load_merges_global_and_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    _write_json(
        tmp_path / "state" / "mcp.json",
        {
            "mcpServers": {
                "fs": {"command": "echo", "args": ["global"], "enabled": True},
                "docs": {"type": "http", "url": "https://x/mcp", "enabled": False},
            }
        },
    )
    ws = tmp_path / "ws"
    _write_json(
        ws / ".coworker" / "mcp.json",
        {
            "mcpServers": {
                "fs": {
                    "command": "echo",
                    "args": ["workspace-wins"],
                },  # overrides global
            }
        },
    )

    servers = {s.name: s for s in load_mcp_servers(ws, secrets=SecretStore())}
    assert servers["fs"].args == ["workspace-wins"]
    assert servers["fs"].transport == "stdio"
    assert servers["docs"].transport == "http" and servers["docs"].enabled is False
    assert servers["docs"].requires_approval is True  # default


def test_var_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DOCS_TOKEN", "sekret")
    _write_json(
        tmp_path / "state" / "mcp.json",
        {
            "mcpServers": {
                "docs": {
                    "type": "http",
                    "url": "https://x/mcp",
                    "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"},
                },
            }
        },
    )
    docs = load_mcp_servers(None, secrets=SecretStore())[0]
    assert docs.headers["Authorization"] == "Bearer sekret"


def test_builtin_kordoc_server_is_ready_only_and_pins_the_exact_mcp_surface(tmp_path):
    node = tmp_path / "node.exe"
    cli = tmp_path / "kordoc" / "dist" / "cli.js"
    mcp = tmp_path / "kordoc" / "dist" / "mcp.js"
    for path in (node, cli, mcp):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    runtime = KordocRuntime(
        node_executable=node.resolve(),
        npm_root=(tmp_path / "npm").resolve(),
        package_dir=(tmp_path / "kordoc").resolve(),
        cli_path=cli.resolve(),
        mcp_path=mcp.resolve(),
    )

    assert builtin_kordoc_server(KordocRuntimeStatus("not_installed")) is None
    server = builtin_kordoc_server(KordocRuntimeStatus("ready", runtime=runtime))

    assert server is not None
    assert server.command == str(node.resolve())
    assert server.args == [str(mcp.resolve())]
    assert server.requires_approval is True
    assert server.include_tools == [
        "detect_format",
        "parse_metadata",
        "parse_chunks",
        "parse_pages",
        "parse_table",
    ] == KORDOC_MCP_TOOL_ALLOWLIST


async def test_mcp_manager_reuses_same_definition_and_retires_replaced_connections(
    monkeypatch,
):
    mcp = MCPManager()
    starts: list[str] = []
    sessions = []

    class FakeSession:
        def __init__(self, label: str) -> None:
            self.label = label
            self.calls: list[tuple[str, dict]] = []

        async def call_tool(self, tool, arguments):
            self.calls.append((tool, arguments))
            return SimpleNamespace(
                content=[SimpleNamespace(text=self.label)],
                isError=False,
                structuredContent=None,
            )

    async def fake_serve(server, ready, *, interactive=False):
        del interactive
        starts.append(server.command)
        session = FakeSession(server.command)
        conn = SimpleNamespace(
            session=session,
            tools=[_fake_tool("read_document")],
            shutdown=asyncio.Event(),
        )
        sessions.append(conn)
        ready.set_result(conn)
        await conn.shutdown.wait()

    monkeypatch.setattr(mcp, "_serve", fake_serve)
    pinned = MCPServerDef(
        name="kordoc",
        transport="stdio",
        command="trusted-node",
        args=["trusted-mcp.js"],
    )
    same_definition = MCPServerDef(
        name="kordoc",
        transport="stdio",
        command="trusted-node",
        args=["trusted-mcp.js"],
    )

    first, second = await asyncio.gather(
        mcp.ensure(pinned), mcp.ensure(same_definition)
    )
    assert first is second
    assert starts == ["trusted-node"]
    assert await mcp.call_on_connection(first, "read_document", {"path": "one"}) == (
        "trusted-node"
    )
    assert await mcp.call_on_connection(second, "read_document", {"path": "two"}) == (
        "trusted-node"
    )

    replacement = MCPServerDef(
        name="kordoc",
        transport="stdio",
        command="replacement-node",
        args=["replacement-mcp.js"],
    )
    current = await mcp.ensure(replacement)
    assert current is not first
    assert first.shutdown.is_set()
    assert starts == ["trusted-node", "replacement-node"]
    with pytest.raises(RuntimeError, match="replaced"):
        await mcp.call_on_connection(first, "read_document", {})
    assert await mcp.call_on_connection(current, "read_document", {}) == "replacement-node"

    await mcp.aclose()
    assert current.shutdown.is_set()
    assert not mcp._conns and not mcp._tasks
    assert len(sessions) == 2


async def test_mcp_manager_cancelled_startup_does_not_orphan_server_task(monkeypatch):
    mcp = MCPManager()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def pending_serve(_server, _ready, *, interactive=False):
        del interactive
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(mcp, "_serve", pending_serve)
    server = MCPServerDef(name="slow", transport="stdio", command="slow-server")

    startup = asyncio.create_task(mcp.ensure(server))
    await started.wait()
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup

    await asyncio.wait_for(stopped.wait(), timeout=1)
    assert not mcp._tasks
    assert not mcp._conns


# -- tool wrapping + bridge ----------------------------------------------------
def test_tool_name_sanitizes():
    assert tool_name("fs", "read_file") == "mcp__fs__read_file"
    assert "." not in tool_name("a.b", "c.d")


def test_schema_and_metadata():
    server = MCPServerDef(name="fs", transport="stdio", requires_approval=True)
    fns = build_callables(
        server, [_fake_tool("read_file")], lambda t, a: None, asyncio.new_event_loop()
    )
    fn = fns[0]
    assert fn.__name__ == "mcp__fs__read_file"
    meta = fn.__aisuite_tool_metadata__
    assert meta.category == "mcp" and meta.requires_approval is True
    schema = fn.__coworker_schema__["function"]
    assert schema["name"] == "mcp__fs__read_file"
    assert schema["parameters"]["required"] == ["path"]


def test_include_exclude_filter():
    server = MCPServerDef(name="fs", transport="stdio", include_tools=["read_file"])
    fns = build_callables(
        server,
        [_fake_tool("read_file"), _fake_tool("delete_file")],
        lambda t, a: None,
        asyncio.new_event_loop(),
    )
    assert [f.__name__ for f in fns] == ["mcp__fs__read_file"]


async def test_bridge_invokes_session_on_loop():
    loop = asyncio.get_running_loop()
    seen = []

    async def call_async(tool, args):
        seen.append((tool, args))
        return {"echo": args}

    server = MCPServerDef(name="fs", transport="stdio")
    fn = build_callables(server, [_fake_tool("read_file")], call_async, loop)[0]
    # The engine runs tools via to_thread; the wrapper bridges back to this loop.
    result = await asyncio.to_thread(fn, path="a.txt")
    assert result == {"echo": {"path": "a.txt"}}
    assert seen == [("read_file", {"path": "a.txt"})]


# -- REST ----------------------------------------------------------------------
def test_rest_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    client = TestClient(create_app(manager))

    assert client.get("/v1/mcp").json()["servers"] == []

    r = client.post(
        "/v1/mcp",
        json={
            "name": "fs",
            "config": {"command": "echo", "args": ["x"], "env": {"SECRET": "shh"}},
        },
    )
    assert r.json()["ok"] is True

    servers = client.get("/v1/mcp").json()["servers"]
    assert servers[0]["name"] == "fs" and servers[0]["status"] == "configured"
    assert servers[0]["config"]["env"]["SECRET"] == "***"  # redacted

    assert client.patch("/v1/mcp/fs", json={"enabled": False}).json()["ok"] is True
    assert client.get("/v1/mcp").json()["servers"][0]["enabled"] is False

    assert client.delete("/v1/mcp/fs").json()["ok"] is True
    assert client.get("/v1/mcp").json()["servers"] == []
    assert client.delete("/v1/mcp/fs").json()["ok"] is False
