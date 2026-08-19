"""Serving-window lookup for local (ollama) models.

The curated matrix is hosted-models-only, so an `ollama:*` id resolved to no window and
compaction inherited DEFAULT_CONTEXT_WINDOW (128k) — about double a typical local server.
That guess is never corrected downstream because ollama truncates silently, so the lookup
has to be right up front. No network here: the /api/ps probe is stubbed.
"""

from __future__ import annotations

import coworker.providers.local_context as lc


def _stub(monkeypatch, loaded: dict[str, int]) -> None:
    lc._cache.clear()
    monkeypatch.setattr(lc, "_probe", lambda root: loaded)


def test_returns_the_window_ollama_is_actually_serving(monkeypatch):
    _stub(monkeypatch, {"qwen3.8-27b:latest": 65536})
    assert lc.local_context_window("ollama:qwen3.8-27b:latest") == 65536


def test_bare_id_matches_the_latest_tag(monkeypatch):
    # `ollama:qwen3.8-27b` is a legitimate way to name a model that loads as `:latest`.
    _stub(monkeypatch, {"qwen3.8-27b:latest": 65536})
    assert lc.local_context_window("ollama:qwen3.8-27b") == 65536


def test_non_ollama_ids_are_left_to_the_matrix(monkeypatch):
    _stub(monkeypatch, {"qwen3.8-27b:latest": 65536})
    assert lc.local_context_window("gpt-5.6-sol") is None
    assert lc.local_context_window("anthropic:claude-opus-4-8") is None


def test_unloaded_model_is_unknown_not_guessed(monkeypatch):
    # Only loaded models appear in /api/ps. Returning None keeps the caller's existing
    # fallback rather than inventing a window.
    _stub(monkeypatch, {"something-else:latest": 8192})
    assert lc.local_context_window("ollama:qwen3.8-27b:latest") is None


def test_unreachable_server_never_raises(monkeypatch):
    """This runs inside the per-turn compaction check — it must fail soft."""
    lc._cache.clear()

    def _boom(root):
        raise OSError("connection refused")

    monkeypatch.setattr(lc, "_probe", _boom)
    try:
        result = lc.local_context_window("ollama:qwen3.8-27b:latest")
    except OSError:
        raise AssertionError("probe failure must not propagate into the compaction check")
    assert result is None


def test_result_is_cached_so_the_turn_loop_does_not_re_probe(monkeypatch):
    lc._cache.clear()
    calls = []

    def _counting(root):
        calls.append(root)
        return {"qwen3.8-27b:latest": 65536}

    monkeypatch.setattr(lc, "_probe", _counting)
    for _ in range(5):
        assert lc.local_context_window("ollama:qwen3.8-27b:latest") == 65536
    assert len(calls) == 1
