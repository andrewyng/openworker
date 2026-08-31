"""AI command intent analysis — explain an operation's consequences before approval.

Mirrors the compaction summarize_span pattern: one single-turn provider.complete with
tools disabled and no side effects. Failure / timeout / non-bullet output all return
None (the engine then simply omits the annotation); the reason is logged as a warning
so "no annotation" is distinguishable between "feature off" and "analysis failed".

Positional signature — the engine calls
    asyncio.to_thread(self.intent_analyzer, tool_call, self.provider, self.model)
`language` rides in via the wrapper the server builds (defaults to English).
"""
import json
import logging
from typing import Any, Optional, Protocol

from .prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)


class _ToolCallLike(Protocol):
    name: str
    arguments: dict[str, Any]


# Aligned with coworker/risk.py's WRITE_TOOLS
_WRITE_TOOLS = {"write_file", "replace_in_file", "apply_patch", "apply_unified_diff"}
_SEND_TOOLS = {"send_message", "send_file"}


def extract_input(tool_call: _ToolCallLike) -> str:
    """Build the operation description handed to the LLM, structured per tool kind."""
    name = tool_call.name
    args = tool_call.arguments or {}
    if name == "run_shell" and args.get("command"):
        return str(args["command"])
    if name in _WRITE_TOOLS:
        path = args.get("path", "")
        return f"Operation: {name}\nPath: {path}"
    if name in _SEND_TOOLS:
        # send_message's real arg is target (connectors/tools.py), not destination/channel
        target = args.get("target") or args.get("destination") or args.get("channel") or ""
        content = args.get("text") or args.get("content") or ""
        return f"Operation: {name}\nTarget: {target}\nContent: {content}"
    return f"Operation: {name}\nArgs: {json.dumps(args, ensure_ascii=False)}"


def _clean(text: str) -> Optional[str]:
    """Keep only bullet lines (`• ` / `- `); strip fences and blank lines."""
    if not text:
        return None
    lines = []
    for line in text.splitlines():
        line = line.strip().strip("`")
        if line.startswith("• ") or line.startswith("- "):
            lines.append(line)
    return "\n".join(lines) if lines else None


def analyze(tool_call, provider, model, language="en") -> Optional[str]:
    """Produce the intent annotation. **Synchronously blocking** (the engine wraps it
    in asyncio.to_thread). The engine-side wait_for enforces the timeout; this function
    swallows provider errors but logs the reason as a warning.
    Returns: bullet-point text; None on failure/empty.
    """
    try:
        messages = [
            {"role": "system", "content": build_system_prompt(language, 2)},
            {"role": "user", "content": build_user_prompt(extract_input(tool_call))},
        ]
        turn = provider.complete(
            model=model, messages=messages, tools=None, max_tokens=300
        )
        cleaned = _clean(getattr(turn, "text", None))
        if cleaned is None:
            # The model answered but without bullet markers; filtered to None. Logged so prompt
            # drift shows up instead of silently degrading to "no annotation".
            logger.warning(
                "intent_analysis: no bullet lines in LLM output, filtered to None; tool=%s raw=%.200s",
                getattr(tool_call, "name", "?"),
                getattr(turn, "text", None) or "",
            )
        return cleaned
    except Exception as exc:
        logger.warning(
            "intent_analysis: call failed, returning None (card renders without the annotation); tool=%s %s: %s",
            getattr(tool_call, "name", "?"),
            type(exc).__name__,
            exc,
        )
        return None
