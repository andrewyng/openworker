"""The logic of the `read_file` and `list_files` tools. Standard library only (it also runs
inside a sandbox).

`read_file` returns `cat -n`-style numbered lines, windows big files instead of failing, and
tells the agent how to continue reading. `list_files` lists folders as well as files (folders
end with '/'), so a workspace whose top level holds nothing but a subfolder never lists as
empty (OPE-203). Both read-only, scoped to the session's folders.
"""

from __future__ import annotations

import struct
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

    shown_path = str(target.relative_to(home)) if home == root else str(target)
    image_info = _inspect_image(target)
    if image_info is not None:
        dims = (
            f", {image_info['width']}x{image_info['height']}"
            if "width" in image_info and "height" in image_info
            else ""
        )
        return {
            "path": shown_path,
            "binary": True,
            **image_info,
            "error": (
                f"binary image ({image_info['mime']}{dims}, {image_info['bytes']} bytes) — "
                "read_file is for text files; inspect or process image bytes with local tools or scripts"
            ),
        }

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
        "path": shown_path,
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


def _inspect_image(target: Path) -> Optional[dict[str, Any]]:
    try:
        size = target.stat().st_size
        with open(target, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return None
    if (
        len(head) >= 24
        and head.startswith(b"\x89PNG\r\n\x1a\n")
        and head[12:16] == b"IHDR"
    ):
        width, height = struct.unpack(">II", head[16:24])
        return {"mime": "image/png", "width": width, "height": height, "bytes": size}
    if len(head) >= 10 and head.startswith((b"GIF87a", b"GIF89a")):
        width, height = struct.unpack("<HH", head[6:10])
        return {"mime": "image/gif", "width": width, "height": height, "bytes": size}
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        info: dict[str, Any] = {"mime": "image/webp", "bytes": size}
        if len(head) >= 30 and head[12:16] == b"VP8X":
            info["width"] = 1 + int.from_bytes(head[24:27], "little")
            info["height"] = 1 + int.from_bytes(head[27:30], "little")
        elif len(head) >= 25 and head[12:16] == b"VP8L" and head[20] == 0x2F:
            bits = int.from_bytes(head[21:25], "little")
            info["width"] = (bits & 0x3FFF) + 1
            info["height"] = ((bits >> 14) & 0x3FFF) + 1
        elif (
            len(head) >= 30
            and head[12:16] == b"VP8 "
            and head[23:26] == b"\x9d\x01\x2a"
        ):
            w, h = struct.unpack("<HH", head[26:30])
            info["width"] = w & 0x3FFF
            info["height"] = h & 0x3FFF
        return info
    if len(head) >= 4 and head.startswith(b"\xff\xd8\xff"):
        info = {"mime": "image/jpeg", "bytes": size}
        pos = 2
        while pos + 4 <= len(head):
            if head[pos] != 0xFF:
                break
            while pos < len(head) and head[pos] == 0xFF:
                pos += 1
            if pos >= len(head):
                break
            marker = head[pos]
            pos += 1
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if pos + 2 > len(head):
                break
            seg_len = struct.unpack(">H", head[pos : pos + 2])[0]
            if seg_len < 2 or pos + seg_len > len(head):
                break
            if (
                0xC0 <= marker <= 0xCF
                and marker not in (0xC4, 0xC8, 0xCC)
                and seg_len >= 7
            ):
                h, w = struct.unpack(">HH", head[pos + 3 : pos + 7])
                info["width"] = w
                info["height"] = h
                break
            pos += seg_len
        return info
    return None
