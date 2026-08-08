"""Tests for the AI command intent analysis module."""
from unittest.mock import MagicMock

from coworker.intent_analysis.analyzer import analyze, extract_input, _clean
from coworker.intent_analysis.prompts import (
    build_system_prompt,
    build_user_prompt,
    MAX_INPUT_CHARS,
    MAX_BULLETS,
)


def _tc(name, **args):
    """Build a duck-typed tool_call fixture."""
    tc = MagicMock()
    tc.name = name
    tc.arguments = args
    return tc


# -- prompts --


def test_build_system_prompt_has_rules():
    s = build_system_prompt(2)
    assert "bullet" in s.lower()
    assert "1-2" in s  # max_bullets is injected


def test_build_system_prompt_clamps_bullets():
    s = build_system_prompt(MAX_BULLETS + 10)
    assert f"1-{MAX_BULLETS}" in s  # clamped to the cap


def test_build_system_prompt_clamps_lower_bound():
    s = build_system_prompt(0)
    assert "1-1" in s  # clamped to 1
    s2 = build_system_prompt(-5)
    assert "1-1" in s2


def test_build_user_prompt_includes_input():
    u = build_user_prompt("rm -rf /tmp")
    assert "rm -rf /tmp" in u
    assert "Return only the bullet points" in u


def test_build_user_prompt_truncates_long_input():
    long = "x" * (MAX_INPUT_CHARS + 50)
    u = build_user_prompt(long)
    assert len(u) < len(long) + 200  # truncated


def test_build_user_prompt_none_is_safe():
    """None/empty input must not raise (defensive guard)."""
    u = build_user_prompt(None)
    assert "Return only the bullet points" in u


# -- extract_input --


def test_extract_input_shell():
    tc = _tc("run_shell", command="rm -rf /tmp")
    assert extract_input(tc) == "rm -rf /tmp"


def test_extract_input_file_write():
    tc = _tc("write_file", path="/etc/config")
    out = extract_input(tc)
    assert "write_file" in out and "/etc/config" in out


def test_extract_input_replace_in_file():
    tc = _tc("replace_in_file", path="/app/main.py")
    out = extract_input(tc)
    assert "replace_in_file" in out


def test_extract_input_send_message_target():
    """send_message's real param is 'target', not 'destination'/'channel'."""
    tc = _tc("send_message", target="slack:#general", text="hello")
    out = extract_input(tc)
    assert "slack:#general" in out and "hello" in out


def test_extract_input_fallback():
    tc = _tc("unknown_tool", foo="bar")
    out = extract_input(tc)
    assert "unknown_tool" in out and "foo" in out


# -- _clean --


def test_clean_valid_bullets():
    assert _clean("• deletes file\n• unrecoverable") == "• deletes file\n• unrecoverable"


def test_clean_strips_fences():
    assert _clean("```\n• deletes file\n```") == "• deletes file"


def test_clean_strips_leading_intent_label():
    assert _clean("Intent:\n• deletes file") == "• deletes file"


def test_clean_empty_returns_none():
    assert _clean("") is None
    assert _clean("no bullets here") is None


# -- analyze (positional signature) --


def test_analyze_positional_signature():
    """analyze(tc, prov, mdl) must accept positional args (engine calls it via
    asyncio.to_thread(self.intent_analyzer, tool_call, provider, model))."""
    prov = MagicMock()
    prov.complete.return_value = MagicMock(text="• ok")
    result = analyze(_tc("run_shell", command="ls"), prov, "test-model")
    assert result == "• ok"


def test_analyze_success():
    prov = MagicMock()
    prov.complete.return_value = MagicMock(text="• permanently deleted\n• unrecoverable")
    result = analyze(_tc("run_shell", command="rm x"), prov, "m")
    assert "permanently deleted" in result


def test_analyze_provider_error_returns_none():
    prov = MagicMock()
    prov.complete.side_effect = RuntimeError("network down")
    assert analyze(_tc("run_shell", command="rm x"), prov, "m") is None


def test_analyze_empty_output_returns_none():
    prov = MagicMock()
    prov.complete.return_value = MagicMock(text="")
    assert analyze(_tc("run_shell", command="rm x"), prov, "m") is None
