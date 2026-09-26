"""OPE-171: a reply cut off at the output limit with no action continues the turn.

Before: thinking filled the whole output budget, the provider returned no text and no
tool call with finish_reason "length", and the engine ended the turn as "completed" — the
task was silently abandoned (two of three Fable 5.1 failures in a ten-task trial). Now the
engine nudges the model to act, at most MAX_TRUNCATION_CONTINUATIONS times in a row, and
otherwise ends the turn with the distinct status "truncated". The cut-off reply is kept in
the transcript but replayed to the provider as a short stub (an unsigned partial thinking
block would be rejected by Anthropic; replaying it is useless everywhere else).
"""

from __future__ import annotations

import asyncio

import aisuite as ai
from coworker.engine import (
    MAX_TRUNCATION_CONTINUATIONS,
    TRUNCATION_NUDGE,
    TRUNCATION_STUB,
    TurnEngine,
)
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.tools import ToolRegistry


def _cut(text=None):
    """A length-truncated, action-free reply (thinking ate the budget)."""
    return AssistantTurn(text=text, finish_reason="length", reasoning="Let me think ab")


def _stop(text="done"):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool(name, args, call_id="c1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


class Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0
        self.seen: list[list[dict]] = []  # outbound history per call

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        self.seen.append(messages)
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, turns, **kwargs):
    provider = Scripted(turns)
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        **kwargs,
    )
    return engine, provider


def _run(engine, text="hello"):
    async def _collect():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(_collect())


def _types(events):
    return [ev.type for ev in events]


def _assistants(engine):
    return [m for m in engine.messages if m.get("role") == "assistant"]


# -- continuation -------------------------------------------------------------------


def test_cut_off_reply_is_nudged_then_the_turn_completes(tmp_path):
    engine, provider = _engine(tmp_path, [_cut(), _stop("the answer")])
    events = _run(engine)

    assert provider.calls == 2
    assert _types(events) == [
        EventType.TURN_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.CONTINUATION,
        EventType.ASSISTANT_MESSAGE,
        EventType.TURN_END,
    ]
    assert events[-1].data["status"] == "completed"
    cont = events[2].data
    assert cont["reason"] == "length" and cont["attempt"] == 1
    assert cont["remaining"] == MAX_TRUNCATION_CONTINUATIONS - 1

    # Transcript: the cut-off reply, the nudge, the real answer — in that order.
    roles = [(m["role"], m.get("finish_reason")) for m in engine.messages if m["role"] != "system"]
    assert roles == [("user", None), ("assistant", "length"), ("user", None), ("assistant", "stop")]
    nudge = engine.messages[-2]
    assert nudge["content"] == TRUNCATION_NUDGE
    assert nudge["_display"] == {"kind": "continuation", "reason": "length", "attempt": 1}
    truncated = _assistants(engine)[0]
    assert truncated["replay"] == "stub"
    assert truncated["reasoning"] == "Let me think ab"  # kept for the record


def test_cut_off_reply_is_replayed_as_a_stub_not_verbatim(tmp_path):
    engine, provider = _engine(tmp_path, [_cut(), _stop()])
    _run(engine)
    second_call = provider.seen[1]
    assistant = [m for m in second_call if m["role"] == "assistant"]
    assert assistant == [{"role": "assistant", "content": TRUNCATION_STUB}]
    assert "replay" not in assistant[0] and "reasoning" not in assistant[0]
    # The nudge itself travels as a plain user message (sidecars stripped).
    assert second_call[-1] == {"role": "user", "content": TRUNCATION_NUDGE}


def test_cut_off_with_partial_text_is_nudged_too(tmp_path):
    """Text that stops mid-sentence with no action is still not an answer."""
    engine, provider = _engine(tmp_path, [_cut("The plan is to first"), _stop("full")])
    events = _run(engine)
    assert provider.calls == 2
    assert EventType.CONTINUATION in _types(events)
    assert events[-1].data["status"] == "completed"


def test_cap_ends_the_turn_as_truncated_with_a_notice(tmp_path):
    turns = [_cut() for _ in range(MAX_TRUNCATION_CONTINUATIONS + 1)]
    engine, provider = _engine(tmp_path, turns)
    events = _run(engine)

    assert provider.calls == MAX_TRUNCATION_CONTINUATIONS + 1
    assert _types(events).count(EventType.CONTINUATION) == MAX_TRUNCATION_CONTINUATIONS
    end = events[-1]
    assert end.type == EventType.TURN_END
    assert end.data["status"] == "truncated"
    assert end.data["continuations"] == MAX_TRUNCATION_CONTINUATIONS
    notice = engine.messages[-1]
    assert notice["role"] == "notice" and notice["kind"] == "truncated"
    assert notice["continuations"] == MAX_TRUNCATION_CONTINUATIONS
    assert "output" in notice["text"]


def test_cut_off_reply_with_a_tool_call_is_not_nudged(tmp_path):
    """A truncated reply that still carries a parseable tool call runs the tool as
    before; the mangled-call path already handles the broken-arguments case."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    cut_with_call = AssistantTurn(
        tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"})],
        finish_reason="length",
    )
    engine, provider = _engine(tmp_path, [cut_with_call, _stop("it says hello")])
    events = _run(engine)
    assert EventType.CONTINUATION not in _types(events)
    assert EventType.TOOL_FINISHED in _types(events)
    assert events[-1].data["status"] == "completed"
    assert "replay" not in _assistants(engine)[0]


def test_counter_resets_after_a_good_reply(tmp_path):
    """cut, act, cut, cut, answer: never more than MAX consecutive, so it completes."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    turns = [
        _cut(),
        _tool("read_file", {"path": "a.txt"}),
        _cut(),
        _cut(),
        _stop("finally"),
    ]
    engine, provider = _engine(tmp_path, turns)
    events = _run(engine)
    assert provider.calls == 5
    assert _types(events).count(EventType.CONTINUATION) == 3
    assert events[-1].data["status"] == "completed"


def test_counter_resets_between_turns(tmp_path):
    engine, provider = _engine(tmp_path, [_cut(), _stop(), _cut(), _cut(), _stop()])

    # Both turns inside ONE event loop, as every real caller does: the engine's stop
    # event binds to the loop it first waits on, so a second asyncio.run() would race
    # against a stale event and could end a turn early (a test artefact, not a bug).
    async def _two_turns():
        first = [ev async for ev in engine.run("one")]
        second = [ev async for ev in engine.run("two")]
        return first, second

    first, second = asyncio.run(_two_turns())
    assert first[-1].data["status"] == "completed"
    assert second[-1].data["status"] == "completed"
    assert provider.calls == 5


def test_normal_completion_is_untouched(tmp_path):
    engine, provider = _engine(tmp_path, [_stop("all done")])
    events = _run(engine)
    assert _types(events) == [
        EventType.TURN_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.TURN_END,
    ]
    assert all("replay" not in m for m in engine.messages)


def test_stub_marker_never_reaches_a_provider_and_survives_persistence(tmp_path):
    engine, provider = _engine(tmp_path, [_cut(), _stop()])
    _run(engine)
    assert any(m.get("replay") == "stub" for m in engine.messages)
    for call in provider.seen:
        assert all("replay" not in m for m in call)
    # A fresh engine over the same persisted history replays the stub the same way.
    engine2, provider2 = _engine(tmp_path, [_stop("again")], messages=list(engine.messages))
    _run(engine2, "next")
    assistants = [m for m in provider2.seen[0] if m["role"] == "assistant"]
    assert assistants[0] == {"role": "assistant", "content": TRUNCATION_STUB}
