"""Durable-data boundary for Browser Use.

Raw browser observations are useful during the active turn but must not enter JSONL,
SQLite audit rows, logs, or telemetry.  Call
``scrub_browser_messages_for_storage`` on a copy of canonical history before saving,
and ``scrub_browser_audit_event`` before appending an audit event.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from .destination import DestinationPolicyError, canonical_origin


BROWSER_OBSERVATION_OMITTED = "[browser observation omitted after turn]"
BROWSER_INPUT_REDACTED = "[redacted browser input]"
BROWSER_VALUE_REDACTED = "[redacted]"

_DURABLE_ARG_KEYS = frozenset(
    {
        "tab_id",
        "snapshot_id",
        "ref",
        "action",
        "direction",
        "button",
        "key",
        "delta_x",
        "delta_y",
        "page",
    }
)
_INPUT_KEYS = frozenset(
    {
        "text",
        "prompt_text",
        "value",
        "values",
        "input",
        "content",
        "body",
        "html",
        "password",
        "secret",
        "token",
        "cookie",
        "cookies",
        "headers",
        "authorization",
        "storage_state",
        "screenshot",
        "image",
        "data",
    }
)
_SAFE_ERROR_CODES = frozenset(
    {
        "ACTION_TIMEOUT",
        "BROWSER_CRASHED",
        "DIALOG_OPEN",
        "DNS_NO_ADDRESSES",
        "DNS_PIN_MISMATCH",
        "DNS_RESOLUTION_FAILED",
        "METADATA_DESTINATION_BLOCKED",
        "NON_PUBLIC_DESTINATION_BLOCKED",
        "PEER_ADDRESS_INVALID",
        "PROFILE_IN_USE",
        "REF_NOT_FOUND",
        "STALE_SNAPSHOT",
        "TAB_NOT_FOUND",
    }
)
_SAFE_ERROR_MESSAGES = {
    "ACTION_TIMEOUT": "Browser action timed out",
    "BROWSER_CRASHED": "Browser process stopped",
    "DIALOG_OPEN": "A browser dialog requires attention",
    "DNS_NO_ADDRESSES": "Destination resolved to no usable addresses",
    "DNS_PIN_MISMATCH": "Destination address changed during connection",
    "DNS_RESOLUTION_FAILED": "Destination DNS resolution failed",
    "METADATA_DESTINATION_BLOCKED": "Cloud metadata destination was blocked",
    "NON_PUBLIC_DESTINATION_BLOCKED": "Private or local destination was blocked",
    "PEER_ADDRESS_INVALID": "Connected destination address was invalid",
    "PROFILE_IN_USE": "Saved browser profile is already in use",
    "REF_NOT_FOUND": "Browser element was not found",
    "STALE_SNAPSHOT": "Browser snapshot is stale",
    "TAB_NOT_FOUND": "Browser tab was not found",
}
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SPECIAL_KEYS = frozenset(
    {
        "alt",
        "arrowdown",
        "arrowleft",
        "arrowright",
        "arrowup",
        "backspace",
        "control",
        "delete",
        "end",
        "enter",
        "escape",
        "home",
        "insert",
        "meta",
        "pagedown",
        "pageup",
        "shift",
        "space",
        "tab",
    }
)


def is_browser_tool(tool_name: Any) -> bool:
    return isinstance(tool_name, str) and tool_name.startswith("browser_")


def scrub_browser_messages_for_storage(
    messages: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return a deep-copied history with all Browser Use payloads removed.

    Tool-call/result pairing and tool names are retained so provider histories remain
    structurally valid.  The caller's live in-memory messages are never mutated.
    """

    copied = [copy.deepcopy(dict(message)) for message in messages]
    browser_call_ids: set[str] = set()
    for message in copied:
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not is_browser_tool(name):
                continue
            call_id = str(call.get("id") or "")
            if call_id:
                browser_call_ids.add(call_id)
            function["arguments"] = _scrub_serialized_arguments(
                name, function.get("arguments")
            )
            # Provider-specific sidecars can replay raw request/response bodies.
            for key in list(call):
                if key not in {"id", "type", "function"}:
                    call.pop(key, None)
        # Strip provider-private replay data on any message that invoked Browser Use.
        if any(
            isinstance(call, dict)
            and isinstance(call.get("function"), dict)
            and is_browser_tool(call["function"].get("name"))
            for call in calls
        ):
            for key in ("extras", "provider_fields", "raw", "thinking"):
                message.pop(key, None)

    for message in copied:
        if (
            message.get("role") == "tool"
            and str(message.get("tool_call_id") or "") in browser_call_ids
        ):
            message["content"] = BROWSER_OBSERVATION_OMITTED
            for key in list(message):
                if key not in {"role", "tool_call_id", "content", "ts"}:
                    message.pop(key, None)
    return copied


