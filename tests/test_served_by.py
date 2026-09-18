"""OpenAI-compatible routers (OpenRouter) name the upstream host that served a reply in a
top-level `provider` field. Keep it on the turn and persist it as the `served_by` sidecar,
so a run pinned to one host can prove the pin held on every reply."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aisuite as ai
from coworker.engine import TurnEngine, _assistant_message
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, OpenAIProvider, ProviderClient
from coworker.tools import ToolRegistry


class _FakeOpenAI:
    def __init__(self, *, response=None, stream_chunks=None):
        self.calls: list[dict] = []

        def create(**kwargs):
            self.calls.append(kwargs)
            return stream_chunks if kwargs.get("stream") else response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_openai_complete_keeps_the_serving_host():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")],
        usage=None,
        provider="Together",
    )
    turn = OpenAIProvider(client=_FakeOpenAI(response=response)).complete(
        model="moonshotai/kimi-k3", messages=[{"role": "user", "content": "x"}]
    )
    assert turn.served_by == "Together"


def test_openai_stream_keeps_the_serving_host_from_any_chunk():
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hi", tool_calls=None), finish_reason=None)], usage=None),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="!", tool_calls=None), finish_reason="stop")], usage=None, provider="Together"),
    ]
    chunks_out = list(
        OpenAIProvider(client=_FakeOpenAI(stream_chunks=chunks)).stream(
            model="moonshotai/kimi-k3", messages=[{"role": "user", "content": "x"}]
        )
    )
    assert chunks_out[-1].turn.served_by == "Together"


def test_plain_endpoints_leave_it_unset():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")],
        usage=None,
    )
    turn = OpenAIProvider(client=_FakeOpenAI(response=response)).complete(
        model="m", messages=[{"role": "user", "content": "x"}]
    )
    assert turn.served_by is None
    assert "served_by" not in _assistant_message(turn)


class _ServedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop", served_by="Together")

    def capabilities(self, model):
        return ModelCapabilities()


def test_engine_persists_served_by_and_strips_it_outbound(tmp_path):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=_ServedProvider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="openrouter:moonshotai/kimi-k3",
    )

    async def _collect():
        return [ev async for ev in engine.run("hello")]

    events = asyncio.run(_collect())
    assistant = next(ev for ev in events if ev.type == EventType.ASSISTANT_MESSAGE)
    assert assistant.data["served_by"] == "Together"
    persisted = next(m for m in engine.messages if m.get("role") == "assistant")
    assert persisted["served_by"] == "Together"
    assert all("served_by" not in m for m in engine._outbound_messages())
