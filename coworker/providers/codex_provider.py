"""`openai-codex` provider — OpenAI models through a ChatGPT subscription.

The backend speaks the same Responses wire as `/v1/responses` (stateless: full
history each turn, `store: false`, encrypted reasoning in the `_openai` sidecar), so
all conversion/parsing is inherited from `OpenAIResponsesProvider` — this subclass
only swaps the credential: a short-lived OAuth bearer from `codex_auth` instead of an
API key, plus the account/originator/session headers the backend requires.

Differences from the API-key path:
  - The backend serves streamed responses only, so `complete()` drains `stream()`.
  - 401 → one refresh-and-retry (the bearer died mid-flight); a rejected refresh
    token surfaces as a typed sign-in-required error, never a crash loop.
  - 429 → the plan's rolling usage window, surfaced as a user-readable message.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from .base import AssistantTurn
from .codex_auth import (
    CODEX_BASE_URL,
    PLAN_LIMIT_ERROR,
    CodexTokenStore,
    backend_headers,
)
from .openai_responses import OpenAIResponsesProvider


def _status_code(exc: Exception) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


class CodexProvider(OpenAIResponsesProvider):
    def __init__(
        self,
        client: Any = None,
        *,
        secrets: Any = None,
        default_model: str = "gpt-5.2-codex",
        reasoning_summary: bool = True,
    ):
        super().__init__(
            client=client,
            default_model=default_model,
            base_url=CODEX_BASE_URL,
            reasoning_summary=reasoning_summary,
        )
        self._store = CodexTokenStore(secrets)
        # One conversation per provider instance in practice (the router caches one
        # client per provider); a uuid per instance satisfies the per-conversation
        # session header without threading conversation ids through ProviderClient.
        self._session_id = str(uuid.uuid4())
        self._client_token: Optional[str] = None
        self._injected = client is not None

    def _ensure_client(self) -> Any:
        if self._injected:
            return self._client
        # The bearer is short-lived: fetch per call (refreshes itself near expiry)
        # and rebuild the SDK client whenever the token rotated.
        token, account = self._store.access_token()
        if self._client is None or token != self._client_token:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=token,
                base_url=CODEX_BASE_URL,
                default_headers=backend_headers(account, self._session_id),
            )
            self._client_token = token
        return self._client

    def _request_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        kwargs = super()._request_kwargs(
            model=model, messages=messages, tools=tools, settings=settings
        )
        # This backend 400s ("Unsupported parameter") on standard sampling/cap knobs —
        # max_output_tokens and temperature confirmed live, top_p same family — which
        # silently killed every autotitle attempt on plan sessions (owner catch
        # 2026-08-24). Callers may pass them freely; they just cannot ride to this
        # backend.
        for unsupported in ("max_output_tokens", "temperature", "top_p"):
            kwargs.pop(unsupported, None)
        # Unlike stock /v1/responses, this backend honors a reasoning effort knob.
        effort = settings.get("reasoning_effort")
        if isinstance(effort, str) and effort:
            kwargs["reasoning"] = {**kwargs.get("reasoning", {}), "effort": effort}
        # The backend rejects requests without instructions; history normally
        # carries a system prompt — this is only the bare-call fallback.
        kwargs.setdefault("instructions", "You are a helpful assistant.")
        return kwargs

    def _create(self, client: Any, kwargs: dict[str, Any]) -> Any:
        try:
            return super()._create(client, kwargs)
        except Exception as exc:
            status = _status_code(exc)
            if status == 401 and not self._injected:
                # The bearer died mid-flight: force one refresh and retry once.
                # A rejected refresh raises CodexSignInRequired out of the store.
                self._store.refresh()
                self._client = None
                self._client_token = None
                return super()._create(self._ensure_client(), kwargs)
            if status == 429:
                raise RuntimeError(PLAN_LIMIT_ERROR) from exc
            raise

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ) -> AssistantTurn:
        # The backend only serves streamed responses — aggregate the stream.
        turn: Optional[AssistantTurn] = None
        for chunk in self.stream(model=model, messages=messages, tools=tools, **settings):
            if chunk.turn is not None:
                turn = chunk.turn
        return turn if turn is not None else AssistantTurn()
