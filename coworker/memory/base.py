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
    def update(self, item_id: int, content: str) -> Optional[MemoryItem]: ...

    @abstractmethod
    def delete(self, item_id: int) -> bool: ...

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[MemoryItem]: ...


def format_memories(items: list[MemoryItem], limit: Optional[int] = None) -> str:
    """Render memories for injection into the system prompt. Ids are shown so the agent
    can revise a memory (`memory_update`) or retire it (`memory_forget`)."""
    if not items:
        return ""
    selected = items[:limit] if limit and limit > 0 else items
    lines = [f"- [#{item.id}] {item.content}" for item in selected]
    return "Known memories (from earlier sessions):\n" + "\n".join(lines)
