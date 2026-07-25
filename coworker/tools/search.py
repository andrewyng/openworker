"""Fast code search (`grep`) — ripgrep when available, a Python walk otherwise.

ripgrep respects `.gitignore`, so it skips `node_modules`/`target`/`dist` automatically; the
fallback skips a hardcoded set of heavy dirs. Read-only, workspace-scoped. Returns file:line:text.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "target",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
}

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
    root = Path(workspace).resolve()

    def grep(
        pattern: str,
        path: str = ".",
        glob: Optional[str] = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        n = max_results if isinstance(max_results, int) and max_results > 0 else 100
        n = min(n, 1000)
        base = (root / (path or ".")).resolve()
        try:
            base.relative_to(root)  # keep searches inside the workspace
        except ValueError:
            return {"error": "path escapes the workspace"}

        rg = shutil.which("rg")
        if rg:
            cmd = [
                rg,
                "--json",
                "--max-count",
                str(n),
                "-e",
                pattern,
            ]
            if glob:
                cmd += ["--glob", glob]
            # Do not rely solely on a workspace's .gitignore: the Python fallback
            # always omits these generated/dependency directories too. Exclusions come
            # last because ripgrep resolves conflicting globs with the later one winning.
            for ignored in sorted(_IGNORE_DIRS):
                cmd += ["--glob", f"!**/{ignored}/**"]
            cmd.append(str(base))
            try:
                out = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
            except Exception as exc:
                return {"error": f"grep failed: {exc}"}
            if out.returncode not in (0, 1):  # 1 = no matches
                return {"error": (out.stderr or "ripgrep error").strip()[:300]}
            return {"engine": "ripgrep", **_parse_rg(out.stdout, root, n)}

        return {"engine": "python", **_py_grep(root, base, pattern, glob, n)}

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


def _rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(root))
    except (ValueError, OSError):
        return path


def _parse_rg(stdout: str, root: Path, n: int) -> dict[str, Any]:
    """Parse ripgrep's structured stream without ambiguous path delimiters."""
    matches: list[dict[str, Any]] = []
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path = _rg_value(data.get("path"))
        text = _rg_value(data.get("lines")).rstrip("\r\n")
        line_number = data.get("line_number")
        if path:
            matches.append(
                {
                    "file": _rel(path, root),
                    "line": line_number if isinstance(line_number, int) else 0,
                    "text": text[:300],
                }
            )
        if len(matches) >= n:
            break
    return {"count": len(matches), "matches": matches}


def _rg_value(value: Any) -> str:
    """Decode ripgrep's text or base64 JSON value."""
    if not isinstance(value, dict):
        return ""
    text = value.get("text")
    if isinstance(text, str):
        return text
    encoded = value.get("bytes")
    if not isinstance(encoded, str):
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _py_grep(
    root: Path, base: Path, pattern: str, glob: Optional[str], n: int
) -> dict[str, Any]:
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return {"error": f"invalid regex: {exc}", "count": 0, "matches": []}
    matches: list[dict[str, Any]] = []
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
        for fn in files:
            if glob and not fnmatch.fnmatch(fn, glob):
                continue
            fp = Path(dirpath) / fn
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            matches.append(
                                {
                                    "file": _rel(str(fp), root),
                                    "line": i,
                                    "text": line.rstrip()[:300],
                                }
                            )
                            if len(matches) >= n:
                                return {"count": len(matches), "matches": matches}
            except OSError:
                continue
    return {"count": len(matches), "matches": matches}
