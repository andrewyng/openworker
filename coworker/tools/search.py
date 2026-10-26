"""Fast code search (`grep`) — ripgrep when available, a Python walk otherwise.

ripgrep respects `.gitignore`, so it skips `node_modules`/`target`/`dist` automatically; the
fallback skips a hardcoded set of heavy dirs. Read-only, workspace-scoped. Returns file:line:text.
"""

from __future__ import annotations

import shutil  # noqa: F401  (tests patch search.shutil / search.subprocess)
import subprocess  # noqa: F401
from typing import Any, Optional

import aisuite as ai

from ..sandbox.runner import tools_grep as _impl
from ..sandbox.runner.tools_grep import (  # noqa: F401  (re-exported: the logic moved)
    _IGNORE_DIRS,
    OS_DATA_DIRS,
    _parse_rg,
    _py_grep,
    _rel,
)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": (
            "Search the workspace for a regular-expression pattern and return matching lines as "
            "file:line:text. Fast and .gitignore-aware (skips node_modules, build dirs, etc.). "
            "Prefer this over reading files blindly to locate code. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Subdirectory to search (default: whole workspace).",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional filename glob filter, e.g. '*.py'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max matches (default 100, max 1000).",
                },
            },
            "required": ["pattern"],
        },
    },
}


def search_tools(workspace: str) -> list:
    def grep(
        pattern: str,
        path: str = ".",
        glob: Optional[str] = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        return _impl.grep(workspace, pattern, path, glob, max_results)

    grep.__name__ = "grep"
    grep.__doc__ = _SCHEMA["function"]["description"]
    grep.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="grep",
        category="search",
        risk_level="low",
        capabilities=["search"],
        requires_approval=False,
    )
    grep.__coworker_schema__ = _SCHEMA
    return [grep]
