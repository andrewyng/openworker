"""File operations served by the tool runner. Standard library only.

Every function takes plain values and returns a plain dict, so the daemon can put the
result straight into a frame. Paths may be relative; the daemon resolves them against the
calling shell's current folder before they get here.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from .protocol import MAX_READ_BYTES


class FsError(Exception):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(
    path: str,
    *,
    offset_line: Optional[int] = None,
    limit_lines: Optional[int] = None,
    offset_bytes: Optional[int] = None,
    limit_bytes: Optional[int] = None,
) -> dict[str, Any]:
    """A page of a file. By lines (text) unless a byte range is asked for (then base64).
    Never more than MAX_READ_BYTES in one call; `eof` says whether there is more."""
    p = Path(path)
    try:
        size = p.stat().st_size
        if offset_bytes is not None or limit_bytes is not None:
            start = max(0, offset_bytes or 0)
            want = min(limit_bytes or MAX_READ_BYTES, MAX_READ_BYTES)
            with p.open("rb") as fh:
                fh.seek(start)
                data = fh.read(want)
            return {
                "data_b64": base64.b64encode(data).decode("ascii"),
                "offset_bytes": start,
                "length": len(data),
                "total_bytes": size,
                "eof": start + len(data) >= size,
            }
        start_line = max(1, offset_line or 1)
        out: list[str] = []
        used = 0
        total = 0
        last = start_line - 1
        full = False
        with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            for number, line in enumerate(fh, start=1):
                total = number
                if number < start_line or full:
                    continue
                if limit_lines is not None and len(out) >= limit_lines:
                    full = True
                    continue
                encoded = len(line.encode("utf-8"))
                if out and used + encoded > MAX_READ_BYTES:
                    full = True
                    continue
                out.append(line)
                used += encoded
                last = number
        return {
            "text": "".join(out),
            "start_line": start_line,
            "end_line": last,
            "total_lines": total,
            "total_bytes": size,
            "eof": last >= total,
        }
    except OSError as exc:
        raise FsError(f"{type(exc).__name__}: {exc}") from exc


def write(
    path: str,
    *,
    text: Optional[str] = None,
    data_b64: Optional[str] = None,
    if_match_sha256: Optional[str] = None,
    append: bool = False,
    make_parents: bool = True,
) -> dict[str, Any]:
    """Write a file. `if_match_sha256` makes an edit fail when the file changed underneath
    (pass the hash of the content the edit was based on; "" means "must not exist")."""
    p = Path(path)
    data = base64.b64decode(data_b64) if data_b64 is not None else (text or "").encode("utf-8")
    try:
        if if_match_sha256 is not None:
            current = _sha256(p.read_bytes()) if p.exists() else ""
            if current != if_match_sha256:
                raise FsError("the file changed since it was read")
        if make_parents:
            p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("ab" if append else "wb") as fh:
            fh.write(data)
        return {"path": str(p), "bytes_written": len(data), "sha256": _sha256(p.read_bytes())}
    except OSError as exc:
        raise FsError(f"{type(exc).__name__}: {exc}") from exc


def stat(path: str) -> dict[str, Any]:
    p = Path(path)
    try:
        st = p.lstat()
    except FileNotFoundError:
        return {"path": str(p), "exists": False}
    except OSError as exc:
        raise FsError(f"{type(exc).__name__}: {exc}") from exc
    kind = "symlink" if p.is_symlink() else "dir" if p.is_dir() else "file"
    info: dict[str, Any] = {
        "path": str(p),
        "exists": True,
        "kind": kind,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "mode": st.st_mode & 0o7777,
    }
    if kind == "file" and st.st_size <= MAX_READ_BYTES:
        try:
            info["sha256"] = _sha256(p.read_bytes())
        except OSError:
            pass
    return info


def list_dir(path: str, *, limit: int = 2000) -> dict[str, Any]:
    p = Path(path)
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(p) as it:
            for entry in sorted(it, key=lambda e: e.name):
                if len(entries) >= limit:
                    truncated = True
                    break
                kind = "symlink" if entry.is_symlink() else "dir" if entry.is_dir() else "file"
                size = None
                if kind == "file":
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        pass
                entries.append({"name": entry.name, "kind": kind, "size": size})
    except OSError as exc:
        raise FsError(f"{type(exc).__name__}: {exc}") from exc
    return {"path": str(p), "entries": entries, "truncated": truncated}


def mkdir(path: str, *, parents: bool = True) -> dict[str, Any]:
    try:
        Path(path).mkdir(parents=parents, exist_ok=True)
    except OSError as exc:
        raise FsError(f"{type(exc).__name__}: {exc}") from exc
    return {"path": str(Path(path))}


def remove(path: str, *, recursive: bool = False) -> dict[str, Any]:
    p = Path(path)
    try:
        if p.is_dir() and not p.is_symlink():
            if recursive:
                shutil.rmtree(p)
            else:
                p.rmdir()
        else:
            p.unlink()
    except OSError as exc:
        raise FsError(f"{type(exc).__name__}: {exc}") from exc
    return {"path": str(p), "removed": True}


def search(
    path: str,
    pattern: str,
    *,
    max_matches: int = 200,
    ignore_case: bool = False,
    max_file_bytes: int = 5 * 1024 * 1024,
) -> dict[str, Any]:
    """Regular-expression search in one file or under one folder. Pure Python, so it
    works in any image; hidden folders and very large or binary files are skipped."""
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise FsError(f"bad pattern: {exc}") from exc
    root = Path(path)
    matches: list[dict[str, Any]] = []
    truncated = False

    def scan(file: Path) -> bool:
        nonlocal truncated
        try:
            if file.stat().st_size > max_file_bytes and file != root:
                return True
            with file.open("r", encoding="utf-8", errors="strict") as fh:
                for number, line in enumerate(fh, start=1):
                    if rx.search(line):
                        if len(matches) >= max_matches:
                            truncated = True
                            return False
                        matches.append({"path": str(file), "line": number, "text": line.rstrip("\n")[:500]})
        except (OSError, UnicodeDecodeError):
            pass
        return True

    if root.is_file():
        scan(root)
    else:
        for folder, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "node_modules")
            if not all(scan(Path(folder) / name) for name in sorted(files)):
                break
    return {"matches": matches, "truncated": truncated}
