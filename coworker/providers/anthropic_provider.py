"""Anthropic provider — native Claude Messages API.

The runtime's canonical message format is OpenAI-shaped (that is what the engine builds and
persists), so this module is mostly a pair of pure converters: OpenAI-style messages → Anthropic
`messages` + `system`, and OpenAI function schemas → Anthropic `tools`. The Messages API differs
from chat.completions in ways the converters must absorb:

- `system` is a top-level param, not a message role.
- Assistant tool calls are `tool_use` content blocks (input is a dict, not a JSON string).
- Tool results are `tool_result` blocks that must ALL land in the single next user message —
  N consecutive `role:"tool"` messages collapse into one user message here.
- `max_tokens` is required.
- Extended thinking (opt-in via the provider profile's `thinking_budget` field): responses
  carry `thinking`/`redacted_thinking` blocks that MUST be replayed verbatim (signatures
  and all) ahead of the same turn's tool_use blocks when returning tool results — they ride
  the canonical assistant message as the `_anthropic` sidecar and are reattached here. The
  thinking text also lands on `AssistantTurn.reasoning` for display.
"""

from __future__ import annotations

import logging

import json
import os
import re
from typing import Any, Optional

from .effort import EffortPlan, anthropic_effort, mentions_effort
from .base import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    StreamChunk,
    TokenUsage,
    ToolCall,
)
from .capabilities import capabilities_for


def _usage_from(usage: Any) -> Optional[TokenUsage]:
    """Messages-API usage object → normalized counts (input_tokens excludes cache)."""
    if usage is None:
        return None
    return TokenUsage(
        input=int(getattr(usage, "input_tokens", 0) or 0),
        output=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )

# Required by the Messages API; a ceiling, not a spend target. Sized for file
# generation, not just chat: a coworker writing a self-contained HTML report ships the
# whole file inside one tool call's arguments, and 16k proved too small in the field
# (the call truncates mid-arguments and the write fails). Current Claude models all
# accept ≥32k output.
DEFAULT_MAX_TOKENS = 32000
# The SDK refuses a NON-streaming request whose max_tokens implies more than ten minutes
# of generation at its pessimistic 128k tokens/hour (max_tokens > 21,333) unless the caller
# sets its own timeout. complete() callers (reviewer, summaries, titles) legitimately run
# with the default cap, so above the ceiling the request carries an explicit timeout sized
# to the SDK's own estimate for the largest default request.
NONSTREAMING_TOKEN_CEILING = 128_000 * 600 // 3600
LONG_REQUEST_TIMEOUT = 900.0

logger = logging.getLogger(__name__)

# Extended thinking is ON by default (owner call 2026-07-23: no user-facing setting —
# most users wouldn't know what a budget is; a per-turn composer control is future work).
# The provider profile's `thinking_budget` remains a hidden override: a number replaces
# the default, 0 disables thinking (where the model allows disabling).
DEFAULT_THINKING_BUDGET = 8192

# API drift (2026): thinking config is MODEL-FAMILY specific.
# - Pre-4.6 models (Haiku 4.5, Sonnet 4.5, Opus 4.5 and older): thinking needs
#   {"type": "enabled", "budget_tokens": N}.
# - 4.6+ and the Claude 5 family (Fable/Mythos 5, Opus 4.8/4.7, Sonnet 5, the 4.6 pair):
#   budget_tokens is deprecated/REMOVED (hard 400 on 4.7+: '"thinking.type.enabled" is
#   not supported for this model') — use {"type": "adaptive"}. Fable 5 thinking is
#   always on and can't be disabled. `display: "summarized"` is required to get trace
#   text on 4.7+ (default "omitted" streams thinking blocks with EMPTY text).
_BUDGET_THINKING_PREFIXES = (
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4-0",
    "claude-sonnet-4-0",
    "claude-3",
    "claude-2",
)


def _uses_budget_thinking(model: str) -> bool:
    return model.startswith(_BUDGET_THINKING_PREFIXES)


