"""Gemini provider — native Google GenAI API (`google-genai` SDK).

Like the Anthropic provider, this is mostly a pair of pure converters from our canonical
OpenAI-shaped history to Gemini's `generateContent` format. The differences the converters
must absorb:

- The system prompt is `system_instruction` inside the request config, not a message role.
- Roles are `user`/`model`; tool results ride as `function_response` parts in a user message.
- Function calls carry NO ids — we synthesize `call_<n>` ids for the engine and map results
  back by name (an id→name map built from the assistant turns during conversion).
- Tool parameter schemas are an OpenAPI 3.0 subset: unsupported JSON Schema keys
  (`additionalProperties`, `$schema`, …) must be stripped or the API rejects the request.
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

# Gemini finishReason → the engine's OpenAI-shaped finish_reason vocabulary. STOP maps to
# "tool_calls" instead when the turn contains function calls (Gemini has no distinct reason).
_FINISH_REASON_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "stop",
    "RECITATION": "stop",
    "MALFORMED_FUNCTION_CALL": "stop",
}

# GenerateContentConfig keys we pass through; everything else (frequency_penalty, …) is dropped.
_SETTINGS_WHITELIST = {
    "temperature",
    "top_p",
    "top_k",
    "max_output_tokens",
    "stop_sequences",
}

# The OpenAPI-subset schema keys Gemini function declarations accept.
_SCHEMA_KEYS = {
    "type",
    "format",
    "description",
    "nullable",
    "enum",
    "items",
    "properties",
    "required",
    "anyOf",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
    "example",
    "default",
    "title",
}

_DATA_URL_RE = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)

_PDF_DATA_URL_RE = re.compile(
    r"^data:application/pdf;base64,(.+)$", re.IGNORECASE | re.DOTALL
)


def resolve_api_key(secrets: Any = None) -> Optional[str]:
    """Resolve the Gemini API key: env `GEMINI_API_KEY` (then `GOOGLE_API_KEY`, the SDK's own
    convention) first, else the SecretStore `provider:gemini` profile (`{api_key}`)."""
    import os

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    if secrets is not None:
        profile = secrets.get("provider:gemini") or {}
        return profile.get("api_key") or None
    return None


def _image_part(url: str) -> Optional[dict[str, Any]]:
    """An OpenAI `image_url` part → a Gemini inline_data part. Attachments are always data
    URLs (attachments.py). Plain http(s) URLs are not fetchable by the API → None."""
    match = _DATA_URL_RE.match(url or "")
    if match:
        return {
            "inline_data": {"mime_type": match.group(1).lower(), "data": match.group(2)}
        }
    return None


def _pdf_part(part: dict[str, Any]) -> Optional[dict[str, Any]]:
    """An OpenAI `file` part (PDF data URL, attachments.py) → a Gemini inline_data part."""
    file = part.get("file") or {}
    match = _PDF_DATA_URL_RE.match(file.get("file_data") or "")
    if match:
        return {"inline_data": {"mime_type": "application/pdf", "data": match.group(1)}}
    return None


def _user_parts(content: Any) -> list[dict[str, Any]]:
    """User content (str or OpenAI parts list) → Gemini parts."""
    if isinstance(content, str):
        return [{"text": content}] if content else []
    parts: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            text = part.get("text") or ""
            if text:
                parts.append({"text": text})
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            image = _image_part(url)
            parts.append(image if image else {"text": "[unsupported image attachment]"})
        elif kind == "file":
            pdf = _pdf_part(part)
            parts.append(pdf if pdf else {"text": "[unsupported file attachment]"})
    return parts


def _parse_args(raw: Any) -> dict[str, Any]:
    """Tool-call arguments: dict passthrough, JSON string parse, `{"_raw": …}` fallback."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except (TypeError, json.JSONDecodeError):
        return {"_raw": raw}


def _result_payload(content: Any) -> dict[str, Any]:
    """A tool result string → the JSON object Gemini requires as a function response."""
    if isinstance(content, dict):
        return content
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except (TypeError, json.JSONDecodeError):
        return {"result": str(content or "")}


def convert_messages(
    messages: list[dict[str, Any]],
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """OpenAI-shaped history → (`system_instruction`, Gemini `contents`).

    Function calls have no ids on the wire, so tool results are matched back to their function
    NAME via an id→name map built from the assistant turns. Consecutive same-role outputs fold
    into one content entry (tool-result runs collapse into a single user message, steering text
    merging after — Gemini also dislikes non-alternating roles).
    """
    system_parts: list[str] = []
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        content = messages[index].get("content")
        if isinstance(content, str) and content:
            system_parts.append(content)
        index += 1

    call_names: dict[str, str] = {}
    converted: list[dict[str, Any]] = []
    for message in messages[index:]:
        role = message.get("role")
        if role == "system":
            # Defensive: a stray mid-thread system message rides as marked user text.
            text = message.get("content") or ""
            if text:
                converted.append(
                    {
                        "role": "user",
                        "parts": [{"text": f"<system>\n{text}\n</system>"}],
                    }
                )
        elif role == "user":
            parts = _user_parts(message.get("content"))
            if parts:
                converted.append({"role": "user", "parts": parts})
        elif role == "assistant":
            parts = []
            text = message.get("content")
            if isinstance(text, str) and text:
                parts.append({"text": text})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                name = function.get("name") or ""
                call_names[call.get("id") or ""] = name
                parts.append(
                    {
                        "function_call": {
                            "name": name,
                            "args": _parse_args(function.get("arguments")),
                        }
                    }
                )
            if parts:
                converted.append({"role": "model", "parts": parts})
        elif role == "tool":
            call_id = message.get("tool_call_id") or ""
            converted.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": call_names.get(call_id) or call_id,
                                "response": _result_payload(message.get("content")),
                            }
                        }
                    ],
                }
            )

    folded: list[dict[str, Any]] = []
    for message in converted:
        if folded and folded[-1]["role"] == message["role"]:
            folded[-1]["parts"].extend(message["parts"])
        else:
            folded.append(message)

    if not folded:
        raise ValueError("no convertible messages for the Gemini API")
    if folded[0]["role"] != "user":
        folded.insert(0, {"role": "user", "parts": [{"text": "(continued)"}]})

    return ("\n\n".join(system_parts) or None), folded


