"""OpenCode Zen and Go: per-model wires, client headers, and independent keys."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers import OpenAIProvider, ProviderRouter
from coworker.providers.gemini_provider import GeminiProvider
from coworker.providers.openai_responses import OpenAIResponsesProvider
from coworker.providers.anthropic_provider import AnthropicProvider
from coworker.providers.matrix import MATRIX
from coworker.providers.opencode_provider import (
    GO,
    USER_AGENT,
    ZEN,
    OpenCodeGateway,
    bind_opencode_session,
    wire_for,
)
from coworker.providers.registry import (
    build_provider_client,
    get_descriptor,
    verify_provider_key,
)
from coworker.tools import ToolRegistry


def test_minimax_and_qwen_use_different_wires_per_product():
    assert wire_for(ZEN, "minimax-m2.7") == "chat"
    assert wire_for(GO, "minimax-m2.7") == "messages"
    assert wire_for(ZEN, "qwen3.8-max") == "chat"
    assert wire_for(GO, "qwen3.8-max") == "messages"


def test_unknown_model_is_not_guessed():
    gateway = OpenCodeGateway(product=GO, api_key="go-key")
    with pytest.raises(RuntimeError, match="not in the OpenCode Go catalog"):
        gateway.complete(model="gpt-5.6-sol", messages=[{"role": "user", "content": "hi"}])


def test_delegates_are_pinned_to_the_product_host():
    zen = OpenCodeGateway(product=ZEN, api_key="zen-key")
    go = OpenCodeGateway(product=GO, api_key="go-key")

    assert zen._build("responses", claude=False)._base_url == "https://opencode.ai/zen/v1"
    assert zen._build("chat", claude=False)._base_url == "https://opencode.ai/zen/v1"
    claude = zen._build("messages", claude=True)
    assert isinstance(claude, AnthropicProvider)
    assert claude._base_url == "https://opencode.ai/zen"
    assert claude._claude_options is True
    plain = go._build("messages", claude=False)
    assert plain._base_url == "https://opencode.ai/zen/go"
    assert plain._claude_options is False
    assert plain.thinking_budget == 0
    gemini = zen._build("gemini", claude=False)
    assert gemini._base_url == "https://opencode.ai/zen"
    assert gemini._api_version == "v1"


def test_request_headers_follow_the_bound_session_and_fall_back():
    gateway = OpenCodeGateway(product=GO, api_key="go-key")
    first = gateway.request_headers()
    second = gateway.request_headers()
    assert first["User-Agent"] == USER_AGENT
    assert first["x-opencode-session"] == second["x-opencode-session"]
    assert first["x-opencode-session"]
    with bind_opencode_session("conv-9"):
        bound = gateway.request_headers()
    assert bound["x-opencode-session"] == "conv-9"
    assert bound["User-Agent"] == USER_AGENT
    assert gateway.request_headers()["x-opencode-session"] == first["x-opencode-session"]


class _Chat:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        if kwargs.get("stream"):
            delta = SimpleNamespace(content="ok", tool_calls=None)
            choice = SimpleNamespace(delta=delta, finish_reason="stop")
            return [SimpleNamespace(choices=[choice], usage=None)]
        message = SimpleNamespace(content="ok", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=None)


def _chat_gateway(product: str) -> tuple[OpenCodeGateway, _Chat]:
    gateway = OpenCodeGateway(product=product, api_key="k")
    chat = _Chat()
    gateway._clients[("chat", False)] = OpenAIProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=chat)),
        api_key="k",
        extra_headers=gateway.request_headers,
    )
    return gateway, chat


def test_chat_call_sends_user_agent_and_session():
    gateway, chat = _chat_gateway(ZEN)
    with bind_opencode_session("conv-9"):
        turn = gateway.complete(
            model="kimi-k3", messages=[{"role": "user", "content": "hi"}]
        )
    assert turn.text == "ok"
    assert chat.kwargs["extra_headers"]["User-Agent"] == USER_AGENT
    assert chat.kwargs["extra_headers"]["x-opencode-session"] == "conv-9"
    assert chat.kwargs["model"] == "kimi-k3"


class _Messages:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return _FinalMessage()


class _FinalMessage:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_final_message(self):
        return SimpleNamespace(content=[], stop_reason="end_turn", usage=None)


def test_qwen_and_minimax_messages_omit_claude_options():
    gateway = OpenCodeGateway(product=GO, api_key="go-key")
    messages = _Messages()

    def _beta(**kwargs):
        raise AssertionError(f"beta endpoint called: {kwargs}")

    client = SimpleNamespace(
        messages=messages,
        beta=SimpleNamespace(messages=SimpleNamespace(stream=_beta, create=_beta)),
    )
    # A non-zero budget would add adaptive thinking on a Claude client. The flag
    # is what keeps it off this wire.
    gateway._clients[("messages", False)] = AnthropicProvider(
        client=client,
        api_key="go-key",
        claude_options=False,
        thinking_budget=8192,
        extra_headers=gateway.request_headers,
    )
    with bind_opencode_session("conv-go"):
        for model in ("qwen3.7-plus", "minimax-m2.7"):
            messages.kwargs = {}
            gateway.complete(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort="high",
            )
            sent = messages.kwargs
            assert "thinking" not in sent
            assert "output_config" not in sent
            assert "betas" not in sent
            assert sent["extra_headers"]["User-Agent"] == USER_AGENT
            assert sent["extra_headers"]["x-opencode-session"] == "conv-go"
            assert sent["model"] == model


def _engine(provider, model, tmp_path, session_id=None):
    return TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model=model,
        session_id=session_id,
    )


def _run(engine, text="hi"):
    async def _collect():
        return [event async for event in engine.run(text)]

    return asyncio.run(_collect())


def test_responses_and_gemini_calls_send_the_same_headers():
    gateway = OpenCodeGateway(product=ZEN, api_key="zen-key")
    responses = _Capture()
    gemini = _Capture()
    gateway._clients[("responses", False)] = OpenAIResponsesProvider(
        client=SimpleNamespace(responses=responses),
        api_key="zen-key",
        extra_headers=gateway.request_headers,
    )
    gateway._clients[("gemini", False)] = GeminiProvider(
        client=SimpleNamespace(models=gemini),
        api_key="zen-key",
        extra_headers=gateway.request_headers,
    )
    with bind_opencode_session("conv-zen"):
        gateway.complete(
            model="gpt-5.6-sol", messages=[{"role": "user", "content": "hi"}]
        )
        gateway.complete(
            model="gemini-3.1-pro", messages=[{"role": "user", "content": "hi"}]
        )
    assert responses.kwargs["extra_headers"]["User-Agent"] == USER_AGENT
    assert responses.kwargs["extra_headers"]["x-opencode-session"] == "conv-zen"
    headers = gemini.kwargs["config"]["http_options"]["headers"]
    assert headers["User-Agent"] == USER_AGENT
    assert headers["x-opencode-session"] == "conv-zen"


class _Capture:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output=[])

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(candidates=[], usage_metadata=None)


def _routed(gateway: OpenCodeGateway) -> ProviderRouter:
    router = ProviderRouter(secrets=None)
    router._clients[gateway.product] = gateway
    return router


def test_engine_session_id_reaches_the_delegated_call(tmp_path):
    gateway, chat = _chat_gateway(GO)
    engine = _engine(
        _routed(gateway), "opencode-go:kimi-k2.7-code", tmp_path, session_id="conv-9"
    )
    _run(engine)
    assert chat.kwargs["extra_headers"]["User-Agent"] == USER_AGENT
    assert chat.kwargs["extra_headers"]["x-opencode-session"] == "conv-9"
    assert chat.kwargs["model"] == "kimi-k2.7-code"


def test_engine_without_a_session_id_sends_one_stable_id(tmp_path):
    gateway, chat = _chat_gateway(GO)
    engine = _engine(_routed(gateway), "opencode-go:kimi-k2.7-code", tmp_path)
    _run(engine, "one")
    first = chat.kwargs["extra_headers"]["x-opencode-session"]
    _run(engine, "two")
    second = chat.kwargs["extra_headers"]["x-opencode-session"]
    assert first == second == engine.opencode_session_id
    assert first


def test_zen_and_go_keys_are_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    for name in ("opencode-zen", "opencode-go", "openai", "anthropic", "gemini"):
        descriptor = get_descriptor(name)
        if descriptor and descriptor.env_key:
            monkeypatch.delenv(descriptor.env_key, raising=False)
    from coworker.server.manager import SessionManager

    # An OpenAI key in the environment must not satisfy OpenCode, and must not
    # be the key the gateway would send.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    with pytest.raises(RuntimeError, match="OpenCode Zen"):
        build_provider_client("opencode-zen", {}, None)
    monkeypatch.delenv("OPENAI_API_KEY")

    manager = SessionManager(data_dir=tmp_path)
    zen = manager.set_provider("opencode-zen", {"api_key": "zen-key"})
    assert zen["ok"] is True
    assert manager.model == "opencode-zen:gpt-5.6-sol"
    go = manager.set_provider("opencode-go", {"api_key": "go-key"})
    assert go["ok"] is True
    assert manager.model == "opencode-zen:gpt-5.6-sol"  # a working default is not stolen

    rows = {row["name"]: row for row in manager.get_providers()}
    assert rows["opencode-zen"]["configured"] is True
    assert rows["opencode-go"]["configured"] is True
    assert "api_key" not in rows["opencode-zen"]["values"]

    assert manager.remove_provider("opencode-zen")["ok"] is True
    rows = {row["name"]: row for row in manager.get_providers()}
    assert rows["opencode-zen"]["configured"] is False
    assert rows["opencode-go"]["configured"] is True
    assert manager.secrets.get("provider:opencode-go")["api_key"] == "go-key"
    assert manager.secrets.get("provider:opencode-zen") in (None, {})


def test_recommended_models_are_in_the_matrix():
    for name, model in (
        ("opencode-zen", "gpt-5.6-sol"),
        ("opencode-go", "kimi-k2.7-code"),
    ):
        assert get_descriptor(name).recommended_model == model
        assert f"{name}:{model}" in MATRIX


def test_verify_probes_the_models_endpoint_with_the_user_agent(monkeypatch):
    seen: dict = {}

    class _Response:
        status_code = 200

    def fake_get(url, headers=None, timeout=None, params=None):
        seen["url"] = url
        seen["headers"] = headers
        return _Response()

    monkeypatch.setattr("httpx.get", fake_get)
    assert verify_provider_key("opencode-go", api_key="go-key") == {"ok": True}
    assert seen["url"] == "https://opencode.ai/zen/go/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer go-key"
    assert seen["headers"]["User-Agent"] == USER_AGENT

    assert verify_provider_key("opencode-zen", api_key="zen-key") == {"ok": True}
    assert seen["url"] == "https://opencode.ai/zen/v1/models"
