"""Bound every tool result before it enters the conversation (OPE-186, change 1).

A tool result is re-sent to the model on every later turn, so one oversized result taxes
the whole rest of the session. The shell tool used to keep the LAST 20,000 characters of
its output and drop the beginning; every other tool was unbounded (a `read_file` of a
486,000-character file was seen in one long session).

Rule, applied in one place for all tools (`TurnEngine._record_result`): if the result, as
it would be serialised into the tool message, exceeds `max_bytes`, the full text is written
to a spill file and the oversized field is replaced by its head, a marker naming the file
and the omitted byte count, and its tail. Head-plus-tail at 10,000 bytes is a common shape;
another is to keep the full output on disk and hand the model only the path.
Head AND tail because a build log needs both its first error and its final verdict.

The marker carries no timestamp, so a bounded result is byte-identical on every later
request and never disturbs the provider's prompt cache. Structured results stay valid
JSON: only the largest string field(s) are bounded, everything else is untouched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

DEFAULT_TOOL_RESULT_MAX_BYTES = 10_000
# Bytes set aside for the marker line and JSON escaping when sizing head + tail.
_MARKER_RESERVE = 400
# Never shrink a field below this many bytes of head + tail, whatever the budget says.
_MIN_KEEP = 1_000
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def serialize_result(result: Any) -> str:
    """Exactly what `_tool_result_message` puts in the message content."""
    return result if isinstance(result, str) else json.dumps(result, default=str)


def _nbytes(text: str) -> int:
    return len(text.encode("utf-8"))


def head_tail(text: str, keep_bytes: int, *, spill_path: Optional[Path], total_bytes: int) -> str:
    """First half of `keep_bytes`, a marker, last half. Cuts are byte-based and decoded
    with errors ignored so a multi-byte character split at the boundary is dropped, never
    corrupted."""
    keep = max(int(keep_bytes), _MIN_KEEP)
    raw = text.encode("utf-8")
    half = keep // 2
    head = raw[:half].decode("utf-8", errors="ignore")
    tail = raw[-half:].decode("utf-8", errors="ignore") if half else ""
    omitted = max(0, len(raw) - _nbytes(head) - _nbytes(tail))
    where = (
        f"full text saved to {spill_path} (read it with read_file, or "
        f"run_shell: sed -n '1,200p' \"{spill_path}\")"
        if spill_path is not None
        else "full text not saved"
    )
    marker = f"\n[... {omitted} bytes omitted here; this result was {total_bytes} bytes; {where} ...]\n"
    return head + marker + tail


def bound_tool_result(
    result: Any,
    *,
    max_bytes: Optional[int],
    spill_dir: Optional[Path],
    step: int,
    tool_name: str,
) -> Any:
    """Return `result` unchanged when it fits, else a bounded copy. `max_bytes` None or
    <= 0 disables bounding. Spill files are written only when `spill_dir` is given."""
    if not max_bytes or max_bytes <= 0:
        return result
    text = serialize_result(result)
    if _nbytes(text) <= max_bytes:
        return result

    safe_tool = _SAFE_NAME.sub("_", tool_name or "tool")[:40] or "tool"

    def spill(name: str, payload: str) -> Optional[Path]:
        if spill_dir is None:
            return None
        try:
            spill_dir.mkdir(parents=True, exist_ok=True)
            path = spill_dir / name
            path.write_text(payload, encoding="utf-8", errors="replace")
            return path
        except OSError:
            return None

    if isinstance(result, dict):
        out: dict[str, Any] = dict(result)
        # Bound the largest string field, re-measure, repeat for the next largest if the
        # whole message is still over budget (a result can carry two big fields).
        for _ in range(4):
            key = max(
                (k for k, v in out.items() if isinstance(v, str)),
                key=lambda k: _nbytes(out[k]),
                default=None,
            )
            if key is None or _nbytes(out[key]) < _MIN_KEEP:
                break
            others = _nbytes(serialize_result({k: v for k, v in out.items() if k != key}))
            budget = max_bytes - others - _MARKER_RESERVE
            original = out[key]
            path = spill(f"{step:04d}-{safe_tool}-{_SAFE_NAME.sub('_', key)[:30]}.txt", original)
            bounded = head_tail(original, budget, spill_path=path, total_bytes=_nbytes(original))
            # JSON escaping (newlines, quotes) grows the serialised size; tighten once.
            over = _nbytes(serialize_result({**out, key: bounded})) - max_bytes
            if over > 0:
                bounded = head_tail(original, budget - over, spill_path=path, total_bytes=_nbytes(original))
            out[key] = bounded
            if _nbytes(serialize_result(out)) <= max_bytes:
                break
        return out

    path = spill(f"{step:04d}-{safe_tool}.txt", text)
    return head_tail(text, max_bytes - _MARKER_RESERVE, spill_path=path, total_bytes=_nbytes(text))
