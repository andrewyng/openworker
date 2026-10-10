"""OPE-136 §3 — the existence lever: `include_tools` decides which of a server's tools
are REGISTERED at all. An unchecked tool is not blocked — it is absent: the model never
sees its name or schema, so there is nothing to invoke, trick, or approve. And because
include_tools is an include-list, tools a server ships later are excluded until the
user opts them in (fail-closed growth).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from coworker.mcp.config import MCPServerDef
from coworker.mcp.tools import build_callables
from coworker.tools.registry import ToolRegistry


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"vendor description of {name}",
        inputSchema={"type": "object", "properties": {}},
    )


def _build(server: MCPServerDef, tool_names: list[str]):
    loop = asyncio.new_event_loop()
    try:
        return build_callables(
            server, [_tool(n) for n in tool_names], lambda t, a: None, loop
        )
    finally:
        loop.close()


OFFERED = ["getIssue", "createIssue", "deleteIssue"]


def test_unchecked_tools_are_absent_from_the_registry():
    server = MCPServerDef(
        name="jirax",
        transport="http",
        url="https://mcp.example.com/v1/mcp",
        include_tools=["getIssue", "createIssue"],  # deleteIssue unchecked
    )
    registry = ToolRegistry()
    registry.register_all(_build(server, OFFERED))
    assert registry.names() == ["mcp__jirax__getIssue", "mcp__jirax__createIssue"]
    # Absent means absent: no schema for the model, nothing to execute.
    assert registry.get("mcp__jirax__deleteIssue") is None


def test_no_include_list_means_everything_registers():
    server = MCPServerDef(
        name="jirax", transport="http", url="https://mcp.example.com/v1/mcp"
    )
    assert len(_build(server, OFFERED)) == 3


def test_fail_closed_growth_a_new_server_tool_stays_out():
    # The user reviewed and saved [getIssue, createIssue]; a later handshake ships a
    # brand-new adminPurge. It must not register until the user opts in.
    server = MCPServerDef(
        name="jirax",
        transport="http",
        url="https://mcp.example.com/v1/mcp",
        include_tools=["getIssue", "createIssue"],
    )
    grown = OFFERED + ["adminPurge"]
    names = [fn.__name__ for fn in _build(server, grown)]
    assert "mcp__jirax__adminPurge" not in names
    assert names == ["mcp__jirax__getIssue", "mcp__jirax__createIssue"]


def test_destination_stamp_travels_with_every_callable():
    # OPE-136 finding 4 plumbing: the card's scope chip reads this — from the user's
    # own server config, never the server's claims.
    http_server = MCPServerDef(
        name="jirax", transport="http", url="https://MCP.Example.com/v1/mcp"
    )
    (fn, *_) = _build(http_server, ["getIssue"])
    assert fn.__coworker_mcp_destination__ == {
        "transport": "http",
        "host": "mcp.example.com",  # lowercased
    }
    stdio_server = MCPServerDef(name="localfs", transport="stdio", command="npx")
    (fn2,) = _build(stdio_server, ["read"])
    assert fn2.__coworker_mcp_destination__ == {"transport": "stdio", "host": ""}
