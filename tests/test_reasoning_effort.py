"""OPE-176: the `reasoning_effort` setting, its per-provider translation and its record.

Unset keeps every request byte-identical to before (Anthropic = API default high, Kimi K3
on Together = its default max). Set, the level travels via `model_settings`, each provider
maps it to its own parameter, drops it once per model if the endpoint rejects it, and the
engine persists `{requested, effective, param, note}` on every assistant message.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import aisuite as ai
import pytest
from coworker.agent import build_code_engine
from coworker.config import EFFORT_LEVELS, load_config
from coworker.engine import TurnEngine, _assistant_message
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import (
    AnthropicProvider,
    AssistantTurn,
    ModelCapabilities,
    OpenAIProvider,
    ProviderClient,
)
from coworker.providers.anthropic_provider import _uses_budget_thinking
from coworker.providers.effort import (
    BUDGET_TOKENS_BY_LEVEL,
    anthropic_effort,
    nearest_supported,
    openai_compat_effort,
    validate_level,
)
from coworker.providers.matrix import MATRIX
from coworker.tools import ToolRegistry

# -- levels and config --------------------------------------------------------------


def test_levels_are_anthropics_five_in_order():
    assert EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")


@pytest.mark.parametrize("raw,expected", [("high", "high"), (" XHigh ", "xhigh"), ("", None), (None, None)])
def test_validate_level_normalises(raw, expected):
    assert validate_level(raw) == expected


@pytest.mark.parametrize("bad", ["highest", "adaptive", "1", "none"])
def test_validate_level_rejects_unknown(bad):
    with pytest.raises(ValueError, match="reasoning_effort"):
        validate_level(bad)


def test_config_default_unset_and_layering(tmp_path, monkeypatch):
    g = tmp_path / "global.toml"
    assert load_config(global_path=tmp_path / "nope.toml").reasoning_effort is None
    g.write_text('reasoning_effort = "medium"\n')
    assert load_config(global_path=g).reasoning_effort == "medium"
    ws = tmp_path / "ws"
    (ws / ".coworker").mkdir(parents=True)
    (ws / ".coworker" / "config.toml").write_text('reasoning_effort = "XHIGH"\n')
    assert load_config(ws, global_path=g).reasoning_effort == "xhigh"
    monkeypatch.setenv("COWORKER_REASONING_EFFORT", "low")
    assert load_config(ws, global_path=g).reasoning_effort == "low"
    monkeypatch.setenv("COWORKER_REASONING_EFFORT", "turbo")
    with pytest.raises(ValueError, match="COWORKER_REASONING_EFFORT"):
        load_config(ws, global_path=g)


# -- build_engine ---------------------------------------------------------------------


class _Stub(ProviderClient):
    def complete(self, **k):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, m):
        return ModelCapabilities()


def _engine_with_config(tmp_path, toml, **kwargs):
    (tmp_path / ".coworker").mkdir(exist_ok=True)
    (tmp_path / ".coworker" / "config.toml").write_text(toml)
    return build_code_engine(workspace=tmp_path, provider=_Stub(), **kwargs)


def test_build_engine_threads_the_level_into_model_settings(tmp_path):
    engine = _engine_with_config(tmp_path, 'reasoning_effort = "xhigh"\nmax_output_tokens = 64000\n')
    try:
        assert engine.model_settings == {"max_tokens": 64000, "reasoning_effort": "xhigh"}
    finally:
        engine.executor.close()


def test_build_engine_explicit_setting_wins_and_unset_is_empty(tmp_path):
    engine = _engine_with_config(
        tmp_path, 'reasoning_effort = "xhigh"\n', model_settings={"reasoning_effort": "low"}
    )
    try:
        assert engine.model_settings["reasoning_effort"] == "low"
    finally:
        engine.executor.close()
    (tmp_path / "b").mkdir()
    engine = _engine_with_config(tmp_path / "b", "max_iterations = 3\n")
    try:
        assert engine.model_settings == {}
    finally:
        engine.executor.close()


# -- mapping tables -------------------------------------------------------------------


def test_nearest_supported_ties_go_up():
    assert nearest_supported("xhigh", ("low", "high", "max")) == "max"
    assert nearest_supported("medium", ("low", "high", "max")) == "high"
    assert nearest_supported("xhigh", ("low", "medium", "high")) == "high"
    assert nearest_supported("max", ("low", "medium", "high")) == "high"
    assert nearest_supported("high", ("low", "high", "max")) == "high"
    assert nearest_supported("low", ()) is None


@pytest.mark.parametrize(
    "model,level,effective",
    [
        ("claude-fable-5-1", "xhigh", "xhigh"),
        ("claude-fable-5-1", "max", "max"),
        ("claude-sonnet-5", "low", "low"),
        ("claude-opus-5", "medium", "medium"),
        ("claude-sonnet-4-6", "xhigh", "max"),  # no xhigh on 4.6 → nearest, tie up
        ("claude-opus-4-5-20251101", "max", "high"),  # low/medium/high only
        ("claude-opus-4-6", "xhigh", "max"),
    ],
)
def test_anthropic_adaptive_mapping(model, level, effective):
    plan = anthropic_effort(model, level, budget_mode=False)
    assert plan.effective == effective
    assert plan.params == {"output_config": {"effort": effective}}
    assert (plan.note == "") == (effective == level)


def test_anthropic_budget_mode_mapping():
    for level in EFFORT_LEVELS:
        plan = anthropic_effort("claude-haiku-4-5", level, budget_mode=True)
        assert plan.params == {"thinking": {"type": "enabled", "budget_tokens": BUDGET_TOKENS_BY_LEVEL[level]}}
        assert plan.effective == level
    assert BUDGET_TOKENS_BY_LEVEL["low"] >= 1024  # Anthropic minimum


def test_anthropic_unknown_model_sends_level_unverified():
    plan = anthropic_effort("claude-next-9", "xhigh", budget_mode=False)
    assert plan.params == {"output_config": {"effort": "xhigh"}} and "unverified" in plan.note


@pytest.mark.parametrize(
    "model,level,effective,noted",
    [
        ("moonshotai/Kimi-K3", "high", "high", False),
        ("moonshotai/Kimi-K3", "max", "max", False),
        ("moonshotai/Kimi-K3", "xhigh", "max", True),
        ("moonshotai/Kimi-K3", "medium", "high", True),
        ("deepseek-ai/DeepSeek-V4-Pro", "max", "high", True),  # unverified → OpenAI vocabulary
        ("gpt-5.6-sol", "medium", "medium", True),  # unverified table → note, value as-is
    ],
)
def test_openai_compat_mapping(model, level, effective, noted):
    plan = openai_compat_effort(model, level)
    assert plan.effective == effective
    assert plan.params == {"reasoning_effort": effective}
    assert bool(plan.note) == noted


def test_every_curated_model_resolves_every_level():
    """No level on any curated row may raise; providers without a knob are simply skipped."""
    for mid in MATRIX:
        provider, _, bare = mid.partition(":") if ":" in mid else ("openai", "", mid)
        for level in EFFORT_LEVELS:
            if provider == "anthropic":
                plan = anthropic_effort(bare, level, budget_mode=_uses_budget_thinking(bare))
            elif provider in ("gemini", "bedrock", "vertex", "ark", "ark-agent-plan-cn", "openai-codex", "openai"):
                continue  # no chat-completions effort knob on these paths (recorded as such)
            else:
                plan = openai_compat_effort(bare, level)
            assert plan.requested == level and plan.effective in EFFORT_LEVELS, (mid, level)


# -- Anthropic provider ---------------------------------------------------------------


class _FakeAnthropic:
    def __init__(self, response=None, events=None, *, reject_effort=False):
        self.kwargs_seen: list[dict] = []
        self._reject = reject_effort

        def _check(kwargs):
            self.kwargs_seen.append(kwargs)
            if self._reject and "output_config" in kwargs:
                raise RuntimeError("400: output_config.effort: Extra inputs are not permitted")

        def create(**kwargs):
            _check(kwargs)
            if kwargs.get("stream"):
                return iter(events or [])
            return response

        @contextmanager
        def stream(**kwargs):
            _check(kwargs)
            yield SimpleNamespace(get_final_message=lambda: response)

        self.messages = SimpleNamespace(create=create, stream=stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create, stream=stream))

    @property
    def kwargs(self):
        return self.kwargs_seen[-1]


def _anthropic_response():
    return SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")], stop_reason="end_turn")


def _anthropic_events():
    return [
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text", text="")),
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text="hi")),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn")),
    ]


def test_anthropic_unset_sends_no_effort_field():
    fake = _FakeAnthropic(response=_anthropic_response())
    turn = AnthropicProvider(client=fake).complete(model="claude-sonnet-5", messages=[{"role": "user", "content": "x"}])
    assert "output_config" not in fake.kwargs and "reasoning_effort" not in fake.kwargs
    assert turn.effort is None


def test_anthropic_complete_and_stream_send_output_config():
    fake = _FakeAnthropic(response=_anthropic_response(), events=_anthropic_events())
    provider = AnthropicProvider(client=fake)
    turn = provider.complete(model="claude-fable-5-1", messages=[{"role": "user", "content": "x"}], reasoning_effort="xhigh")
    assert fake.kwargs["output_config"] == {"effort": "xhigh"}
    assert "reasoning_effort" not in fake.kwargs
    assert turn.effort == {"requested": "xhigh", "effective": "xhigh", "param": {"output_config": {"effort": "xhigh"}}}
    chunks = list(provider.stream(model="claude-fable-5-1", messages=[{"role": "user", "content": "x"}], reasoning_effort="low"))
    assert fake.kwargs["output_config"] == {"effort": "low"}
    assert chunks[-1].turn.effort["effective"] == "low"


def test_anthropic_budget_model_maps_to_budget_and_floors_max_tokens():
    fake = _FakeAnthropic(response=_anthropic_response())
    turn = AnthropicProvider(client=fake).complete(model="claude-haiku-4-5", messages=[{"role": "user", "content": "x"}], reasoning_effort="max")
    assert fake.kwargs["thinking"] == {"type": "enabled", "budget_tokens": 32_000}
    assert fake.kwargs["max_tokens"] > 32_000
    assert "output_config" not in fake.kwargs
    assert turn.effort["effective"] == "max" and "budget_tokens" in turn.effort["note"]


def test_anthropic_rejection_falls_back_once_and_is_remembered():
    fake = _FakeAnthropic(response=_anthropic_response(), reject_effort=True)
    provider = AnthropicProvider(client=fake)
    turn = provider.complete(model="claude-sonnet-5", messages=[{"role": "user", "content": "x"}], reasoning_effort="low")
    assert len(fake.kwargs_seen) == 2  # rejected, then resent without the field
    assert "output_config" not in fake.kwargs
    assert turn.effort["requested"] == "low" and turn.effort["effective"] is None
    assert "rejected" in turn.effort["note"]
    # Next call on the same model does not try again.
    turn2 = provider.complete(model="claude-sonnet-5", messages=[{"role": "user", "content": "y"}], reasoning_effort="low")
    assert len(fake.kwargs_seen) == 3 and "output_config" not in fake.kwargs
    assert turn2.effort["effective"] is None


def test_anthropic_unrelated_error_still_raises():
    class _Boom(_FakeAnthropic):
        pass

    fake = _Boom(response=_anthropic_response())

    def create(**kwargs):
        raise RuntimeError("overloaded_error")

    fake.messages = SimpleNamespace(create=create, stream=fake.messages.stream)
    with pytest.raises(RuntimeError, match="overloaded"):
        list(AnthropicProvider(client=fake).stream(model="claude-sonnet-5", messages=[{"role": "user", "content": "x"}], reasoning_effort="low"))


# -- OpenAI-compatible provider -------------------------------------------------------


class _FakeOpenAI:
    def __init__(self, *, response=None, stream_chunks=None, reject_effort=False):
        self.calls: list[dict] = []
        self._reject = reject_effort

        def create(**kwargs):
            self.calls.append(kwargs)
            if self._reject and "reasoning_effort" in kwargs:
                raise RuntimeError("Unrecognized request argument supplied: reasoning_effort")
            return stream_chunks if kwargs.get("stream") else response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _openai_response():
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")], usage=None)


def _openai_chunks():
    return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")], usage=None)]


def test_openai_compat_unset_sends_nothing():
    fake = _FakeOpenAI(response=_openai_response())
    turn = OpenAIProvider(client=fake).complete(model="moonshotai/Kimi-K3", messages=[{"role": "user", "content": "x"}])
    assert "reasoning_effort" not in fake.calls[-1] and turn.effort is None


def test_openai_compat_kimi_sends_mapped_value_on_both_paths():
    fake = _FakeOpenAI(response=_openai_response(), stream_chunks=_openai_chunks())
    provider = OpenAIProvider(client=fake)
    turn = provider.complete(model="moonshotai/Kimi-K3", messages=[{"role": "user", "content": "x"}], reasoning_effort="high")
    assert fake.calls[-1]["reasoning_effort"] == "high"
    assert turn.effort == {"requested": "high", "effective": "high", "param": {"reasoning_effort": "high"}}
    chunks = list(provider.stream(model="moonshotai/Kimi-K3", messages=[{"role": "user", "content": "x"}], reasoning_effort="xhigh"))
    assert fake.calls[-1]["reasoning_effort"] == "max"
    assert chunks[-1].turn.effort["effective"] == "max" and "sent max" in chunks[-1].turn.effort["note"]


def test_openai_compat_rejection_drops_the_parameter_and_records_it():
    fake = _FakeOpenAI(response=_openai_response(), reject_effort=True)
    provider = OpenAIProvider(client=fake)
    turn = provider.complete(model="some/other-model", messages=[{"role": "user", "content": "x"}], reasoning_effort="high")
    assert len(fake.calls) == 2 and "reasoning_effort" not in fake.calls[-1]
    assert turn.effort["effective"] is None and "rejected" in turn.effort["note"]
    provider.complete(model="some/other-model", messages=[{"role": "user", "content": "y"}], reasoning_effort="high")
    assert len(fake.calls) == 3 and "reasoning_effort" not in fake.calls[-1]


# -- engine record --------------------------------------------------------------------


class _EffortProvider(ProviderClient):
    def __init__(self, effort):
        self._effort = effort

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop", effort=self._effort)

    def capabilities(self, model):
        return ModelCapabilities()


def _run(tmp_path, provider, **engine_kwargs):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(provider=provider, registry=registry, permissions=PermissionEngine(workspace_root=tmp_path), model="m", **engine_kwargs)

    async def _collect():
        return [ev async for ev in engine.run("hello")]

    return engine, asyncio.run(_collect())


def test_engine_persists_the_provider_record_and_strips_it_outbound(tmp_path):
    rec = {"requested": "xhigh", "effective": "xhigh", "param": {"output_config": {"effort": "xhigh"}}}
    engine, events = _run(tmp_path, _EffortProvider(rec), model_settings={"reasoning_effort": "xhigh"})
    assistant = next(ev for ev in events if ev.type == EventType.ASSISTANT_MESSAGE)
    assert assistant.data["reasoning_effort"] == rec
    persisted = next(m for m in engine.messages if m.get("role") == "assistant")
    assert persisted["reasoning_effort"] == rec
    assert all("reasoning_effort" not in m for m in engine._outbound_messages())


def test_engine_records_a_requested_level_even_when_the_provider_reports_nothing(tmp_path):
    engine, _ = _run(tmp_path, _EffortProvider(None), model_settings={"reasoning_effort": "low"})
    persisted = next(m for m in engine.messages if m.get("role") == "assistant")
    assert persisted["reasoning_effort"]["requested"] == "low"
    assert persisted["reasoning_effort"]["effective"] is None
    assert "not report" in persisted["reasoning_effort"]["note"]


def test_no_record_when_unset():
    assert "reasoning_effort" not in _assistant_message(AssistantTurn(text="x"))
