"""Work-item model for agent teams — states, actors, links, errors.

The board is not a database of record: it is a projection of the append-only team
event log (see teams.store). These are the shapes the projection folds into, and the
rules the verbs enforce. Deliberately minimal — no sprints, estimates, priorities, or
custom fields; anyone needing those graduates to a real tracker via connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ItemState(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    CANCELED = "canceled"


# Legal edges of the state machine. There is NO draft/proposed state (decided
# 2026-08-16): a plan proposal lives in the conversation (plan-approval flow) and
# the board only ever contains accepted work — items are created `open`, and the
# control point for work starting is ASSIGNMENT (a granted, revocable authority),
# not a per-item approval. review→done stays the verification gate; canceled→open
# is reopen.
EDGES: dict[ItemState, set[ItemState]] = {
    ItemState.OPEN: {ItemState.IN_PROGRESS, ItemState.CANCELED},
    ItemState.IN_PROGRESS: {ItemState.BLOCKED, ItemState.REVIEW, ItemState.CANCELED},
    ItemState.BLOCKED: {ItemState.IN_PROGRESS, ItemState.CANCELED},
    ItemState.REVIEW: {ItemState.DONE, ItemState.IN_PROGRESS, ItemState.CANCELED},
    ItemState.DONE: set(),
    ItemState.CANCELED: {ItemState.OPEN},
}

# Targets a worker may move its OWN item to. Workers never approve, never close:
# done is the lead's verdict at review, cancel is a lead/user board decision.
WORKER_TARGETS = {ItemState.IN_PROGRESS, ItemState.BLOCKED, ItemState.REVIEW}


class Role(str, Enum):
    USER = "user"
    LEAD = "lead"
    WORKER = "worker"
    SYSTEM = "system"


@dataclass(frozen=True)
class Actor:
    """Who is speaking to the board. `id` is the agent instance id ("user" for the
    human); role decides verb authority — the capability firebreak in data form."""

    id: str
    role: Role
    persona: str = ""
    model: str = ""
    session_id: str = ""


LINK_KINDS = ("parent", "blocks")  # link(src, "parent", dst): dst is src's parent
                                   # link(src, "blocks", dst): src blocks dst

# `note` is any observation — the journal is not only for investigations. `raw` is
# a capture (log excerpt, command output); reads skip raw unless asked, and large
# payloads belong in a file the entry references.
JOURNAL_KINDS = ("finding", "evidence", "decision", "note", "raw")

# An entry body is an excerpt/summary, never a blob: oversized payloads make every
# read (and replay) drag. Full captures live as files the entry points at.
JOURNAL_BODY_LIMIT = 16_000


def space_for_workspace(workspace: str | Path) -> str:
    """Spaces are keyed to the project/workspace (boards are views over a space).
    The resolved path is the one unambiguous local key; a display name is its
    basename."""
    return str(Path(workspace).expanduser().resolve())


class BoardError(Exception):
    """A verb call the board refuses — illegal transition, missing item, bad input."""


class BoardNotFoundError(BoardError):
    """A requested board object is missing or is not visible to the actor."""


class AuthorityError(BoardError):
    """The actor's role does not permit this verb on this item."""


class ChainError(Exception):
    """Hash-chain verification failed — the log was modified out of band."""
