"""OPE-170 problem 1: the model matrix knows the current models' context windows.

A model missing from the matrix falls back to `DEFAULT_CONTEXT_WINDOW` (128,000), so
auto-compaction fires at 102,400 tokens — half or a tenth of what the newest models have
(observed on Claude Sonnet 5: two compactions in one 33-minute run, each rebuilding the
prompt cache). These tests pin the current Anthropic rows, keep every model the product
recommends or has evaluated in the table, and make the fallback visible in the log.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import aisuite as ai
import pytest
from coworker.compaction import DEFAULT_CONTEXT_WINDOW, trigger_tokens
from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.providers.matrix import MATRIX, model_context_windows
from coworker.providers.registry import DESCRIPTORS
from coworker.tools import ToolRegistry

REPO = Path(__file__).resolve().parents[1]


# -- rows ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,window",
    [
        # platform.claude.com/docs/en/models/overview, read 2026-09-08: 1M is the default
        # and only context size on the Claude 5 generation; Haiku 4.5 stays at 200K.
        ("anthropic:claude-fable-5-1", 1_000_000),
        ("anthropic:claude-opus-5", 1_000_000),
        ("anthropic:claude-sonnet-5", 1_000_000),
        ("anthropic:claude-fable-5", 1_000_000),
        ("anthropic:claude-haiku-4-5", 200_000),
        # Together /v1/models `context_length`, read 2026-09-08 (exact per model).
        ("together:moonshotai/Kimi-K3", 1_048_576),
        ("openrouter:moonshotai/kimi-k3", 1_048_576),  # openrouter.ai models API, 2026-09-09
        ("together:zai-org/GLM-5.2", 1_048_575),
        ("together:deepseek-ai/DeepSeek-V4-Pro", 512_000),
        # Vendor pages read 2026-09-08 (docs.z.ai "1M"; openrouter 1,048,576;
        # fireworks "1040k").
        ("zai:glm-5.2", 1_000_000),
        ("openrouter:z-ai/glm-5.2", 1_048_576),
        ("fireworks:accounts/fireworks/models/glm-5p2", 1_040_000),
    ],
)
def test_current_models_have_verified_windows(model, window):
    assert model_context_windows()[model] == window


def test_claude_5_rows_are_vision_capable_and_labelled():
    for mid in ("anthropic:claude-fable-5-1", "anthropic:claude-opus-5", "anthropic:claude-sonnet-5"):
        entry = MATRIX[mid]
        assert entry.caps.tools and entry.caps.vision and entry.caps.pdf, mid
        assert "Anthropic" in entry.label, mid


def _matrix_key(provider: str, model: str) -> str:
    # OpenAI rows are stored bare (bare ids route to the OpenAI default).
    return model if provider == "openai" else f"{provider}:{model}"


# Recommended models whose vendor spec has not been re-checked: the matrix keeps their
# window at None on purpose (the GUI meter hides rather than show a made-up number) and
# they compact on the 128k guess. Verify the spec, fill the row, and shrink this set.
_KNOWN_UNVERIFIED_RECOMMENDATIONS = {"minimax:MiniMax-M2.5", "meta:muse-spark-1.1"}


def test_every_recommended_model_is_in_the_matrix():
    """The Settings pane suggests each provider's `recommended_model`; every one must be a
    curated row (Ollama excepted: a user-local model name, not a vendor catalog id)."""
    missing = [
        _matrix_key(d.name, d.recommended_model)
        for d in DESCRIPTORS
        if d.name != "ollama" and d.recommended_model
        and _matrix_key(d.name, d.recommended_model) not in MATRIX
    ]
    assert not missing, f"recommended models without a matrix row: {missing}"


def test_recommended_models_without_a_window_are_exactly_the_known_set():
    """A recommendation without a window compacts on the 128k guess. New ones must not
    slip in silently; verified ones must leave the allowlist."""
    windows = model_context_windows()
    without = {
        _matrix_key(d.name, d.recommended_model)
        for d in DESCRIPTORS
        if d.name != "ollama" and d.recommended_model
        and _matrix_key(d.name, d.recommended_model) not in windows
    }
    assert without == _KNOWN_UNVERIFIED_RECOMMENDATIONS


def test_every_model_in_the_reviewer_eval_reports_has_a_window():
    """`reports/reviewer-eval-*.md` are the models the product has been evaluated on."""
    reports = sorted((REPO / "reports").glob("reviewer-eval-*.md"))
    if not reports:
        pytest.skip("no reviewer-eval reports in this checkout")
    pattern = re.compile(r"\b(anthropic|openai|together|gemini|zai|deepseek|fireworks|openrouter):([A-Za-z0-9._/-]+)")
    ids = set()
    for path in reports:
        for provider, model in pattern.findall(path.read_text(encoding="utf-8")):
            ids.add(_matrix_key(provider, model))
    assert ids, "no model ids found in the reports"
    windows = model_context_windows()
    missing = sorted(m for m in ids if m not in windows)
    assert not missing, f"evaluated models without a matrix window: {missing}"


# -- trigger arithmetic (documents what the rows buy) -------------------------------


def test_trigger_for_a_1m_model_is_the_cap_not_the_128k_guess():
    assert trigger_tokens(None) == int(0.8 * DEFAULT_CONTEXT_WINDOW)  # 102,400
    assert trigger_tokens(1_000_000) == 250_000
    assert trigger_tokens(200_000) == 160_000


# -- fallback is visible ------------------------------------------------------------


class _OneTurn(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, model):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    return TurnEngine(
        provider=_OneTurn(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model=model,
    )


def _run(engine):
    async def _collect():
        return [ev async for ev in engine.run("hello")]

    return asyncio.run(_collect())


def test_unlisted_model_logs_the_128k_fallback_once(tmp_path, caplog):
    engine = _engine(tmp_path, "anthropic:claude-unlisted-9")
    with caplog.at_level(logging.WARNING, logger="coworker.engine"):
        _run(engine)
        _run(engine)  # second turn: no repeat
    hits = [r for r in caplog.records if "context window" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in caplog.records]
    msg = hits[0].getMessage()
    assert "anthropic:claude-unlisted-9" in msg and str(DEFAULT_CONTEXT_WINDOW) in msg


def test_listed_model_does_not_warn(tmp_path, caplog):
    engine = _engine(tmp_path, "anthropic:claude-sonnet-5")
    with caplog.at_level(logging.WARNING, logger="coworker.engine"):
        _run(engine)
    assert not [r for r in caplog.records if "context window" in r.getMessage()]


def test_compaction_settings_override_silences_the_warning(tmp_path, caplog):
    """A caller that supplies its own context_window (the GUI's per-model settings) has
    made the choice explicitly; nothing to warn about."""
    engine = _engine(tmp_path, "anthropic:claude-unlisted-9")
    engine.compaction_settings = lambda: {"context_window": 400_000}
    with caplog.at_level(logging.WARNING, logger="coworker.engine"):
        _run(engine)
    assert not [r for r in caplog.records if "context window" in r.getMessage()]
