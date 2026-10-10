"""The logic of the `git_log` tool. Standard library only (it also runs inside a sandbox)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional

_SEP = "\x1f"


def git_log(workspace: str, path: Optional[str] = None, max_count: int = 20) -> dict[str, Any]:
    root = str(Path(workspace).resolve())
    n = max_count if isinstance(max_count, int) and max_count > 0 else 20
    n = min(n, 200)
    cmd = [
        "git",
        "-C",
        root,
        "log",
        f"-n{n}",
        f"--pretty=format:%h{_SEP}%an{_SEP}%ad{_SEP}%s",
        "--date=short",
    ]
    if path:
        cmd += ["--", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return {"error": f"git log failed: {exc}"}
    if out.returncode != 0:
        return {"error": (out.stderr or "git log failed").strip()[:300]}
    commits = []
    for line in out.stdout.splitlines():
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
