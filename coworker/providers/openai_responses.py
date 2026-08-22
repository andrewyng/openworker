"""OpenAI Responses provider — native and compatible models via `/responses`.

Chat Completions rejects function tools combined with any `reasoning_effort` other than
`none` on GPT-5.6+ ("use /v1/responses"), which had reasoning pinned OFF for native OpenAI
models (see `openai_provider._pin_reasoning_effort`). This provider is the Responses path:
reasoning + tools at real effort levels, streamed reasoning summaries (→ the same
`reasoning_delta` / `AssistantTurn.reasoning` plumbing the GUI already renders), and
chain-of-thought continuity across tool round-trips via `store: false` +
`include: ["reasoning.encrypted_content"]` — nothing retained server-side.

Routing: the `openai` provider entry with NO custom base_url builds this class. Most custom
endpoints (Azure, vLLM, and the existing compat vendors) keep the Chat Completions
`OpenAIProvider`; vendors that explicitly implement the Responses wire can opt into this
class with their own base URL (registry.py).

Like the other native providers, this is mostly a pair of pure converters from the
canonical OpenAI-chat-shaped history to Responses `input` items. What the converters
must absorb:

- The system prompt is the `instructions` request field, not a message role.
- Assistant tool calls are top-level `function_call` items; tool results are
  `function_call_output` items paired by `call_id` (ids only need to pair up, so foreign
  `toolu_…` ids from a mid-conversation provider switch are fine).
- Tool schemas are FLAT (`{"type": "function", "name", …}` — no nested `function` key).
- Reasoning continuity: the raw output items (reasoning item with `encrypted_content`,
  `function_call` items with their ids) ride the canonical assistant message as the
  `_openai` sidecar (see providers/base.py). Present → replayed verbatim for exact CoT
  continuity; absent (history from another provider) → items are synthesized from the
  canonical fields. Reasoning items WITHOUT `encrypted_content` never enter the sidecar:
  with `store: false` the server can't resolve them and would reject the replay.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .base import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    StreamChunk,
    ToolCall,
)
from .capabilities import capabilities_for
from .openai_provider import resolve_api_key

# Request params passed through from model settings; everything else (frequency_penalty,
# reasoning_effort — no effort knob in v1, the server default rides) is dropped.
_SETTINGS_WHITELIST = {
    "temperature",
    "top_p",
    "max_output_tokens",
    "tool_choice",
    "parallel_tool_calls",
}

# "Unsupported parameter: 'temperature' is not supported with this model." — reasoning
# models reject sampling params; non-reasoning models reject `reasoning`/`include`. The
# server names exactly one offender per error, so each retry drops exactly that.
_UNSUPPORTED_PARAM = re.compile(r"unsupported (?:parameter|value)s?:?\s*'([^']+)'")


def _param_fix_retry(kwargs: dict[str, Any], exc: Exception) -> dict[str, Any]:
    """Kwargs for the one retry an unsupported-parameter error earns, or re-raise.

    Same contract as the Chat Completions retries: fix exactly what the server named.
    A dotted name (`reasoning.summary`) drops its top-level param.
    """
    match = _UNSUPPORTED_PARAM.search(str(exc).lower())
    if match:
        param = match.group(1).split(".", 1)[0].split("[", 1)[0]
        if param in kwargs and param not in ("model", "input"):
            fixed = dict(kwargs)
            del fixed[param]
            return fixed
    raise exc


def _user_content(content: Any) -> Any:
    """User content (str or OpenAI chat parts) → Responses content (str or input parts)."""
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            parts.append({"type": "input_text", "text": part.get("text") or ""})
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            parts.append({"type": "input_image", "image_url": url})
        elif kind == "file":
            file = part.get("file") or {}
            entry: dict[str, Any] = {"type": "input_file"}
            if file.get("filename"):
                entry["filename"] = file["filename"]
            if file.get("file_data"):
                entry["file_data"] = file["file_data"]
            parts.append(entry)
    return parts


def _synthesized_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    """An assistant message WITHOUT a usable `_openai` sidecar (history produced by another
    provider before a switch) → items rebuilt from the canonical fields."""
    items: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        items.append({"role": "assistant", "content": text})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {})
        items.append(
            {
                "type": "function_call",
                "call_id": call.get("id") or "",
                "name": function.get("name") or "",
                "arguments": arguments,
            }
        )
    return items


def convert_messages(
    messages: list[dict[str, Any]],
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Canonical OpenAI-chat history → (`instructions`, Responses `input` items).

    Leading system messages join into `instructions`; a stray mid-thread system message
    rides as a system message item. Assistant messages replay their `_openai` sidecar
    verbatim when present (exact CoT continuity), else synthesize from canonical fields.
    """
    system_parts: list[str] = []
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        content = messages[index].get("content")
        if isinstance(content, str) and content:
            system_parts.append(content)
        index += 1

    items: list[dict[str, Any]] = []
    for message in messages[index:]:
        role = message.get("role")
        if role == "system":
            text = message.get("content") or ""
            if text:
                items.append({"role": "system", "content": text})
        elif role == "user":
            content = _user_content(message.get("content"))
            if content:
                items.append({"role": "user", "content": content})
        elif role == "assistant":
            sidecar = message.get("_openai") or {}
            replay = sidecar.get("items") or []
            if replay:
                items.extend(replay)
            else:
                items.extend(_synthesized_items(message))
        elif role == "tool":
            content = message.get("content")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id") or "",
                    "output": content if isinstance(content, str) else str(content or ""),
                }
            )

    return ("\n\n".join(system_parts) or None), items


