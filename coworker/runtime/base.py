"""Runtime protocol used by the server without changing the existing engine API."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from ..events import Event
from .types import RuntimeKind, RuntimeState


class AgentRuntime(ABC):
    kind: RuntimeKind

    def __init__(self, *, session_id: str, engine: Any, store: Any) -> None:
        self.session_id = session_id
        self.engine = engine
        self.store = store

    @abstractmethod
    async def run(self, content: Any, **kwargs: Any) -> AsyncIterator[Event]: ...

    @abstractmethod
    async def retry(self) -> AsyncIterator[Event]: ...

    @abstractmethod
    async def resume(self) -> AsyncIterator[Event]: ...

    @abstractmethod
    def state(self) -> RuntimeState | None: ...

    def request_interrupt(self) -> None:
        self.engine.request_interrupt()
