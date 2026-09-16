"""Obsidian vault operations — local-first, dependency-free.

A vault is a plain folder of Markdown files (the `.obsidian/` dir inside is the app's
own config), so the connector needs no plugin, no server, no keys: tools read and
write the files directly and work whether or not Obsidian is running. Everything is
sandboxed to the connected vault — every resolved path is checked back against the
vault root, mirroring the workspace guard in the files toolkit.

`open_in_obsidian` is the one hand-off point: it launches the user's own app on a
note via the `obsidian://` URL scheme (never required for anything else to work).
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Optional

MAX_NOTE_CHARS = 60_000  # per read; large notes are truncated with a marker
MAX_RESULTS = 25
_SKIP_DIRS = {".obsidian", ".trash", ".git"}

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z0-9_/-]+)")


def _is_note(path: Path) -> bool:
    return path.suffix.lower() == ".md" and not any(
        part in _SKIP_DIRS for part in path.parts
    )


def iter_notes(vault: Path) -> list[Path]:
    return sorted(p for p in vault.rglob("*.md") if _is_note(p.relative_to(vault)))


def _inside(vault: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(vault.resolve())
        return True
    except ValueError:
        return False


def resolve_note(vault: Path, ref: str) -> Optional[Path]:
    """A note by relative path, bare title, or wikilink (`[[Title]]`). Title matches
    are case-insensitive on the filename stem; ties break to the shortest path
    (Obsidian's own link-resolution habit)."""
    ref = ref.strip().strip("[]").split("|")[0].split("#")[0].strip()
    if not ref:
        return None
    candidate = (
        (vault / ref).with_suffix(".md") if not ref.endswith(".md") else vault / ref
    )
    if (
        candidate.is_file()
        and _inside(vault, candidate)
        and _is_note(candidate.relative_to(vault))
    ):
        return candidate
    stem = ref.lower().removesuffix(".md")
    matches = [p for p in iter_notes(vault) if p.stem.lower() == stem]
    if not matches:
        return None
    return min(matches, key=lambda p: len(str(p)))


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """YAML frontmatter (if any) + body. Malformed frontmatter degrades to {}."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    try:
        import yaml

        data = yaml.safe_load(text[4:end]) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return data, text[end + 4 :].lstrip("\n")


def note_tags(frontmatter: dict[str, Any], body: str) -> list[str]:
    tags: list[str] = []
    raw = frontmatter.get("tags")
    if isinstance(raw, str):
        tags += [t.strip().lstrip("#") for t in raw.split(",") if t.strip()]
    elif isinstance(raw, list):
        tags += [str(t).lstrip("#") for t in raw]
    tags += _TAG_RE.findall(body)
    return sorted({t for t in tags if t})


def _title(vault: Path, path: Path) -> str:
    return path.stem


def _rel(vault: Path, path: Path) -> str:
    return str(path.relative_to(vault))


def _preview(body: str, query: str) -> str:
    """A ~200-char window around the first hit (or the note's start)."""
    low = body.lower()
    at = low.find(query.lower()) if query else -1
    start = max(0, at - 60) if at >= 0 else 0
    snippet = body[start : start + 200].strip().replace("\n", " ")
    return ("…" if start > 0 else "") + snippet


def search_notes(
    vault: Path, query: str, tag: str = "", max_results: int = 10
) -> dict[str, Any]:
    """Title/tag/content search, title hits first. `tag` narrows to notes carrying it."""
    query = (query or "").strip()
    tag = (tag or "").lstrip("#").strip()
    limit = max(1, min(int(max_results or 10), MAX_RESULTS))
    hits: list[tuple[int, dict[str, Any]]] = []
    for path in iter_notes(vault):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter, body = parse_frontmatter(text)
        tags = note_tags(frontmatter, body)
        if tag and tag not in tags:
            continue
        title = _title(vault, path)
        score = 0
        if query:
            if query.lower() in title.lower():
                score = 3
            elif any(query.lower() == t.lower() for t in tags):
                score = 2
            elif query.lower() in body.lower():
                score = 1
            if score == 0:
                continue
        hits.append(
            (
                score,
                {
                    "title": title,
                    "path": _rel(vault, path),
                    "tags": tags[:12],
                    "preview": _preview(body, query),
                },
            )
        )
    hits.sort(key=lambda h: (-h[0], h[1]["path"]))
    return {"count": len(hits), "notes": [h[1] for h in hits[:limit]]}


def read_note(vault: Path, ref: str) -> dict[str, Any]:
    path = resolve_note(vault, ref)
    if path is None:
        return {"error": f"note not found: {ref}"}
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = parse_frontmatter(text)
    truncated = len(body) > MAX_NOTE_CHARS
    return {
        "title": _title(vault, path),
        "path": _rel(vault, path),
        "tags": note_tags(frontmatter, body),
        "frontmatter": frontmatter,
        "links": sorted({m.strip() for m in _WIKILINK_RE.findall(body)})[:50],
        "content": body[:MAX_NOTE_CHARS] + ("\n…[truncated]" if truncated else ""),
    }


def list_notes(vault: Path, folder: str = "", max_results: int = 30) -> dict[str, Any]:
    """Most recently modified first; `folder` narrows to a subfolder."""
    limit = max(1, min(int(max_results or 30), 100))
    base = vault / folder.strip("/") if folder.strip() else vault
    if not (base.is_dir() and _inside(vault, base)):
        return {"error": f"folder not found: {folder}"}
    notes = [p for p in iter_notes(vault) if base in p.parents or base == vault]
    notes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "count": len(notes),
        "notes": [
            {
                "title": _title(vault, p),
                "path": _rel(vault, p),
                "modified": datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                ),
            }
            for p in notes[:limit]
        ],
    }


