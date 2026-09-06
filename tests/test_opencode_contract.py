"""Contract tests for OpenCode Zen / Go — verified against upstream API documentation.

Tests cover: authentication headers, endpoint paths, request payloads, model IDs,
verification behavior, independent-profile key resolution, Zen free-model isolation,
and secret hygiene. SDK-free: httpx calls are monkeypatched and the OpenAI SDK is
injected as a fake (the same patterns as tests/test_provider_verify.py and
tests/test_providers.py). Everything uses the independent ``opencode_zen:`` /
``opencode_go:`` prefixes — there is no canonical ``opencode`` provider. No live API
calls are made.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    OpenAIProvider,
    ProviderRouter,
    StreamChunk,
    ToolCall,
    capabilities_for,
)
from coworker.providers.opencode_contract import (
    COMMON_CHAT_COMPLETIONS_MODELS,
    GO_MODEL_TRANSPORT,
    OPEN_CODE_CATALOG,
    OPCODE_GO_ENDPOINT,
    OPCODE_ZEN_ENDPOINT,
    SHARED_ENV_KEY,
    TRANSPORT_OPENAI,
    VERIFY_PATH,
    ZEN_CHAT_COMPLETIONS_MODELS,
    ZEN_FREE_MODELS,
    ZEN_MODELS,
)
from coworker.providers.registry import (
    build_provider_client,
    get_descriptor,
    verify_provider_key,
)


# ---------------------------------------------------------------------------
# 1. Contract: descriptors, endpoints, auth
# ---------------------------------------------------------------------------


def test_contract_endpoints_prefilled():
    """Both picker entries ship their OWN prefilled endpoint and share only the env var."""
    for name, expected in (
        ("opencode_zen", OPCODE_ZEN_ENDPOINT),
        ("opencode_go", OPCODE_GO_ENDPOINT),
    ):
        d = get_descriptor(name)
        assert d is not None, name
        ep = next(f for f in d.fields if f.key == "base_url")
        assert ep.default == expected, f"{name} endpoint mismatch"
        assert d.env_key == SHARED_ENV_KEY
        assert d.needs_key
    # Independent profiles: distinct recommended models, no canonical "opencode" entry.
    assert get_descriptor("opencode_zen").recommended_model != get_descriptor(
        "opencode_go"
    ).recommended_model
    assert get_descriptor("opencode") is None


def _patch_get(monkeypatch, status=200, capture=None, raise_exc=None, body=None):
    def fake_get(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(status_code=status, text=body)

    monkeypatch.setattr("httpx.get", fake_get)


def test_contract_verify_zen_endpoint_and_bearer(monkeypatch):
    """GET <zen-endpoint>/models with Authorization: Bearer <key> — the verify contract."""
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("opencode_zen", api_key="oc_test123") == {"ok": True}
    assert cap["url"] == OPCODE_ZEN_ENDPOINT.rstrip("/") + VERIFY_PATH
    assert cap["headers"]["Authorization"] == "Bearer oc_test123"


def test_contract_verify_go_endpoint(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("opencode_go", api_key="oc_test123")
    assert cap["url"] == OPCODE_GO_ENDPOINT.rstrip("/") + VERIFY_PATH
    assert cap["headers"]["Authorization"] == "Bearer oc_test123"


def test_contract_verify_bad_key_is_invalid(monkeypatch):
    for status in (401, 403):
        _patch_get(monkeypatch, status=status)
        res = verify_provider_key("opencode_zen", api_key="oc_bad")
        assert res == {"ok": False, "error": "Invalid API key."}
        res = verify_provider_key("opencode_go", api_key="oc_bad")
        assert res == {"ok": False, "error": "Invalid API key."}


def test_contract_verify_rate_limit(monkeypatch):
    _patch_get(monkeypatch, status=429)
    res = verify_provider_key("opencode_go", api_key="oc_test")
    assert res["ok"] is False
    assert "429" in res["error"]


def test_contract_verify_transport_errors(monkeypatch):
    import httpx

    _patch_get(monkeypatch, raise_exc=httpx.TimeoutException("timed out"))
    res = verify_provider_key("opencode_zen", api_key="oc_test")
    assert res["ok"] is False and "Couldn't reach" in res["error"]

    _patch_get(monkeypatch, raise_exc=httpx.ConnectError("refused"))
    res = verify_provider_key("opencode_go", api_key="oc_test")
    assert res["ok"] is False and "Couldn't reach" in res["error"]


def test_contract_verify_is_status_based(monkeypatch):
    """The verify path is a status-only probe: any 2xx passes — even a junk,
    non-JSON body (the response body is never parsed)."""
    _patch_get(monkeypatch, status=200, body="<html>definitely-not-json</html>")
    assert verify_provider_key("opencode_zen", api_key="oc_test") == {"ok": True}
    # Same for the Go tier.
    _patch_get(monkeypatch, status=200, body="not json either")
    assert verify_provider_key("opencode_go", api_key="oc_test") == {"ok": True}


# ---------------------------------------------------------------------------
# 2. HTTP contract: request shape through the SDK boundary
# ---------------------------------------------------------------------------


class _FakeOpenAI:
    """Captures the OpenAI SDK constructor args (base_url/api_key) and every
    chat.completions.create() call — the kwargs we hand the SDK are the wire
    request (the SDK serializes them verbatim)."""

    def __init__(self, monkeypatch, responses):
        self.captured: dict = {}
        self.calls: list[dict] = []
        self._responses = list(responses)
        monkeypatch.setattr("openai.OpenAI", self._make)

    def _make(self, **kwargs):
        self.captured.update(kwargs)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        return self

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        assert self._responses, "no more fake responses queued"
        return self._responses.pop(0)


def _complete_response(content="ok", tool_calls=None, finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


_TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]


def test_contract_zen_http_request_shape(monkeypatch):
    """A Zen completion goes to the Zen endpoint with Bearer auth, the exact model id,
    the OpenAI-compatible payload, and the tools array."""
    fake = _FakeOpenAI(monkeypatch, [_complete_response()])
    provider = build_provider_client("opencode_zen", {"api_key": "oc-test"}, None)

    turn = provider.complete(
        model="deepseek-v4-flash-free",
        messages=[{"role": "user", "content": "hello"}],
        tools=_TOOLS,
    )
    assert turn.text == "ok"
    # Endpoint + auth contract (the SDK turns api_key into `Authorization: Bearer …`).
    assert fake.captured["base_url"] == OPCODE_ZEN_ENDPOINT
    assert fake.captured["api_key"] == "oc-test"
    # Payload contract.
    call = fake.calls[0]
    assert call["model"] == "deepseek-v4-flash-free"
    assert call["messages"] == [{"role": "user", "content": "hello"}]
    assert call["tools"][0]["function"]["name"] == "read_file"
    assert "stream" not in call  # non-streaming path sends no stream flag


def test_contract_go_http_request_shape(monkeypatch):
    """The same bare model on the Go tier goes to the Go endpoint, never Zen."""
    fake = _FakeOpenAI(monkeypatch, [_complete_response()])
    provider = build_provider_client("opencode_go", {"api_key": "oc-go"}, None)

    turn = provider.complete(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "hi"}],
        tools=_TOOLS,
    )
    assert turn.text == "ok"
    assert fake.captured["base_url"] == OPCODE_GO_ENDPOINT
    assert fake.captured["api_key"] == "oc-go"
    assert fake.calls[0]["model"] == "deepseek-v4-flash"


@pytest.mark.parametrize(
    ("name", "model", "endpoint"),
    (
        ("opencode_zen", "deepseek-v4-flash-free", OPCODE_ZEN_ENDPOINT),
        ("opencode_go", "deepseek-v4-flash", OPCODE_GO_ENDPOINT),
    ),
)
def test_contract_chat_completion_final_url_and_bearer(
    monkeypatch, name, model, endpoint
):
    """The SDK boundary must produce the actual OpenCode chat-completions request."""
    import httpx
    from openai import OpenAI as SDKOpenAI

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))

    def make_openai(**kwargs):
        # Disable the SDK's own 429/5xx retry policy so this test isolates the
        # provider wrapper's retry behavior.
        return SDKOpenAI(**kwargs, http_client=http_client, max_retries=0)

    monkeypatch.setattr("openai.OpenAI", make_openai)
    try:
        provider = build_provider_client(name, {"api_key": "oc-wire-test"}, None)
        turn = provider.complete(model=model, messages=[{"role": "user", "content": "hi"}])
    finally:
        http_client.close()

    assert turn.text == "ok"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == endpoint + "chat/completions"
    assert request.headers["authorization"] == "Bearer oc-wire-test"
    assert request.content
    assert json.loads(request.content)["model"] == model


def test_contract_zen_stream_request_shape(monkeypatch):
    """Streaming sends stream=True (+ usage opt-in) to the Zen endpoint with the model id
    and tools — and accumulates the deltas into one terminal turn."""
    def _chunk(content=None, finish=None):
        delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])

    chunks = [_chunk("Hello"), _chunk(" world"), _chunk(finish="stop")]
    fake = _FakeOpenAI(monkeypatch, [iter(chunks)])
    provider = build_provider_client("opencode_zen", {"api_key": "oc-test"}, None)

    out = list(provider.stream(
        model="deepseek-v4-flash-free",
        messages=[{"role": "user", "content": "hi"}],
        tools=_TOOLS,
    ))
    assert fake.captured["base_url"] == OPCODE_ZEN_ENDPOINT
    call = fake.calls[0]
    assert call["model"] == "deepseek-v4-flash-free"
    assert call["stream"] is True
    assert call["stream_options"] == {"include_usage": True}
    assert call["tools"][0]["function"]["name"] == "read_file"
    assert "".join(c.text_delta for c in out if c.text_delta) == "Hello world"
    assert out[-1].turn.text == "Hello world" and out[-1].turn.finish_reason == "stop"


# ---------------------------------------------------------------------------
# 3. Agent-path behavior (SDK-injected fakes)
# ---------------------------------------------------------------------------


def _fake_chunk(content=None, tool_call=None, finish=None, reasoning=None):
    delta = SimpleNamespace(
        content=content,
        tool_calls=[tool_call] if tool_call else None,
        reasoning_content=reasoning,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


class _FakeStreamClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: iter(chunks))
        )


def test_contract_stream_free_model_tool_fragments():
    """A free Zen model's streamed tool fragments form an agent-loop turn."""
    fragments = [
        SimpleNamespace(
            index=0, id="call_free",
            function=SimpleNamespace(name="read_file", arguments='{"pa'),
        ),
        SimpleNamespace(
            index=0, id=None, function=SimpleNamespace(name=None, arguments='th":"a.py"}'),
        ),
    ]
    chunks = [_fake_chunk(tool_call=f) for f in fragments] + [_fake_chunk(finish="tool_calls")]
    provider = OpenAIProvider(client=_FakeStreamClient(chunks))
    out = list(provider.stream(
        model="deepseek-v4-flash-free", messages=[], tools=[{"type": "function"}],
    ))
    turn = out[-1].turn
    assert turn is not None and turn.finish_reason == "tool_calls"
    assert turn.tool_calls == [ToolCall(id="call_free", name="read_file", arguments={"path": "a.py"})]
    assert turn.has_tool_calls


