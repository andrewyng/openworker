"""Provider-facing context projection and request-budget accounting.

The durable transcript is canonical application state.  This module creates a
separate, bounded copy for provider calls; callers must continue to persist the
original transcript.
"""

from __future__ import annotations

import json
import math
import hashlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Collection, Mapping, Optional, Sequence


_OMISSION_MARKER = {
    "role": "system",
    "content": "[Earlier conversation omitted to stay within the model context budget.]",
}
_DISPLAY_SIDECARS = frozenset(
    {"source", "_display", "_steering", "ts", "reasoning", "usage"}
)
_GATEWAY_MAX_REQUEST_BYTES = 2 * 1_024 * 1_024
_GATEWAY_WIRE_HEADROOM_BYTES = 64 * 1_024
_IMAGE_ATTACHMENT_TOKEN_ESTIMATE = 16_000
_PDF_ATTACHMENT_TOKEN_ESTIMATE = 50_000


class ContextBudgetExceeded(ValueError):
    """Raised when protected context cannot fit inside the hard request limits."""


@dataclass(frozen=True)
class _MessageGroup:
    indexed_messages: tuple[tuple[int, dict[str, Any]], ...]
    protected: bool = False
    valid: bool = True


@dataclass(frozen=True)
class ContextBudget:
    """Soft trimming targets and optional transport request limits.

    Direct providers do not share one request-byte ceiling, so the default only
    applies message and model-token budgets. Hosted gateways opt into the explicit
    byte policy via :meth:`gateway`.
    """

    soft_message_limit: int = 220
    hard_message_limit: int = 256
    soft_request_bytes: Optional[int] = None
    hard_request_bytes: Optional[int] = None
    soft_token_limit: Optional[int] = None
    hard_token_limit: Optional[int] = None
    bytes_per_token: float = 4.0
    token_target_ratio: float = 0.85
    reserved_output_tokens: int = 20_000
    historical_file_write_tools: frozenset[str] = frozenset(
        {"write_file", "create_file"}
    )
    historical_file_replace_tools: frozenset[str] = frozenset({"replace_in_file"})
    historical_tool_argument_redaction_bytes: int = 8 * 1_024

    def __post_init__(self) -> None:
        _validate_limits(
            "message",
            self.soft_message_limit,
            self.hard_message_limit,
        )
        if self.soft_request_bytes is not None or self.hard_request_bytes is not None:
            if self.soft_request_bytes is None or self.hard_request_bytes is None:
                raise ValueError(
                    "soft_request_bytes and hard_request_bytes must be configured together"
                )
            _validate_limits(
                "request byte",
                self.soft_request_bytes,
                self.hard_request_bytes,
            )
        if self.soft_token_limit is not None or self.hard_token_limit is not None:
            if self.soft_token_limit is None or self.hard_token_limit is None:
                raise ValueError(
                    "soft_token_limit and hard_token_limit must be configured together"
                )
            _validate_limits(
                "token",
                self.soft_token_limit,
                self.hard_token_limit,
            )
        if self.bytes_per_token <= 0:
            raise ValueError("bytes_per_token must be greater than zero")
        if not 0 < self.token_target_ratio <= 1:
            raise ValueError(
                "token_target_ratio must be greater than zero and at most one"
            )
        if self.reserved_output_tokens < 0:
            raise ValueError("reserved_output_tokens cannot be negative")
        if self.historical_tool_argument_redaction_bytes < 0:
            raise ValueError(
                "historical_tool_argument_redaction_bytes cannot be negative"
            )

    @classmethod
    def gateway(cls, **overrides: Any) -> "ContextBudget":
        """Budget for a 2 MiB JSON gateway, reserving transport-adapter headroom."""

        values = {
            "soft_request_bytes": 1_536 * 1_024,
            "hard_request_bytes": (
                _GATEWAY_MAX_REQUEST_BYTES - _GATEWAY_WIRE_HEADROOM_BYTES
            ),
        }
        values.update(overrides)
        return cls(**values)

    def token_limits(
        self,
        model_context_window: Optional[int],
    ) -> tuple[Optional[int], Optional[int]]:
        """Return the active soft target and hard token ceiling for a model."""

        if model_context_window is not None and model_context_window <= 0:
            raise ValueError("model_context_window must be greater than zero")
        hard_limit = self.hard_token_limit
        if model_context_window is not None:
            hard_limit = (
                model_context_window
                if hard_limit is None
                else min(hard_limit, model_context_window)
            )

        soft_limit = self.soft_token_limit
        if soft_limit is None and model_context_window is not None:
            soft_limit = min(
                int(model_context_window * self.token_target_ratio),
                model_context_window - self.reserved_output_tokens,
            )
            if soft_limit <= 0:
                raise ValueError(
                    "model_context_window must exceed reserved_output_tokens"
                )
        if soft_limit is not None and hard_limit is not None:
            soft_limit = min(soft_limit, hard_limit)
        return soft_limit, hard_limit


