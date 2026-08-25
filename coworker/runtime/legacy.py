"""Compatibility runtime: preserves established TurnEngine behavior with checkpoints."""

from __future__ import annotations

import time
import uuid
from typing import Any, AsyncIterator

from ..events import Event, EventType
from .base import AgentRuntime
from .types import RuntimeKind, RuntimeState, RuntimeStatus


class LegacyRuntime(AgentRuntime):
    kind = RuntimeKind.LEGACY

    def _new_state(self) -> RuntimeState:
        state = RuntimeState(
            session_id=self.session_id,
            run_id=str(uuid.uuid4()),
            runtime=self.kind.value,
            status=RuntimeStatus.RUNNING.value,
            node="turn_engine",
            messages=list(self.engine.messages),
            created_at=time.time(),
        )
        self.engine.execution_context = {"session_id": self.session_id, "run_id": state.run_id}
        self.store.save(state)
        return state

    async def _drive(self, events: AsyncIterator[Event]) -> AsyncIterator[Event]:
        state = self._new_state()
        try:
            async for event in events:
                state.messages = list(self.engine.messages)
                state.node = event.type.value
                if event.type in {
                    EventType.PERMISSION_REQUIRED, EventType.DIRECTORY_REQUESTED,
                    EventType.QUESTION_REQUESTED, EventType.PLAN_PROPOSED,
                }:
                    state.status = RuntimeStatus.WAITING.value
                    state.pending_approval = dict(event.data)
                elif event.type is EventType.ERROR:
                    state.status = RuntimeStatus.FAILED.value
                    state.errors.append(dict(event.data))
                elif event.type is EventType.INTERRUPTED:
                    state.status = RuntimeStatus.INTERRUPTED.value
                elif event.type is EventType.TURN_END:
                    state.status = RuntimeStatus.COMPLETED.value
                self.store.save(state)
                yield Event(EventType.RUN_STATE, {"run": state.to_public_dict()})
                yield event
        finally:
            if state.status == RuntimeStatus.RUNNING.value:
                state.status = RuntimeStatus.COMPLETED.value
            state.messages = list(self.engine.messages)
            self.store.save(state)

    async def run(self, content: Any, **kwargs: Any) -> AsyncIterator[Event]:
        async for event in self._drive(self.engine.run(content, **kwargs)):
            yield event

    async def retry(self) -> AsyncIterator[Event]:
        async for event in self._drive(self.engine.retry()):
            yield event

    async def resume(self) -> AsyncIterator[Event]:
        async for event in self._drive(self.engine.resume()):
            yield event

    def state(self) -> RuntimeState | None:
        return self.store.get(self.session_id)
