"""Line-numbered file reading (`read_file`) and folder-aware listing (`list_files`) —
both replace the aisuite toolkit's versions.

The toolkit's `read_file` returns raw text (the agent can't cite path:line without
counting) and raises outright on large files (the agent errors and guesses). This one
returns `cat -n`-style numbered lines, windows big files instead of failing, and tells
the agent how to continue reading.

The toolkit's `list_files` returns files only, so a workspace whose top level holds
nothing but a subfolder lists as `[]` and the agent concludes it is empty (OPE-203). This
one lists folders too, marked with a trailing `/`, so one non-recursive look shows the
shape of the tree. Both tools are read-only and scoped to the session's roots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import aisuite as ai

try:  # the toolkit's own skip-list, so both listings hide the same folders
    from aisuite.toolkits.files import DEFAULT_IGNORES as _IGNORED_DIRS
except ImportError:  # pragma: no cover - older aisuite without the constant
    _IGNORED_DIRS = (".git", ".venv", "__pycache__", "node_modules")

_DEFAULT_MAX_LINES = 2000
_MAX_LINE_CHARS = 500
_DEFAULT_MAX_RESULTS = 100
_MAX_RESULTS_CAP = 2000

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
    root = Path(workspace).resolve()
    extra_roots = [Path(str(r.path)).resolve() for r in (roots or [])]

    def read_file(
        path: str,
        start_line: int = 1,
        max_lines: int = _DEFAULT_MAX_LINES,
    ) -> dict[str, Any]:
        start = start_line if isinstance(start_line, int) and start_line > 0 else 1
        n = (
            max_lines
            if isinstance(max_lines, int) and max_lines > 0
            else _DEFAULT_MAX_LINES
        )
        n = min(n, _DEFAULT_MAX_LINES)
        target = (root / path).resolve()
        home = root
        try:
            target.relative_to(root)  # keep reads inside the workspace
        except ValueError:
            for r in extra_roots:
                try:
                    target.relative_to(r)
                    home = r
                    break
                except ValueError:
                    continue
            else:
                return {"error": "path escapes the session's directories"}
        if not target.is_file():
            return {"error": f"not a file: {path}"}

        selected: list[str] = []
        total = 0
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    total = i
                    if i < start or len(selected) >= n:
                        continue
                    text = line.rstrip("\n")
                    if len(text) > _MAX_LINE_CHARS:
                        text = text[:_MAX_LINE_CHARS] + "… (line truncated)"
                    selected.append(f"{i:>6}\t{text}")
        except OSError as exc:
            return {"error": f"read failed: {exc}"}

        end = start + len(selected) - 1 if selected else start - 1
        result: dict[str, Any] = {
            "path": str(target.relative_to(home)) if home == root else str(target),
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "content": "\n".join(selected),
        }
        if end < total:
            result["note"] = (
                f"showing lines {start}-{end} of {total}; "
                f"call again with start_line={end + 1} to continue"
            )
        return result

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

    def _home_for(target: Path) -> Optional[Path]:
        for r in (root, *extra_roots):
            try:
                target.relative_to(r)
                return r
            except ValueError:
                continue
        return None

    def list_files(
        path: str = ".",
        pattern: str = "*",
        recursive: bool = True,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> Any:
        n = (
            max_results
            if isinstance(max_results, int) and max_results > 0
            else _DEFAULT_MAX_RESULTS
        )
        n = min(n, _MAX_RESULTS_CAP)
        p = Path(str(path or ".")).expanduser()
        base = p.resolve() if p.is_absolute() else (root / p).resolve()
        home = _home_for(base)
        if home is None:
            return {"error": "path escapes the session's directories"}
        if not base.is_dir():
            return {"error": f"not a directory: {path}"}

        results: list[str] = []
        try:
            iterator = base.rglob(pattern or "*") if recursive else base.glob(pattern or "*")
            for item in iterator:
                if any(part in _IGNORED_DIRS for part in item.relative_to(home).parts):
                    continue
                shown = (
                    item.relative_to(root).as_posix() if home == root else str(item)
                )
                if item.is_dir():
                    results.append(shown + "/")
                elif item.is_file():
                    results.append(shown)
                else:
                    continue
                if len(results) >= n:
                    break
        except OSError as exc:
            return {"error": f"list failed: {exc}"}
        return sorted(results)

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
