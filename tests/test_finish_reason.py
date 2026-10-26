"""OPE-173: the provider's stop reason is persisted on the assistant message.

The engine already receives `AssistantTurn.finish_reason` (`stop` / `tool_calls` /
`length`, normalised per provider) and uses it transiently (`_turn_truncated`). These
tests pin down that it is also (a) written to the saved message as a sidecar, (b) carried
on the ASSISTANT_MESSAGE event, (c) stripped before any provider call, and (d) that the
Anthropic provider keeps its raw `stop_reason` in the `_anthropic` sidecar so values the
normalisation collapses (`pause_turn`, `stop_sequence` → `stop`) are not lost.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import aisuite as ai
import pytest
from coworker.engine import TurnEngine, _assistant_message
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import (
    AnthropicProvider,
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.tools import ToolRegistry


# -- engine plumbing ----------------------------------------------------------------


class _OneTurnProvider(ProviderClient):
    def __init__(self, turn: AssistantTurn):
        self._turn = turn

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turn

    def capabilities(self, model):
        return ModelCapabilities()


def _run_engine(tmp_path, turn: AssistantTurn):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=_OneTurnProvider(turn),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    async def _collect():
        return [ev async for ev in engine.run("hello")]

    return engine, asyncio.run(_collect())


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_engine_persists_finish_reason_on_message_and_event(tmp_path, finish_reason):
    turn = AssistantTurn(text="partial answer", finish_reason=finish_reason)
    engine, events = _run_engine(tmp_path, turn)

    assistant = next(ev for ev in events if ev.type == EventType.ASSISTANT_MESSAGE)
    assert assistant.data["finish_reason"] == finish_reason

    persisted = next(m for m in engine.messages if m.get("role") == "assistant")
    assert persisted["finish_reason"] == finish_reason


def test_outbound_messages_strip_finish_reason_sidecar(tmp_path):
    engine, _ = _run_engine(tmp_path, AssistantTurn(text="done", finish_reason="stop"))
    assert any("finish_reason" in m for m in engine.messages)
    assert all("finish_reason" not in m for m in engine._outbound_messages())


def test_assistant_message_omits_finish_reason_when_unknown():
    """Partial turns (interrupt / provider error mid-stream) and providers that never
    report a stop reason must not write a `finish_reason: null` key."""
    message = _assistant_message(AssistantTurn(text="half a reply"))
    assert "finish_reason" not in message


def test_assistant_message_keeps_finish_reason_with_tool_calls():
    turn = AssistantTurn(
        tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "x"})],
        finish_reason="tool_calls",
    )
    message = _assistant_message(turn, model="m")
    assert message["finish_reason"] == "tool_calls"
    assert message["tool_calls"][0]["function"]["name"] == "read_file"


# -- Anthropic raw stop reason ------------------------------------------------------


class _FakeClient:
    def __init__(self, response=None, events=None):
        self.kwargs: dict = {}

        def create(**kwargs):
            self.kwargs = kwargs
            if kwargs.get("stream"):
                return iter(events or [])
            return response

        @contextmanager
        def stream(**kwargs):
            self.kwargs = kwargs
            yield SimpleNamespace(get_final_message=lambda: response)

        self.messages = SimpleNamespace(create=create, stream=stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create, stream=stream))


def _text_response(text="hello", stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )


@pytest.mark.parametrize(
    "stop_reason,expected",
    [
        ("end_turn", "stop"),
        ("max_tokens", "length"),
        ("pause_turn", "stop"),  # collapsed by the map — only the sidecar keeps it
        ("stop_sequence", "stop"),
    ],
)
def test_anthropic_complete_keeps_raw_stop_reason_in_sidecar(stop_reason, expected):
    provider = AnthropicProvider(
        client=_FakeClient(response=_text_response(stop_reason=stop_reason))
    )
    turn = provider.complete(model="m", messages=[{"role": "user", "content": "x"}])
    assert turn.finish_reason == expected
    assert turn.extras["_anthropic"]["stop_reason"] == stop_reason


def test_anthropic_stream_keeps_raw_stop_reason_in_sidecar():
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="cut off mid"),
        ),
        SimpleNamespace(
            type="message_delta", delta=SimpleNamespace(stop_reason="max_tokens")
        ),
    ]
    provider = AnthropicProvider(client=_FakeClient(events=events))
    chunks = list(
        provider.stream(model="m", messages=[{"role": "user", "content": "x"}])
    )
    turn = chunks[-1].turn
    assert turn.finish_reason == "length"
    assert turn.extras["_anthropic"]["stop_reason"] == "max_tokens"


def test_anthropic_sidecar_with_stop_reason_replays_cleanly():
    """A persisted assistant message carrying `_anthropic.stop_reason` but no thinking
    blocks must convert back to the wire shape without error or extra blocks."""
    fake = _FakeClient(response=_text_response())
    provider = AnthropicProvider(client=fake)
    history = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": "earlier reply",
            "_anthropic": {"stop_reason": "end_turn"},
            "finish_reason": "stop",  # would be stripped by the engine; belt and braces
        },
        {"role": "user", "content": "y"},
    ]
    provider.complete(model="m", messages=history)
    sent = fake.kwargs["messages"]
    assistant = next(m for m in sent if m["role"] == "assistant")
    assert assistant["content"] == [{"type": "text", "text": "earlier reply"}]
    assert "finish_reason" not in assistant
    assert "_anthropic" not in assistant
