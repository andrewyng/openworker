"""Wire contract and validation for the external-browser extension bridge.

The bridge intentionally exposes a small, closed command set.  In particular,
there is no remote ``attach`` command: attaching a tab must originate from a
user gesture in the extension popup.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


PROTOCOL_VERSION = 1
SUPPORTED_COMMANDS = frozenset(
    {
        "tabs",
        "snapshot",
        "inspect",
        "screenshot",
        "click",
        "fill",
        "keypress",
        "scroll",
    }
)
SUPPORTED_EVENTS = frozenset(
    {
        "heartbeat",
        "tab_claimed",
        "tab_released",
        "tab_navigated",
        "debugger_detached",
    }
)

MAX_COMMAND_BYTES = 512 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 128 * 1024


class ProtocolValidationError(ValueError):
    """Raised when an extension protocol payload is malformed or unsafe."""

    def __init__(self, message: str, *, code: str = "INVALID_PAYLOAD") -> None:
        super().__init__(message)
        self.code = code


def _json_size(value: Any, *, limit: int, label: str) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError(f"{label} must be JSON serializable") from exc
    if len(encoded) > limit:
        raise ProtocolValidationError(
            f"{label} exceeds the {limit}-byte limit",
            code="PAYLOAD_TOO_LARGE",
        )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolValidationError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ProtocolValidationError(f"{label} keys must be strings")
    return dict(value)


def _only_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = required.difference(value)
    if missing:
        raise ProtocolValidationError(
            f"missing required fields: {', '.join(sorted(missing))}"
        )
    unknown = set(value).difference(required | optional)
    if unknown:
        raise ProtocolValidationError(
            f"unsupported fields: {', '.join(sorted(unknown))}"
        )


def _tab_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolValidationError("tab_id must be a non-negative integer")
    return value


def _nonempty_string(value: Any, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolValidationError(f"{label} must be a non-empty string")
    if len(value) > maximum:
        raise ProtocolValidationError(f"{label} is too long")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolValidationError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ProtocolValidationError(f"{label} must be finite")
    return number


def validate_command(command: str, params: Any) -> dict[str, Any]:
    """Validate and normalize an outbound command.

    This is also the security boundary that prevents the local service from
    being used as an arbitrary Chrome DevTools Protocol relay.
    """

    if command not in SUPPORTED_COMMANDS:
        raise ProtocolValidationError(
            f"unsupported browser command: {command}", code="UNSUPPORTED_COMMAND"
        )
    data = _object(params, "params")

    if command == "tabs":
        _only_keys(data, required=set())
    elif command in {"snapshot", "screenshot"}:
        optional = (
            {"full_page", "format", "quality"}
            if command == "screenshot"
            else set()
        )
        _only_keys(data, required={"tab_id"}, optional=optional)
        data["tab_id"] = _tab_id(data["tab_id"])
        if command == "screenshot":
            if "full_page" in data and not isinstance(data["full_page"], bool):
                raise ProtocolValidationError("full_page must be a boolean")
            if "format" in data and data["format"] not in {"png", "jpeg"}:
                raise ProtocolValidationError("format must be png or jpeg")
            if "quality" in data:
                quality = data["quality"]
                if (
                    isinstance(quality, bool)
                    or not isinstance(quality, int)
                    or not 0 <= quality <= 100
                ):
                    raise ProtocolValidationError("quality must be an integer from 0 to 100")
    elif command == "inspect":
        _only_keys(
            data,
            required={"tab_id", "snapshot_id", "ref", "action"},
            optional={"key"},
        )
        data["tab_id"] = _tab_id(data["tab_id"])
        data["snapshot_id"] = _nonempty_string(
            data["snapshot_id"], "snapshot_id"
        )
        data["ref"] = _nonempty_string(data["ref"], "ref", maximum=128)
        if data["action"] not in {
            "browser_click",
            "browser_fill",
            "browser_press",
        }:
            raise ProtocolValidationError("inspect action is unsupported")
        if data["action"] == "browser_press":
            data["key"] = _nonempty_string(data.get("key"), "key", maximum=128)
        elif "key" in data:
            raise ProtocolValidationError("key is only valid for browser_press")
    elif command in {"click", "fill"}:
        required = {"tab_id", "snapshot_id", "ref"}
        if command == "fill":
            required.add("text")
        _only_keys(data, required=required, optional={"confirmation_token"})
        data["tab_id"] = _tab_id(data["tab_id"])
        data["snapshot_id"] = _nonempty_string(data["snapshot_id"], "snapshot_id")
        data["ref"] = _nonempty_string(data["ref"], "ref", maximum=128)
        if command == "fill":
            if not isinstance(data["text"], str):
                raise ProtocolValidationError("text must be a string")
            if len(data["text"]) > 128 * 1024:
                raise ProtocolValidationError("text is too long")
        if "confirmation_token" in data:
            data["confirmation_token"] = _nonempty_string(
                data["confirmation_token"], "confirmation_token", maximum=128
            )
    elif command == "keypress":
        _only_keys(
            data,
            required={"tab_id", "snapshot_id", "ref", "key"},
            optional={"confirmation_token"},
        )
        data["tab_id"] = _tab_id(data["tab_id"])
        data["snapshot_id"] = _nonempty_string(
            data["snapshot_id"], "snapshot_id"
        )
        data["ref"] = _nonempty_string(data["ref"], "ref", maximum=128)
        data["key"] = _nonempty_string(data["key"], "key", maximum=128)
        if "confirmation_token" in data:
            data["confirmation_token"] = _nonempty_string(
                data["confirmation_token"], "confirmation_token", maximum=128
            )
    elif command == "scroll":
        _only_keys(
            data,
            required={"tab_id"},
            optional={
                "delta_x",
                "delta_y",
                "x",
                "y",
                "snapshot_id",
                "ref",
            },
        )
        data["tab_id"] = _tab_id(data["tab_id"])
        if "delta_x" not in data and "delta_y" not in data:
            raise ProtocolValidationError("scroll needs delta_x or delta_y")
        has_snapshot = "snapshot_id" in data
        has_ref = "ref" in data
        if has_snapshot != has_ref:
            raise ProtocolValidationError(
                "scroll snapshot_id and ref must be supplied together"
            )
        if has_snapshot:
            data["snapshot_id"] = _nonempty_string(
                data["snapshot_id"], "snapshot_id"
            )
            data["ref"] = _nonempty_string(
                data["ref"], "ref", maximum=128
            )
        for field in ("delta_x", "delta_y", "x", "y"):
            if field in data:
                data[field] = _finite_number(data[field], field)

    _json_size({"command": command, "params": data}, limit=MAX_COMMAND_BYTES, label="command")
    return data


def validate_result_payload(payload: Any) -> dict[str, Any]:
    data = _object(payload, "result")
    _json_size(data, limit=MAX_RESULT_BYTES, label="result")
    return data


def validate_error_payload(error: Any) -> dict[str, Any]:
    data = _object(error, "error")
    _only_keys(data, required={"code", "message"}, optional={"retryable"})
    code = _nonempty_string(data["code"], "error.code", maximum=128)
    message = _nonempty_string(data["message"], "error.message", maximum=4096)
    retryable = data.get("retryable", False)
    if not isinstance(retryable, bool):
        raise ProtocolValidationError("error.retryable must be a boolean")
    normalized: dict[str, Any] = {"code": code, "message": message}
    if retryable:
        normalized["retryable"] = True
    _json_size(normalized, limit=MAX_EVENT_BYTES, label="error")
    return normalized


def validate_event(event: Any) -> dict[str, Any]:
    data = _object(event, "event")
    event_type = data.get("type")
    if event_type not in SUPPORTED_EVENTS:
        raise ProtocolValidationError(
            f"unsupported browser event: {event_type}", code="UNSUPPORTED_EVENT"
        )
    if event_type != "heartbeat":
        data["tab_id"] = _tab_id(data.get("tab_id"))
    _json_size(data, limit=MAX_EVENT_BYTES, label="event")
    return data


def targeted_tab_id(command: str, params: Mapping[str, Any]) -> int | None:
    if command == "tabs":
        return None
    value = params.get("tab_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None