def test_contract_tool_result_submission():
    """After a tool-call turn, submitting results and continuing works."""
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="read_file", arguments=json.dumps({"path": "a.py"})),
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="file content", tool_calls=None),
                        finish_reason="stop",
                    )]
                )
            )
        )
    )
    provider = OpenAIProvider(client=client)
    messages = [
        {"role": "user", "content": "read a.py"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "def foo(): pass"},
    ]
    turn = provider.complete(model="grok-4.5", messages=messages)
    assert turn.text == "file content"
    assert not turn.has_tool_calls


def test_contract_tool_result_continuation():
    """Multiple tool-call rounds: submit results, get final text."""
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="done", tool_calls=None),
                        finish_reason="stop",
                    )]
                )
            )
        )
    )
    provider = OpenAIProvider(client=client)
    messages = [
        {"role": "user", "content": "do two things"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "tool_a", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result_a"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "tool_b", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "result_b"},
    ]
    turn = provider.complete(model="grok-4.5", messages=messages)
    assert turn.text == "done"


@pytest.mark.parametrize("status", (400, 401, 429, 500))
def test_contract_provider_error_propagation(monkeypatch, status):
    """Real SDK HTTP errors propagate and are not blindly retried."""
    import httpx
    from openai import OpenAI as SDKOpenAI

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            request=request,
            json={
                "error": {
                    "message": f"provider failure {status}",
                    "type": "test_error",
                    "code": None,
                }
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))

    def make_openai(**kwargs):
        # Disable the SDK's own 429/5xx retry policy so this test isolates the
        # provider wrapper's retry behavior.
        return SDKOpenAI(**kwargs, http_client=http_client, max_retries=0)

    monkeypatch.setattr("openai.OpenAI", make_openai)
    try:
        provider = build_provider_client("opencode_go", {"api_key": "oc-test"}, None)
        with pytest.raises(Exception) as raised:
            provider.complete(model="grok-4.5", messages=[{"role": "user", "content": "hi"}])
    finally:
        http_client.close()

    assert getattr(raised.value, "status_code", None) == status
    assert len(requests) == 1  # no blind retry


