"""Ollama chat calls request a context window the Cowork prompt actually fits in."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from coworker.compaction import is_context_overflow
from coworker.engine import TurnEngine
from coworker.providers import ollama_context
from coworker.providers.ollama_context import (
    DEFAULT_OLLAMA_NUM_CTX,
    OllamaContextTransport,
    context_length_from_show,
    context_window_for,
    to_native_chat,
)
from coworker.providers.openai_provider import OpenAIProvider

GEMMA_SHOW = {
    "parameters": "temperature                    1\ntop_p                          0.95",
    "model_info": {"general.architecture": "gemma4", "gemma4.context_length": 262144},
}


@pytest.fixture(autouse=True)
def _fresh_resolution(monkeypatch):
    monkeypatch.delenv("OPENWORKER_OLLAMA_NUM_CTX", raising=False)
    monkeypatch.setattr(ollama_context, "_resolved", {})


def test_native_chat_sets_num_ctx_and_refuses_to_shift():
    native = to_native_chat(
        {
            "model": "gemma4:e4b",
            "messages": [
                {"role": "system", "content": "You are Cowork."},
                {
                    "role": "user",
                    "content": "Analyze the files in this folder and summarize what matters.",
                },
            ],
            "tools": [{"type": "function", "function": {"name": "list_files"}}],
            "max_tokens": 32000,
            "stream": True,
        },
        num_ctx=32768,
    )
    assert native["options"]["num_ctx"] == 32768
    assert native["options"]["num_predict"] == 32000
    assert native["shift"] is False
    assert native["stream"] is True
    assert native["tools"][0]["function"]["name"] == "list_files"
    assert native["messages"][0]["content"] == "You are Cowork."


def test_tool_history_round_trips_with_a_name():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "reasoning": "listing the folder",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": '{"path": "."}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "README.md"},
    ]
    native = to_native_chat({"model": "m", "messages": messages}, num_ctx=32768)
    assistant, tool = native["messages"]
    assert assistant["thinking"] == "listing the folder"
    assert assistant["tool_calls"][0]["function"]["arguments"] == {"path": "."}
    assert tool["tool_name"] == "list_files"
    assert tool["content"] == "README.md"


class _Inner(httpx.BaseTransport):
    """Fake Ollama: one canned `/api/chat` response, optional `/api/show` per path."""

    def __init__(
        self, response: httpx.Response, show: httpx.Response | None = None
    ) -> None:
        self.response = response
        self.show = show
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/api/show"):
            if self.show is None:
                raise httpx.ConnectError("no show endpoint", request=request)
            return self.show
        return self.response

    def chat_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/api/chat")]


def _chat_request(model: str = "gemma4:e4b", base: str = "http://127.0.0.1:11434/v1"):
    return httpx.Request(
        "POST",
        f"{base}/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )


def _done(model: str = "gemma4:e4b") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "message": {"role": "assistant", "content": "ok"},
            "done": True,
            "done_reason": "stop",
        },
    )


def test_context_length_prefers_modelfile_num_ctx_then_trained_length():
    assert context_length_from_show(GEMMA_SHOW) == 262144
    pinned = {**GEMMA_SHOW, "parameters": "num_ctx                        16384\ntop_p 0.9"}
    assert context_length_from_show(pinned) == 16384
    # Unknown architecture key still finds the one *.context_length entry.
    assert context_length_from_show({"model_info": {"qwen3.context_length": 40960}}) == 40960
    assert context_length_from_show({"model_info": {"general.architecture": "x"}}) is None
    assert context_length_from_show({}) is None


def test_transport_reads_the_trained_window_from_api_show_once_per_model():
    inner = _Inner(_done(), show=httpx.Response(200, json=GEMMA_SHOW))
    transport = OllamaContextTransport(inner=inner)
    transport.handle_request(_chat_request())
    transport.handle_request(_chat_request())
    shows = [r for r in inner.requests if r.url.path == "/api/show"]
    assert len(shows) == 1
    assert json.loads(shows[0].content) == {"model": "gemma4:e4b"}
    for chat in inner.chat_requests():
        assert json.loads(chat.content)["options"]["num_ctx"] == 262144
    # The engine sizes compaction against the window actually in use.
    assert context_window_for("ollama:gemma4:e4b") == 262144


def test_env_override_caps_the_trained_window(monkeypatch):
    monkeypatch.setenv("OPENWORKER_OLLAMA_NUM_CTX", "32768")
    inner = _Inner(_done(), show=httpx.Response(200, json=GEMMA_SHOW))
    OllamaContextTransport(inner=inner).handle_request(_chat_request())
    assert json.loads(inner.chat_requests()[0].content)["options"]["num_ctx"] == 32768
    assert context_window_for("ollama:gemma4:e4b") == 32768
    # A cap above the trained window changes nothing.
    monkeypatch.setenv("OPENWORKER_OLLAMA_NUM_CTX", "1000000")
    small = {"model_info": {"general.architecture": "llama", "llama.context_length": 8192}}
    inner = _Inner(_done(), show=httpx.Response(200, json=small))
    OllamaContextTransport(inner=inner).handle_request(_chat_request(model="llama3.2"))
    assert json.loads(inner.chat_requests()[0].content)["options"]["num_ctx"] == 8192


def test_unreachable_api_show_falls_back_to_the_default_and_retries_later():
    inner = _Inner(_done(), show=None)
    transport = OllamaContextTransport(inner=inner)
    transport.handle_request(_chat_request())
    assert json.loads(inner.chat_requests()[0].content)["options"]["num_ctx"] == (
        DEFAULT_OLLAMA_NUM_CTX
    )
    # Now the server answers; the cached failure must not stick.
    inner.show = httpx.Response(200, json=GEMMA_SHOW)
    transport.handle_request(_chat_request())
    assert json.loads(inner.chat_requests()[1].content)["options"]["num_ctx"] == 262144


def test_native_endpoints_keep_a_reverse_proxy_prefix():
    inner = _Inner(_done(), show=httpx.Response(200, json=GEMMA_SHOW))
    OllamaContextTransport(inner=inner).handle_request(
        _chat_request(base="http://gateway.local/ollama/v1")
    )
    paths = [r.url.path for r in inner.requests]
    assert paths == ["/ollama/api/show", "/ollama/api/chat"]


def test_transport_rewrites_chat_onto_native_api_and_back():
    native = {
        "model": "gemma4:e4b",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_abc",
                    "function": {"name": "list_files", "arguments": {"path": "."}},
                }
            ],
        },
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 7123,
        "eval_count": 40,
    }
    inner = _Inner(httpx.Response(200, json=native))
    transport = OllamaContextTransport(num_ctx=DEFAULT_OLLAMA_NUM_CTX, inner=inner)
    request = httpx.Request(
        "POST",
        "http://127.0.0.1:11434/v1/chat/completions",
        json={
            "model": "gemma4:e4b",
            "messages": [{"role": "user", "content": "list the files"}],
            "max_tokens": 32,
        },
    )
    response = transport.handle_request(request)
    sent = json.loads(inner.requests[0].content)
    assert inner.requests[0].url.path == "/api/chat"
    assert sent["options"]["num_ctx"] == DEFAULT_OLLAMA_NUM_CTX
    assert sent["shift"] is False
    parsed = response.json()
    assert parsed["usage"]["prompt_tokens"] == 7123
    assert parsed["choices"][0]["finish_reason"] == "tool_calls"
    call = parsed["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "list_files"
    assert json.loads(call["function"]["arguments"]) == {"path": "."}


def test_provider_stream_sees_the_full_prompt_usage():
    lines = [
        json.dumps(
            {
                "model": "gemma4:e4b",
                "message": {"role": "assistant", "content": "README.md matters."},
                "done": False,
            }
        ),
        json.dumps(
            {
                "model": "gemma4:e4b",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 6900,
                "eval_count": 12,
            }
        ),
    ]
    inner = _Inner(
        httpx.Response(200, stream=httpx.ByteStream("\n".join(lines).encode()))
    )
    transport = OllamaContextTransport(num_ctx=32768, inner=inner)
    provider = OpenAIProvider(
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        http_client=httpx.Client(transport=transport),
    )
    turn = None
    text = []
    for chunk in provider.stream(
        model="gemma4:e4b",
        messages=[{"role": "user", "content": "summarize the folder"}],
    ):
        if chunk.text_delta:
            text.append(chunk.text_delta)
        if chunk.turn is not None:
            turn = chunk.turn
    assert "".join(text) == "README.md matters."
    assert turn is not None
    assert turn.usage is not None
    assert turn.usage.input == 6900
    sent = json.loads(inner.requests[0].content)
    assert sent["options"]["num_ctx"] == 32768


def test_registry_builds_ollama_with_the_context_transport():
    from coworker.providers.registry import build_provider_client

    provider = build_provider_client("ollama", {"base_url": "http://127.0.0.1:11434"}, None)
    client = provider._ensure_client()
    transport = client._client._transport
    assert isinstance(transport, OllamaContextTransport)
    assert transport.num_ctx is None  # resolved per model from /api/show


def test_ollama_compaction_window_matches_the_requested_context():
    engine = SimpleNamespace(
        model="ollama:gemma4:e4b",
        compaction_settings=None,
        _warned_context_fallback=False,
    )
    # Before the first chat call resolves the model: the default, no 128k-guess warning.
    cfg = TurnEngine._compaction_config(engine)
    assert cfg["context_window"] == DEFAULT_OLLAMA_NUM_CTX
    assert engine._warned_context_fallback is False
    assert context_window_for("gpt-5.5") is None
    # After: exactly the window the transport sent.
    inner = _Inner(_done(), show=httpx.Response(200, json=GEMMA_SHOW))
    OllamaContextTransport(inner=inner).handle_request(_chat_request())
    assert TurnEngine._compaction_config(engine)["context_window"] == 262144


def test_ollama_context_overflow_is_recognized():
    exc = RuntimeError(
        "the prompt is longer than the context length currently available to the model"
    )
    assert is_context_overflow(exc)
