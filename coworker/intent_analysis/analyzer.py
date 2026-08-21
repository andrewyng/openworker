"""AI command intent analysis — generate a consequence summary before approval.

Uses a single blocking provider.complete call (tools disabled, no side effects),
mirroring the compaction.summarize_span pattern. Returns None on failure/empty
so the engine simply omits the annotation.

Positional signature: the engine calls
    asyncio.to_thread(self.intent_analyzer, tool_call, self.provider, self.model)
so positional args must align.
"""
import json
from typing import Any, Optional, Protocol

from .prompts import build_system_prompt, build_user_prompt


class _ToolCallLike(Protocol):
    name: str
    arguments: dict[str, Any]


# Aligned with coworker/risk.py WRITE_TOOLS (workspace-mutating tools whose
# path argument is the meaningful thing to show the user).
_WRITE_TOOLS = {"write_file", "replace_in_file", "apply_patch", "apply_unified_diff"}
_SEND_TOOLS = {"send_message", "send_file"}


def extract_input(tool_call: _ToolCallLike) -> str:
    """Extract a structured 'operation description' from the tool call."""
    name = tool_call.name
    args = tool_call.arguments or {}
    if name == "run_shell" and args.get("command"):
        return str(args["command"])
    if name in _WRITE_TOOLS:
        path = args.get("path", "")
        return f"Operation: {name}\nPath: {path}"
    if name in _SEND_TOOLS:
        target = args.get("target") or args.get("destination") or args.get("channel") or ""
        content = args.get("text") or args.get("content") or ""
        return f"Operation: {name}\nTarget: {target}\nContent: {content}"
    return f"Operation: {name}\nArgs: {json.dumps(args, ensure_ascii=False)}"


def _clean(text: str) -> Optional[str]:
    """Keep only '• '/'- ' bullet lines, strip code fences and leading labels."""
    if not text:
        return None
    lines = []
    for line in text.splitlines():
        line = line.strip().strip("`")
        if line.startswith("• ") or line.startswith("- "):
            lines.append(line)
    return "\n".join(lines) if lines else None


def analyze(tool_call, provider, model) -> Optional[str]:
    """Generate the consequence summary. Synchronous blocking (the engine
    wraps it in asyncio.to_thread). Returns None on failure/empty."""
    try:
        messages = [
            {"role": "system", "content": build_system_prompt(2)},
            {"role": "user", "content": build_user_prompt(extract_input(tool_call))},
        ]
        turn = provider.complete(
            model=model, messages=messages, tools=None, max_tokens=300
        )
        return _clean(getattr(turn, "text", None))
    except Exception:
        return None
