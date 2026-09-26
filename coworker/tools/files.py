"""Line-numbered file reading (`read_file`) — replaces the aisuite toolkit's reader.

The toolkit's `read_file` returns raw text (the agent can't cite path:line without
counting) and raises outright on large files (the agent errors and guesses). This one
returns `cat -n`-style numbered lines, windows big files instead of failing, and tells
the agent how to continue reading. Read-only, workspace-scoped.
"""

from __future__ import annotations

from typing import Any, Optional

import aisuite as ai

from ..sandbox.runner import tools_read as _impl
from ..sandbox.runner.tools_read import _DEFAULT_MAX_LINES, _MAX_LINE_CHARS  # noqa: F401

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file, returning numbered lines ('   12\\ttext') so code can be "
            "referenced as path:line. Large files are windowed: pass start_line to continue "
            "where the previous read stopped. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, relative to the workspace.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to read, 1-based (default 1).",
                },
                "max_lines": {
                    "type": "integer",
                    "description": f"How many lines (default {_DEFAULT_MAX_LINES}).",
                },
            },
            "required": ["path"],
        },
    },
}


def file_tools(workspace: str, roots: Optional[list] = None) -> list:
    """Windowed read_file rooted at `workspace`. With `roots` (RootDir list), absolute
    paths inside ANY root also resolve — multi-root sessions (universal scratch) address
    their scratch/extra dirs by the absolute paths the roots context advertises."""
    def read_file(
        path: str,
        start_line: int = 1,
        max_lines: int = _DEFAULT_MAX_LINES,
    ) -> dict[str, Any]:
        # `roots` is the session's live list: read it on every call.
        return _impl.read_file(
            workspace, path, start_line, max_lines, roots=[str(r.path) for r in (roots or [])]
        )

    read_file.__name__ = "read_file"
    read_file.__doc__ = _SCHEMA["function"]["description"]
    read_file.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="read_file",
        category="filesystem",
        risk_level="low",
        capabilities=["read"],
        requires_approval=False,
    )
    read_file.__coworker_schema__ = _SCHEMA
    return [read_file]
