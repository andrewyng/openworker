"""Observability E21: every message carries a stable `mid`.

A recorded step points at the transcript message it produced by its `mid`, a ULID stamped
when the message is appended or inserted. Positions move (a stub inserted mid-history after
a restart, a rewritten file); the `mid` does not. Like `ts`, it must never reach a provider.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


class CapturingProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)
        self.calls: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append([dict(m) for m in messages])
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _engine(tmp_path, turns, *, messages=None, instructions=None):
    provider = CapturingProvider(turns)
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
        instructions=instructions,
    )
    return engine, provider


def _run(engine, text):
    async def _go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(_go())


def test_every_message_gets_a_unique_mid(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, _ = _engine(
        tmp_path,
        [_tool("read_file", {"path": "a.txt"}), _text("it says hello")],
        instructions="be brief",
    )
    _run(engine, "read a.txt")
    assert [m["role"] for m in engine.messages] == ["system", "user", "assistant", "tool", "assistant"]
    mids = [m["mid"] for m in engine.messages]
    assert all(isinstance(mid, str) and len(mid) == 26 for mid in mids)
    assert len(set(mids)) == len(mids)
    assert mids[1:] == sorted(mids[1:])  # sortable by creation


def test_mid_never_reaches_a_provider(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, provider = _engine(tmp_path, [_tool("read_file", {"path": "a.txt"}), _text("done")])
    _run(engine, "read a.txt")
    assert provider.calls
    assert all("mid" not in m for call in provider.calls for m in call)
    assert all("mid" in m for m in engine.messages)  # the strip is a copy


def test_a_stub_inserted_mid_history_gets_its_own_mid_and_others_keep_theirs(tmp_path):
    # A restart left an assistant tool call with no result, followed by later messages.
    history = [
        {"role": "user", "content": "do it", "mid": "01OLDUSER000000000000000000"[:26]},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_9", "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"}}],
            "mid": "01OLDASSIST0000000000000000"[:26],
        },
        {"role": "user", "content": "still there?", "mid": "01OLDUSER200000000000000000"[:26]},
    ]
    before = {m["mid"] for m in history}
    engine, _ = _engine(tmp_path, [_text("yes")], messages=history)
    _run(engine, "hello")
    stub = engine.messages[2]
    assert stub["role"] == "tool" and stub["tool_call_id"] == "call_9"
    assert stub["mid"] not in before
    assert {m["mid"] for m in engine.messages if m["mid"] in before} == before


def test_messages_saved_before_mids_still_work(tmp_path):
    # A transcript written before E21: no mid anywhere. Nothing breaks, nothing is backfilled.
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    engine, provider = _engine(tmp_path, [_text("again")], messages=history)
    _run(engine, "once more")
    assert "mid" not in engine.messages[0] and "mid" not in engine.messages[1]
    assert "mid" in engine.messages[-1]
    assert all("mid" not in m for call in provider.calls for m in call)
