"""The logic of the `read_file` tool. Standard library only (it also runs inside a sandbox).

Returns `cat -n`-style numbered lines, windows big files instead of failing, and tells the
agent how to continue reading. Read-only, scoped to the session's folders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

_DEFAULT_MAX_LINES = 2000
_MAX_LINE_CHARS = 500


def read_file(
    workspace: str,
    path: str,
    start_line: int = 1,
    max_lines: int = _DEFAULT_MAX_LINES,
    roots: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """`roots`: the absolute paths of the session's other folders; a path inside any of
    them resolves too."""
    root = Path(workspace).resolve()
    extra_roots = [Path(str(r)).resolve() for r in (roots or [])]
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