# Fable/Mythos 5 run safety classifiers that can decline benign-adjacent requests
# (HTTP 200, stop_reason "refusal", empty or partial content). Recommended posture is
# the server-side fallback: the API re-serves the declined request on Opus 4.8 within
# the same call. Beta header + param, beta messages endpoint.
_FALLBACK_BETA = "server-side-fallback-2026-06-01"
_FALLBACK_MODEL = "claude-opus-4-8"


# DIAGNOSTIC INSTRUMENT, off unless COWORKER_CACHE_DIAGNOSTICS=1 in the environment.
# In one long Fable 5.1 session, one call in six failed to reuse the conversation
# cache and rewrote the whole prompt at 1.25x input — three quarters of the session's cost.
# Replaying those exact requests off-container caches perfectly, so the cause is not the
# content we send and cannot be found from the recorded data. This asks Anthropic directly:
# each request carries the previous response's id, and the reply names the reason the cache
# prefix could not be reused. Never enabled in a normal run — it changes the request.
_CACHE_DIAGNOSTICS_BETA = "cache-diagnosis-2026-04-07"


def _outbound_fingerprints(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Diagnostic only: a short hash per outbound message (plus system and tools), so two
    consecutive requests can be diffed offline to find the first message that changed."""
    import hashlib

    def h(obj: Any) -> str:
        return hashlib.sha1(
            json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:12]

    return {
        "system": h(kwargs.get("system")),
        "tools": h(kwargs.get("tools")),
        "messages": [h(m) for m in kwargs.get("messages") or []],
    }


def _cache_diagnostics_on() -> bool:
    return (os.environ.get("COWORKER_CACHE_DIAGNOSTICS") or "").strip() not in ("", "0")


def _needs_refusal_fallback(model: str) -> bool:
    return model.startswith(("claude-fable", "claude-mythos"))


def _raise_on_refusal(stop_reason: Any, raw: Any) -> None:
    """A refusal that survived the fallback chain becomes a normal provider error —
    the engine persists it as an error notice with Retry, instead of a silent blank."""
    if stop_reason != "refusal":
        return
    details = getattr(raw, "stop_details", None)
    category = getattr(details, "category", None)
    suffix = f" (category: {category})" if category else ""
    raise RuntimeError(
        "Claude's safety filter declined this request"
        + suffix
        + " — try rephrasing, or switch model and press Retry."
    )

# Anthropic stop_reason → the engine's OpenAI-shaped finish_reason vocabulary.
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "refusal": "stop",
    "pause_turn": "stop",
}

# Settings the Messages API accepts; everything else (frequency_penalty, …) is dropped.
_SETTINGS_WHITELIST = {
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
    "stop_sequences",
    "metadata",
    "thinking",
}

# Sampling knobs the API rejects alongside extended thinking (temperature must stay 1).
_THINKING_INCOMPATIBLE = ("temperature", "top_p", "top_k")

_DATA_URL_RE = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)

_PDF_DATA_URL_RE = re.compile(
    r"^data:application/pdf;base64,(.+)$", re.IGNORECASE | re.DOTALL
)


def resolve_api_key(secrets: Any = None) -> Optional[str]:
    """Resolve the Anthropic API key: env `ANTHROPIC_API_KEY` first, else the SecretStore
    `provider:anthropic` profile (`{api_key}`). Same contract as the OpenAI resolver: the
    Tauri-launched sidecar does not inherit the shell env, so Settings-entered keys must work.
    """
    import os

    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    if secrets is not None:
        profile = secrets.get("provider:anthropic") or {}
        return profile.get("api_key") or None
    return None


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


def _image_block(url: str) -> Optional[dict[str, Any]]:
    """An OpenAI `image_url` part → an Anthropic image block. Attachments are always data URLs
    (attachments.py); plain http(s) URLs map to a url source. Anything else → None."""
    match = _DATA_URL_RE.match(url or "")
    if match:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": match.group(1).lower(),
                "data": match.group(2),
            },
        }
    if (url or "").startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _document_block(part: dict[str, Any]) -> Optional[dict[str, Any]]:
    """An OpenAI `file` part (PDF data URL, attachments.py) → an Anthropic document block."""
    file = part.get("file") or {}
    match = _PDF_DATA_URL_RE.match(file.get("file_data") or "")
    if not match:
        return None
    block: dict[str, Any] = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": match.group(1),
        },
    }
    name = file.get("filename")
    if name:
        block["title"] = str(name)
    return block


def _user_blocks(content: Any) -> list[dict[str, Any]]:
    """User content (str or OpenAI parts list) → Anthropic content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            text = part.get("text") or ""
            if text:
                blocks.append({"type": "text", "text": text})
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            block = _image_block(url)
            blocks.append(
                block
                if block
                else {"type": "text", "text": "[unsupported image attachment]"}
            )
        elif kind == "file":
            block = _document_block(part)
            blocks.append(
                block
                if block
                else {"type": "text", "text": "[unsupported file attachment]"}
            )
    return blocks


def convert_messages(
    messages: list[dict[str, Any]],
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """OpenAI-shaped history → (`system`, Anthropic `messages`).

    Leading system messages become the `system` param. Consecutive same-role outputs are folded
    into one message — this is what collapses a run of `role:"tool"` results (one per parallel
    call) into the single user message Anthropic requires, with any steering user text after.
    """
    system_parts: list[str] = []
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        content = messages[index].get("content")
        if isinstance(content, str) and content:
            system_parts.append(content)
        index += 1

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
                        "content": [
                            {"type": "text", "text": f"<system>\n{text}\n</system>"}
                        ],
                    }
                )
        elif role == "user":
            blocks = _user_blocks(message.get("content"))
            if blocks:
                converted.append({"role": "user", "content": blocks})
        elif role == "assistant":
            blocks = []
            # Replay thinking/redacted_thinking blocks VERBATIM, ahead of the turn's own
            # blocks — required whenever the turn's tool calls are being answered.
            blocks.extend((message.get("_anthropic") or {}).get("blocks") or [])
            text = message.get("content")
            if isinstance(text, str) and text:
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": function.get("name") or "",
                        "input": _parse_args(function.get("arguments")),
                    }
                )
            if blocks:
                converted.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.get("tool_call_id") or "",
                            "content": str(message.get("content") or ""),
                        }
                    ],
                }
            )

    folded: list[dict[str, Any]] = []
    for message in converted:
        if folded and folded[-1]["role"] == message["role"]:
            folded[-1]["content"].extend(message["content"])
        else:
            folded.append(message)

    if not folded:
        raise ValueError("no convertible messages for the Anthropic Messages API")
    if folded[0]["role"] != "user":
        folded.insert(
            0, {"role": "user", "content": [{"type": "text", "text": "(continued)"}]}
        )

    return ("\n\n".join(system_parts) or None), folded


