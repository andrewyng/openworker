"""Wire format between the workspace client and the tool runner. Standard library only.

One JSON-RPC 2.0 object per line, UTF-8. Tool output never goes onto the pipe raw: it
travels inside a JSON string, where a line break is the two characters `\\n`, so a frame
can never contain a real line break however many lines the output has.
"""

from __future__ import annotations

import json
from typing import Any, Optional

PROTOCOL_VERSION = 1

# Live output (`shell.output` notifications) is a view, not the record: the result carries
# the output. A chunk goes out when it reaches this size or this age, whichever is first,
# and live text stops after the budget (progress notifications continue).
LIVE_CHUNK_BYTES = 64 * 1024
LIVE_CHUNK_SECONDS = 0.2
LIVE_BUDGET_BYTES = 1024 * 1024
PROGRESS_SECONDS = 5.0

# One fs.read call returns at most this much.
MAX_READ_BYTES = 1024 * 1024

# The client sends a ping this often; the relay exits after this much silence.
PING_SECONDS = 10.0
RELAY_SILENCE_SECONDS = 45.0

# JSON-RPC error codes. -32xxx are the standard ones; ours start at 1.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SHELL_BUSY = 1
UNKNOWN_SHELL = 2
UNKNOWN_TASK = 3
FS_ERROR = 4
VERSION_MISMATCH = 5
RUNNER_RESTARTED = 6  # raised by the client, never sent by the runner
TOOL_RAISED = 7  # a workspace tool raised; data carries the error's type name


def encode(obj: dict[str, Any]) -> bytes:
    """One frame: compact JSON, one line. `ensure_ascii` keeps every byte printable."""
    return (json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def decode(line: bytes | str) -> Optional[dict[str, Any]]:
    """A frame, or None when the line is not a JSON object (a stray print, a banner).
    The reader drops such lines instead of failing."""
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            return None
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def request(req_id: str, method: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


def notification(method: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params or {}}


def result(req_id: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": value}


def error(req_id: Optional[str], code: int, message: str, data: Optional[dict] = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


class RunnerError(Exception):
    """An error response, as an exception on the client side."""

    def __init__(self, code: int, message: str, data: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}
