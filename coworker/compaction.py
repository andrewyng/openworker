"""Codex-style conversation compaction for the provider-facing history.

The durable transcript remains append-only. A compaction notice records the summary;
provider projection treats the latest notice as a checkpoint and rebuilds active history
from recent user messages, that summary, and messages written after the checkpoint.
"""

from __future__ import annotations

import json
from typing import Any

from .attachments import content_to_text

SUMMARIZATION_PROMPT = """\
You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work."""

SUMMARY_PREFIX = """\
Another language model started to solve this problem and produced a summary of its thinking process. You also have access to the state of the tools that were used by that language model. Use this to build on the work that has already been done and avoid duplicating work. Here is the summary produced by the other language model, use the information in this summary to assist with your own analysis:"""

RECENT_USER_MESSAGE_MAX_TOKENS = 20_000
AUTO_COMPACT_CONTEXT_PERCENT = 90
_APPROX_BYTES_PER_TOKEN = 4


def approx_token_count(text: str) -> int:
    """Match Codex's provider-independent estimate: UTF-8 bytes rounded up by four."""
    size = len(text.encode("utf-8"))
    return (size + _APPROX_BYTES_PER_TOKEN - 1) // _APPROX_BYTES_PER_TOKEN


def _truncate_middle(text: str, max_tokens: int) -> str:
    max_bytes = max_tokens * _APPROX_BYTES_PER_TOKEN
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    left_budget = max_bytes // 2
    right_budget = max_bytes - left_budget
    left = raw[:left_budget].decode("utf-8", errors="ignore")
    right = raw[len(raw) - right_budget :].decode("utf-8", errors="ignore")
    removed = max(0, len(text) - len(left) - len(right))
    return f"{left}…{removed} chars truncated…{right}"


def recent_user_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = RECENT_USER_MESSAGE_MAX_TOKENS,
) -> list[dict[str, str]]:
    """Return the newest user messages that fit the Codex compaction budget."""
    selected: list[dict[str, str]] = []
    remaining = max_tokens
    for message in reversed(messages):
        if message.get("role") != "user" or remaining <= 0:
            continue
        text = content_to_text(message.get("content"))
        if not text:
            continue
        tokens = approx_token_count(text)
        if tokens <= remaining:
            selected.append({"role": "user", "content": text})
            remaining -= tokens
        else:
            selected.append(
                {"role": "user", "content": _truncate_middle(text, remaining)}
            )
            break
    selected.reverse()
    return selected


def compacted_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Build replacement history from the latest durable compaction checkpoint."""
    checkpoint = -1
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            message.get("role") == "notice"
            and message.get("kind") == "context_compaction"
        ):
            checkpoint = index
            break
    if checkpoint < 0:
        return None

    marker = messages[checkpoint]
    summary = str(marker.get("summary") or f"{SUMMARY_PREFIX}\n(no summary available)")
    systems = [message for message in messages if message.get("role") == "system"]
    return [
        *systems,
        *recent_user_messages(messages[:checkpoint]),
        {"role": "user", "content": summary},
        *messages[checkpoint + 1 :],
    ]


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate replacement-history size with the same four-byte rule Codex uses."""
    return approx_token_count(
        json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    )


def should_auto_compact(
    messages: list[dict[str, Any]], *, context_window: int | None
) -> bool:
    """Use the last provider-reported active usage, but never cross a checkpoint."""
    if not context_window:
        return False
    checkpoint = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "notice"
            and message.get("kind") == "context_compaction"
        ),
        default=-1,
    )
    for index in range(len(messages) - 1, checkpoint, -1):
        message = messages[index]
        usage = message.get("usage")
        if message.get("role") != "assistant" or not isinstance(usage, dict):
            continue
        active_tokens = sum(
            max(0, int(usage.get(key) or 0))
            for key in ("input", "output", "cache_read", "cache_write")
        )
        return (
            active_tokens * 100
            >= context_window * AUTO_COMPACT_CONTEXT_PERCENT
        )
    return False
