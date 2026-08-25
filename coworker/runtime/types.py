"""Serializable runtime state shared by the legacy and LangGraph runtimes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RuntimeKind(str, Enum):
    LEGACY = "legacy"
    GRAPH = "graph"


class RuntimeStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


@dataclass
class RuntimeState:
    """The minimal, JSON-only execution checkpoint.

    Clients, executors, secrets and callables intentionally never enter this object.
    It is safe to persist locally and to return through the read-only run-state API.
    """

    session_id: str
    run_id: str
    runtime: str = RuntimeKind.GRAPH.value
    version: int = 1
    status: str = RuntimeStatus.IDLE.value
    node: str = "idle"
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    todos: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    delegations: list[dict[str, Any]] = field(default_factory=list)
    pending_approval: dict[str, Any] | None = None
    context_refs: list[dict[str, Any]] = field(default_factory=list)
    memory_refs: list[dict[str, Any]] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        """Small WebSocket-safe status view; canonical messages stay in ConversationStore."""
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "runtime": self.runtime,
            "version": self.version,
            "status": self.status,
            "node": self.node,
            "artifacts": self.artifacts,
            "delegations": self.delegations,
            "pending_approval": self.pending_approval,
            "budget": self.budget,
            "usage": self.usage,
            "errors": self.errors[-1:],
            "retries": self.retries,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RuntimeState":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value[key] for key in allowed if key in value})