def scrub_browser_tool_arguments(
    tool_name: str, arguments: Optional[Mapping[str, Any]]
) -> dict[str, Any]:
    """Keep only non-page identifiers and redacted placeholders."""

    if not is_browser_tool(tool_name) or not isinstance(arguments, Mapping):
        return dict(arguments or {})
    result: dict[str, Any] = {}
    for raw_key, value in arguments.items():
        key = str(raw_key)
        lower = key.casefold()
        if lower == "url" or lower == "origin":
            result[key] = _origin_only(value)
        elif lower in _INPUT_KEYS or any(
            marker in lower
            for marker in ("secret", "token", "cookie", "password", "header")
        ):
            result[key] = BROWSER_INPUT_REDACTED
        elif lower == "key":
            result[key] = _safe_key(value)
        elif lower in _DURABLE_ARG_KEYS:
            result[key] = _safe_identifier_or_scalar(value)
        else:
            # Unknown future fields fail toward redaction.  Browser schemas can opt a
            # harmless field into _DURABLE_ARG_KEYS after review.
            result[key] = BROWSER_VALUE_REDACTED
    return result


def durable_browser_event(
    *,
    tool: str,
    status: str,
    origin: Optional[str] = None,
    title: Optional[str] = None,
    error: Any = None,
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Build the complete durable Browser Use event allowlist."""

    return {
        "tool": str(tool) if is_browser_tool(tool) else "browser_action",
        "origin": _origin_only(origin),
        "title": _safe_title(title),
        "status": _safe_status(status),
        "timestamp": timestamp
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error": redact_browser_error(error),
    }


def scrub_browser_audit_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return an audit-safe copy; non-browser events are left as deep copies."""

    source = copy.deepcopy(dict(event))
    tool = str(source.get("tool") or source.get("tool_name") or "")
    if not is_browser_tool(tool):
        return source
    arguments = source.get("arguments")
    scrubbed_arguments = scrub_browser_tool_arguments(
        tool, arguments if isinstance(arguments, Mapping) else {}
    )
    origin = _origin_from_values(
        [
            source.get("origin"),
            arguments.get("origin") if isinstance(arguments, Mapping) else None,
            arguments.get("url") if isinstance(arguments, Mapping) else None,
            source.get("resource"),
        ]
    )
    # Explicit allowlist: future event fields cannot accidentally become a durable
    # page-content channel.
    return {
        "session_id": _safe_identifier_or_scalar(source.get("session_id")),
        "agent": _safe_identifier_or_scalar(source.get("agent")),
        "workspace": "",  # browser audit does not need a local filesystem path
        "connector": "browser",
        "tool": tool,
        "stage": _safe_identifier_or_scalar(source.get("stage")),
        "status": _safe_status(source.get("status")),
        "approval": _safe_identifier_or_scalar(source.get("approval")),
        "arguments": scrubbed_arguments,
        "origin": origin,
        "title": _safe_title(source.get("title")),
        "resource": origin,
        "result": BROWSER_OBSERVATION_OMITTED,
        "result_preview": BROWSER_OBSERVATION_OMITTED,
        "reason": redact_browser_error(source.get("reason")),
        "timestamp": _safe_identifier_or_scalar(source.get("timestamp")),
    }


def redact_browser_error(error: Any) -> Optional[dict[str, str]]:
    """Reduce an error to a stable public code and non-page-derived message."""

    if error is None or error == "":
        return None
    code = ""
    if isinstance(error, Mapping):
        code = str(error.get("code") or "")
    else:
        match = re.search(r"\b[A-Z][A-Z0-9_]{2,64}\b", str(error))
        code = match.group(0) if match else ""
    code = code if code in _SAFE_ERROR_CODES else "BROWSER_ERROR"
    return {
        "code": code,
        "message": _SAFE_ERROR_MESSAGES.get(code, "Browser action failed"),
    }


def _scrub_serialized_arguments(tool_name: str, raw: Any) -> str:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
    elif isinstance(raw, Mapping):
        parsed = raw
    else:
        parsed = {}
    return json.dumps(
        scrub_browser_tool_arguments(tool_name, parsed),
        separators=(",", ":"),
        sort_keys=True,
    )


def _origin_only(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        return canonical_origin(value).value
    except DestinationPolicyError:
        # Never persist an unparsed path/query merely to aid debugging.
        return ""


def _origin_from_values(values: Iterable[Any]) -> str:
    for value in values:
        origin = _origin_only(value)
        if origin:
            return origin
    return ""


def _safe_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    clean = _CONTROL_PATTERN.sub("", value).replace("\r", " ").replace("\n", " ")
    return " ".join(clean.split())[:200]


def _safe_status(value: Any) -> str:
    normalized = str(value or "").casefold()
    return normalized if normalized in {"started", "succeeded", "failed", "cancelled"} else ""


def _safe_identifier_or_scalar(value: Any) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, str):
        clean = _CONTROL_PATTERN.sub("", value)
        return clean if _OPAQUE_ID_PATTERN.fullmatch(clean) else BROWSER_VALUE_REDACTED
    return BROWSER_VALUE_REDACTED


def _safe_key(value: Any) -> str:
    if not isinstance(value, str):
        return BROWSER_VALUE_REDACTED
    parts = [part.strip().casefold() for part in value.split("+")]
    if not parts or any(part not in _SPECIAL_KEYS for part in parts):
        return BROWSER_VALUE_REDACTED
    return "+".join(parts)
