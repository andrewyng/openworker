"""Read-only git context tools: `git_log`, `git_blame`, `git_show`.

aisuite's git toolkit gives `git_status`/`git_diff`; these add history — log (how a file
came to be), blame (who last touched each line), show (what a commit changed) — so the
agent can understand how code evolved before changing it. Read-only; no commit/push here
(the prompt forbids those without explicit ask, and they'd go through run_shell anyway).
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

_SEP = "\x1f"

# Blame/show output is bounded so a huge file or mega-commit can't flood the context.
_BLAME_MAX_LINES = 300
_SHOW_MAX_CHARS = 8000

# Refs are user/model input on a subprocess argv: a leading "-" would be parsed as an
# option (`git show --output=…` writes files), so refs must look like refs. Paths are
# always passed after `--` and need no such guard.
_REF_RE = re.compile(r"^[\w.@{}^~/-]+$")


def _bad_ref(ref: str) -> bool:
    return not ref or ref.startswith("-") or not _REF_RE.match(ref)


_LOG_SCHEMA = {
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

_BLAME_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_blame",
        "description": (
            "Who last touched each line of a file (hash, author, date, content per line). "
            "Optionally scope to a line range. Use it to find the commit and author behind "
            "a specific line before changing it. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File to blame (required).",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line of the range (1-based).",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line of the range (inclusive).",
                },
            },
            "required": ["path"],
        },
    },
}

_SHOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_show",
        "description": (
            "A commit's message, diffstat, and patch (default HEAD). Optionally limit the "
            "patch to one path. Use it to see exactly what a commit from git_log/git_blame "
            "changed. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Commit ref (hash, branch, HEAD~2, …). Default HEAD.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional file/dir to limit the patch to.",
                },
            },
        },
    },
}


def _run_git(root: str, args: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Run a git subcommand; returns (stdout, None) or (None, error-string)."""
    try:
        out = subprocess.run(
            ["git", "-C", root, *args], capture_output=True, text=True, timeout=15
        )
    except Exception as exc:
        return None, f"git {args[0]} failed: {exc}"
    if out.returncode != 0:
        return None, (out.stderr or f"git {args[0]} failed").strip()[:300]
    return out.stdout, None


def git_tools(workspace: str) -> list:
    root = str(Path(workspace).resolve())

    def git_log(path: Optional[str] = None, max_count: int = 20) -> dict[str, Any]:
        n = max_count if isinstance(max_count, int) and max_count > 0 else 20
        n = min(n, 200)
        args = [
            "log",
            f"-n{n}",
            f"--pretty=format:%h{_SEP}%an{_SEP}%ad{_SEP}%s",
            "--date=short",
        ]
        if path:
            args += ["--", path]
        out, err = _run_git(root, args)
        if err:
            return {"error": err}
        commits = []
        for line in out.splitlines():
            parts = line.split(_SEP)
            if len(parts) == 4:
                commits.append(
                    {
                        "hash": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "subject": parts[3],
                    }
                )
        return {"count": len(commits), "commits": commits}

    def git_blame(
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> dict[str, Any]:
        if not path or not isinstance(path, str):
            return {"error": "path is required"}
        args = ["blame", "--line-porcelain"]
        if start_line is not None or end_line is not None:
            s = start_line if isinstance(start_line, int) and start_line > 0 else 1
            e = end_line if isinstance(end_line, int) and end_line >= s else s
            args += ["-L", f"{s},{e}"]
        args += ["--", path]
        out, err = _run_git(root, args)
        if err:
            return {"error": err}
        lines: list[dict[str, Any]] = []
        cur: dict[str, Any] = {}
        for raw in out.splitlines():
            if raw.startswith("\t"):
                cur["content"] = raw[1:][:300]
                lines.append(cur)
                cur = {}
            elif "hash" not in cur and re.match(r"^[0-9a-f]{7,40} \d+ \d+", raw):
                head = raw.split()
                cur = {"line": int(head[2]), "hash": head[0][:8]}
            elif raw.startswith("author "):
                cur["author"] = raw[len("author ") :]
            elif raw.startswith("author-time "):
                cur["date"] = date.fromtimestamp(int(raw.split()[1])).isoformat()
        result: dict[str, Any] = {"path": path, "count": len(lines)}
        if len(lines) > _BLAME_MAX_LINES:
            lines = lines[:_BLAME_MAX_LINES]
            result["note"] = (
                f"showing first {_BLAME_MAX_LINES} lines; "
                "pass start_line/end_line to scope the rest"
            )
        result["lines"] = lines
        return result

    def git_show(ref: str = "HEAD", path: Optional[str] = None) -> dict[str, Any]:
        ref = str(ref or "HEAD").strip()
        if _bad_ref(ref):
            return {"error": f"invalid ref: {ref!r}"}
        args = ["show", ref, "--date=short", "--stat", "--patch"]
        if path:
            args += ["--", path]
        out, err = _run_git(root, args)
        if err:
            return {"error": err}
        result: dict[str, Any] = {"ref": ref}
        if len(out) > _SHOW_MAX_CHARS:
            out = out[:_SHOW_MAX_CHARS]
            result["note"] = (
                f"output truncated to {_SHOW_MAX_CHARS} chars; "
                "pass `path` to limit the patch to one file"
            )
        result["output"] = out
        return result

    for fn, schema in (
        (git_log, _LOG_SCHEMA),
        (git_blame, _BLAME_SCHEMA),
        (git_show, _SHOW_SCHEMA),
    ):
        name = schema["function"]["name"]
        fn.__name__ = name
        fn.__doc__ = schema["function"]["description"]
        fn.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=name,
            category="git",
            risk_level="low",
            capabilities=["git"],
            requires_approval=False,
        )
        fn.__coworker_schema__ = schema
    return [git_log, git_blame, git_show]
