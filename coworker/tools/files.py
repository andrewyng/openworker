"""Line-numbered file reading (`read_file`) and folder-aware listing (`list_files`) —
both replace the aisuite toolkit's versions.

The toolkit's `read_file` returns raw text (the agent can't cite path:line without
counting) and raises outright on large files (the agent errors and guesses). This one
returns `cat -n`-style numbered lines, windows big files instead of failing, and tells
the agent how to continue reading. Read-only, workspace-scoped.

The toolkit's `list_files` returns files only, so a workspace whose top level holds
nothing but a subfolder lists as `[]` and the agent concludes it is empty (OPE-203). This
one lists folders too, marked with a trailing `/`, so one non-recursive look shows the
shape of the tree. Both tools are read-only and scoped to the session's roots.
"""

from __future__ import annotations

from typing import Any, Optional

import aisuite as ai

from ..sandbox.runner import tools_read as _impl
from ..sandbox.runner.tools_read import (  # noqa: F401
    _DEFAULT_MAX_LINES,
    _DEFAULT_MAX_RESULTS,
    _MAX_LINE_CHARS,
)

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

_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": (
            "List files and folders under a path; folders end with '/'. Use "
            "recursive=false to see one level, and pattern (a glob such as '*.py') to "
            "filter by name. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Folder to list, relative to the workspace (default '.').",
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob to match names against (default '*').",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Descend into subfolders (default true).",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Stop after this many entries (default {_DEFAULT_MAX_RESULTS}).",
                },
            },
            "required": [],
        },
    },
}


def file_tools(workspace: str, roots: Optional[list] = None) -> list:
    """Windowed read_file and folder-aware list_files rooted at `workspace`. With `roots`
    (RootDir list), absolute paths inside ANY root also resolve — multi-root sessions
    (universal scratch) address their scratch/extra dirs by the absolute paths the roots
    context advertises."""
    def read_file(
        path: str,
        start_line: int = 1,
        max_lines: int = _DEFAULT_MAX_LINES,
    ) -> dict[str, Any]:
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

    def list_files(
        path: str = ".",
        pattern: str = "*",
        recursive: bool = True,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> Any:
        return _impl.list_files(
            workspace, path, pattern, recursive, max_results,
            roots=[str(r.path) for r in (roots or [])],
        )

    list_files.__name__ = "list_files"
    list_files.__doc__ = _LIST_SCHEMA["function"]["description"]
    list_files.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="list_files",
        category="filesystem",
        risk_level="low",
        capabilities=["list_files"],
        requires_approval=False,
    )
    list_files.__coworker_schema__ = _LIST_SCHEMA
    return [read_file, list_files]