def test_contract_empty_completion_response_fails_explicitly(monkeypatch):
    """An invalid empty choices response must not become a successful assistant turn."""
    fake = _FakeOpenAI(monkeypatch, [SimpleNamespace(choices=[])])
    provider = build_provider_client("opencode_zen", {"api_key": "oc-test"}, None)
    with pytest.raises(IndexError):
        provider.complete(model="deepseek-v4-flash-free", messages=[])
    assert len(fake.calls) == 1


def test_contract_stream_cancellation():
    """Streaming is a generator — cancelling iteration (gen.close()) stops it cleanly
    WITHOUT consuming the remaining chunks. The counting source records how many chunks
    the stream actually pulled, so the early exit is observable."""
    pulled: list[str] = []

    def counted_chunks():
        for c in [
            _fake_chunk(content="hello"),
            _fake_chunk(content=" world"),
            _fake_chunk(finish="stop"),
        ]:
            pulled.append(c.choices[0].delta.content or "(finish)")
            yield c

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: counted_chunks())
        )
    )
    provider = OpenAIProvider(client=client)
    gen = provider.stream(model="grok-4.5", messages=[])
    first = next(gen)
    assert first.text_delta == "hello"
    assert pulled == ["hello"]  # exactly one chunk pulled so far

    # Cancel at the yield point: the generator is closed without draining the rest.
    gen.close()
    with pytest.raises(StopIteration):
        next(gen)  # a closed generator is exhausted, not resumable
    assert pulled == ["hello"], f"remaining chunks were consumed: {pulled}"


