"""Long MCP names stay distinct and remain callable by their full names (#746)."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

from coworker.mcp.config import MCPServerDef
from coworker.mcp.tools import build_callables, tool_name
from coworker.permissions import PermissionEngine
from coworker.tools.registry import ToolRegistry

SERVER = "hub-gmail-thresholdstack"
TOOLS = [
    "gmail-thresholdstack__get_gmail_message_content",
    "gmail-thresholdstack__get_gmail_messages_content_batch",
]


def _tool(name):
    return SimpleNamespace(
        name=name,
        description="Read a message",
        inputSchema={
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    )


def test_long_names_are_distinct_stable_and_provider_valid():
    names = [tool_name(SERVER, name) for name in TOOLS]
    assert len(set(names)) == 2
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{64}", name) for name in names)
    assert names == [tool_name(SERVER, name) for name in TOOLS]
    # Names at the limit do not change, preserving existing grants.
    full = "mcp__fs__" + "x" * 55
    assert tool_name("fs", "x" * 55) == full


@pytest.mark.parametrize("use_full_name", [False, True])
async def test_long_names_dispatch_to_the_correct_remote_tool(use_full_name):
    async def call_async(remote, args):
        return {"remote": remote, "message_id": args["message_id"]}

    server = MCPServerDef(name=SERVER, transport="http", url="https://example.com/mcp")
    registry = ToolRegistry()
    registry.register_all(
        build_callables(
            server,
            [_tool(name) for name in TOOLS],
            call_async,
            asyncio.get_running_loop(),
        )
    )
    assert len(registry.names()) == len(registry.schemas()) == 2
    for index, remote in enumerate(TOOLS):
        name = f"mcp__{SERVER}__{remote}" if use_full_name else registry.names()[index]
        result = await asyncio.to_thread(registry.execute, name, {"message_id": "123"})
        assert result == {"remote": remote, "message_id": "123"}
        spec = registry.get(name)
        assert spec is not None
        assert spec.schema["function"]["name"] == spec.name
        assert len(spec.name) <= 64


@pytest.mark.parametrize("selection", ["include", "exclude"])
async def test_full_alias_keeps_approval_metadata_and_tool_selection(tmp_path, selection):
    server = MCPServerDef(
        name=SERVER,
        transport="http",
        url="https://example.com/mcp",
        include_tools=[TOOLS[0]] if selection == "include" else None,
        exclude_tools=[TOOLS[1]] if selection == "exclude" else None,
    )
    registry = ToolRegistry()
    registry.register_all(
        build_callables(
            server,
            [_tool(name) for name in TOOLS],
            lambda t, a: None,
            asyncio.get_running_loop(),
        )
    )
    full = f"mcp__{SERVER}__{TOOLS[0]}"
    spec = registry.get(full)
    assert spec is not None
    assert spec is registry.get(spec.name)
    assert spec.func.__coworker_mcp_destination__ == {
        "transport": "http",
        "host": "example.com",
    }
    decision = PermissionEngine(workspace_root=tmp_path).evaluate(
        full, {}, spec.metadata
    )
    assert not decision.allowed and decision.needs_user
    excluded = f"mcp__{SERVER}__{TOOLS[1]}"
    assert registry.get(excluded) is None
    with pytest.raises(KeyError):
        registry.execute(excluded)


async def test_reregistering_refreshes_full_alias():
    server = MCPServerDef(name=SERVER, transport="stdio")
    registry = ToolRegistry()
    for description in ("old", "new"):
        tool = _tool(TOOLS[0])
        tool.description = description
        registry.register_all(
            build_callables(
                server,
                [tool],
                lambda t, a: None,
                asyncio.get_running_loop(),
            )
        )
    spec = registry.get(f"mcp__{SERVER}__{TOOLS[0]}")
    assert spec is not None
    assert spec.schema["function"]["description"] == "new"
    assert len(registry.schemas()) == 1


@pytest.mark.parametrize("alias_first", [False, True])
def test_alias_cannot_shadow_a_different_registered_tool(alias_first):
    def original():
        return "original"

    def other():
        return "other"

    other.__coworker_aliases__ = ("original",)
    registry = ToolRegistry()
    first, second = (other, original) if alias_first else (original, other)
    registry.register(first)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(second)
    assert registry.execute("original") == ("other" if alias_first else "original")


@pytest.mark.parametrize("approve", [False, True])
async def test_engine_full_name_still_requires_user_approval(tmp_path, approve):
    from coworker.engine import ApprovalOutcome, TurnEngine
    from coworker.events import EventType
    from coworker.providers import (
        AssistantTurn,
        ModelCapabilities,
        ProviderClient,
        ToolCall,
    )

    full = f"mcp__{SERVER}__{TOOLS[0]}"

    class Provider(ProviderClient):
        def __init__(self):
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            if self.calls == 1:
                return AssistantTurn(
                    tool_calls=[
                        ToolCall(
                            id="long_name",
                            name=full,
                            arguments={"message_id": "123"},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            return AssistantTurn(text="done")

        def capabilities(self, model):
            return ModelCapabilities(tools=True)

    invoked = []

    async def call_async(remote, args):
        invoked.append(remote)
        return {"remote": remote}

    requests = []

    async def approver(request):
        requests.append(request)
        return ApprovalOutcome.ONCE if approve else ApprovalOutcome.DENY

    registry = ToolRegistry()
    registry.register_all(
        build_callables(
            MCPServerDef(name=SERVER, transport="http", url="https://example.com/mcp"),
            [_tool(TOOLS[0])],
            call_async,
            asyncio.get_running_loop(),
        )
    )
    engine = TurnEngine(
        provider=Provider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="test",
        approver=approver,
    )
    events = [event async for event in engine.run("Read a message")]
    assert len(requests) == 1
    assert requests[0].metadata.category == "mcp"
    assert requests[0].mcp_destination == {"transport": "http", "host": "example.com"}
    assert invoked == ([TOOLS[0]] if approve else [])
    finished = next(event for event in events if event.type == EventType.TOOL_FINISHED)
    assert finished.data["status"] == ("ok" if approve else "denied")


def test_replacing_a_tool_removes_obsolete_aliases():
    def current():
        return "current"

    current.__coworker_aliases__ = ("old_alias",)
    registry = ToolRegistry()
    registry.register(current)
    current.__coworker_aliases__ = ("new_alias",)
    registry.register(current)
    assert registry.get("old_alias") is None
    assert registry.execute("new_alias") == "current"
    current.__coworker_aliases__ = ()
    registry.register(current)
    assert registry.get("new_alias") is None
    assert registry.execute("current") == "current"


def test_conflicting_reregistration_preserves_existing_tools_and_aliases():
    def first():
        return "first"

    def second():
        return "second"

    first.__coworker_aliases__ = ("first_alias",)
    second.__coworker_aliases__ = ("second_alias",)
    registry = ToolRegistry()
    registry.register_all([first, second])
    first.__coworker_aliases__ = ("second_alias",)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(first)
    assert registry.execute("first_alias") == "first"
    assert registry.execute("second_alias") == "second"
    assert registry.execute("first") == "first"
    assert registry.execute("second") == "second"
