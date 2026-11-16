"""OPE-245: the `timing` sidecar measures with a millisecond clock on every OS.

On Windows `time.monotonic()` ticks every 15.625 ms (GetTickCount64), so a tool that took
4 ms was saved as `tool_ms` 0 or 15.6. `time.perf_counter()` is monotonic too and ticks in
fractions of a microsecond everywhere."""

from __future__ import annotations

import asyncio
import time

from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


class _Provider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


class _ReadOnly:
    requires_approval = False
    risk_level = "low"


def four_ms() -> dict:
    """Busy for about 4 ms."""
    end = time.perf_counter() + 0.004
    while time.perf_counter() < end:
        pass
    return {"ok": True}


def test_tool_ms_has_millisecond_resolution(tmp_path):
    registry = ToolRegistry()
    registry.register(four_ms, metadata=_ReadOnly())
    engine = TurnEngine(
        provider=_Provider([
            AssistantTurn(tool_calls=[ToolCall(id="c1", name="four_ms", arguments={})],
                          finish_reason="tool_calls"),
            AssistantTurn(text="done", finish_reason="stop"),
        ]),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    async def go():
        return [ev async for ev in engine.run("go")]

    asyncio.run(go())
    tool = next(m for m in engine.messages if m.get("role") == "tool")
    # 15.6 ms ticks would give 0 or ~15.6 here.
    assert 3.5 <= tool["timing"]["tool_ms"] < 15
