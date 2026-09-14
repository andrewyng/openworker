"""Existing sessions must see connection changes without reconnecting their socket.

Only external account validation/model output is stubbed. The HTTP routes, connection
stores, WebSocket turn loop and tool schemas supplied to the provider are real.
"""

import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from coworker.connectors.setup import managed_connect_connector
from coworker.engine import ApprovalOutcome
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.server import SessionManager, create_app


class RecordingProvider(ProviderClient):
    def __init__(self):
        self.tool_names = []
        self.next_call: ToolCall | None = None

    def complete(self, *, model, messages, tools=None, **settings):
        if tools is None:  # Background title generation is not an agent turn.
            return AssistantTurn(text="Connection refresh test", finish_reason="stop")
        names = {tool["function"]["name"] for tool in tools or []}
        self.tool_names.append(names)
        if self.next_call is not None:
            call, self.next_call = self.next_call, None
            return AssistantTurn(tool_calls=[call])
        return AssistantTurn(text="Tool schema recorded.", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def send_turn(ws, text):
    ws.send_json({"type": "user_message", "text": text})
    while True:
        event = ws.receive_json()
        assert event["type"] not in {"error", "input_rejected"}, event
        if event["type"] == "turn_done":
            return


@pytest.mark.parametrize("managed", [False, True], ids=["manual", "managed-oauth"])
def test_connect_existing_websocket(tmp_path, monkeypatch, managed):
    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/refresh?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"
        send_turn(ws, "Before connecting")
        assert "outlook_search_messages" not in provider.tool_names[-1]
        engine = manager.get_engine("refresh")
        assert engine is not None
        permissions = engine.permissions
        original_messages = list(engine.messages)

        if managed:
            from coworker import cloud

            monkeypatch.setattr(cloud, "consume_managed_state", lambda state: True)
            response = client.post(
                "/oauth/callback",
                data={
                    "connector": "outlook",
                    "app_state": "synthetic-test-state",
                    "access_token": "synthetic-test-token",
                    "refresh_token": "synthetic-refresh-token",
                    "email": "test@example.invalid",
                },
            )
            assert response.status_code == 200
        else:
            from coworker.connectors.descriptors import get_descriptor, ValidationResult

            monkeypatch.setattr(
                get_descriptor("outlook"),
                "validate",
                lambda fields: ValidationResult(
                    ok=True, identity="test@example.invalid"
                ),
            )
            response = client.post(
                "/v1/connectors/outlook/connect",
                json={"fields": {"access_token": "synthetic-test-token"}},
            )
            assert response.json()["ok"], response.json()

        assert "outlook" in manager.effective_connectors("refresh", "cowork")
        fresh = manager.get_engine("fresh-control", agent="cowork")
        assert fresh is not None
        assert "outlook_search_messages" in fresh.registry.names()
        send_turn(ws, "After connecting, in the same chat")
        assert "outlook_search_messages" in provider.tool_names[-1]
        assert manager.get_engine("refresh") is engine
        assert engine.permissions is permissions
        assert engine.messages[: len(original_messages)] == original_messages


def connect_outlook(manager):
    result = managed_connect_connector(
        manager.secrets,
        "outlook",
        {"type": "oauth", "enabled": True, "access_token": "synthetic-test-token"},
    )
    assert result["ok"]


@pytest.mark.parametrize("change", ["disconnect", "tool", "session", "persona"])
def test_remove_and_restore_access_in_existing_chat(tmp_path, change):
    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    connect_outlook(manager)
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/access?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"
        send_turn(ws, "Connected")
        assert "outlook_search_messages" in provider.tool_names[-1]
        engine = manager.get_engine("access")
        assert engine is not None
        file_tool = engine.registry.get("read_file")
        todo = getattr(engine, "todo")
        if change == "disconnect":
            assert manager.disconnect_connector("outlook")["ok"]
        elif change == "tool":
            assert manager.update_connector_tools(
                "outlook", {"outlook_search_messages": False}
            )["ok"]
        elif change == "session":
            manager.session_connections.set("access", "outlook", False)
        else:
            assert manager.set_persona_connection("cowork", "outlook", False)["ok"]
        send_turn(ws, "Access removed")
        assert "outlook_search_messages" not in provider.tool_names[-1]
        if change == "disconnect":
            connect_outlook(manager)
        elif change == "tool":
            manager.update_connector_tools("outlook", {"outlook_search_messages": True})
        elif change == "session":
            manager.session_connections.clear("access", "outlook")
        else:
            manager.set_persona_connection("cowork", "outlook", True)
        send_turn(ws, "Access restored")
        assert "outlook_search_messages" in provider.tool_names[-1]
        assert manager.get_engine("access") is engine
        assert engine.registry.get("read_file") is file_tool
        assert getattr(engine, "todo") is todo


@pytest.mark.asyncio
async def test_connection_during_pending_question_waits_for_next_turn(tmp_path):
    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    asked = asyncio.Event()
    answer = asyncio.Event()

    async def ask(args, tool_call_id=None):
        asked.set()
        await answer.wait()
        return {"answer": "Continue"}

    engine = manager.get_engine("pending", agent="cowork", question_asker=ask)
    assert engine is not None
    provider.next_call = ToolCall(
        id="question-1", name="ask_user", arguments={"question": "Continue?"}
    )

    async def run(text):
        return [event async for event in engine.run(text)]

    task = asyncio.create_task(run("Ask before continuing"))
    try:
        await asyncio.wait_for(asked.wait(), timeout=5)
        connect_outlook(manager)
        assert not task.done()
        assert manager.get_engine("pending") is engine
        assert "outlook_search_messages" not in engine.registry.names()
        answer.set()
        events = await asyncio.wait_for(task, timeout=5)
        assert not [event for event in events if event.type.value == "error"]
        # The remaining model/tool loop belongs to the original turn.
        assert "outlook_search_messages" not in provider.tool_names[-1]
        await run("Now use the new connection")
        assert "outlook_search_messages" in provider.tool_names[-1]
    finally:
        answer.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_background_turn_refreshes_connected_tools(tmp_path):
    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    engine = manager.get_engine("background", agent="cowork")
    await manager.deliver_to_session("background", "Before connection")
    assert "outlook_search_messages" not in provider.tool_names[-1]
    connect_outlook(manager)
    await manager.deliver_to_session("background", "After connection")
    assert "outlook_search_messages" in provider.tool_names[-1]
    assert manager.get_engine("background") is engine


@pytest.mark.asyncio
async def test_custom_mcp_refresh_and_execution_in_existing_session(tmp_path):
    # A real local stdio MCP server: no remote service or credentials.
    server = tmp_path / "mcp_fixture.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        'mcp = FastMCP("refresh-fixture")\n'
        "@mcp.tool()\n"
        "def echo(value: str) -> str:\n"
        '    return "fixture:" + value\n'
        'mcp.run(transport="stdio")\n'
    )
    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    approved = []

    async def approve(request):
        approved.append(request.tool_name)
        return ApprovalOutcome.ONCE

    engine = manager.get_engine("mcp-refresh", agent="cowork", approver=approve)
    assert engine is not None

    async def run(text):
        events = [event async for event in engine.run(text)]
        assert not [e for e in events if e.type.value == "error"]

    name = "mcp__refreshfixture__echo"
    try:
        await run("Before adding a server")
        assert name not in provider.tool_names[-1]
        assert manager.add_mcp(
            "refreshfixture",
            {
                "command": sys.executable,
                "args": [str(server)],
                "enabled": True,
                "include_tools": ["echo"],
            },
        )["ok"]
        provider.next_call = ToolCall(
            id="mcp-1", name=name, arguments={"value": "hello"}
        )
        await asyncio.wait_for(run("Use the newly added tool"), timeout=20)
        assert name in provider.tool_names[-1]
        assert approved == [name]
        results = [m for m in engine.messages if m.get("tool_call_id") == "mcp-1"]
        assert len(results) == 1 and "fixture:hello" in str(results[0]["content"])
        conn = manager.mcp._conns["refreshfixture"]
        assert manager.patch_mcp("refreshfixture", {"include_tools": []})["ok"]
        await run("Tool unchecked")
        assert name not in provider.tool_names[-1]
        manager.patch_mcp("refreshfixture", {"include_tools": ["echo"]})
        await run("Tool restored")
        assert name in provider.tool_names[-1]
        assert manager.mcp._conns["refreshfixture"] is conn
        manager.patch_mcp("refreshfixture", {"enabled": False})
        await run("Server disabled")
        assert name not in provider.tool_names[-1]
        manager.patch_mcp("refreshfixture", {"enabled": True})
        await run("Server re-enabled")
        assert name in provider.tool_names[-1]
        manager.delete_mcp("refreshfixture")
        await run("Server removed")
        assert name not in provider.tool_names[-1]
        assert manager.get_engine("mcp-refresh") is engine
    finally:
        await manager.mcp.aclose()