@dataclass(frozen=True)
class ContextProjection:
    """A measured provider request projection built from a durable transcript."""

    messages: list[dict[str, Any]]
    message_count: int
    request_bytes: int
    estimated_tokens: int
    soft_token_limit: Optional[int] = None
    hard_token_limit: Optional[int] = None
    omitted_message_count: int = 0
    omitted_group_count: int = 0
    redacted_tool_argument_bytes: int = 0

    @classmethod
    def build(
        cls,
        messages: Sequence[Mapping[str, Any]],
        *,
        budget: ContextBudget,
        model: str,
        model_context_window: Optional[int] = None,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        settings: Optional[Mapping[str, Any]] = None,
        replay_sidecar_keys: Collection[str] = (),
    ) -> "ContextProjection":
        """Copy, trim, and measure messages for one provider request."""

        active_sidecars = frozenset(replay_sidecar_keys)
        steering_indices = {
            index
            for index, message in enumerate(messages)
            if message.get("_steering") is True
        }
        indexed = [
            (index, _normalize_message(message, active_sidecars))
            for index, message in enumerate(messages)
            if message.get("role") != "notice"
        ]
        latest_user_index = next(
            (
                index
                for index, message in reversed(indexed)
                if message.get("role") == "user"
            ),
            None,
        )
        leading_system_indices: set[int] = set()
        for index, message in indexed:
            if message.get("role") != "system":
                break
            leading_system_indices.add(index)
        steered_turn_indices, exact_tool_call_indices = (
            _steered_current_turn_indices(
                indexed,
                steering_indices=steering_indices,
            )
        )
        trailing_tool_result_index = (
            indexed[-1][0] if indexed and indexed[-1][1].get("role") == "tool" else None
        )
        candidate_groups = _atomic_groups(
            indexed,
            protected_indices=leading_system_indices
            | steered_turn_indices
            | {
                index
                for index in (latest_user_index, trailing_tool_result_index)
                if index is not None
            },
        )
        groups = _coalesce_historical_user_turns(
            [group for group in candidate_groups if group.valid],
            latest_user_index=latest_user_index,
        )
        omitted_messages = sum(
            len(group.indexed_messages) for group in candidate_groups if not group.valid
        )
        omitted_groups = sum(not group.valid for group in candidate_groups)
        valid_indexed = [
            indexed_message
            for group in groups
            for indexed_message in group.indexed_messages
        ]
        redacted_tool_argument_bytes = _project_completed_file_tool_arguments(
            valid_indexed,
            write_tool_names=budget.historical_file_write_tools,
            replace_tool_names=budget.historical_file_replace_tools,
            threshold_bytes=budget.historical_tool_argument_redaction_bytes,
            exact_tool_call_indices=exact_tool_call_indices,
        )
        soft_token_limit, hard_token_limit = budget.token_limits(model_context_window)

        while True:
            projected = [
                message for group in groups for _, message in group.indexed_messages
            ]
            if omitted_groups:
                projected.insert(
                    _leading_system_count(projected),
                    dict(_OMISSION_MARKER),
                )
            request_bytes, estimated_tokens = _measure(
                projected,
                budget=budget,
                model=model,
                tools=tools,
                settings=settings,
            )
            if not _over_soft_budget(
                message_count=len(projected),
                request_bytes=request_bytes,
                estimated_tokens=estimated_tokens,
                budget=budget,
                soft_token_limit=soft_token_limit,
            ):
                break
            removable = next(
                (
                    position
                    for position, group in enumerate(groups)
                    if not group.protected
                ),
                None,
            )
            if removable is None:
                break
            removed = groups.pop(removable)
            omitted_messages += len(removed.indexed_messages)
            omitted_groups += 1

        if _over_hard_budget(
            message_count=len(projected),
            request_bytes=request_bytes,
            estimated_tokens=estimated_tokens,
            budget=budget,
            hard_token_limit=hard_token_limit,
        ):
            raise ContextBudgetExceeded(
                "protected context exceeds the configured hard request limits"
            )

        return cls(
            messages=projected,
            message_count=len(projected),
            request_bytes=request_bytes,
            estimated_tokens=estimated_tokens,
            soft_token_limit=soft_token_limit,
            hard_token_limit=hard_token_limit,
            omitted_message_count=omitted_messages,
            omitted_group_count=omitted_groups,
            redacted_tool_argument_bytes=redacted_tool_argument_bytes,
        )


