"""Persistent memory — adapter interface + scopes.

Memory is the long-lived layer above transient conversation state: durable facts,
preferences, task notes, summaries. Scopes: global (user-wide), workspace (per project),
session. Backends are adapters (`SQLiteMemoryStore` now, `PostgresMemoryStore` later).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Scope(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    SESSION = "session"


@dataclass
class MemoryItem:
    id: int
    scope: Scope
    content: str
    key: Optional[str] = None
    summary: Optional[str] = None
    workspace: Optional[str] = None
    session_id: Optional[str] = None
    created_at: Optional[str] = None


class MemoryStore(ABC):
    @abstractmethod
    def add(
        self,
        content: str,
        *,
        scope: Scope = Scope.WORKSPACE,
        key: Optional[str] = None,
        summary: Optional[str] = None,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> MemoryItem: ...

    @abstractmethod
    def get(self, item_id: int) -> Optional[MemoryItem]: ...

    @abstractmethod
    def list(
        self,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[MemoryItem]: ...

    @abstractmethod
    def update(
        self, item_id: int, content: str, *, summary: Optional[str] = None
    ) -> Optional[MemoryItem]: ...

    @abstractmethod
    def delete(self, item_id: int) -> bool: ...

    @abstractmethod
    def delete_all(self, *, scope: Optional[Scope] = None) -> int: ...


# MEMORY-SPEC §7: below this rendered size, every memory is injected in full; above it,
# the block flips to index mode (newest few in full, one-line summaries for the rest,
# bodies fetched on demand via memory_read). ~2k tokens: a typical memory is 20-40
# tokens, so this only trips past ~50-100 memories — and the weakest supported setup
# (a local model with an 8k context) binds the ceiling.
INDEX_THRESHOLD_CHARS = 8_000
# In index mode the newest N stay in full: recent facts are disproportionately relevant,
# which softens the two-step recall cost where it matters most.
INDEX_FULL_NEWEST = 10

_INDEX_NOTE = (
    "(Some memories above show only a one-line summary. Call memory_read with the "
    "[#id]s before acting on anything a summary hints at.)"
)


def _index_line(item: MemoryItem) -> str:
    """One-line rendering: the saved summary, or a truncated first line for rows
    written before summaries existed (no data migration)."""
    text = (item.summary or "").strip()
    if not text:
        text = item.content.strip().splitlines()[0] if item.content.strip() else ""
        if len(text) > 80:
            text = text[:77] + "..."
    return f"- [#{item.id}] {text}"


def format_memories(items: list[MemoryItem]) -> str:
    """Render memories in full for injection into the system prompt. Ids are shown so
    the agent can revise a memory (`memory_update`) or retire it (`memory_forget`)."""
    if not items:
        return ""
    lines = [f"- [#{item.id}] {item.content}" for item in items]
    return "Known memories (from earlier sessions):\n" + "\n".join(lines)


def format_memory_index(
    items: list[MemoryItem], *, full_newest: int = INDEX_FULL_NEWEST
) -> str:
    """Index rendering: newest `full_newest` in full, one-line summaries for the rest,
    plus the fetch-before-acting note for memory_read."""
    if not items:
        return ""
    newest = {item.id for item in sorted(items, key=lambda i: i.id)[-full_newest:]}
    lines = [
        f"- [#{item.id}] {item.content}" if item.id in newest else _index_line(item)
        for item in items
    ]
    return (
        "Known memories (from earlier sessions):\n"
        + "\n".join(lines)
        + f"\n{_INDEX_NOTE}"
    )


def render_memory_block(
    items: list[MemoryItem], *, threshold_chars: int = INDEX_THRESHOLD_CHARS
) -> str:
    """The injected memories block. Full mode while it's affordable; automatically and
    invisibly flips to index mode when the full rendering exceeds the threshold
    (MEMORY-SPEC §7). Evaluated once per engine build — a session is always in exactly
    one mode for its whole life."""
    full = format_memories(items)
    if len(full) <= threshold_chars:
        return full
    return format_memory_index(items)
