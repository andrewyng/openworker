"""Mangled tool calls (`{"_raw": …}` fallback args) get a real diagnosis, not execution.

The field failure (2026-08-15, report-page generation): a large write_file call blew the
output-token limit, the truncated JSON became `{"_raw": …}`, and the tool's bare
parameter error sent the model into a "wrong parameter" retry loop. Worse, each stored
`_raw` call read as a worked example — after a few, the model began emitting `_raw` as
if it were a real parameter, and every replay re-sent thousands of junk tokens.
"""

from __future__ import annotations

import json

import pytest

from coworker.engine import EventType, TurnEngine, _MANGLED_PREVIEW_CHARS
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


class MangledProvider(ProviderClient):
    """One turn with unparseable write_file args, then a plain reply."""

    def __init__(self, finish_reason: str, raw: str = "x" * 5000):
        self.calls = 0
        self.finish_reason = finish_reason
        self.raw = raw

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="write_file", arguments={"_raw": self.raw})
                ],
                finish_reason=self.finish_reason,
            )
        return AssistantTurn(text="done", tool_calls=[])

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _engine(tmp_path, provider) -> TurnEngine:
    return TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE),
        model="m",
    )


async def _run(engine) -> list:
    return [e async for e in engine.run("build the report")]


def _last_tool_message(engine) -> str:
    return str([m for m in engine.messages if m.get("role") == "tool"][-1]["content"])


@pytest.mark.asyncio
async def test_truncated_call_is_answered_with_the_truncation_diagnosis(tmp_path):
    """finish_reason "length" + unparseable args = the call was cut off. The model's cure
    is smaller pieces — the error must say so instead of a bare parameter complaint."""
    engine = _engine(tmp_path, MangledProvider("length"))
    events = await _run(engine)

    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished[0].data["status"] == "error"
    body = _last_tool_message(engine)
    assert "output-token limit" in body
    assert "smaller pieces" in body
    # The turn continues — no retry loop, the model answers after the diagnosis.
    assert [e for e in events if e.type is EventType.TURN_END]


@pytest.mark.asyncio
async def test_plain_bad_json_is_answered_with_the_parameter_diagnosis(tmp_path):
    """No truncation → the model wrote bad JSON (or imitated `_raw`). Tell it `_raw` is
    not a parameter and to re-issue with the declared ones."""
    engine = _engine(tmp_path, MangledProvider("stop"))
    await _run(engine)
    body = _last_tool_message(engine)
    assert "_raw" in body and "not a parameter" in body
    assert "declared parameters" in body


@pytest.mark.asyncio
async def test_raw_junk_is_shrunk_before_it_enters_history(tmp_path):
    """The unparsed text must not be replayed at full size: it costs tokens every turn
    and teaches the model that `_raw` is a shape to imitate."""
    engine = _engine(tmp_path, MangledProvider("length", raw="y" * 5000))
    await _run(engine)

    assistant = [m for m in engine.messages if m.get("role") == "assistant" and m.get("tool_calls")][0]
    stored = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
    assert len(stored["_raw"]) < _MANGLED_PREVIEW_CHARS + 100
    assert "truncated in history" in stored["_raw"]


@pytest.mark.asyncio
async def test_short_raw_args_are_kept_verbatim(tmp_path):
    """Below the preview cap there is nothing to shrink — storage stays faithful."""
    engine = _engine(tmp_path, MangledProvider("stop", raw='{"path": "repo'))
    await _run(engine)
    assistant = [m for m in engine.messages if m.get("role") == "assistant" and m.get("tool_calls")][0]
    stored = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
    assert stored == {"_raw": '{"path": "repo'}
