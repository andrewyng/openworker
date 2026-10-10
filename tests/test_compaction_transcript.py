"""OPE-186 change 3: the summariser can fire earlier (configurable cap), and the compacted
turns stay readable: the verbatim transcript is written to a file the model is told about
in the compacted block. Scripted provider, tiny forced window, no network."""

from __future__ import annotations

import asyncio
import os

from coworker import compaction as C
from coworker.config import COMPACTION_CAP_TOKENS_ENV, load_config
from coworker.engine import TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.tools import ToolRegistry

# Must clear the OPE-189 quality gate: all eight sections, past the minimum length.
SUMMARY = "\n".join(
    f"## {name}\nenough detail in this section to clear the summary minimum length"
    for name in (
        "Primary request and intent",
        "Key concepts and decisions",
        "Artifacts and files",
        "Errors and fixes",
        "All user messages",
        "Pending tasks",
        "Current work",
        "Next step",
    )
)


class CompactingProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if messages and "compacting an AI coworker" in str(messages[0].get("content", "")):
            return AssistantTurn(text=SUMMARY, finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _history(turns=8, bulk=1500):
    msgs = [{"role": "system", "content": "be helpful"}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"request {i}", "ts": 1.0})
        msgs.append(
            {
                "role": "assistant",
                "content": f"answer {i} " + "x" * bulk,
                "ts": 1.0,
                "reasoning": "private thinking " + "r" * 200,
                "tool_calls": [{"id": f"c{i}", "function": {"name": "run_shell", "arguments": '{"command": "ls -la"}'}}],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"listing {i}: file_{i}.txt", "ts": 1.0})
    return msgs


def _collect(engine):
    async def go():
        return [e async for e in engine.run("continue")]

    return asyncio.run(go())


def test_compaction_writes_transcript_and_tells_the_model(tmp_path):
    spill = tmp_path / "spill"
    engine = TurnEngine(
        provider=CompactingProvider([AssistantTurn(text="done", finish_reason="stop")]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=_history(),
        tool_result_spill_dir=spill,
    )
    engine.compaction_settings = lambda: {"cap_tokens": 400, "threshold_pct": 0.8, "context_window": 100_000}
    events = _collect(engine)
    assert any(e.type == EventType.COMPACTED for e in events)
    state = engine.compaction_state
    assert state is not None and state.transcript_path
    files = list(spill.glob("compacted-transcript-upto-*.md"))
    assert len(files) == 1 and str(files[0]) == state.transcript_path
    text = files[0].read_text(encoding="utf-8")
    # Verbatim content of the compacted turns: text, tool calls with arguments, results.
    assert "request 0" in text and "answer 0" in text
    assert "tool call: run_shell" in text and '"command": "ls -la"' in text
    assert "listing 0: file_0.txt" in text
    # Private reasoning is not written out.
    assert "private thinking" not in text
    # The compacted block the model receives names the file.
    out = engine._outbound_messages()
    assert state.transcript_path in out[1]["content"]
    assert "read that file" in out[1]["content"]
    # The record round-trips with the path.
    assert C.CompactionState.from_dict(state.as_dict()).transcript_path == state.transcript_path


def test_no_spill_dir_means_no_transcript_and_no_mention(tmp_path):
    engine = TurnEngine(
        provider=CompactingProvider([AssistantTurn(text="done", finish_reason="stop")]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=_history(),
    )
    engine.compaction_settings = lambda: {"cap_tokens": 400, "threshold_pct": 0.8, "context_window": 100_000}
    _collect(engine)
    state = engine.compaction_state
    assert state is not None and state.transcript_path == ""
    assert "saved at" not in engine._outbound_messages()[1]["content"]


def test_compaction_cap_config_and_env(tmp_path, monkeypatch):
    (tmp_path / ".coworker").mkdir()
    (tmp_path / ".coworker" / "config.toml").write_text("compaction_cap_tokens = 60000\n", encoding="utf-8")
    monkeypatch.delenv(COMPACTION_CAP_TOKENS_ENV, raising=False)
    cfg = load_config(tmp_path, global_path=tmp_path / "no-global.toml")
    assert cfg.compaction_cap_tokens == 60000  # a workspace config.toml may set it
    monkeypatch.setenv(COMPACTION_CAP_TOKENS_ENV, "80000")
    cfg = load_config(tmp_path, global_path=tmp_path / "no-global.toml")
    assert cfg.compaction_cap_tokens == 80000
    monkeypatch.setenv(COMPACTION_CAP_TOKENS_ENV, "0")
    try:
        load_config(tmp_path, global_path=tmp_path / "no-global.toml")
    except ValueError as exc:
        assert "compaction_cap_tokens" in str(exc)
    else:
        raise AssertionError("a zero cap must be rejected")


def test_summary_budget_config_and_env(tmp_path, monkeypatch):
    """OPE-189: the summariser's output ceiling is a setting, not a constant — on a
    reasoning model it is shared with the model's thinking."""
    from coworker.config import COMPACTION_SUMMARY_MAX_TOKENS_ENV

    monkeypatch.delenv(COMPACTION_SUMMARY_MAX_TOKENS_ENV, raising=False)
    (tmp_path / ".coworker").mkdir()
    cfg_file = tmp_path / ".coworker" / "config.toml"
    cfg_file.write_text("compaction_summary_max_tokens = 24000\n", encoding="utf-8")
    cfg = load_config(tmp_path, global_path=tmp_path / "no-global.toml")
    assert cfg.compaction_summary_max_tokens == 24000
    monkeypatch.setenv(COMPACTION_SUMMARY_MAX_TOKENS_ENV, "9000")
    cfg = load_config(tmp_path, global_path=tmp_path / "no-global.toml")
    assert cfg.compaction_summary_max_tokens == 9000  # env wins
    monkeypatch.setenv(COMPACTION_SUMMARY_MAX_TOKENS_ENV, "0")
    try:
        load_config(tmp_path, global_path=tmp_path / "no-global.toml")
    except ValueError as exc:
        assert "compaction_summary_max_tokens" in str(exc)
    else:
        raise AssertionError("a zero budget must be rejected")


def test_engine_passes_the_configured_budgets_to_the_summarizer(tmp_path):
    """The trigger drives both derived budgets, and the summariser call gets the ceiling."""
    seen: dict = {}

    class Recorder(CompactingProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            seen.update(settings)
            return super().complete(model=model, messages=messages, tools=tools, **settings)

    engine = TurnEngine(
        provider=Recorder([AssistantTurn(text="done", finish_reason="stop")]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=_history(),
    )
    engine.compaction_settings = lambda: {
        "cap_tokens": 400,
        "threshold_pct": 0.8,
        "context_window": 100_000,
        "summary_max_tokens": 7_777,
    }
    _collect(engine)
    assert seen.get("max_tokens") == 7_777
    assert C.user_message_budget(400) == C._USER_BUDGET_MIN  # floor at a tiny trigger


def test_render_transcript_is_readable_and_skips_system():
    text = C.render_transcript(_history(turns=2), upto=7)
    assert text.startswith("# Compacted transcript")
    assert "be helpful" not in text  # system prompt is not part of the compacted span
    assert "## [1] user" in text and "## [2] assistant" in text and "## [3] tool" in text