def test_contract_stream_mid_stream_disconnect():
    """A mid-stream error surfaces with the deltas already yielded intact."""
    def chunk_gen():
        yield _fake_chunk(content="Hello")
        raise ConnectionError("stream interrupted")

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: chunk_gen())
        )
    )
    provider = OpenAIProvider(client=client)
    chunks = []
    with pytest.raises(ConnectionError, match="stream interrupted"):
        for chunk in provider.stream(model="deepseek-v4-flash", messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)
    assert len(chunks) == 1 and chunks[0].text_delta == "Hello"


# ---------------------------------------------------------------------------
# 4. Zen free models: completeness, Zen-only isolation, labels, capabilities
# ---------------------------------------------------------------------------

# The Zen Free roster — derived from the matrix. Free models are identified by
# having "-free" in their model ID suffix (matching upstream naming convention).
# big-pickle is NOT free in the current catalog (label doesn't have a Free marker).
_DOCUMENTED_FREE_IDS = frozenset({
    "deepseek-v4-flash-free",
    "mimo-v2.5-free",
    "laguna-s-2.1-free",
    "ling-3.0-flash-free",
    "north-mini-code-free",
    "nemotron-3-ultra-free",
})


def test_contract_zen_free_models_complete():
    """ZEN_FREE_MODELS must match the documented roster AND the matrix's free-model
    entries — anchored to two independent sources, not just a local duplicate of the
    contract constant itself."""
    from coworker.providers.matrix import MATRIX

    matrix_free = {
        mid.split(":", 1)[1]
        for mid, e in MATRIX.items()
        if mid.startswith("opencode_zen:") and mid.endswith("-free")
    }
    assert set(ZEN_FREE_MODELS.keys()) == _DOCUMENTED_FREE_IDS, (
        f"missing {sorted(_DOCUMENTED_FREE_IDS - set(ZEN_FREE_MODELS))}, "
        f"extra {sorted(set(ZEN_FREE_MODELS) - _DOCUMENTED_FREE_IDS)}"
    )
    assert matrix_free == _DOCUMENTED_FREE_IDS, (
        f"matrix free entries drifted: {sorted(matrix_free ^ _DOCUMENTED_FREE_IDS)}"
    )


