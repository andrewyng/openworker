"""Focused coverage for the LangGraph/OpenWork runtime boundary."""

from __future__ import annotations

import asyncio

import aisuite as ai

from coworker.engine import TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.runtime import EffectLedger, GraphRuntime, RuntimeStore
from coworker.runtime.artifacts import ArtifactOffloader, MAX_INLINE_BYTES
from coworker.runtime.tools import OpenWorkToolAdapter
from coworker.runtime.model import OpenWorkChatModel
from coworker.runtime.deepagents import DeepAgentHarness
from coworker.tools import ToolRegistry


class OneTurnProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def test_graph_runtime_streams_engine_and_persists_checkpoint(tmp_path):
    engine = TurnEngine(
        provider=OneTurnProvider(), registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path), model="test",
    )
    runtime = GraphRuntime(
        session_id="graph-test", engine=engine, store=RuntimeStore(tmp_path / "runtime.db")
    )

    async def collect():
        return [event async for event in runtime.run("hello")]

    events = asyncio.run(collect())
    assert any(event.type is EventType.ASSISTANT_MESSAGE for event in events)
    assert any(event.type is EventType.RUN_STATE for event in events)
    assert runtime.state().status == "completed"
    assert (tmp_path / "langgraph-checkpoints.db").exists()


def test_effect_ledger_reuses_completed_consequential_call(tmp_path):
    calls = {"count": 0}

    def write_file(path: str) -> dict:
        calls["count"] += 1
        return {"path": path, "count": calls["count"]}

    registry = ToolRegistry()
    registry.register(
        write_file,
        metadata=ai.ToolMetadata(category="filesystem", risk_level="medium", requires_approval=True),
        schema={"type": "function", "function": {"name": "write_file", "parameters": {"type": "object"}}},
    )
    engine = TurnEngine(
        provider=OneTurnProvider(), registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path), model="test",
    )
    engine.effect_ledger = EffectLedger(tmp_path / "runtime.db")
    engine.execution_context = {"session_id": "s", "run_id": "r"}
    call = ToolCall(id="call-1", name="write_file", arguments={"path": "x.txt"})

    first, first_status = engine._execute_sync(call)
    second, second_status = engine._execute_sync(call)
    assert (first_status, second_status) == ("ok", "ok")
    assert first == second
    assert calls["count"] == 1


def test_artifact_offloader_replaces_large_result_with_reference(tmp_path):
    offloader = ArtifactOffloader(tmp_path)
    result, record = offloader.prepare({"body": "x" * (MAX_INLINE_BYTES + 1)}, tool_call_id="call-1")
    assert record is not None
    assert result["offloaded"] is True
    assert result["bytes"] > MAX_INLINE_BYTES
    assert (tmp_path / ".openwork-artifacts").is_dir()


def test_langchain_tool_adapter_uses_guarded_executor():
    registry = ToolRegistry()

    def inspect_target(path: str) -> dict:
        return {"unsafe": True, "path": path}

    registry.register(
        inspect_target,
        schema={"type": "function", "function": {"name": "inspect_target", "description": "inspect", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    )
    calls = []
    tool = OpenWorkToolAdapter(registry, lambda name, args: calls.append((name, args)) or {"ok": True}).as_langchain()[0]
    assert tool.invoke({"path": "safe.txt"}) == {"ok": True}
    assert calls == [("inspect_target", {"path": "safe.txt"})]


def test_openwork_chat_model_uses_provider_router_contract():
    model = OpenWorkChatModel(provider=OneTurnProvider(), model="test")
    assert model.invoke("hello").content == "done"


def test_deep_agent_harness_is_available_without_filesystem_backend():
    assert DeepAgentHarness.available() is True


def test_new_agentic_session_uses_sticky_graph_runtime(tmp_path):
    from coworker.server.manager import SessionManager

    manager = SessionManager(workspace=tmp_path, provider=OneTurnProvider())
    engine = manager.get_engine("runtime-session", agent="cowork", workspace=str(tmp_path))
    runtime = manager.get_runtime("runtime-session", engine)
    assert runtime.kind.value == "graph"

    async def drive():
        return [event async for event in runtime.run("hello")]

    asyncio.run(drive())
    manager.save("runtime-session", engine)
    assert manager.list_sessions()[0]["runtime"] == "graph"
    assert manager.runtime_state("runtime-session")["run"]["status"] == "completed"


def test_run_state_and_delegation_routes_are_exposed(tmp_path):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    manager = SessionManager(workspace=tmp_path, provider=OneTurnProvider())
    engine = manager.get_engine("runtime-api", agent="cowork", workspace=str(tmp_path))
    runtime = manager.get_runtime("runtime-api", engine)

    async def drive():
        return [event async for event in runtime.run("hello")]

    asyncio.run(drive())
    client = TestClient(create_app(manager))
    response = client.get("/v1/sessions/runtime-api/run-state")
    assert response.status_code == 200
    assert response.json()["run"]["runtime"] == "graph"
    assert client.post(
        "/v1/sessions/runtime-api/delegations/missing/cancel"
    ).json()["ok"] is False
