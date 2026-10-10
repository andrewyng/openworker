"""Which per-session controls a model has (UX-056): a thinking switch and reasoning effort."""

from __future__ import annotations

import pytest

from coworker.providers import model_controls as mc
from coworker.providers import effort


def test_the_table_parses_and_every_default_is_one_of_its_levels():
    rows = mc._rows()
    assert rows, "model_controls.json did not load"
    for row in rows:
        r = row.controls.reasoning
        if r.support is mc.Support.SUPPORTED:
            assert r.levels and r.default in r.levels


@pytest.mark.parametrize(
    "provider, model, levels, default",
    [
        ("anthropic", "claude-fable-5-1", ["low", "medium", "high", "xhigh", "max"], "high"),
        ("anthropic", "claude-opus-4-6", ["low", "medium", "high", "max"], "high"),
        ("anthropic", "claude-opus-4-5-20251101", ["low", "medium", "high"], "high"),
        ("openai", "gpt-5.6-sol", ["low", "medium", "high"], "medium"),
        ("openai-codex", "gpt-5.6-terra", ["low", "medium", "high"], "medium"),
        ("openrouter", "moonshotai/kimi-k3", ["low", "high", "max"], "max"),
        ("together", "moonshotai/Kimi-K3", ["low", "high", "max"], "max"),
        ("ollama", "gpt-oss:20b", ["low", "medium", "high"], "medium"),
    ],
)
def test_cloud_and_listed_models_have_reasoning_effort_and_no_switch(provider, model, levels, default):
    c = mc.controls_for(provider, model, local=provider == "ollama", server_thinking=True).as_dict()
    assert c["reasoning"] == {"support": "supported", "levels": levels, "default": default}
    assert c["thinking"] == {"support": "not_supported"}


def test_claude_levels_agree_with_what_the_provider_sends():
    """The table and effort.py's wire mapping must not drift apart."""
    for model in ("claude-fable-5-1", "claude-opus-4-6", "claude-opus-4-5"):
        levels = tuple(lvl.value for lvl in mc.controls_for("anthropic", model).reasoning.levels)
        assert levels == effort._anthropic_supported(model)


def test_local_thinking_models_get_a_switch_from_the_server_or_the_table():
    on = mc.controls_for("ollama", "qwen3:8b", local=True, server_thinking=True).as_dict()
    assert on == {"thinking": {"support": "supported", "default": True}, "reasoning": {"support": "not_supported"}}
    # The server did not say; the recommendation table knows the model thinks.
    rec = mc.controls_for("llamacpp", "nemotron-3.5-lightning", local=True, recommended_thinking=(True, True))
    assert rec.thinking.support is mc.Support.SUPPORTED and rec.thinking.default is True
    # Not a thinking model, or nobody says: no switch.
    assert mc.controls_for("ollama", "llama3.1:8b", local=True, server_thinking=False) == mc.NONE
    assert mc.controls_for("vllm", "some-model", local=True) == mc.NONE


def test_unknown_or_unverified_models_have_neither_control():
    assert mc.controls_for("deepseek", "deepseek-chat") == mc.NONE
    assert mc.controls_for("bedrock", "anthropic.claude-fable-5-1") == mc.NONE  # not verified there
    assert mc.controls_for("llamacpp", "gpt-oss-20b.gguf", local=True) == mc.NONE


def test_saved_settings_become_the_defaults_only_when_valid():
    c = mc.controls_for("anthropic", "claude-fable-5-1", saved={"reasoning_effort": "max"})
    assert c.reasoning.default is mc.EffortLevel.MAX
    c = mc.controls_for("openai", "gpt-5.6-sol", saved={"reasoning_effort": "max"})  # not a GPT level
    assert c.reasoning.default is mc.EffortLevel.MEDIUM
    c = mc.controls_for("ollama", "qwen3:8b", local=True, server_thinking=True, saved={"thinking": False})
    assert c.thinking.default is False
