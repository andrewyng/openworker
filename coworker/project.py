"""Project context — AGENTS.md + knowledge-wiki ingestion into the system prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .secrets import state_dir


def default_global_agents_path() -> Path:
    return state_dir() / "AGENTS.md"


def load_agents_md(
    workspace: str | Path, *, global_path: Optional[str | Path] = None
) -> str:
    """Return a system-prompt block from the global and project AGENTS.md files.

    v1 loads global (`<state-dir>/AGENTS.md`) + project-root `AGENTS.md` only;
    nested discovery is a fast-follow.
    """
    parts: list[tuple[str, str]] = []

    g = Path(global_path) if global_path is not None else default_global_agents_path()
    if g.is_file():
        parts.append(("global", g.read_text(encoding="utf-8")))

    root = Path(workspace).expanduser().resolve() / "AGENTS.md"
    if root.is_file():
        parts.append(("project", root.read_text(encoding="utf-8")))

    if not parts:
        return ""

    blocks = [
        f"<{label} AGENTS.md>\n{text.strip()}\n</{label} AGENTS.md>"
        for label, text in parts
    ]
    return "Project conventions:\n" + "\n\n".join(blocks)


# Knowledge-wiki index injection. Convention: a workspace that maintains a
# long-lived knowledge base keeps it as markdown pages under `wiki/` with an
# `INDEX.md` map-of-content. Injecting ONLY the index (progressive disclosure,
# same trick as the skills catalog) gives every session standing knowledge of
# what is known — pages load on demand via the normal file tools — at a context
# cost that stays flat as the wiki grows.
_WIKI_INDEX_CAP = 6_000  # chars; an index past this is a wiki smell, not a need


def load_wiki_index(workspace: str | Path) -> str:
    """System-prompt block from `<workspace>/wiki/INDEX.md`, if present."""
    index = Path(workspace).expanduser().resolve() / "wiki" / "INDEX.md"
    if not index.is_file():
        return ""
    try:
        text = index.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > _WIKI_INDEX_CAP:
        text = text[:_WIKI_INDEX_CAP] + "\n… (index truncated — trim it; an index is a map, not a page)"
    return (
        "This workspace maintains a knowledge wiki (markdown pages under `wiki/`, "
        "linked with [[wikilinks]]). Its index follows. Open pages on demand with "
        "the file tools; when you learn something durable that changes a page's "
        "claims, revise that page in place rather than appending duplicates.\n"
        f"<wiki INDEX.md>\n{text}\n</wiki INDEX.md>"
    )
