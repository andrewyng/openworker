"""Agent teams substrate. Two append-only stores, one record discipline:
the board log (space-scoped — a board lives and dies with its team) and the
journal store (case-keyed — knowledge that outlives boards and teams)."""

from .journal import JournalStore
from .model import (
    Actor,
    AuthorityError,
    BoardError,
    BoardNotFoundError,
    ChainError,
    ItemState,
    Role,
)
from .store import TeamStore
from .tools import board_tools, journal_tools

__all__ = [
    "Actor",
    "AuthorityError",
    "BoardError",
    "BoardNotFoundError",
    "ChainError",
    "ItemState",
    "JournalStore",
    "Role",
    "TeamStore",
    "board_tools",
    "journal_tools",
]

# BoardDialect / LocalDialect / RemoteDialect live in .dialect, BoardTokens in
# .tokens — imported directly by their consumers (CLI, MCP server, `/v1/board`)
# to keep this package root light for the common in-app path.