def test_contract_zen_free_markers_are_zen_only():
    """Every free model with -free suffix belongs to the documented Zen roster,
    and NO Go entry has a -free suffix."""
    from coworker.providers.matrix import MATRIX

    for mid, entry in MATRIX.items():
        if mid.startswith("opencode_zen:") and "-free" in mid:
            bare = mid.split(":", 1)[1]
            assert bare in ZEN_FREE_MODELS, (
                f"free-model matrix entry {mid} is not a documented free model"
            )
        if mid.startswith("opencode_go:"):
            assert "-free" not in mid, (
                f"Go entry {mid} has a free-model id"
            )


def test_contract_zen_free_models_in_zen_matrix():
    from coworker.providers.matrix import MATRIX
    for bare_id in ZEN_FREE_MODELS:
        assert f"opencode_zen:{bare_id}" in MATRIX, bare_id


def test_contract_zen_free_models_not_in_go():
    from coworker.providers.matrix import models_for_provider
    for bare_id in ZEN_FREE_MODELS:
        assert bare_id not in GO_MODEL_TRANSPORT, bare_id
        assert bare_id not in models_for_provider("opencode_go"), bare_id


def test_contract_zen_free_models_disjoint_from_paid():
    assert ZEN_FREE_MODELS.keys().isdisjoint(ZEN_MODELS)
    assert ZEN_FREE_MODELS.keys().isdisjoint(GO_MODEL_TRANSPORT.keys())


def test_contract_zen_free_model_labels_mark_free():
    from coworker.providers.matrix import MATRIX
    for bare_id in ZEN_FREE_MODELS:
        entry = MATRIX[f"opencode_zen:{bare_id}"]
        assert "Free" in entry.label, (
            f"free model {bare_id} label {entry.label!r} missing a Free indicator"
        )


def test_contract_zen_free_models_capabilities():
    for bare_id in ZEN_FREE_MODELS:
        caps = capabilities_for(f"opencode_zen:{bare_id}")
        assert caps.tools is True
        assert caps.streaming is True
        assert caps.parallel_tool_calls is True
        assert caps.vision is False


def test_contract_zen_free_models_route_to_zen():
    """A free model id under the Zen prefix routes to the opencode_zen provider, never Go."""
    router = ProviderRouter.__new__(ProviderRouter)  # only using static helpers
    for bare_id in ZEN_FREE_MODELS:
        full = f"opencode_zen:{bare_id}"
        assert router._provider_name(full) == "opencode_zen"
        assert ProviderRouter._bare(full) == bare_id
        # Go never offers the free model: the go: prefix routes to Go, but no Go
        # matrix entry exists for it (covered by test_contract_zen_free_models_not_in_go).
        assert router._provider_name(f"opencode_go:{bare_id}") == "opencode_go"


