"""OpenCode Zen and OpenCode Go — one gateway, two products, four wires.

Zen (pay-as-you-go) and Go (the subscription) are separate hosts. Each model id is
pinned to one of OpenAI Responses, Chat Completions, Anthropic Messages, or Gemini.
The same id can use a different wire on each product (MiniMax is chat on Zen and
Messages on Go), so a name-prefix guess is a 400. Ids missing from the table fail
closed.

Go's client policy (https://opencode.ai/docs/go/) asks every caller to identify
itself and send a stable per-conversation id. Both products get:

- ``User-Agent: OpenWorker/<package version>`` rather than the SDK default
- ``x-opencode-session`` from the active turn (see ``bind_opencode_session``)

The Anthropic SDK appends ``/v1/messages`` itself, so its base is the host without
``/v1``. The OpenAI SDK expects the ``/v1`` root. Gemini's client appends
``api_version``, so the host is ``https://opencode.ai/zen`` with ``api_version=v1``,
which lands on ``/zen/v1/models/{id}``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from .anthropic_provider import DEFAULT_THINKING_BUDGET, AnthropicProvider
from .base import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from .capabilities import capabilities_for
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider
from .openai_responses import OpenAIResponsesProvider

# Matches pyproject.toml. coworker.__version__ is still the 0.0.0 placeholder.
USER_AGENT = "OpenWorker/0.2.3"

ZEN = "opencode-zen"
GO = "opencode-go"

# OpenAI-compatible root (Chat Completions and Responses).
_OPENAI_BASE = {
    ZEN: "https://opencode.ai/zen/v1",
    GO: "https://opencode.ai/zen/go/v1",
}
# Anthropic SDK base: the SDK adds /v1/messages.
_ANTHROPIC_BASE = {
    ZEN: "https://opencode.ai/zen",
    GO: "https://opencode.ai/zen/go",
}
# Gemini SDK base + api_version "v1" → https://opencode.ai/zen/v1/models/{id}.
_GEMINI_BASE = "https://opencode.ai/zen"
_GEMINI_API_VERSION = "v1"

# Wire names. "messages" plus a claude* id keeps Claude thinking/effort/beta;
# every other Messages model is sent as plain Anthropic-shaped chat.
Wire = str

_ZEN_WIRES: dict[str, Wire] = {
    "gpt-5.6-sol": "responses",
    "grok-4.5": "responses",
    "claude-fable-5": "messages",
    "gemini-3.1-pro": "gemini",
    "kimi-k3": "chat",
    # Not in the curated picker. Pinned so a typed id cannot be guessed onto the
    # wrong wire — these two differ between Zen and Go.
    "minimax-m2.7": "chat",
    "qwen3.8-max": "chat",
}
_GO_WIRES: dict[str, Wire] = {
    "kimi-k2.7-code": "chat",
    "glm-5.2": "chat",
    "deepseek-v4-flash": "chat",
    "kimi-k3": "chat",
    "qwen3.7-plus": "messages",
    "minimax-m2.7": "messages",
    "qwen3.8-max": "messages",
}
_WIRES = {ZEN: _ZEN_WIRES, GO: _GO_WIRES}

_session_id: ContextVar[Optional[str]] = ContextVar("opencode_session", default=None)


def current_opencode_session() -> Optional[str]:
    """The conversation id bound for the current provider call, if any."""
    return _session_id.get()


def new_session_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def bind_opencode_session(session_id: str) -> Iterator[None]:
    """Bind ``x-opencode-session`` for the current context, including threads that
    copy it (``asyncio.to_thread``)."""
    token = _session_id.set(session_id)
    try:
        yield
    finally:
        _session_id.reset(token)


def wire_for(product: str, model: str) -> Wire:
    """The wire ``model`` uses on ``product``. Unknown ids raise — no prefix guess."""
    table = _WIRES.get(product)
    if table is None:
        raise RuntimeError(f"Unknown OpenCode product {product!r}.")
    wire = table.get(model)
    if wire is None:
        title = "OpenCode Zen" if product == ZEN else "OpenCode Go"
        raise RuntimeError(
            f"{model} is not in the {title} catalog OpenWorker routes. "
            "Pick a model from Settings ▸ Models."
        )
    return wire


def openai_base_url(product: str) -> str:
    """``/v1`` root used by the key probe and the OpenAI SDK."""
    try:
        return _OPENAI_BASE[product]
    except KeyError:
        raise RuntimeError(f"Unknown OpenCode product {product!r}.") from None


class OpenCodeGateway(ProviderClient):
    """Routes one OpenCode product's model ids onto the existing provider clients."""

    def __init__(self, product: str, api_key: str) -> None:
        if product not in _WIRES:
            raise RuntimeError(f"Unknown OpenCode product {product!r}.")
        key = (api_key or "").strip()
        if not key:
            title = "OpenCode Zen" if product == ZEN else "OpenCode Go"
            raise RuntimeError(
                f"No {title} API key configured — add it in Settings ▸ Models."
            )
        self.product = product
        self._api_key = key
        # Calls that never entered a turn (no bound session) still send a stable id.
        self._fallback_session = new_session_id()
        self._clients: dict[tuple[str, bool], ProviderClient] = {}
        self._static_headers = {"User-Agent": USER_AGENT}

    def request_headers(self) -> dict[str, str]:
        session = current_opencode_session() or self._fallback_session
        return {"User-Agent": USER_AGENT, "x-opencode-session": session}

    def _client_for(self, model: str) -> ProviderClient:
        wire = wire_for(self.product, model)
        claude = wire == "messages" and model.startswith("claude")
        key = (wire, claude)
        client = self._clients.get(key)
        if client is None:
            client = self._build(wire, claude=claude)
            self._clients[key] = client
        return client

    def _build(self, wire: Wire, *, claude: bool) -> ProviderClient:
        headers = self._static_headers
        extra = self.request_headers
        if wire == "responses":
            return OpenAIResponsesProvider(
                api_key=self._api_key,
                base_url=_OPENAI_BASE[self.product],
                default_headers=headers,
                extra_headers=extra,
            )
        if wire == "chat":
            return OpenAIProvider(
                api_key=self._api_key,
                base_url=_OPENAI_BASE[self.product],
                default_headers=headers,
                extra_headers=extra,
            )
        if wire == "messages":
            return AnthropicProvider(
                api_key=self._api_key,
                base_url=_ANTHROPIC_BASE[self.product],
                default_headers=headers,
                extra_headers=extra,
                claude_options=claude,
                thinking_budget=DEFAULT_THINKING_BUDGET if claude else 0,
            )
        if wire == "gemini":
            return GeminiProvider(
                api_key=self._api_key,
                base_url=_GEMINI_BASE,
                api_version=_GEMINI_API_VERSION,
                default_headers=headers,
                extra_headers=extra,
            )
        raise RuntimeError(f"Unknown OpenCode wire {wire!r}.")

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ) -> AssistantTurn:
        return self._client_for(model).complete(
            model=model, messages=messages, tools=tools, **settings
        )

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ):
        yield from self._client_for(model).stream(
            model=model, messages=messages, tools=tools, **settings
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(f"{self.product}:{model}")
