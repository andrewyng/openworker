"""`git_log` — recent commit history for context (read-only).

aisuite's git toolkit gives `git_status`/`git_diff`; this adds history so the agent can see how
a file came to be the way it is before changing it. Read-only; no commit/push here (the prompt
forbids those without explicit ask, and they'd go through run_shell anyway).
"""

from __future__ import annotations

from typing import Any, Optional

import aisuite as ai

from ..sandbox.runner import tools_git as _impl

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_log",
        "description": (
            "Recent git commit history (hash, author, date, subject). Optionally scope to a path. "
            "Use it to understand how code evolved before editing. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional file/dir to scope history to.",
                },
                "max_count": {
                    "type": "integer",
                    "description": "How many commits (default 20, max 200).",
                },
            },
        },
    },
}


def git_tools(workspace: str) -> list:
    def git_log(path: Optional[str] = None, max_count: int = 20) -> dict[str, Any]:
        return _impl.git_log(workspace, path, max_count)

    git_log.__name__ = "git_log"
    git_log.__doc__ = _SCHEMA["function"]["description"]
    git_log.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="git_log",
        category="git",
        risk_level="low",
        capabilities=["git"],
        requires_approval=False,
    )
    git_log.__coworker_schema__ = _SCHEMA
    return [git_log]