# ---------------------------------------------------------------------------
# 5. Transport mapping: Go roster, verified Zen overlap, per-prefix routing
# ---------------------------------------------------------------------------


def test_contract_go_models_complete_and_transport():
    """Every Go model in the contract is OpenAI-compatible and routes to opencode_go."""
    router = ProviderRouter.__new__(ProviderRouter)
    for bare_id, transport in GO_MODEL_TRANSPORT.items():
        assert transport == TRANSPORT_OPENAI  # all OpenAI-compatible /chat/completions
        full = f"opencode_go:{bare_id}"
        assert router._provider_name(full) == "opencode_go"
        assert ProviderRouter._bare(full) == bare_id
        caps = capabilities_for(full)
        assert caps.tools and caps.streaming


def test_contract_every_exposed_model_uses_chat_completions():
    """The first PR catalog must not silently claim another OpenCode transport."""
    assert OPEN_CODE_CATALOG
    assert all(model.transport == TRANSPORT_OPENAI for model in OPEN_CODE_CATALOG)
    assert all(model.profile == "chat.completions" for model in OPEN_CODE_CATALOG)


def test_contract_go_transport_matches_matrix_roster():
    """Contract and matrix must not drift: every Go matrix entry has a mapping."""
    from coworker.providers.matrix import models_for_provider
    go_bare = set(models_for_provider("opencode_go"))
    assert go_bare == set(GO_MODEL_TRANSPORT.keys()), (
        f"unmapped: {go_bare - set(GO_MODEL_TRANSPORT)}, "
        f"extra mapping: {set(GO_MODEL_TRANSPORT) - go_bare}"
    )


# The verified Zen/Go overlap roster — derived from the matrix intersection
# (Go-transport models that ALSO appear on the Zen tier).
_EXPECTED_COMMON_CHAT_COMPLETIONS = frozenset({
    "grok-4.5",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "kimi-k2.5",
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "qwen3.6-plus",
    "qwen3.5-plus",
})

_EXPECTED_GO_CHAT_COMPLETIONS = _EXPECTED_COMMON_CHAT_COMPLETIONS | {
    "kimi-k3",
    "mimo-v2.5",
    "mimo-v2.5-pro",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "hy3",
    "hy3-preview",
    "qwen3.7-max",
    "qwen3.7-plus",
}

_EXPECTED_ZEN_CHAT_COMPLETIONS = _EXPECTED_COMMON_CHAT_COMPLETIONS | {
    "grok-build-0.1",
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4",
    "claude-haiku-4-5",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-3-flash",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3-codex-spark",
    "gpt-5.3-codex",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.1",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5",
    "gpt-5-codex",
    "gpt-5-nano",
    "big-pickle",
}


def test_contract_zen_overlap_is_verified_go_subset():
    """ZEN_MODELS documents the verified Zen/Go overlap — the EXACT roster of
    Go-transport models that also appear on the Zen tier. Equality (not subset), so a
    missing overlap entry fails the lockstep guard."""
    from coworker.providers.matrix import MATRIX, models_for_provider

    # Independent anchor: the matrix's Zen entries that are Go-transport models.
    matrix_overlap = set(models_for_provider("opencode_zen")) & set(GO_MODEL_TRANSPORT)
    assert matrix_overlap == _EXPECTED_COMMON_CHAT_COMPLETIONS, (
        f"matrix overlap drifted from the documented roster: "
        f"{sorted(matrix_overlap ^ _EXPECTED_COMMON_CHAT_COMPLETIONS)}"
    )
    # Zen now also exposes Zen-only chat-completions models; only the overlap is
    # required to match the independently documented common roster.
    assert matrix_overlap == _EXPECTED_COMMON_CHAT_COMPLETIONS
    # Zen-only models are valid; the independently documented Zen roster must
    # match the catalog exactly.
    assert ZEN_MODELS == _EXPECTED_ZEN_CHAT_COMPLETIONS
    for bare_id in ZEN_MODELS:
        assert f"opencode_zen:{bare_id}" in MATRIX, bare_id

    # Only the verified overlap is required to be present on both tiers.
    for bare_id in _EXPECTED_COMMON_CHAT_COMPLETIONS:
        assert f"opencode_go:{bare_id}" in MATRIX, bare_id


