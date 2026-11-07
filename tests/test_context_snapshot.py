"""`context_snapshot` — the window-occupancy readout behind the GUI's Context panel (FB-015).

The split (system / tools / messages) is always a chars/4 estimate; `used` prefers the
provider-reported prompt-side total from the latest round trip and says when it had to
fall back to the estimate.
"""

from __future__ import annotations

import pytest

from coworker.engine import EventType, TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
)
from coworker.providers.base import TokenUsage
from coworker.tools import ToolRegistry


class OneReplyProvider(ProviderClient):
    def __init__(self, usage: TokenUsage | None = None):
        self.usage = usage

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", tool_calls=[], usage=self.usage)

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _engine(tmp_path, provider):
    return TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE),
        model="m",
    )


def test_estimate_fallback_splits_system_tools_and_messages(tmp_path):
    engine = _engine(tmp_path, OneReplyProvider())
    engine.messages.append({"role": "system", "content": "s" * 400})
    engine.messages.append({"role": "user", "content": "u" * 800})

    snap = engine.context_snapshot()

    assert snap["estimated"] is True
    b = snap["breakdown"]
    # chars/4 over the serialized message: at least the content's share, in each bucket.
    assert b["system"] >= 100
    assert b["messages"] >= 200
    assert b["tools"] == 0  # a bare registry carries no schemas
    assert snap["used"] == b["system"] + b["tools"] + b["messages"]
    # An unlisted model has no window; the GUI shows "window unknown" (UX-048).
    assert snap["window"] is None
    assert "compaction" not in snap  # never compacted


@pytest.mark.asyncio
async def test_reported_usage_wins_and_rides_the_assistant_message(tmp_path):
    usage = TokenUsage(input=900, output=10, cache_read=2100, cache_write=0)
    engine = _engine(tmp_path, OneReplyProvider(usage))

    events = [e async for e in engine.run("hello")]

    replies = [e for e in events if e.type is EventType.ASSISTANT_MESSAGE]
    snap = replies[-1].data["context"]
    assert snap["used"] == usage.context_tokens  # 900 + 2100
    assert snap["estimated"] is False
    assert set(snap["breakdown"]) == {"system", "tools", "messages"}
