"""OPE-177: the per-reply output-token limit is configurable.

`max_output_tokens` (config.toml, `COWORKER_MAX_OUTPUT_TOKENS`, or an explicit
`model_settings["max_tokens"]`) reaches every provider request as `max_tokens`; unset keeps
each provider's own default byte-for-byte. The value the provider actually sent (after the
Anthropic budget-thinking floor, or the OpenAI `max_completion_tokens` rename) comes back on
`AssistantTurn.output_limit` and is persisted on the assistant message as the
`max_output_tokens` sidecar, next to `usage` and `finish_reason`.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import aisuite as ai
import pytest
from coworker.agent import build_code_engine
from coworker.config import load_config
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
from coworker.providers.anthropic_provider import DEFAULT_MAX_TOKENS as ANTHROPIC_DEFAULT
from coworker.providers.openai_provider import _MAX_TOKENS_ERROR
from coworker.providers.openai_provider import DEFAULT_MAX_TOKENS as OPENAI_DEFAULT
from coworker.tools import ToolRegistry

# -- config -------------------------------------------------------------------------


def test_config_default_is_unset(tmp_path):
    cfg = load_config(global_path=tmp_path / "nope.toml")
    assert cfg.max_output_tokens is None


def test_config_global_then_workspace_override(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text("max_output_tokens = 64000\n")
    assert load_config(global_path=g).max_output_tokens == 64000
    ws = tmp_path / "ws"
    (ws / ".coworker").mkdir(parents=True)
    (ws / ".coworker" / "config.toml").write_text("max_output_tokens = 48000\n")
    assert load_config(ws, global_path=g).max_output_tokens == 48000


def test_config_env_override_beats_files(tmp_path, monkeypatch):
    g = tmp_path / "global.toml"
    g.write_text("max_output_tokens = 64000\n")
    monkeypatch.setenv("COWORKER_MAX_OUTPUT_TOKENS", "70000")
    assert load_config(global_path=g).max_output_tokens == 70000


@pytest.mark.parametrize(
    "toml_value,env_value",
    [("0", "0"), ("-5", "-5"), ('"lots"', "lots"), ("1.5", "1.5"), ("true", "true")],
)
def test_config_rejects_non_positive_or_non_integer(tmp_path, monkeypatch, toml_value, env_value):
    g = tmp_path / "global.toml"
    g.write_text(f"max_output_tokens = {toml_value}\n")
    with pytest.raises(ValueError, match="max_output_tokens"):
        load_config(global_path=g)
    g.write_text("")
    monkeypatch.setenv("COWORKER_MAX_OUTPUT_TOKENS", env_value)
    with pytest.raises(ValueError, match="max_output_tokens"):
        load_config(global_path=g)


# -- build_engine -------------------------------------------------------------------


class _Stub(ProviderClient):
    def complete(self, **k):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, m):
        return ModelCapabilities()


def _engine_with_config(tmp_path, toml: str, **kwargs):
    (tmp_path / ".coworker").mkdir(exist_ok=True)
    (tmp_path / ".coworker" / "config.toml").write_text(toml)
    return build_code_engine(workspace=tmp_path, provider=_Stub(), **kwargs)


def test_build_engine_passes_config_value_as_max_tokens(tmp_path):
    engine = _engine_with_config(tmp_path, "max_output_tokens = 64000\n")
    try:
        assert engine.model_settings["max_tokens"] == 64000
    finally:
        engine.executor.close()


def test_build_engine_explicit_model_settings_win_over_config(tmp_path):
    engine = _engine_with_config(
        tmp_path, "max_output_tokens = 64000\n", model_settings={"max_tokens": 1000}
    )
    try:
        assert engine.model_settings["max_tokens"] == 1000
    finally:
        engine.executor.close()


def test_build_engine_unset_leaves_model_settings_empty(tmp_path):
    engine = _engine_with_config(tmp_path, "max_iterations = 3\n")
    try:
        assert engine.model_settings == {}
    finally:
        engine.executor.close()


# -- Anthropic ----------------------------------------------------------------------


class _FakeAnthropic:
    def __init__(self, response=None, events=None):
        self.kwargs: dict = {}

        def create(**kwargs):
            self.kwargs = kwargs
            if kwargs.get("stream"):
                return iter(events or [])
            return response

        @contextmanager
        def stream(**kwargs):
            self.kwargs = kwargs
            yield SimpleNamespace(get_final_message=lambda: response)

        self.messages = SimpleNamespace(create=create, stream=stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create, stream=stream))


def _anthropic_response(stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hi")], stop_reason=stop_reason
    )


def _anthropic_events():
    return [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="hi"),
        ),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn")),
    ]


def test_anthropic_complete_and_stream_send_configured_limit():
    fake = _FakeAnthropic(response=_anthropic_response(), events=_anthropic_events())
    provider = AnthropicProvider(client=fake)
    turn = provider.complete(
        model="m", messages=[{"role": "user", "content": "x"}], max_tokens=64000
    )
    assert fake.kwargs["max_tokens"] == 64000
    assert turn.output_limit == 64000

    chunks = list(
        provider.stream(
            model="m", messages=[{"role": "user", "content": "x"}], max_tokens=64000
        )
    )
    assert fake.kwargs["max_tokens"] == 64000
    assert chunks[-1].turn.output_limit == 64000


def test_anthropic_default_path_unchanged():
    fake = _FakeAnthropic(response=_anthropic_response())
    turn = AnthropicProvider(client=fake).complete(
        model="m", messages=[{"role": "user", "content": "x"}]
    )
    assert fake.kwargs["max_tokens"] == ANTHROPIC_DEFAULT
    assert turn.output_limit == ANTHROPIC_DEFAULT


def test_anthropic_budget_floor_overrides_small_value_and_warns(caplog):
    fake = _FakeAnthropic(response=_anthropic_response())
    provider = AnthropicProvider(client=fake, thinking_budget=8192)
    with caplog.at_level(logging.WARNING, logger="coworker.providers.anthropic_provider"):
        turn = provider.complete(
            model="claude-haiku-4-5",
            messages=[{"role": "user", "content": "x"}],
            max_tokens=4000,
        )
    floor = max(ANTHROPIC_DEFAULT, 8192 + 4096)
    assert fake.kwargs["max_tokens"] == floor
    assert turn.output_limit == floor
    assert any("max_output_tokens" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="coworker.providers.anthropic_provider"):
        turn = provider.complete(
            model="claude-haiku-4-5",
            messages=[{"role": "user", "content": "x"}],
            max_tokens=64000,
        )
    assert fake.kwargs["max_tokens"] == 64000
    assert turn.output_limit == 64000
    assert not caplog.records


# -- OpenAI-compatible --------------------------------------------------------------


class _FakeOpenAI:
    """Records every create() kwargs; can reject `max_tokens` once like a reasoning model."""

    def __init__(self, *, stream_chunks=None, response=None, reject_max_tokens_once=False):
        self.calls: list[dict] = []
        self._reject = reject_max_tokens_once

        def create(**kwargs):
            self.calls.append(kwargs)
            if self._reject and "max_tokens" in kwargs:
                self._reject = False
                raise RuntimeError(
                    "Unsupported parameter: " + _MAX_TOKENS_ERROR + " with this model."
                )
            return stream_chunks if kwargs.get("stream") else response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _openai_chunks():
    return [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="hi", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
    ]


def _openai_response():
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop"
            )
        ],
        usage=None,
    )


def test_openai_stream_and_complete_send_configured_limit():
    fake = _FakeOpenAI(stream_chunks=_openai_chunks(), response=_openai_response())
    provider = OpenAIProvider(client=fake)
    chunks = list(
        provider.stream(
            model="m", messages=[{"role": "user", "content": "x"}], max_tokens=64000
        )
    )
    assert fake.calls[-1]["max_tokens"] == 64000
    assert chunks[-1].turn.output_limit == 64000

    turn = provider.complete(
        model="m", messages=[{"role": "user", "content": "x"}], max_tokens=64000
    )
    assert fake.calls[-1]["max_tokens"] == 64000
    assert turn.output_limit == 64000


def test_openai_default_path_unchanged():
    fake = _FakeOpenAI(response=_openai_response())
    turn = OpenAIProvider(client=fake).complete(
        model="m", messages=[{"role": "user", "content": "x"}]
    )
    assert fake.calls[-1]["max_tokens"] == OPENAI_DEFAULT
    assert turn.output_limit == OPENAI_DEFAULT


def test_openai_rename_retry_keeps_the_configured_limit():
    fake = _FakeOpenAI(response=_openai_response(), reject_max_tokens_once=True)
    turn = OpenAIProvider(client=fake).complete(
        model="m", messages=[{"role": "user", "content": "x"}], max_tokens=64000
    )
    assert len(fake.calls) == 2
    assert "max_tokens" not in fake.calls[-1]
    assert fake.calls[-1]["max_completion_tokens"] == 64000
    assert turn.output_limit == 64000


# -- engine record ------------------------------------------------------------------


class _LimitProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop", output_limit=64000)

    def capabilities(self, model):
        return ModelCapabilities()


def test_engine_records_output_limit_on_message_and_event(tmp_path):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=_LimitProvider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    async def _collect():
        return [ev async for ev in engine.run("hello")]

    events = asyncio.run(_collect())
    assistant = next(ev for ev in events if ev.type == EventType.ASSISTANT_MESSAGE)
    assert assistant.data["max_output_tokens"] == 64000
    persisted = next(m for m in engine.messages if m.get("role") == "assistant")
    assert persisted["max_output_tokens"] == 64000
    assert all("max_output_tokens" not in m for m in engine._outbound_messages())


def test_assistant_message_omits_limit_when_unknown():
    assert "max_output_tokens" not in _assistant_message(AssistantTurn(text="x"))