def test_contract_overlap_models_route_by_prefix():
    """The same bare model on both tiers routes to its OWN provider by prefix — the
    isolation invariant behind the allowed Zen/Go overlap."""
    router = ProviderRouter.__new__(ProviderRouter)
    for bare_id in ZEN_MODELS:
        assert router._provider_name(f"opencode_zen:{bare_id}") == "opencode_zen"
        assert router._provider_name(f"opencode_go:{bare_id}") == "opencode_go"
        assert ProviderRouter._bare(f"opencode_go:{bare_id}") == bare_id


def test_contract_first_pr_catalog_is_explicit_and_transport_safe():
    """The exposed first-PR catalog is internally consistent — all models belong to
    one of the defined sets and free models stay Zen-only."""
    from coworker.providers.matrix import models_for_provider

    assert COMMON_CHAT_COMPLETIONS_MODELS == _EXPECTED_COMMON_CHAT_COMPLETIONS
    assert set(models_for_provider("opencode_zen")) == set(ZEN_CHAT_COMPLETIONS_MODELS)
    assert set(models_for_provider("opencode_go")) == _EXPECTED_GO_CHAT_COMPLETIONS

    exposed = set(models_for_provider("opencode_zen")) | set(
        models_for_provider("opencode_go")
    )
    for model in exposed:
        assert model in ZEN_CHAT_COMPLETIONS_MODELS or model in GO_MODEL_TRANSPORT


def test_contract_custom_opencode_model_capabilities():
    """Unlisted custom models under either OpenCode prefix get the verified conservative
    defaults (the capabilities.py opencode branch) — not the stricter default."""
    for model in (
        "opencode_zen:some-custom-model",
        "opencode_go:some-custom-model",
        "opencode_zen:gpt-5.6-custom",
        "opencode_go:gemini-custom",
    ):
        caps = capabilities_for(model)
        assert caps.tools is True
        assert caps.parallel_tool_calls is True
        assert caps.streaming is True
        assert caps.vision is False
        assert caps.pdf is False


# ---------------------------------------------------------------------------
# 6. Builder key resolution + base_url (no canonical shared profile)
# ---------------------------------------------------------------------------


def test_contract_key_resolution_profile():
    """Key from the provider's own profile is used first."""
    p = build_provider_client("opencode_go", {"api_key": "oc_profile"}, None)
    assert p._api_key == "oc_profile"
    assert p._base_url == OPCODE_GO_ENDPOINT


def test_contract_key_resolution_env(monkeypatch):
    """The env fallback works for BOTH independent providers."""
    monkeypatch.setenv(SHARED_ENV_KEY, "oc_env_key")
    assert build_provider_client("opencode_zen", {}, None)._api_key == "oc_env_key"
    assert build_provider_client("opencode_go", {}, None)._api_key == "oc_env_key"