def _validate_limits(kind: str, soft: int, hard: int) -> None:
    if soft <= 0 or hard <= 0:
        raise ValueError(f"{kind} limits must be greater than zero")
    if soft > hard:
        raise ValueError(f"soft {kind} limit cannot exceed hard limit")


def _normalize_message(
    message: Mapping[str, Any],
    replay_sidecar_keys: frozenset[str],
) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in message.items()
        if key not in _DISPLAY_SIDECARS
        and (not key.startswith("_") or key in replay_sidecar_keys)
    }


def _project_completed_file_tool_arguments(
    indexed_messages: Sequence[tuple[int, dict[str, Any]]],
    *,
    write_tool_names: frozenset[str],
    replace_tool_names: frozenset[str],
    threshold_bytes: int,
    exact_tool_call_indices: frozenset[int] = frozenset(),
) -> int:
    redacted_bytes = 0
    for position, (message_index, message) in enumerate(indexed_messages):
        if message.get("role") != "assistant":
            continue
        # Native Anthropic/Gemini history carries signed replay state. Rewriting
        # the associated function-call arguments invalidates that provider-owned
        # turn, so keep signed exchanges exact.
        if (
            message_index in exact_tool_call_indices
            or "_anthropic" in message
            or "_gemini" in message
        ):
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        call_ids = {
            call.get("id")
            for call in tool_calls
            if isinstance(call, Mapping) and call.get("id")
        }
        result_ids: set[str] = set()
        cursor = position + 1
        while cursor < len(indexed_messages):
            _, result = indexed_messages[cursor]
            if (
                result.get("role") != "tool"
                or result.get("tool_call_id") not in call_ids
            ):
                break
            result_ids.add(result["tool_call_id"])
            cursor += 1
        # The just-completed exchange must remain exact for its immediate
        # tool-result follow-up sampling call.
        if cursor == len(indexed_messages):
            continue
        for call in tool_calls:
            if not isinstance(call, dict) or call.get("id") not in result_ids:
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            tool_name = function.get("name")
            if tool_name in write_tool_names:
                content_keys = ("content",)
            elif tool_name in replace_tool_names:
                content_keys = ("old", "new")
            else:
                continue
            raw_arguments = function.get("arguments")
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except (TypeError, ValueError):
                    continue
            elif isinstance(raw_arguments, Mapping):
                arguments = dict(raw_arguments)
            else:
                continue
            if not isinstance(arguments, dict):
                continue
            contents = {
                key: arguments[key]
                for key in content_keys
                if isinstance(arguments.get(key), str)
            }
            encoded_contents = {
                key: content.encode("utf-8") for key, content in contents.items()
            }
            content_size = sum(len(content) for content in encoded_contents.values())
            if content_size <= threshold_bytes:
                continue

            for key, content_bytes in encoded_contents.items():
                arguments.pop(key)
                arguments[f"{key}_bytes"] = len(content_bytes)
                arguments[f"{key}_sha256"] = hashlib.sha256(content_bytes).hexdigest()
            arguments["context_note"] = (
                "Full file edit text was omitted from historical context; "
                "use read_file on the saved path if needed."
            )
            function["arguments"] = (
                json.dumps(
                    arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if isinstance(raw_arguments, str)
                else arguments
            )
            redacted_bytes += content_size
    return redacted_bytes


def _steered_current_turn_indices(
    indexed_messages: Sequence[tuple[int, dict[str, Any]]],
    *,
    steering_indices: set[int],
) -> tuple[set[int], frozenset[int]]:
    """Protect an active tool exchange when queued steering follows its results.

    Steering is persisted as one or more user messages immediately after a completed
    tool-result run.  That means the literal tail is no longer a tool result, but the
    next provider call is still the immediate follow-up for that exchange.  Preserve
    the active turn's user inputs and latest exchange, and keep its signed/write
    arguments exact.
    """

    suffix_start = len(indexed_messages)
    while (
        suffix_start > 0
        and indexed_messages[suffix_start - 1][1].get("role") == "user"
        and indexed_messages[suffix_start - 1][0] in steering_indices
    ):
        suffix_start -= 1
    if suffix_start == len(indexed_messages) or suffix_start == 0:
        return set(), frozenset()

    result_start = suffix_start
    while (
        result_start > 0
        and indexed_messages[result_start - 1][1].get("role") == "tool"
    ):
        result_start -= 1
    assistant_position = result_start - 1
    if assistant_position < 0:
        return set(), frozenset()

    assistant_index, assistant = indexed_messages[assistant_position]
    tool_calls = (
        assistant.get("tool_calls")
        if assistant.get("role") == "assistant"
        else None
    )
    if not isinstance(tool_calls, list) or not tool_calls:
        return set(), frozenset()

    call_id_list = [
        call.get("id")
        for call in tool_calls
        if isinstance(call, Mapping) and call.get("id")
    ]
    call_ids = set(call_id_list)
    result_ids = [
        message.get("tool_call_id")
        for _, message in indexed_messages[result_start:suffix_start]
    ]
    if not (
        len(call_id_list) == len(tool_calls)
        and len(call_ids) == len(call_id_list)
        and len(result_ids) == len(call_id_list)
        and set(result_ids) == call_ids
    ):
        return set(), frozenset()

    # The active turn starts after the last completed assistant answer. Preserve all
    # user inputs in it (the original prompt plus any earlier steering), while the
    # latest valid tool exchange is protected atomically below.
    turn_boundary = -1
    for position in range(assistant_position - 1, -1, -1):
        message = indexed_messages[position][1]
        prior_calls = (
            message.get("tool_calls")
            if message.get("role") == "assistant"
            else None
        )
        if message.get("role") == "assistant" and not prior_calls:
            turn_boundary = position
            break

    protected = {
        index
        for index, message in indexed_messages[
            turn_boundary + 1 : assistant_position
        ]
        if message.get("role") == "user"
    }
    protected.update(
        index
        for index, _ in indexed_messages[assistant_position:suffix_start]
    )
    protected.update(index for index, _ in indexed_messages[suffix_start:])
    return protected, frozenset({assistant_index})


def _leading_system_count(messages: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for message in messages:
        if message.get("role") != "system":
            break
        count += 1
    return count


def _atomic_groups(
    indexed_messages: Sequence[tuple[int, dict[str, Any]]],
    *,
    protected_indices: set[int],
) -> list[_MessageGroup]:
    groups: list[_MessageGroup] = []
    position = 0
    while position < len(indexed_messages):
        index, message = indexed_messages[position]
        grouped = [(index, message)]
        valid = True
        tool_calls = (
            message.get("tool_calls") if message.get("role") == "assistant" else None
        )
        if isinstance(tool_calls, list) and tool_calls:
            call_id_list = [
                call.get("id")
                for call in tool_calls
                if isinstance(call, Mapping) and call.get("id")
            ]
            call_ids = set(call_id_list)
            result_ids: list[str] = []
            cursor = position + 1
            while cursor < len(indexed_messages):
                result_index, result = indexed_messages[cursor]
                if result.get("role") != "tool":
                    break
                grouped.append((result_index, result))
                result_ids.append(result.get("tool_call_id"))
                cursor += 1
            valid = (
                len(call_id_list) == len(tool_calls)
                and len(call_ids) == len(call_id_list)
                and len(result_ids) == len(call_id_list)
                and set(result_ids) == call_ids
            )
            position = cursor
        else:
            valid = message.get("role") != "tool"
            position += 1

        groups.append(
            _MessageGroup(
                indexed_messages=tuple(grouped),
                protected=any(
                    item_index in protected_indices for item_index, _ in grouped
                ),
                valid=valid,
            )
        )
    return groups


def _coalesce_historical_user_turns(
    groups: Sequence[_MessageGroup],
    *,
    latest_user_index: Optional[int],
) -> list[_MessageGroup]:
    """Keep an old user prompt and every response to it in one removable unit.

    The active turn is intentionally left as smaller API-round groups: its latest user
    message and trailing tool exchange are protected independently, while old intermediate
    tool rounds may still be shed if one unusually long turn approaches a hard limit.
    """

    coalesced: list[_MessageGroup] = []
    position = 0
    while position < len(groups):
        group = groups[position]
        user_indices = [
            index
            for index, message in group.indexed_messages
            if message.get("role") == "user"
        ]
        if not user_indices or latest_user_index in user_indices:
            coalesced.append(group)
            position += 1
            continue

        combined = list(group.indexed_messages)
        protected = group.protected
        position += 1
        while position < len(groups):
            following = groups[position]
            if any(
                message.get("role") == "user"
                for _, message in following.indexed_messages
            ):
                break
            combined.extend(following.indexed_messages)
            protected = protected or following.protected
            position += 1
        coalesced.append(
            _MessageGroup(
                indexed_messages=tuple(combined),
                protected=protected,
            )
        )
    return coalesced


def _over_soft_budget(
    *,
    message_count: int,
    request_bytes: int,
    estimated_tokens: int,
    budget: ContextBudget,
    soft_token_limit: Optional[int],
) -> bool:
    return (
        message_count > budget.soft_message_limit
        or (
            budget.soft_request_bytes is not None
            and request_bytes > budget.soft_request_bytes
        )
        or (soft_token_limit is not None and estimated_tokens > soft_token_limit)
    )


def _over_hard_budget(
    *,
    message_count: int,
    request_bytes: int,
    estimated_tokens: int,
    budget: ContextBudget,
    hard_token_limit: Optional[int],
) -> bool:
    return (
        message_count > budget.hard_message_limit
        or (
            budget.hard_request_bytes is not None
            and request_bytes > budget.hard_request_bytes
        )
        or (hard_token_limit is not None and estimated_tokens > hard_token_limit)
    )


def _request_payload(
    *,
    messages: Sequence[Mapping[str, Any]],
    model: str,
    tools: Optional[Sequence[Mapping[str, Any]]],
    settings: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "messages": list(messages)}
    if tools is not None:
        payload["tools"] = list(tools)
    for key, value in (settings or {}).items():
        if key not in {"model", "messages", "tools"}:
            payload[key] = value
    return payload


def _measure(
    messages: Sequence[Mapping[str, Any]],
    *,
    budget: ContextBudget,
    model: str,
    tools: Optional[Sequence[Mapping[str, Any]]],
    settings: Optional[Mapping[str, Any]],
) -> tuple[int, int]:
    payload = _request_payload(
        messages=messages,
        model=model,
        tools=tools,
        settings=settings,
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    request_bytes = len(encoded)
    token_payload, attachment_tokens = _token_projection(payload)
    token_bytes = len(
        json.dumps(
            token_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    return (
        request_bytes,
        math.ceil(token_bytes / budget.bytes_per_token) + attachment_tokens,
    )


def _token_projection(value: Any) -> tuple[Any, int]:
    """Replace inline binary transport data before estimating language tokens.

    Image/PDF base64 bytes count toward an HTTP body but providers meter those
    modalities independently from text. Treating every four base64 bytes as a
    language token falsely rejects otherwise-supported direct-provider uploads.
    The fixed allowances are deliberately generous heuristics; actual usage from
    the provider remains authoritative.
    """

    if isinstance(value, Mapping):
        projected = dict(value)
        kind = value.get("type")
        attachment_tokens = 0

        if kind in {"image_url", "input_image"}:
            image = value.get("image_url")
            if isinstance(image, Mapping):
                url = image.get("url")
                if _is_inline_binary(url):
                    projected["image_url"] = {
                        **image,
                        "url": "[inline image attachment]",
                    }
                    attachment_tokens += _IMAGE_ATTACHMENT_TOKEN_ESTIMATE
            elif _is_inline_binary(image):
                projected["image_url"] = "[inline image attachment]"
                attachment_tokens += _IMAGE_ATTACHMENT_TOKEN_ESTIMATE

        if kind in {"file", "input_file"}:
            file_value = value.get("file")
            if isinstance(file_value, Mapping):
                file_data = file_value.get("file_data")
                if _is_inline_binary(file_data):
                    projected["file"] = {
                        **file_value,
                        "file_data": "[inline PDF attachment]",
                    }
                    attachment_tokens += _PDF_ATTACHMENT_TOKEN_ESTIMATE
            else:
                file_data = value.get("file_data")
                if _is_inline_binary(file_data):
                    projected["file_data"] = "[inline PDF attachment]"
                    attachment_tokens += _PDF_ATTACHMENT_TOKEN_ESTIMATE

        for key, child in tuple(projected.items()):
            child_projection, child_tokens = _token_projection(child)
            projected[key] = child_projection
            attachment_tokens += child_tokens
        return projected, attachment_tokens

    if isinstance(value, (list, tuple)):
        projected_items = []
        attachment_tokens = 0
        for child in value:
            child_projection, child_tokens = _token_projection(child)
            projected_items.append(child_projection)
            attachment_tokens += child_tokens
        return projected_items, attachment_tokens

    return value, 0


def _is_inline_binary(value: Any) -> bool:
    return isinstance(value, str) and (value.startswith("data:") or len(value) > 1_024)