def _sanitize_schema(schema: Any) -> Any:
    """Strip JSON Schema keys Gemini's OpenAPI subset rejects (recursively)."""
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {name: _sanitize_schema(sub) for name, sub in value.items()}
        elif key == "items":
            cleaned[key] = _sanitize_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            cleaned[key] = [_sanitize_schema(sub) for sub in value]
        else:
            cleaned[key] = value
    return cleaned


def convert_tools(tools: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """OpenAI function schemas → Gemini tool declarations (one tool, N function_declarations)."""
    declarations = []
    for tool in tools or []:
        function = tool.get("function") or {}
        entry: dict[str, Any] = {"name": function.get("name") or ""}
        if function.get("description"):
            entry["description"] = function["description"]
        parameters = function.get("parameters")
        if isinstance(parameters, dict) and parameters.get("properties"):
            entry["parameters"] = _sanitize_schema(parameters)
        # parameter-less functions omit `parameters` entirely (Gemini rejects empty objects)
        declarations.append(entry)
    return [{"function_declarations": declarations}] if declarations else []


def _parse_candidate(response: Any) -> tuple[list[str], list[ToolCall], Optional[str]]:
    """Pull text parts, function calls (with synthesized ids), and the finish reason out of a
    GenerateContentResponse (or one streamed chunk of it)."""
    texts: list[str] = []
    calls: list[ToolCall] = []
    finish = None
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return texts, calls, finish
    candidate = candidates[0]
    content = getattr(candidate, "content", None)
    for part in getattr(content, "parts", None) or []:
        text = getattr(part, "text", None)
        if text:
            texts.append(text)
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            calls.append(
                ToolCall(
                    id="",  # synthesized by the caller (needs the running count)
                    name=getattr(function_call, "name", "") or "",
                    arguments=dict(getattr(function_call, "args", None) or {}),
                )
            )
    raw_finish = getattr(candidate, "finish_reason", None)
    if raw_finish is not None:
        finish = getattr(raw_finish, "name", None) or str(raw_finish)
    return texts, calls, finish


def _map_finish(finish: Optional[str], has_calls: bool) -> Optional[str]:
    if has_calls:
        return "tool_calls"
    if finish is None:
        return None
    return _FINISH_REASON_MAP.get(finish, finish.lower())


class GeminiProvider(ProviderClient):
    def __init__(
        self,
        client: Any = None,
        *,
        default_model: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        secrets: Any = None,
    ):
        # Mirrors AnthropicProvider: the SDK client is built lazily so engines can be assembled
        # before any key exists; the key resolves at call time (explicit → env → SecretStore).
        # Tests inject a `client` directly.
        self._client = client
        self._api_key = api_key
        self._secrets = secrets
        self.default_model = default_model

    def _ensure_client(self) -> Any:
        if self._client is None:
            # Lazy import so the SDK is only required when actually talking to Gemini.
            from google import genai

            key = self._api_key or resolve_api_key(self._secrets)
            if not key:
                raise RuntimeError(
                    "No Gemini API key configured. Set GEMINI_API_KEY in the environment, "
                    "or add your key in Manage → Configure Models."
                )
            self._client = genai.Client(api_key=key)
        return self._client

    def _request_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        system, contents = convert_messages(messages)
        if "max_tokens" in settings and "max_output_tokens" not in settings:
            settings["max_output_tokens"] = settings["max_tokens"]
        if "stop" in settings and "stop_sequences" not in settings:
            stop = settings["stop"]
            settings["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
        config: dict[str, Any] = {
            k: v for k, v in settings.items() if k in _SETTINGS_WHITELIST
        }
        if system:
            config["system_instruction"] = system
        if tools:
            converted = convert_tools(tools)
            if converted:
                config["tools"] = converted
        return {"model": model, "contents": contents, "config": config}

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
        response = self._ensure_client().models.generate_content(**kwargs)
        texts, calls, finish = _parse_candidate(response)
        tool_calls = [
            ToolCall(id=f"call_{i}", name=c.name, arguments=c.arguments)
            for i, c in enumerate(calls)
        ]
        return AssistantTurn(
            text="".join(texts) or None,
            tool_calls=tool_calls,
            finish_reason=_map_finish(finish, bool(tool_calls)),
            raw=response,
        )

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
        client = self._ensure_client()

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        finish = None

        # Unlike Anthropic, function_call parts arrive whole (args are a complete dict per
        # part), so there is no JSON accumulation — just collect parts across chunks.
        for chunk in client.models.generate_content_stream(**kwargs):
            texts, chunk_calls, chunk_finish = _parse_candidate(chunk)
            for text in texts:
                text_parts.append(text)
                yield StreamChunk(text_delta=text)
            calls.extend(chunk_calls)
            if chunk_finish:
                finish = chunk_finish

        tool_calls = [
            ToolCall(id=f"call_{i}", name=c.name, arguments=c.arguments)
            for i, c in enumerate(calls)
        ]
        yield StreamChunk(
            turn=AssistantTurn(
                text="".join(text_parts) or None,
                tool_calls=tool_calls,
                finish_reason=_map_finish(finish, bool(tool_calls)),
            )
        )
