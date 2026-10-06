"""The logic of the `read_file` and `list_files` tools. Standard library only (it also runs
inside a sandbox).

`read_file` returns `cat -n`-style numbered lines, windows big files instead of failing, and
tells the agent how to continue reading. `list_files` lists folders as well as files (folders
end with '/'), so a workspace whose top level holds nothing but a subfolder never lists as
empty (OPE-203). Both read-only, scoped to the session's folders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

_DEFAULT_MAX_LINES = 2000
_MAX_LINE_CHARS = 500
_DEFAULT_MAX_RESULTS = 100
_MAX_RESULTS_CAP = 2000
# The same skip-list the aisuite file toolkit uses (copied: this module must stay
# standard-library only), so every listing hides the same folders.
_IGNORED_DIRS = (
    ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "dist", "build",
)


def _home_for(target: Path, root: Path, extra_roots: Sequence[Path]) -> Optional[Path]:
    for r in (root, *extra_roots):
        try:
            target.relative_to(r)
            return r
        except ValueError:
            continue
    return None


def list_files(
    workspace: str,
    path: str = ".",
    pattern: str = "*",
    recursive: bool = True,
    max_results: int = _DEFAULT_MAX_RESULTS,
    roots: Optional[Sequence[str]] = None,
) -> Any:
    """Files and folders under `path`, folders marked with a trailing '/', sorted. Paths
    inside the workspace come back relative to it; paths inside another of the session's
    `roots` come back absolute."""
    root = Path(workspace).resolve()
    extra_roots = [Path(str(r)).resolve() for r in (roots or [])]
    n = (
        max_results
        if isinstance(max_results, int) and max_results > 0
        else _DEFAULT_MAX_RESULTS
    )
    n = min(n, _MAX_RESULTS_CAP)
    p = Path(str(path or ".")).expanduser()
    base = p.resolve() if p.is_absolute() else (root / p).resolve()
    home = _home_for(base, root, extra_roots)
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
            shown = item.relative_to(root).as_posix() if home == root else str(item)
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
