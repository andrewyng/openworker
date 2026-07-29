"""Provider-boundary coverage for bounded model context.

The durable transcript may keep every record, but a provider call must stay inside the
request limits used by the hosted Companion gateway.
"""

from __future__ import annotations

import asyncio
import json

from coworker.context import ContextBudget
from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers import (
    AnthropicProvider,
    AssistantTurn,
    GeminiProvider,
    ModelCapabilities,
    OpenAIProvider,
    ProviderClient,
    ToolCall,
)
from coworker.tools import ToolRegistry


class RecordingProvider(ProviderClient):
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append(messages)
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


class ReplayRecordingProvider(RecordingProvider):
    def __init__(self, native_provider: ProviderClient) -> None:
        super().__init__()
        self.native_provider = native_provider

    def replay_sidecar_keys(self, model):
        return self.native_provider.replay_sidecar_keys(model)


def _run(engine: TurnEngine, prompt: str) -> None:
    async def collect() -> None:
        async for _ in engine.run(prompt):
            pass

    asyncio.run(collect())


def test_turn_engine_never_sends_more_than_gateway_message_budget(tmp_path):
    provider = RecordingProvider()
    durable = [{"role": "system", "content": "system"}]
    durable.extend(
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"message-{index}",
        }
        for index in range(260)
    )
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=durable,
        context_budget=ContextBudget.gateway(),
    )

    _run(engine, "LATEST_USER_SENTINEL")

    sent = provider.requests[0]
    assert len(sent) <= 220
    assert sent[0] == {"role": "system", "content": "system"}
    assert sent[-1]["content"] == "LATEST_USER_SENTINEL"
    assert len(engine.messages) == len(durable) + 2


def _request_bytes(messages: list[dict]) -> int:
    body = {
        "model": "gpt-5.5",
        "messages": messages,
        "tools": None,
    }
    return len(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def test_turn_engine_bounds_request_bytes_without_orphaning_tool_results(tmp_path):
    provider = RecordingProvider()
    durable = [{"role": "system", "content": "system"}]
    for index in range(4):
        call_id = f"call-{index}"
        durable.extend(
            [
                {"role": "user", "content": f"look up record {index}"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "crm_search",
                                "arguments": json.dumps({"index": index}),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": "x" * 625_000,
                },
                {"role": "assistant", "content": f"record {index} processed"},
            ]
        )
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=durable,
        context_budget=ContextBudget.gateway(),
    )

    _run(engine, "LATEST_USER_SENTINEL")

    sent = provider.requests[0]
    assert _request_bytes(sent) <= 1_572_864
    retained_call_ids = {
        call["id"] for message in sent for call in message.get("tool_calls", [])
    }
    assert {
        message["tool_call_id"] for message in sent if message.get("role") == "tool"
    } <= retained_call_ids
    assert sent[-1]["content"] == "LATEST_USER_SENTINEL"
    assert len(engine.messages) == len(durable) + 2


def test_model_switch_ignores_foreign_provider_replay_sidecars(tmp_path):
    provider = RecordingProvider()
    foreign_thinking = {
        "blocks": [
            {
                "type": "thinking",
                "thinking": "x" * (1_600 * 1_024),
                "signature": "foreign-signature",
            }
        ]
    }
    durable = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old question"},
        {
            "role": "assistant",
            "content": "useful old answer",
            "_anthropic": foreign_thinking,
        },
    ]
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=durable,
    )

    _run(engine, "question after switching providers")

    sent = provider.requests[0]
    assert any(message.get("content") == "useful old answer" for message in sent)
    assert all("_anthropic" not in message for message in sent)
    assert engine.messages[2]["_anthropic"] == foreign_thinking


def test_native_providers_declare_only_their_signed_replay_sidecars():
    assert AnthropicProvider().replay_sidecar_keys("claude-fable-5") == frozenset(
        {"_anthropic"}
    )
    assert GeminiProvider().replay_sidecar_keys("gemini-3.6-flash") == frozenset(
        {"_gemini"}
    )
    assert OpenAIProvider().replay_sidecar_keys(
        "deepseek-v4-flash"
    ) == frozenset({"_openai"})
    assert OpenAIProvider().replay_sidecar_keys("deepseek-v4-pro") == frozenset(
        {"_openai"}
    )
    assert OpenAIProvider().replay_sidecar_keys("gpt-5.6-sol") == frozenset()


def test_active_signed_replay_sidecar_survives_projection_exactly(tmp_path):
    cases = [
        (
            AnthropicProvider(),
            "claude-fable-5",
            "_anthropic",
            {
                "blocks": [
                    {
                        "type": "thinking",
                        "thinking": "signed plan",
                        "signature": "anthropic-signature",
                    }
                ]
            },
        ),
        (
            GeminiProvider(),
            "gemini-3.6-flash",
            "_gemini",
            {"text_sig": "dGV4dA==", "call_sigs": ["Y2FsbA=="]},
        ),
        (
            OpenAIProvider(),
            "deepseek-v4-flash",
            "_openai",
            {"reasoning_content": "signed DeepSeek reasoning"},
        ),
    ]

    for native_provider, model, sidecar_key, signed_value in cases:
        provider = ReplayRecordingProvider(native_provider)
        durable = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old question"},
            {
                "role": "assistant",
                "content": "calling",
                sidecar_key: signed_value,
                "_foreign": {"payload": "must not leak"},
                "tool_calls": [
                    {
                        "id": "signed-call",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "signed-call", "content": "result"},
        ]
        engine = TurnEngine(
            provider=provider,
            registry=ToolRegistry(),
            permissions=PermissionEngine(workspace_root=tmp_path),
            model=model,
            messages=durable,
        )

        _run(engine, "next question")

        signed_message = next(
            message for message in provider.requests[0] if message.get("tool_calls")
        )
        assert signed_message[sidecar_key] == signed_value
        assert "_foreign" not in signed_message
        assert engine.messages[2][sidecar_key] == signed_value
        assert engine.messages[2]["_foreign"] == {"payload": "must not leak"}


class ToolThenDoneProvider(ProviderClient):
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append(messages)
        if len(self.requests) == 1:
            return AssistantTurn(
                tool_calls=[
                    ToolCall(id="large-call", name="large_lookup", arguments={})
                ],
                finish_reason="tool_calls",
            )
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def test_oversized_tool_result_is_recoverable_but_not_replayed_in_full(tmp_path):
    from coworker.tool_outputs import SessionToolOutputStore

    provider = ToolThenDoneProvider()
    registry = ToolRegistry()

    def large_lookup() -> str:
        return "HEAD" + ("z" * 20_000) + "TAIL"

    registry.register(large_lookup)
    store = SessionToolOutputStore(tmp_path / "state", "session-1")
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        tool_output_store=store,
    )

    _run(engine, "find it")

    result = next(
        message for message in engine.messages if message.get("role") == "tool"
    )
    envelope = json.loads(result["content"])
    assert envelope["truncated"] is True
    assert envelope["original_chars"] == 20_008
    assert envelope["output_ref"] in store.list_references()
    assert "HEAD" in envelope["preview"]
    assert "TAIL" in envelope["preview"]
    assert len(result["content"]) <= 8_000
    assert all("z" * 20_000 not in json.dumps(request) for request in provider.requests)
