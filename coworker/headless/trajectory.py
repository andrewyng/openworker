"""Build an ATIF (Agent Trajectory Interchange Format) trajectory from an OpenWorker
conversation, so a headless run's record can be read by tools that consume that format.

Written against ATIF-v1.7:

- steps are numbered from 1, sequentially;
- `source` is "user", "agent", or "system"; model_name / tool_calls / metrics only on
  agent steps;
- an observation result's `source_call_id` must name a tool call in the SAME step;
- an agent step with llm_call_count == 0 may not carry metrics.

OpenWorker persists one message per model round-trip (role "assistant", with
`tool_calls` in OpenAI form and a `usage` sidecar) followed by one "tool" message per
tool call, so the mapping is: assistant message -> agent step, following tool messages ->
that step's observation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .pricing import estimate_cost

SCHEMA_VERSION = "ATIF-v1.7"


def _iso(ts: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        from ..attachments import content_to_text

        return content_to_text(content)
    except Exception:  # noqa: BLE001 - never fail the trajectory over a content part
        return json.dumps(content, default=str)


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"_raw": str(raw)}
    return parsed if isinstance(parsed, dict) else {"_value": parsed}


def build_trajectory(
    messages: list[dict[str, Any]],
    *,
    agent_name: str,
    agent_version: str,
    model_name: str,
    session_id: str,
    outcome: str,
    prices: Optional[dict] = None,
    agent_extra: Optional[dict[str, Any]] = None,
    reasoning_effort: Optional[str] = None,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    pending: Optional[dict[str, Any]] = None  # the last agent step, for observations
    totals = {"prompt": 0, "completion": 0, "cached": 0}
    cost_total = 0.0
    cost_known = False

    for msg in messages:
        role = msg.get("role")
        ts = _iso(msg.get("ts"))

        if role == "user":
            pending = None
            steps.append({"source": "user", "message": _text(msg.get("content")), "timestamp": ts})
            continue

        if role == "assistant":
            step: dict[str, Any] = {
                "source": "agent",
                "message": _text(msg.get("content")),
                "timestamp": ts,
                "llm_call_count": 1,
            }
            usage = msg.get("usage") or {}
            step["model_name"] = str(usage.get("model") or model_name)
            if reasoning_effort:
                step["reasoning_effort"] = reasoning_effort  # ATIF: agent steps only
            if msg.get("reasoning"):
                step["reasoning_content"] = str(msg["reasoning"])
            calls = msg.get("tool_calls") or []
            if calls:
                step["tool_calls"] = [
                    {
                        "tool_call_id": str(tc.get("id")),
                        "function_name": str((tc.get("function") or {}).get("name", "")),
                        "arguments": _parse_args((tc.get("function") or {}).get("arguments")),
                    }
                    for tc in calls
                ]
            if usage:
                prompt = (
                    int(usage.get("input", 0) or 0)
                    + int(usage.get("cache_read", 0) or 0)
                    + int(usage.get("cache_write", 0) or 0)
                )
                completion = int(usage.get("output", 0) or 0)
                cached = int(usage.get("cache_read", 0) or 0)
                metrics: dict[str, Any] = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "cached_tokens": cached,
                }
                # The four raw counts and the serving host travel too, so a consumer can
                # price the call itself (cache reads and writes are billed differently,
                # and a router's upstream host decides which rate applies).
                extra = {
                    k: int(usage.get(k, 0) or 0)
                    for k in ("input", "output", "cache_read", "cache_write")
                }
                if msg.get("served_by"):
                    extra["served_by"] = str(msg["served_by"])
                metrics["extra"] = extra
                cost = estimate_cost(step["model_name"], usage, prices)
                if cost is not None:
                    metrics["cost_usd"] = cost
                    cost_total += cost
                    cost_known = True
                step["metrics"] = metrics
                totals["prompt"] += prompt
                totals["completion"] += completion
                totals["cached"] += cached
            steps.append(step)
            pending = step
            continue

        if role == "tool":
            result = {"content": _text(msg.get("content"))}
            call_id = msg.get("tool_call_id")
            if pending is not None and any(
                tc["tool_call_id"] == call_id for tc in pending.get("tool_calls") or []
            ):
                result["source_call_id"] = str(call_id)
                pending.setdefault("observation", {"results": []})["results"].append(result)
            else:
                # Orphan tool output (should not happen); keep it as a system step.
                steps.append({"source": "system", "message": result["content"], "timestamp": ts})
            continue

        # Notices, system prompts, and anything else become system steps. Engine notices
        # carry their wording in `text` (not `content`); a compacted notice also carries
        # the compaction record, so the trajectory shows what the model's memory was
        # replaced with, not just that it was.
        text = (
            _text(msg.get("content"))
            or str(msg.get("text") or "")
            or str(msg.get("kind") or role or "")
        )
        compaction = msg.get("compaction") if role == "notice" else None
        if isinstance(compaction, dict):
            summary = str(compaction.get("summary_text") or "")
            working = str(compaction.get("working_state") or "")
            parts = [text]
            if compaction.get("trimmed"):
                # The trim path stores a placeholder sentence as its summary; say what
                # happened instead of quoting it as if it were a summary.
                parts.append("[compaction: oldest turns trimmed; no summary was produced]")
            elif summary:
                parts.append("[compaction summary]\n" + summary)
            if working:
                parts.append("[working state]\n" + working)
            text = "\n\n".join(x for x in parts if x)
        if text:
            steps.append({"source": "system", "message": text, "timestamp": ts})

    for i, step in enumerate(steps, start=1):
        step["step_id"] = i
        if step.get("timestamp") is None:
            step.pop("timestamp", None)

    final_metrics: dict[str, Any] = {
        "total_prompt_tokens": totals["prompt"],
        "total_completion_tokens": totals["completion"],
        "total_cached_tokens": totals["cached"],
        "total_steps": len(steps),
        "extra": {"outcome": outcome},
    }
    if cost_known:
        final_metrics["total_cost_usd"] = round(cost_total, 6)

    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "agent": {
            "name": agent_name,
            "version": agent_version,
            "model_name": model_name,
            **({"extra": agent_extra} if agent_extra else {}),
        },
        "steps": steps,
        "final_metrics": final_metrics,
    }


def write_trajectory(path: str | Path, trajectory: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trajectory, indent=1, default=str), encoding="utf-8")