@pytest.mark.asyncio
async def test_preexisting_session_restriction_survives_connection(tmp_path):
    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    engine = manager.get_engine("restricted", agent="cowork")
    assert engine is not None
    manager.session_connections.set("restricted", "outlook", False)
    connect_outlook(manager)
    _ = [event async for event in engine.run("Still restricted")]
    assert "outlook_search_messages" not in provider.tool_names[-1]
    manager.session_connections.clear("restricted", "outlook")
    _ = [event async for event in engine.run("Restriction cleared")]
    assert "outlook_search_messages" in provider.tool_names[-1]


@pytest.mark.asyncio
async def test_injected_non_mcp_tool_survives_refresh(tmp_path):
    def fixture_tool() -> str:
        """A caller-owned tool, unrelated to connection discovery."""
        return "fixture"

    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    engine = manager.get_engine("injected", agent="cowork", extra_tools=[fixture_tool])
    assert engine is not None
    _ = [event async for event in engine.run("Keep caller tools")]
    assert "fixture_tool" in provider.tool_names[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["retry", "resume"])
async def test_retry_and_durable_resume_refresh_tools(tmp_path, boundary):
    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    engine = manager.get_engine("resume", agent="cowork")
    assert engine is not None
    _ = [event async for event in engine.run("Original request")]
    connect_outlook(manager)
    if boundary == "retry":
        engine._append_notice("error", "Synthetic provider failure")
        events = [event async for event in engine.retry()]
    else:
        # A harmless pending local read with a stable call id, as persisted at a crash.
        engine.messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "pending-read",
                        "type": "function",
                        "function": {
                            "name": "list_directory",
                            "arguments": '{"path":"."}',
                        },
                    }
                ],
            }
        )
        events = [event async for event in engine.resume()]
        assert (
            len([m for m in engine.messages if m.get("tool_call_id") == "pending-read"])
            == 1
        )
    assert not [e for e in events if e.type.value == "error"]
    assert "outlook_search_messages" in provider.tool_names[-1]


def test_registry_group_replacement_is_atomic():
    from coworker.tools.registry import ToolRegistry

    def stable() -> str:
        """A stateful tool outside the connection group."""
        return "stable"

    def old() -> str:
        """The old connection tool."""
        return "old"

    def new() -> str:
        """The replacement connection tool."""
        return "new"

    registry = ToolRegistry()
    registry.register_all([stable, old])
    stable_spec = registry.get("stable")
    with pytest.raises(ValueError):
        registry.replace({"old"}, [new, object()])
    assert registry.names() == ["stable", "old"]
    registry.replace({"old"}, [new])
    assert registry.names() == ["stable", "new"]
    assert registry.get("stable") is stable_spec


@pytest.mark.asyncio
async def test_refresh_failure_does_not_use_stale_tools(tmp_path):
    provider = RecordingProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    engine = manager.get_engine("failed-refresh", agent="cowork")
    assert engine is not None

    async def fail():
        raise ValueError("synthetic refresh failure")

    engine.prepare_turn = fail
    events = [event async for event in engine.run("Do not run stale tools")]
    assert provider.tool_names == []
    assert any(e.type.value == "error" and "refresh" in e.data["error"] for e in events)