def convert_tools(tools: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """OpenAI function schemas → Anthropic tool definitions. Missing description is omitted;
    missing/typeless parameters become an empty object schema (Anthropic requires one).
    """
    converted = []
    for tool in tools or []:
        function = tool.get("function") or {}
        entry: dict[str, Any] = {"name": function.get("name") or ""}
        if function.get("description"):
            entry["description"] = function["description"]
        parameters = function.get("parameters")
        if not isinstance(parameters, dict) or not parameters.get("type"):
            parameters = {"type": "object", "properties": {}}
        entry["input_schema"] = parameters
        converted.append(entry)
    return converted


def _add_cache_breakpoints(kwargs: dict[str, Any]) -> None:
    """Opt the request into prompt caching (5-minute ephemeral, prefix-matched).

    Two breakpoints, the standard agent-loop shape:
    - last system block — caches tools + system together (tools render first);
    - last content block of the final message — caches the whole conversation
      prefix, so each request re-reads the previous turns' cache and writes only
      the new tail (append-only history keeps the prefix byte-identical).

    Outbound-only: the canonical history never carries `cache_control` (the final
    message's blocks are freshly built by convert_messages — thinking replays sit
    in earlier assistant turns — and the marked block is copied, not mutated).
    Prefixes under the model's cacheable minimum silently don't cache; reads bill
    ~0.1x and show up as `cache_read_input_tokens` (the metering's cache_read).
    """
    marker = {"type": "ephemeral"}
    system = kwargs.get("system")
    if isinstance(system, str) and system:
        kwargs["system"] = [{"type": "text", "text": system, "cache_control": marker}]
    messages = kwargs.get("messages") or []
    if messages:
        content = messages[-1].get("content")
        if isinstance(content, list) and content:
            content[-1] = {**content[-1], "cache_control": marker}


def _reasoning_text(thinking_blocks: list[dict[str, Any]]) -> Optional[str]:
    """Display text for the GUI's disclosure — thinking text only (redacted stays opaque)."""
    text = "".join(
        b.get("thinking", "") for b in thinking_blocks if b.get("type") == "thinking"
    )
    return text or None


def _effort_record(plan: Optional[EffortPlan], fallback_note: Optional[str]) -> Optional[dict[str, Any]]:
    if plan is None:
        return None
    if fallback_note:
        return plan.without_param(fallback_note).record()
    return plan.record()


def _anthropic_extras(
    thinking_blocks: list[dict[str, Any]],
    stop_reason: Any = None,
    *,
    cache_diagnostics: Optional[dict[str, Any]] = None,
    outbound_hashes: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """The `_anthropic` sidecar persisted on the assistant message (empty when nothing
    to keep): raw thinking blocks, which convert_messages replays verbatim, and the raw
    `stop_reason` (OPE-173) so values the engine's finish_reason map collapses —
    `pause_turn`, `stop_sequence` → "stop" — stay recoverable from a saved session.
    convert_messages only reads `blocks`; other providers strip the whole key."""
    sidecar: dict[str, Any] = {}
    if thinking_blocks:
        sidecar["blocks"] = thinking_blocks
    if stop_reason:
        sidecar["stop_reason"] = stop_reason
    # Diagnostic only: Anthropic's reason for not reusing the prompt cache on this call,
    # recorded on the message so it can be read back from a finished run.
    if cache_diagnostics:
        sidecar["cache_diagnostics"] = cache_diagnostics
    if outbound_hashes:
        sidecar["outbound_hashes"] = outbound_hashes
    return {"_anthropic": sidecar} if sidecar else {}


class AnthropicProvider(ProviderClient):
    def __init__(
        self,
        client: Any = None,
        *,
        default_model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
        secrets: Any = None,
        thinking_budget: Optional[int] = None,
    ):
        # Diagnostic only (see _cache_diagnostics_on): the id of the previous response, so
        # the next request can ask why the cache prefix was not reused.
        self._last_message_id: Optional[str] = None
        # Mirrors OpenAIProvider: the SDK client is built lazily so engines can be assembled
        # before any key exists; the key resolves at call time (explicit → env → SecretStore).
        # Tests inject a `client` directly. `thinking_budget` (tokens, from the provider
        # profile's optional field) opts every request into extended thinking.
        self._client = client
        self._api_key = api_key
        self._secrets = secrets
        self.default_model = default_model
        self.thinking_budget = thinking_budget or 0
        # Models whose endpoint rejected `output_config.effort` this run (OPE-176): the
        # parameter is not sent to them again; the record says so.
        self._effort_rejected: set[str] = set()

    def _ensure_client(self) -> Any:
        if self._client is None:
            # Lazy import so the SDK is only required when actually talking to Anthropic.
            from anthropic import Anthropic

            key = self._api_key or resolve_api_key(self._secrets)
            if not key:
                raise RuntimeError(
                    "No Anthropic API key configured. Set ANTHROPIC_API_KEY in the environment, "
                    "or add your key in Manage → Configure Models."
                )
            self._client = Anthropic(api_key=key)
        return self._client

    def _request_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
        settings: dict[str, Any],
        effort: Optional[EffortPlan] = None,
    ) -> dict[str, Any]:
        system, converted = convert_messages(messages)
        if "stop" in settings and "stop_sequences" not in settings:
            stop = settings["stop"]
            settings["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
        filtered = {k: v for k, v in settings.items() if k in _SETTINGS_WHITELIST}
        if self.thinking_budget > 0 and "thinking" not in filtered:
            if _uses_budget_thinking(model):
                filtered["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self.thinking_budget,
                }
            else:
                # 4.6+/Claude 5 family: adaptive only (budget_tokens 400s on 4.7+);
                # display opt-in or the trace text arrives empty.
                filtered["thinking"] = {"type": "adaptive", "display": "summarized"}
        if effort is not None and effort.params:
            # OPE-176: a configured level becomes `output_config.effort` (adaptive models)
            # or the thinking budget (budget-mode models); the floor below still applies.
            filtered.update(effort.params)
        thinking = filtered.get("thinking") or {}
        if thinking.get("type") == "enabled":
            # Budget must fit under max_tokens.
            budget = int(thinking.get("budget_tokens") or 0)
            floor = max(DEFAULT_MAX_TOKENS, budget + 4096)
            requested = int(filtered.get("max_tokens") or 0)
            if requested <= budget:
                if requested:
                    # A configured max_output_tokens (OPE-177) that does not clear the
                    # budget would be rejected by the API; say so rather than silently
                    # sending a different ceiling than the one configured.
                    logger.warning(
                        "max_output_tokens=%d is not above the thinking budget (%d); "
                        "sending max_tokens=%d instead",
                        requested,
                        budget,
                        floor,
                    )
                filtered["max_tokens"] = floor
        if thinking.get("type") in ("enabled", "adaptive"):
            # Sampling knobs are rejected alongside thinking (and removed outright on 4.7+).
            for key in _THINKING_INCOMPATIBLE:
                filtered.pop(key, None)
        filtered.setdefault("max_tokens", DEFAULT_MAX_TOKENS)
        kwargs: dict[str, Any] = {"model": model, "messages": converted, **filtered}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = convert_tools(tools)
        _add_cache_breakpoints(kwargs)
        return kwargs

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ) -> AssistantTurn:
        plan = self._effort_plan(model, settings)
        kwargs = self._request_kwargs(
            model=model, messages=messages, tools=tools, settings=settings, effort=plan
        )
        if int(kwargs.get("max_tokens") or 0) > NONSTREAMING_TOKEN_CEILING:
            kwargs.setdefault("timeout", LONG_REQUEST_TIMEOUT)
        client = self._ensure_client()
        # Stream-and-accumulate, not a plain create: the SDK REFUSES non-streaming
        # requests whose max_tokens could exceed ~10 minutes (ValueError before any
        # network I/O). With DEFAULT_MAX_TOKENS=32000 that killed every consumer of
        # the non-streaming path — the auto-approve reviewer errored on ALL rows
        # for every Anthropic model (found by the 2026-08-31 eval run; fail-closed,
        # so verdicts fell back to asking a human). get_final_message() returns the
        # same Message shape create() would.
        def _final(kw: dict[str, Any]) -> Any:
            if _needs_refusal_fallback(model):
                with client.beta.messages.stream(
                    **kw,
                    betas=[_FALLBACK_BETA],
                    fallbacks=[{"model": _FALLBACK_MODEL}],
                ) as stream:
                    return stream.get_final_message()
            with client.messages.stream(**kw) as stream:
                return stream.get_final_message()

        response, kwargs, fallback_note = self._call_with_effort_fallback(
            model, kwargs, _final
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        thinking_blocks: list[dict[str, Any]] = []
        for block in getattr(response, "content", None) or []:
            kind = getattr(block, "type", None)
            if kind == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif kind == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", "") or "",
                        name=getattr(block, "name", "") or "",
                        arguments=dict(getattr(block, "input", None) or {}),
                    )
                )
            elif kind == "thinking":
                thinking_blocks.append(
                    {
                        "type": "thinking",
                        "thinking": getattr(block, "thinking", "") or "",
                        "signature": getattr(block, "signature", "") or "",
                    }
                )
            elif kind == "redacted_thinking":
                thinking_blocks.append(
                    {
                        "type": "redacted_thinking",
                        "data": getattr(block, "data", "") or "",
                    }
                )
        stop_reason = getattr(response, "stop_reason", None)
        _raise_on_refusal(stop_reason, response)
        return AssistantTurn(
            text="".join(text_parts) or None,
            tool_calls=tool_calls,
            finish_reason=_STOP_REASON_MAP.get(stop_reason, stop_reason),
            raw=response,
            reasoning=_reasoning_text(thinking_blocks),
            extras=_anthropic_extras(thinking_blocks, stop_reason),
            usage=_usage_from(getattr(response, "usage", None)),
            output_limit=kwargs.get("max_tokens"),
            effort=_effort_record(plan, fallback_note),
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(model)

    # -- reasoning effort (OPE-176) ---------------------------------------------------

    def _effort_plan(self, model: str, settings: dict[str, Any]) -> Optional[EffortPlan]:
        level = settings.get("reasoning_effort")
        if not level:
            return None
        if model in self._effort_rejected:
            return EffortPlan(
                str(level),
                None,
                {},
                "endpoint rejected output_config.effort earlier in this run; not sent",
            )
        return anthropic_effort(model, str(level), budget_mode=_uses_budget_thinking(model))

    def _call_with_effort_fallback(self, model: str, kwargs: dict[str, Any], fn: Any):
        """Run `fn(kwargs)`; if the endpoint rejects the effort parameter (HTTP 400 naming
        it), drop it, remember the model, and run once more. Returns (result, kwargs
        actually used, fallback note or None). Any other error propagates unchanged."""
        try:
            return fn(kwargs), kwargs, None
        except Exception as exc:
            if "output_config" in kwargs and mentions_effort(exc):
                self._effort_rejected.add(model)
                retry = {k: v for k, v in kwargs.items() if k != "output_config"}
                return (
                    fn(retry),
                    retry,
                    "endpoint rejected output_config.effort; resent without it",
                )
            raise

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ):
        plan = self._effort_plan(model, settings)
        kwargs = self._request_kwargs(
            model=model, messages=messages, tools=tools, settings=settings, effort=plan
        )
        kwargs["stream"] = True
        client = self._ensure_client()
        diagnosing = _cache_diagnostics_on()
        outbound_hashes = _outbound_fingerprints(kwargs) if diagnosing else None

        def _open(kw: dict[str, Any]) -> Any:
            if _needs_refusal_fallback(model):
                extra: dict[str, Any] = {}
                betas = [_FALLBACK_BETA]
                if diagnosing:
                    betas.append(_CACHE_DIAGNOSTICS_BETA)
                    extra["diagnostics"] = {
                        "previous_message_id": self._last_message_id
                    }
                return client.beta.messages.create(
                    **kw,
                    betas=betas,
                    fallbacks=[{"model": _FALLBACK_MODEL}],
                    **extra,
                )
            if diagnosing:
                return client.beta.messages.create(
                    **kw,
                    betas=[_CACHE_DIAGNOSTICS_BETA],
                    diagnostics={"previous_message_id": self._last_message_id},
                )
            return client.messages.create(**kw)

        events, kwargs, fallback_note = self._call_with_effort_fallback(model, kwargs, _open)

        text_parts: list[str] = []
        tool_accum: dict[int, dict[str, str]] = {}
        # Thinking blocks accumulate per stream index and must be replayed verbatim later,
        # so both the text and the signature_delta tail are collected (in block order).
        thinking_accum: dict[int, dict[str, Any]] = {}
        stop_reason = None
        usage: Optional[TokenUsage] = None

        last_message_delta: Any = None
        cache_diagnostics: Optional[dict[str, Any]] = None
        for event in events:
            kind = getattr(event, "type", None)
            if kind == "message_start":
                # Prompt-side counts (input + cache split) ride the opening event.
                started = getattr(event, "message", None)
                usage = _usage_from(getattr(started, "usage", None)) or usage
                if diagnosing and started is not None:
                    self._last_message_id = getattr(started, "id", None)
                    diag = getattr(started, "diagnostics", None)
                    if diag is not None:
                        try:
                            cache_diagnostics = (
                                diag if isinstance(diag, dict) else diag.model_dump()
                            )
                        except Exception:  # noqa: BLE001
                            cache_diagnostics = {"raw": str(diag)}
            elif kind == "content_block_start":
                block = getattr(event, "content_block", None)
                block_kind = getattr(block, "type", None)
                if block_kind == "tool_use":
                    tool_accum[getattr(event, "index", 0)] = {
                        "id": getattr(block, "id", "") or "",
                        "name": getattr(block, "name", "") or "",
                        "json": "",
                    }
                elif block_kind == "thinking":
                    thinking_accum[getattr(event, "index", 0)] = {
                        "type": "thinking",
                        "thinking": getattr(block, "thinking", "") or "",
                        "signature": getattr(block, "signature", "") or "",
                    }
                elif block_kind == "redacted_thinking":
                    # Arrives whole — opaque data, no deltas.
                    thinking_accum[getattr(event, "index", 0)] = {
                        "type": "redacted_thinking",
                        "data": getattr(block, "data", "") or "",
                    }
            elif kind == "content_block_delta":
                delta = getattr(event, "delta", None)
                delta_kind = getattr(delta, "type", None)
                if delta_kind == "text_delta":
                    text = getattr(delta, "text", "") or ""
                    if text:
                        text_parts.append(text)
                        yield StreamChunk(text_delta=text)
                elif delta_kind == "input_json_delta":
                    acc = tool_accum.get(getattr(event, "index", 0))
                    if acc is not None:
                        acc["json"] += getattr(delta, "partial_json", "") or ""
                elif delta_kind == "thinking_delta":
                    acc = thinking_accum.get(getattr(event, "index", 0))
                    thought = getattr(delta, "thinking", "") or ""
                    if acc is not None and thought:
                        acc["thinking"] += thought
                        yield StreamChunk(reasoning_delta=thought)
                elif delta_kind == "signature_delta":
                    acc = thinking_accum.get(getattr(event, "index", 0))
                    if acc is not None:
                        acc["signature"] = (acc.get("signature") or "") + (
                            getattr(delta, "signature", "") or ""
                        )
            elif kind == "message_delta":
                last_message_delta = getattr(event, "delta", None)
                reason = getattr(last_message_delta, "stop_reason", None)
                if reason:
                    stop_reason = reason
                # Final (cumulative) output-token count rides message_delta.usage.
                out = int(
                    getattr(getattr(event, "usage", None), "output_tokens", 0) or 0
                )
                if out:
                    usage = usage or TokenUsage()
                    usage.output = out

        _raise_on_refusal(stop_reason, last_message_delta)
        tool_calls = []
        for index in sorted(tool_accum):
            acc = tool_accum[index]
            tool_calls.append(
                ToolCall(
                    id=acc["id"], name=acc["name"], arguments=_parse_args(acc["json"])
                )
            )
        thinking_blocks = [thinking_accum[i] for i in sorted(thinking_accum)]

        yield StreamChunk(
            turn=AssistantTurn(
                text="".join(text_parts) or None,
                tool_calls=tool_calls,
                finish_reason=_STOP_REASON_MAP.get(stop_reason, stop_reason),
                reasoning=_reasoning_text(thinking_blocks),
                extras=_anthropic_extras(
                    thinking_blocks,
                    stop_reason,
                    cache_diagnostics=cache_diagnostics,
                    outbound_hashes=outbound_hashes,
                ),
                usage=usage,
                output_limit=kwargs.get("max_tokens"),
                effort=_effort_record(plan, fallback_note),
            )
        )
