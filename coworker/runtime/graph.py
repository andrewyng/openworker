"""LangGraph-backed runtime that streams the established OpenWork engine safely."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncIterator

from ..events import Event, EventType
from .base import AgentRuntime
from .types import RuntimeKind, RuntimeState, RuntimeStatus


class GraphRuntime(AgentRuntime):
    """Durably run an OpenWork turn inside a small LangGraph state machine.

    The graph does not receive tool callables.  Its only executable node calls the existing
    TurnEngine, retaining its provider adapter, permissions, audit, Inbox and root checks.
    """

    kind = RuntimeKind.GRAPH

    @staticmethod
    def available() -> bool:
        try:
            import langgraph.graph  # noqa: F401
        except ImportError:
            return False
        return True

    def _new_state(self) -> RuntimeState:
        state = RuntimeState(
            session_id=self.session_id,
            run_id=str(uuid.uuid4()),
            runtime=self.kind.value,
            status=RuntimeStatus.RUNNING.value,
            node="prepare",
            messages=list(self.engine.messages),
            created_at=time.time(),
        )
        self.engine.execution_context = {"session_id": self.session_id, "run_id": state.run_id}
        self.store.save(state)
        return state

    async def _drive(self, action: str, content: Any = None, **kwargs: Any) -> AsyncIterator[Event]:
        state = self._new_state()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()

        async def execute(_graph_state: dict[str, Any]) -> dict[str, Any]:
            state.node = "turn_engine"
            self.store.save(state)
            stream = (
                self.engine.run(content, **kwargs)
                if action == "run"
                else self.engine.retry() if action == "retry" else self.engine.resume()
            )
            async for event in stream:
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
                await queue.put(event)
                await queue.put(Event(EventType.RUN_STATE, {"run": state.to_public_dict()}))
            return {"messages": list(self.engine.messages), "node": "finalize"}

        async def finalize(graph_state: dict[str, Any]) -> dict[str, Any]:
            if state.status == RuntimeStatus.RUNNING.value:
                state.status = RuntimeStatus.COMPLETED.value
            state.node = "finalize"
            state.messages = list(self.engine.messages)
            self.store.save(state)
            return graph_state

        from langgraph.graph import END, START, StateGraph
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        graph = StateGraph(dict)
        graph.add_node("execute", execute)
        graph.add_node("finalize", finalize)
        graph.add_edge(START, "execute")
        graph.add_edge("execute", "finalize")
        graph.add_edge("finalize", END)
        # Keep LangGraph checkpoints in a sibling local SQLite file. A separate connection to
        # the inspectable runtime-state table can otherwise contend with the graph writer during
        # streaming on Windows. Both files are local-only and move as one state directory.
        checkpoint_path = self.store.path.with_name("langgraph-checkpoints.db")
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            task = asyncio.create_task(
                graph.compile(checkpointer=checkpointer).ainvoke(
                    {"session_id": state.session_id, "run_id": state.run_id, "messages": state.messages},
                    config={"configurable": {"thread_id": self.session_id}},
                )
            )
            try:
                while not task.done() or not queue.empty():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.05)
                    except asyncio.TimeoutError:
                        continue
                    if event is not None:
                        yield event
                await task
            except Exception as exc:
                state.status = RuntimeStatus.FAILED.value
                state.errors.append({"error": str(exc), "node": state.node})
                self.store.save(state)
                raise
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if state.status == RuntimeStatus.RUNNING.value:
                    state.status = RuntimeStatus.COMPLETED.value
                state.messages = list(self.engine.messages)
                self.store.save(state)

    async def run(self, content: Any, **kwargs: Any) -> AsyncIterator[Event]:
        async for event in self._drive("run", content, **kwargs):
            yield event

    async def retry(self) -> AsyncIterator[Event]:
        async for event in self._drive("retry"):
            yield event

    async def resume(self) -> AsyncIterator[Event]:
        async for event in self._drive("resume"):
            yield event

    def state(self) -> RuntimeState | None:
        return self.store.get(self.session_id)
