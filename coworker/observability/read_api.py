"""What the app's screens read (observability §12): turns, a turn's steps, one step in full,
and the session's token numbers. Served by the engine's local web server, and through the
existing machine proxy for remote and cloud machines.

- `turns`: every turn of the session, with its stamp, time buckets (§7.1) and usage (§6.3).
- `turn_steps`: a light list of one turn's steps, in canonical order, without content.
- `step`: one step in full: content, the exchange rebuilt from its pieces, the transcript
  message it points at (cut at 4 KB), the audit rows with its call id, and its child steps.
- `usage`: the session's token numbers for the pill and its popover, and context fill.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from . import calculator
from .capture import rebuild_request

MESSAGE_CUT = 4096


def _light(step: dict[str, Any]) -> dict[str, Any]:
    attrs = {k: v for k, v in (step.get("attrs") or {}).items() if k != "provider_meta"}
    return {**{k: v for k, v in step.items() if k not in ("attrs", "content")}, "attrs": attrs}


def turns(recording: dict[str, Any]) -> list[dict[str, Any]]:
    now = int(time.time() * 1000)
    out = []
    for t in recording.get("turns") or []:
        out.append({
            **t,
            "buckets": calculator.buckets(recording, t, now=now),
            "usage": calculator.usage(recording, turn_id=t["turn_id"]),
        })
    return out


def turn_steps(recording: dict[str, Any], turn_id: str) -> list[dict[str, Any]]:
    return [_light(s) for s in recording.get("steps") or [] if s.get("turn_id") == turn_id]


def step(recording: dict[str, Any], step_id: str, *, get_piece: Callable[[str], Optional[bytes]],
         messages: list[dict[str, Any]], audit_rows: Callable[[str], list[dict[str, Any]]]) -> Optional[dict[str, Any]]:
    steps = recording.get("steps") or []
    found = next((s for s in steps if s.get("step_id") == step_id), None)
    if found is None:
        return None
    out = dict(found)
    exchange = (found.get("content") or {}).get("exchange")
    if exchange:
        out["exchange_rebuilt"] = [_rebuilt(x, get_piece) for x in exchange]
    mid = found.get("message_id")
    if mid:
        message = next((m for m in messages if m.get("mid") == mid), None)
        if message is not None:
            out["message"] = _cut(message)
    out["audit"] = audit_rows(found["call_id"]) if found.get("call_id") else []
    out["children"] = [_light(s) for s in steps if s.get("parent_step_id") == step_id]
    return out


def _rebuilt(entry: dict[str, Any], get_piece: Callable[[str], Optional[bytes]]) -> dict[str, Any]:
    body = get_piece((entry.get("response") or {}).get("body_blob") or "") or b""
    try:
        request = rebuild_request(entry, get_piece)
    except Exception:  # noqa: BLE001 - a piece already swept: show what is left
        request = None
    if isinstance(request, bytes):
        request = request.decode("utf-8", "replace")
    return {"auxiliary": bool(entry.get("auxiliary")), "request": request,
            "response": body.decode("utf-8", "replace")}


def _cut(message: dict[str, Any]) -> dict[str, Any]:
    """The transcript message, its content cut at 4 KB ("Open full result" reads the rest)."""
    content = message.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    cut = len(text) > MESSAGE_CUT
    return {"role": message.get("role"), "mid": message.get("mid"), "tool_call_id": message.get("tool_call_id"),
            "content": text[:MESSAGE_CUT], "cut": cut}


def usage(recording: dict[str, Any], session_id: str) -> dict[str, Any]:
    out = calculator.usage(recording, session_id)
    out["recorded"] = bool(recording.get("turns"))
    out["turns"] = len(recording.get("turns") or [])
    # The popover's parts: input, from cache, output, per model row and in all (OB-4).
    parts: dict[str, dict[str, int]] = {}
    totals = {"input": 0, "cache": 0, "output": 0}
    for s in recording.get("steps") or []:
        a = s.get("attrs") or {}
        u = a.get("usage") or {}
        if s.get("kind") != "model" or not u.get("reported") or a.get("error") or a.get("purpose") == "proxy":
            continue
        p = a.get("purpose", "main")
        key = a.get("model", "?") + ("" if p == "main" else f" ({p})")
        row = parts.setdefault(key, {"input": 0, "cache": 0, "output": 0})
        for target in (row, totals):
            target["input"] += calculator.input_total(u)
            target["cache"] += u.get("cache_read") or 0
            target["output"] += u.get("output") or 0
    out["by_model_parts"] = parts
    out["totals"] = totals
    # Context fill: the last main call's total input against its model's context window (M-R18).
    last = next((s for s in reversed(recording.get("steps") or [])
                 if s.get("kind") == "model" and (s.get("attrs") or {}).get("purpose") == "main"
                 and ((s.get("attrs") or {}).get("usage") or {}).get("reported")), None)
    if last is not None:
        a = last["attrs"]
        out["context"] = {"tokens": calculator.input_total(a["usage"]), "window": a.get("context_window"),
                          "model": a.get("model")}
    else:
        out["context"] = None
    return out
