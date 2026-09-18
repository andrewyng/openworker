"""OPE-178: reasoning models on the OpenAI-compatible path get their thinking back.

Kimi K3 (Together / Moonshot / OpenRouter), DeepSeek, GLM… return thinking in a message
field (`reasoning_content`, or `reasoning` via OpenRouter) and — Kimi K3 explicitly —
require it passed back as-is on later turns. The client keeps what it received in the
`_openai_compat` sidecar and re-attaches it under the same field name on every request;
messages that carried no reasoning are byte-identical to before.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aisuite as ai
from coworker.engine import TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import OpenAIProvider
from coworker.providers.openai_provider import _strip_foreign_sidecars, replay_reasoning
from coworker.tools import ToolRegistry


class _FakeOpenAI:
    def __init__(self, *, response=None, stream_chunks=None):
        self.calls: list[dict] = []

        def create(**kwargs):
            self.calls.append(kwargs)
            return stream_chunks if kwargs.get("stream") else response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _response(**message_fields):
    message = SimpleNamespace(content="ok", tool_calls=None, **message_fields)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


def _chunks(field, parts):
    out = []
    for p in parts:
        delta = SimpleNamespace(content=None, tool_calls=None, **{field: p})
        out.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None))
    out.append(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")], usage=None))
    return out


# -- capture ------------------------------------------------------------------------


def test_complete_keeps_reasoning_content_in_the_sidecar():
    turn = OpenAIProvider(client=_FakeOpenAI(response=_response(reasoning_content="think A"))).complete(
        model="moonshotai/Kimi-K3", messages=[{"role": "user", "content": "x"}]
    )
    assert turn.reasoning == "think A"
    assert turn.extras == {"_openai_compat": {"field": "reasoning_content", "text": "think A"}}


def test_complete_keeps_openrouter_reasoning_field_name():
    turn = OpenAIProvider(client=_FakeOpenAI(response=_response(reasoning="think B"))).complete(
        model="moonshotai/kimi-k3", messages=[{"role": "user", "content": "x"}]
    )
    assert turn.extras == {"_openai_compat": {"field": "reasoning", "text": "think B"}}


def test_stream_joins_deltas_under_the_field_they_arrived_in():
    provider = OpenAIProvider(client=_FakeOpenAI(stream_chunks=_chunks("reasoning_content", ["th", "ink"])))
    chunks = list(provider.stream(model="moonshotai/Kimi-K3", messages=[{"role": "user", "content": "x"}]))
    turn = chunks[-1].turn
    assert turn.reasoning == "think"
    assert turn.extras == {"_openai_compat": {"field": "reasoning_content", "text": "think"}}
    provider = OpenAIProvider(client=_FakeOpenAI(stream_chunks=_chunks("reasoning", ["a", "b"])))
    turn = list(provider.stream(model="m", messages=[{"role": "user", "content": "x"}]))[-1].turn
    assert turn.extras["_openai_compat"]["field"] == "reasoning"


def test_no_reasoning_means_no_sidecar():
    turn = OpenAIProvider(client=_FakeOpenAI(response=_response())).complete(
        model="gpt-5.5", messages=[{"role": "user", "content": "x"}]
    )
    assert turn.reasoning is None and turn.extras == {}


# -- replay -------------------------------------------------------------------------


def _history():
    return [
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}],
            "_openai_compat": {"field": "reasoning_content", "text": "plan: run ls"},
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file.txt"},
        {"role": "assistant", "content": "plain reply, no thinking"},
        {"role": "assistant", "content": "foreign sidecar", "_anthropic": {"blocks": []}},
    ]


def test_replay_reattaches_under_the_original_field_and_strips_sidecars():
    wire = _strip_foreign_sidecars(replay_reasoning(_history()))
    assert wire[1]["reasoning_content"] == "plan: run ls"
    assert wire[1]["tool_calls"][0]["id"] == "c1"
    assert "_openai_compat" not in wire[1]
    assert "reasoning_content" not in wire[3] and "reasoning" not in wire[3]
    assert "_anthropic" not in wire[4] and "reasoning_content" not in wire[4]
    assert wire[0] == {"role": "user", "content": "do it"}


def test_replay_uses_openrouter_field_when_that_is_what_arrived():
    history = _history()
    history[1]["_openai_compat"] = {"field": "reasoning", "text": "plan"}
    wire = _strip_foreign_sidecars(replay_reasoning(history))
    assert wire[1]["reasoning"] == "plan" and "reasoning_content" not in wire[1]


def test_replay_is_a_no_op_without_sidecars():
    plain = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    assert replay_reasoning(plain) == plain


def test_the_wire_request_carries_the_reasoning_on_the_next_call():
    fake = _FakeOpenAI(response=_response(reasoning_content="second"))
    OpenAIProvider(client=fake).complete(model="moonshotai/Kimi-K3", messages=_history())
    sent = fake.calls[-1]["messages"]
    assert sent[1]["reasoning_content"] == "plan: run ls"
    assert all(not k.startswith("_") for m in sent for k in m)


# -- engine round trip --------------------------------------------------------------


def test_engine_persists_the_sidecar_and_the_provider_gets_it_back(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")

    def _delta(**fields):
        base = {"content": None, "tool_calls": None}
        base.update(fields)
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**base), finish_reason=fields.pop("finish", None))], usage=None)

    # The engine streams: turn 1 thinks then calls read_file; turn 2 thinks then answers.
    call = SimpleNamespace(index=0, id="c1", function=SimpleNamespace(name="read_file", arguments='{"path": "a.txt"}'))
    first = [
        _delta(reasoning_content="I should read the file"),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]), finish_reason="tool_calls")], usage=None),
    ]
    second = [
        _delta(reasoning_content="it said hello"),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")], usage=None),
    ]
    responses = [first, second]
    fake = _FakeOpenAI()
    fake.chat.completions.create = lambda **kwargs: (fake.calls.append(kwargs), responses.pop(0))[1]
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=OpenAIProvider(client=fake),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="moonshotai/Kimi-K3",
    )

    async def _collect():
        return [ev async for ev in engine.run("read a.txt")]

    events = asyncio.run(_collect())
    assert events[-1].type == EventType.TURN_END and events[-1].data["status"] == "completed"
    persisted = [m for m in engine.messages if m.get("role") == "assistant"]
    assert persisted[0]["_openai_compat"] == {"field": "reasoning_content", "text": "I should read the file"}
    assert persisted[0]["reasoning"] == "I should read the file"  # display sidecar still there
    # The second request carried the first reply's thinking under the original field.
    second_call = fake.calls[1]["messages"]
    replayed = [m for m in second_call if m.get("role") == "assistant"]
    assert replayed[0]["reasoning_content"] == "I should read the file"
    assert "_openai_compat" not in replayed[0] and "reasoning" not in replayed[0]