def convert_tools(tools: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """OpenAI chat function schemas → Responses FLAT tool entries (no nested `function`)."""
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        function = (tool or {}).get("function") or {}
        name = function.get("name")
        if not name:
            continue
        entry: dict[str, Any] = {"type": "function", "name": name}
        if function.get("description"):
            entry["description"] = function["description"]
        if function.get("parameters") is not None:
            entry["parameters"] = function["parameters"]
        converted.append(entry)
    return converted


def _dump(value: Any) -> Any:
    """An output item (SDK model, dict, or test namespace) → plain jsonl-safe data."""
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_dump(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    if hasattr(value, "__dict__"):  # SimpleNamespace fakes in tests
        return {k: _dump(v) for k, v in vars(value).items() if v is not None}
    return value


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except (TypeError, json.JSONDecodeError):
        # Surface unparseable arguments rather than dropping the call; the engine
        # can return a tool-error so the model corrects itself.
        return {"_raw": raw}


def _sidecar_extras(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Output items → the `_openai` sidecar, or {} when replay would add nothing.

    Reasoning items without `encrypted_content` are dropped: under `store: false` the
    server can't resolve them by id and rejects the replay. The sidecar is only worth
    persisting when something beyond plain answer text needs continuity.
    """
    kept = [
        item
        for item in items
        if item.get("type") != "reasoning" or item.get("encrypted_content")
    ]
    if any(item.get("type") in ("reasoning", "function_call") for item in kept):
        return {"_openai": {"items": kept}}
    return {}


def _parse_response(response: Any) -> AssistantTurn:
    """One Responses result → an AssistantTurn (+ `_openai` extras)."""
    items = [_dump(item) for item in getattr(response, "output", None) or []]
    texts: list[str] = []
    summaries: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in items:
        kind = item.get("type")
        if kind == "message" or (kind is None and "content" in item):
            content = item.get("content")
            if isinstance(content, str):
                texts.append(content)
            else:
                for part in content or []:
                    if part.get("type") == "output_text" and part.get("text"):
                        texts.append(part["text"])
        elif kind == "reasoning":
            for part in item.get("summary") or []:
                text = part.get("text") if isinstance(part, dict) else part
                if text:
                    summaries.append(text)
        elif kind == "function_call":
            tool_calls.append(
                ToolCall(
                    id=item.get("call_id") or item.get("id") or "",
                    name=item.get("name") or "",
                    arguments=_parse_arguments(item.get("arguments")),
                )
            )

    incomplete = _dump(getattr(response, "incomplete_details", None)) or {}
    if tool_calls:
        finish = "tool_calls"
    elif incomplete.get("reason") == "max_output_tokens":
        finish = "length"
    else:
        finish = "stop"

    return AssistantTurn(
        text="".join(texts) or None,
        tool_calls=tool_calls,
        finish_reason=finish,
        raw=response,
        reasoning="".join(summaries) or None,
        extras=_sidecar_extras(items),
    )


class OpenAIResponsesProvider(ProviderClient):
    def __init__(
        self,
        client: Any = None,
        *,
        default_model: str = "gpt-5.6-sol",
        api_key: Optional[str] = None,
        secrets: Any = None,
        base_url: Optional[str] = None,
        reasoning_summary: bool = True,
    ):
        # Same deferred-client contract as OpenAIProvider: built lazily so an engine can be
        # assembled before any key exists; key resolves at call time (explicit → env →
        # SecretStore). Tests inject a `client` directly. `base_url` is opt-in: stock OpenAI
        # leaves it unset, while Responses-compatible vendors can supply their own endpoint.
        self._client = client
        self._api_key = api_key
        self._secrets = secrets
        self._base_url = (base_url or "").strip().rstrip("/") or None
        if not isinstance(reasoning_summary, bool):
            raise TypeError("reasoning_summary must be a bool")
        self._reasoning_summary = reasoning_summary
        self.default_model = default_model

    def _ensure_client(self) -> Any:
        if self._client is None:
            # Lazy import so the SDK is only required when actually talking to OpenAI.
            from openai import OpenAI

            key = self._api_key or resolve_api_key(self._secrets)
            if not key:
                raise RuntimeError(
                    "No model API key configured. Set OPENAI_API_KEY in the environment, "
                    "or add your key in Manage → Settings."
                )
            kwargs = {"api_key": key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _request_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        instructions, items = convert_messages(messages)
        if "max_tokens" in settings and "max_output_tokens" not in settings:
            settings = {**settings, "max_output_tokens": settings["max_tokens"]}
        kwargs: dict[str, Any] = {
            "model": model,
            "input": items,
            # Stateless: nothing retained server-side; the encrypted reasoning rides the
            # `_openai` sidecar instead, and summaries feed the GUI's thinking display.
            "store": False,
            "include": ["reasoning.encrypted_content"],
            **{k: v for k, v in settings.items() if k in _SETTINGS_WHITELIST},
        }
        if self._reasoning_summary:
            kwargs["reasoning"] = {"summary": "auto"}
        if instructions:
            kwargs["instructions"] = instructions
        if tools:
            converted = convert_tools(tools)
            if converted:
                kwargs["tools"] = converted
        return kwargs

    def _create(self, client: Any, kwargs: dict[str, Any]) -> Any:
        # Up to three param-fix retries: sampling params, `reasoning`, and `include` can
        # each need dropping depending on the model (reasoning vs not).
        for _ in range(3):
            try:
                return client.responses.create(**kwargs)
            except Exception as exc:
                kwargs = _param_fix_retry(kwargs, exc)
        return client.responses.create(**kwargs)

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ) -> AssistantTurn:
        kwargs = self._request_kwargs(
            model=model, messages=messages, tools=tools, settings=settings
        )
        response = self._create(self._ensure_client(), kwargs)
        return _parse_response(response)

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(model)

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ):
        kwargs = self._request_kwargs(
            model=model, messages=messages, tools=tools, settings=settings
        )
        kwargs["stream"] = True
        events = self._create(self._ensure_client(), kwargs)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        final: Optional[Any] = None
        for event in events:
            kind = getattr(event, "type", None)
            if kind == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if delta:
                    text_parts.append(delta)
                    yield StreamChunk(text_delta=delta)
            elif kind == "response.reasoning_summary_text.delta":
                delta = getattr(event, "delta", None)
                if delta:
                    reasoning_parts.append(delta)
                    yield StreamChunk(reasoning_delta=delta)
            elif kind in ("response.completed", "response.incomplete", "response.failed"):
                final = getattr(event, "response", None)

        if final is not None:
            # The terminal event carries the full response — parse it whole so tool
            # calls, finish reason, and the `_openai` sidecar come from one place.
            yield StreamChunk(turn=_parse_response(final))
        else:
            yield StreamChunk(
                turn=AssistantTurn(
                    text="".join(text_parts) or None,
                    reasoning="".join(reasoning_parts) or None,
                )
            )
