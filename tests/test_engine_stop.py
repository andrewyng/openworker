"""Stop-button semantics: interrupt must bite in EVERY engine state, not just the
between-iterations checkpoint (v0.1.4 shipped with that as the only one — ledgered
2026-07-21). History invariant throughout: every tool_call gets a tool result, since
hosted chat templates reject orphans and durable-resume re-prompts them."""

from __future__ import annotations

import asyncio
import time

from coworker.engine import ApprovalOutcome, TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    StreamChunk,
    ToolCall,
)
from coworker.tools import ToolRegistry


class EndlessStreamProvider(ProviderClient):
    """Streams deltas ~forever (bounded so a regression fails instead of hanging)."""

    def __init__(self):
        self.chunks_produced = 0

    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()

    def stream(self, *, model, messages, tools=None, **settings):
        for i in range(200):
            self.chunks_produced += 1
            yield StreamChunk(text_delta=f"w{i} ")
            time.sleep(0.01)
        yield StreamChunk(turn=AssistantTurn(text="full", finish_reason="stop"))


def _tool_turn(calls):
    return AssistantTurn(
        tool_calls=[ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)],
        finish_reason="tool_calls",
    )


class OneTurnProvider(ProviderClient):
    def __init__(self, turn):
        self._turn = turn
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        return self._turn

    def capabilities(self, model):
        return ModelCapabilities()


def _tool_results(engine):
    return [m for m in engine.messages if m.get("role") == "tool"]


def test_stop_mid_stream_keeps_partial_text(tmp_path):
    provider = EndlessStreamProvider()
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    async def run():
        events = []
        async for ev in engine.run("go"):
            events.append(ev)
            if ev.type == EventType.ASSISTANT_DELTA and len(events) > 3:
                engine.request_interrupt()
        return events

    events = asyncio.run(run())
    assert events[-1].type == EventType.INTERRUPTED
    # Far fewer than the full 200 chunks were consumed…
    assert provider.chunks_produced < 100
    # …and the partial text the user watched is persisted, with no tool calls.
    last = engine.messages[-1]
    assert last["role"] == "assistant" and last["content"].startswith("w0 ")
    assert "tool_calls" not in last


class FailingStreamProvider(ProviderClient):
    """Streams a few deltas, then dies — a provider outage mid-answer."""

    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()

    def stream(self, *, model, messages, tools=None, **settings):
        yield StreamChunk(text_delta="partial ")
        yield StreamChunk(text_delta="answer")
        raise RuntimeError("provider went away")


def test_provider_error_mid_stream_keeps_partial_text(tmp_path):
    engine = TurnEngine(
        provider=FailingStreamProvider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    async def run():
        return [ev async for ev in engine.run("go")]

    events = asyncio.run(run())
    assert events[-1].type == EventType.ERROR
    last = engine.messages[-1]
    assert last["role"] == "assistant" and last["content"] == "partial answer"
    assert "tool_calls" not in last


def test_stop_while_awaiting_approval(tmp_path):
    async def never_answers(_req):
        await asyncio.Event().wait()  # a pending approval card nobody answers

    registry = ToolRegistry()

    def write_file(path: str, content: str):  # pragma: no cover — never approved
        raise AssertionError("executed while awaiting approval")

    registry.register(write_file)
    engine = TurnEngine(
        provider=OneTurnProvider(_tool_turn([("write_file", {"path": "x", "content": "y"})])),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=never_answers,
    )

    async def run():
        events = []
        async for ev in engine.run("go"):
            events.append(ev)
            if ev.type == EventType.PERMISSION_REQUIRED:
                engine.request_interrupt()
        return events

    events = asyncio.run(run())
    assert events[-1].type == EventType.INTERRUPTED
    (result,) = _tool_results(engine)
    assert "interrupted by user" in result["content"]


def test_stop_skips_remaining_tool_calls(tmp_path):
    registry = ToolRegistry()
    holder = {}

    def first_tool():
        """Runs, then the user hits Stop while it holds the turn."""
        holder["engine"].request_interrupt()
        return {"ok": True}

    def second_tool():  # pragma: no cover — must never run
        raise AssertionError("second tool executed after stop")

    registry.register(first_tool)
    registry.register(second_tool)

    async def approve(_req):
        return ApprovalOutcome.ONCE

    engine = TurnEngine(
        provider=OneTurnProvider(_tool_turn([("first_tool", {}), ("second_tool", {})])),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approve,
    )
    holder["engine"] = engine

    async def run():
        return [ev async for ev in engine.run("go")]

    events = asyncio.run(run())
    assert events[-1].type == EventType.INTERRUPTED
    results = _tool_results(engine)
    assert len(results) == 2  # both calls answered — no orphans
    assert "interrupted by user" in results[1]["content"]


def test_interrupt_hook_fires(tmp_path):
    fired = []
    engine = TurnEngine(
        provider=OneTurnProvider(AssistantTurn(text="hi", finish_reason="stop")),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        interrupt_hooks=[lambda: fired.append(True)],
    )
    engine.request_interrupt()
    assert fired == [True]