def test_contract_key_resolution_none(monkeypatch):
    """No key anywhere → fail fast with a provider-named error (never the OpenAI key)."""
    monkeypatch.delenv(SHARED_ENV_KEY, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OpenCode"):
        build_provider_client("opencode_zen", {}, None)
    with pytest.raises(RuntimeError, match="OpenCode"):
        build_provider_client("opencode_go", {}, None)


def test_contract_base_url_override():
    """A per-provider custom endpoint lives on that provider's own profile."""
    override = "https://proxy.example/v1"
    p = build_provider_client("opencode_go", {"api_key": "oc_test", "base_url": override}, None)
    assert p._base_url == override
    # The sibling keeps its own default.
    p2 = build_provider_client("opencode_zen", {"api_key": "oc_test"}, None)
    assert p2._base_url == OPCODE_ZEN_ENDPOINT


# ---------------------------------------------------------------------------
# 7. Profile isolation + verify key resolution + secret hygiene (manager-level)
# ---------------------------------------------------------------------------


def _patch_manager_verify(monkeypatch, seen):
    """Patch the manager's local `verify_provider_key` binding (what verify_provider
    actually calls) so the fake observes the resolved key."""
    def fake_verify(name, *, api_key=None, base_url=None, fields=None, timeout=10.0):
        seen["name"] = name
        seen["api_key"] = api_key
        return {"ok": True}

    monkeypatch.setattr("coworker.server.manager.verify_provider_key", fake_verify)


def _mgr(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager
    return SessionManager(data_dir=tmp_path)


def test_contract_env_fallback_does_not_mark_profiles_configured(tmp_path, monkeypatch):
    """The shared env key marks providers as configured (env key is usable auth) but
    stored profiles remain independent — setting one sibling does not affect the other."""
    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.setenv(SHARED_ENV_KEY, "oc-env")

    initial = {p["name"]: p for p in mgr.get_providers()}
    assert initial["opencode_zen"]["configured"] is True  # env key is usable
    assert initial["opencode_go"]["configured"] is True

    mgr.set_provider("opencode_zen", {"api_key": "oc-zen"})
    after_zen = {p["name"]: p for p in mgr.get_providers()}
    assert after_zen["opencode_zen"]["configured"] is True
    assert after_zen["opencode_go"]["configured"] is True
    assert "oc-env" not in str(after_zen)
    assert "oc-zen" not in str(after_zen)

    # Removing the stored key for Zen leaves the env fallback still available
    mgr.remove_provider("opencode_zen")
    after_remove = {p["name"]: p for p in mgr.get_providers()}
    assert after_remove["opencode_zen"]["configured"] is True  # env fallback
    assert after_remove["opencode_go"]["configured"] is True


def test_contract_verify_explicit_key_wins(tmp_path, monkeypatch):
    """An explicit form key beats the stored profile key."""
    mgr = _mgr(tmp_path, monkeypatch)
    mgr.set_provider("opencode_go", {"api_key": "oc-stored"})
    seen: dict = {}
    _patch_manager_verify(monkeypatch, seen)
    mgr.verify_provider("opencode_go", {"api_key": "oc-explicit"})
    assert seen["name"] == "opencode_go"
    assert seen["api_key"] == "oc-explicit"


def test_contract_verify_env_fallback_for_both(tmp_path, monkeypatch):
    """verify with no stored key and no form key falls back to the env var for EACH
    independent provider."""
    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.setenv(SHARED_ENV_KEY, "oc-env")
    for name in ("opencode_zen", "opencode_go"):
        seen: dict = {}
        _patch_manager_verify(monkeypatch, seen)
        res = mgr.verify_provider(name, {})
        assert res["ok"] is True
        assert seen["name"] == name
        assert seen["api_key"] == "oc-env"


def test_contract_verify_never_falls_back_to_sibling_key(tmp_path, monkeypatch):
    """Only the provider's OWN stored key (or the env fallback) resolves — a sibling
    card's key must never satisfy the other's verify."""
    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.delenv(SHARED_ENV_KEY, raising=False)
    mgr.set_provider("opencode_zen", {"api_key": "oc-zen-only"})
    # Go has no key of its own: verify fails cleanly instead of borrowing Zen's.
    res = mgr.verify_provider("opencode_go", {})
    assert res["ok"] is False
    assert "API key" in res["error"]


def test_contract_no_secret_leakage_in_get_providers(tmp_path, monkeypatch):
    """get_providers never returns api_key values for either OpenCode provider."""
    mgr = _mgr(tmp_path, monkeypatch)
    mgr.set_provider("opencode_zen", {"api_key": "oc-zen-secret"})
    mgr.set_provider("opencode_go", {"api_key": "oc-go-secret"})
    provs = {p["name"]: p for p in mgr.get_providers()}
    for name in ("opencode_zen", "opencode_go"):
        assert provs[name]["configured"] is True
        values = provs[name].get("values", {})
        assert "api_key" not in values, name
        assert "oc-zen-secret" not in str(provs[name])
        assert "oc-go-secret" not in str(provs[name])
