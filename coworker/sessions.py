"""Session record — the metadata + messages for one conversation.

Storage lives in `coworker.conversations.ConversationStore`: a SQLite index keyed by
project, with each conversation's messages in an append-only `.jsonl` file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class SessionBranch:
    """A durable parent/child relationship between two conversation sessions.

    The child's own transcript contains only branch-local messages. ``mode=follow``
    means the latest parent history is inherited again at the start of every child
    turn; ``base_message_count`` records where the branch was originally created for
    provenance and for a future snapshot mode.
    """

    child_session_id: str
    parent_session_id: str
    mode: str = "follow"
    base_message_count: int = 0
    state: str = "active"
    created_at: Optional[str] = None
    merged_at: Optional[str] = None


@dataclass
class SessionRecord:
    session_id: str
    workspace: str
    model: str
    mode: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    title: Optional[str] = None
    agent: str = "code"
    message_count: int = 0
    # Provider-safe inheritance boundary for live-follow side sessions. Checkpoint
    # saves may advance ``message_count`` mid-turn; this advances only at turn end.
    committed_message_count: int = 0
    updated_at: Optional[str] = None
    # Folders added to the session beyond its primary scratch dir, each {path, writable, label}.
    # The primary scratch is re-provisioned at engine build, so only these extras are persisted.
    extra_roots: list[dict[str, Any]] = field(default_factory=list)
    # "Always allow" approvals granted in this session ({tools: [...], commands: [...]}) —
    # session-scoped by design, but the session outlives the process, so they must too
    # (owner-hit 2026-07-22: grants forgotten on every restart).
    grants: dict[str, Any] = field(default_factory=dict)
    pinned: bool = False
    archived: bool = False
    # Where the session came from, when not user-started (§31): machine key + display label
    # (e.g. origin="slack", origin_label="#general · T0ABCD"). Set once at spawn.
    origin: Optional[str] = None
    origin_label: Optional[str] = None