def backlinks(vault: Path, ref: str) -> dict[str, Any]:
    """Notes that wikilink to this one."""
    target = resolve_note(vault, ref)
    if target is None:
        return {"error": f"note not found: {ref}"}
    stem = target.stem.lower()
    linking = []
    for path in iter_notes(vault):
        if path == target:
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(m.strip().lower() == stem for m in _WIKILINK_RE.findall(body)):
            linking.append({"title": _title(vault, path), "path": _rel(vault, path)})
    return {"note": _rel(vault, target), "count": len(linking), "backlinks": linking}


def _daily_config(vault: Path) -> tuple[str, str]:
    """(folder, filename-format) from .obsidian/daily-notes.json; Obsidian's defaults
    otherwise. Only the common YYYY/MM/DD moment tokens are translated."""
    folder, fmt = "", "YYYY-MM-DD"
    try:
        raw = json.loads((vault / ".obsidian" / "daily-notes.json").read_text())
        folder = str(raw.get("folder") or "").strip("/")
        fmt = str(raw.get("format") or fmt)
    except Exception:
        pass
    return folder, fmt


def daily_note(vault: Path, date: str = "") -> dict[str, Any]:
    """Today's (or a given YYYY-MM-DD day's) daily note, honoring the vault's
    daily-notes folder/format config."""
    try:
        day = (
            datetime.date.fromisoformat(date) if date.strip() else datetime.date.today()
        )
    except ValueError:
        return {"error": f"invalid date (want YYYY-MM-DD): {date}"}
    folder, fmt = _daily_config(vault)
    name = (
        fmt.replace("YYYY", f"{day.year:04d}")
        .replace("MM", f"{day.month:02d}")
        .replace("DD", f"{day.day:02d}")
    )
    rel = f"{folder}/{name}.md" if folder else f"{name}.md"
    path = vault / rel
    if not path.is_file():
        return {"error": f"no daily note for {day.isoformat()} (looked at {rel})"}
    return read_note(vault, rel)


def write_note(
    vault: Path, ref: str, content: str, mode: str = "append"
) -> dict[str, Any]:
    """append (default) | create (fails if it exists) | overwrite. New notes may name
    folders that don't exist yet; everything must land inside the vault."""
    if mode not in ("append", "create", "overwrite"):
        return {"error": "mode must be append, create, or overwrite"}
    existing = resolve_note(vault, ref)
    if existing is None:
        rel = ref.strip().strip("[]")
        target = (
            (vault / rel).with_suffix(".md") if not rel.endswith(".md") else vault / rel
        )
        if not _inside(vault, target):
            return {"error": "path escapes the vault"}
        if mode == "append":
            mode = "create"  # appending to a note that doesn't exist creates it
    else:
        target = existing
        if mode == "create":
            return {"error": f"note already exists: {_rel(vault, existing)}"}
    if not _inside(vault, target):
        return {"error": "path escapes the vault"}
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append" and target.is_file():
        base = target.read_text(encoding="utf-8", errors="replace")
        joiner = "" if (not base or base.endswith("\n")) else "\n"
        target.write_text(base + joiner + content, encoding="utf-8")
    else:
        target.write_text(content, encoding="utf-8")
    return {"ok": True, "path": _rel(vault, target), "mode": mode}


def open_in_obsidian(vault: Path, ref: str) -> dict[str, Any]:
    """Open the note in the Obsidian app via its obsidian:// URL scheme."""
    path = resolve_note(vault, ref)
    if path is None:
        return {"error": f"note not found: {ref}"}
    rel = str(path.relative_to(vault))[: -len(".md")]
    url = (
        "obsidian://open?vault="
        + urllib.parse.quote(vault.name)
        + "&file="
        + urllib.parse.quote(rel)
    )
    error = _launch(url)
    if error:
        return {"error": f"could not open Obsidian: {error}", "url": url}
    return {"ok": True, "opened": rel, "url": url}


def _launch(url: str) -> Optional[str]:
    """OS-native URL open (same pattern as reveal_artifact). Returns an error string."""
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=True, capture_output=True, timeout=10)
        elif sys.platform == "win32":
            import os

            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.run(
                ["xdg-open", url], check=True, capture_output=True, timeout=10
            )
        return None
    except Exception as exc:
        return str(exc) or exc.__class__.__name__
