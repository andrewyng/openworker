"""Tests for `providers.registry.ollama_context_window` — backs the GUI's context-window
status bar (Ollama only: it's the one provider whose native API exposes the real configured
number instead of a hardcoded per-model guess). SDK-free: httpx.get/post are monkeypatched.

Ollama fixes a model's ACTUAL context size at load time (Modelfile override, server
OLLAMA_CONTEXT_LENGTH default, or built-in default — often smaller than the trained max), so
`/api/ps` (currently loaded models) is checked first as the authoritative source; `/api/show`
(Modelfile override, else trained max) is only a best-effort fallback for a model that hasn't
been loaded yet.
"""

from __future__ import annotations

from types import SimpleNamespace

from coworker.providers.registry import ollama_context_window


def _patch_ps(monkeypatch, models=None, raise_exc=None, capture=None):
    def fake_get(url, **kwargs):
        if capture is not None:
            capture["get_url"] = url
        if raise_exc is not None:
            raise raise_exc
        resp = SimpleNamespace(status_code=200)
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"models": models or []}
        return resp

    monkeypatch.setattr("httpx.get", fake_get)


def _patch_show(monkeypatch, json_body=None, capture=None, raise_exc=None):
    def fake_post(url, **kwargs):
        if capture is not None:
            capture["post_url"] = url
            capture.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        resp = SimpleNamespace(status_code=200)
        resp.raise_for_status = lambda: None
        resp.json = lambda: json_body or {}
        return resp

    monkeypatch.setattr("httpx.post", fake_post)


# -- /api/ps: authoritative when the model is currently loaded ------------------------


def test_loaded_model_uses_ps_context_length(monkeypatch):
    cap: dict = {}
    _patch_ps(
        monkeypatch,
        models=[{"name": "llama3.2:1b", "model": "llama3.2:1b", "context_length": 4096}],
        capture=cap,
    )
    _patch_show(monkeypatch, json_body={"model_info": {"llama.context_length": 131072}})

    assert ollama_context_window("http://localhost:11434", "llama3.2:1b") == 4096
    # native /api/ps at the root — NOT the OpenAI-compatible /v1 path
    assert cap["get_url"] == "http://localhost:11434/api/ps"


def test_ps_wins_even_when_smaller_than_trained_max(monkeypatch):
    """The whole point: a model trained for 128k tokens can still be RUNNING with a much
    smaller window (server default/Modelfile) — /api/show's trained max must not override it."""
    _patch_ps(
        monkeypatch,
        models=[{"name": "qwen3-coder:30b", "context_length": 8192}],
    )
    _patch_show(
        monkeypatch, json_body={"model_info": {"qwen3.context_length": 131072}}
    )
    assert ollama_context_window("http://localhost:11434", "qwen3-coder:30b") == 8192


def test_ps_ignores_other_loaded_models(monkeypatch):
    _patch_ps(
        monkeypatch,
        models=[{"name": "other-model", "context_length": 2048}],
    )
    _patch_show(monkeypatch, json_body={"parameters": "num_ctx 16384"})
    assert ollama_context_window("http://localhost:11434", "llama3.2:1b") == 16384


# -- /api/show fallback: model not (yet) loaded ----------------------------------------


def test_falls_back_to_show_when_not_loaded(monkeypatch):
    cap: dict = {}
    _patch_ps(monkeypatch, models=[])
    _patch_show(
        monkeypatch,
        json_body={"parameters": "num_ctx 8192\nstop <|end|>", "model_info": {}},
        capture=cap,
    )
    assert ollama_context_window("http://localhost:11434", "qwen3-coder:30b") == 8192
    assert cap["post_url"] == "http://localhost:11434/api/show"
    assert cap["json"] == {"model": "qwen3-coder:30b"}


def test_strips_v1_suffix_before_hitting_native_apis(monkeypatch):
    cap: dict = {}
    _patch_ps(monkeypatch, models=[], capture=cap)
    _patch_show(monkeypatch, json_body={"parameters": "num_ctx 4096"})
    ollama_context_window("http://localhost:11434/v1", "llama3.3")
    assert cap["get_url"] == "http://localhost:11434/api/ps"


def test_falls_back_to_model_info_context_length(monkeypatch):
    _patch_ps(monkeypatch, models=[])
    _patch_show(
        monkeypatch,
        json_body={"parameters": "", "model_info": {"qwen3.context_length": 32768}},
    )
    assert ollama_context_window("http://localhost:11434", "qwen3-coder:30b") == 32768


def test_modelfile_override_wins_over_model_info(monkeypatch):
    _patch_ps(monkeypatch, models=[])
    _patch_show(
        monkeypatch,
        json_body={
            "parameters": "num_ctx 2048",
            "model_info": {"llama.context_length": 131072},
        },
    )
    assert ollama_context_window("http://localhost:11434", "llama3.3") == 2048


def test_none_when_neither_field_present(monkeypatch):
    _patch_ps(monkeypatch, models=[])
    _patch_show(monkeypatch, json_body={"parameters": "", "model_info": {}})
    assert ollama_context_window("http://localhost:11434", "mystery-model") is None


def test_ps_unreachable_still_falls_back_to_show(monkeypatch):
    _patch_ps(monkeypatch, raise_exc=ConnectionError("boom"))
    _patch_show(monkeypatch, json_body={"parameters": "num_ctx 4096"})
    assert ollama_context_window("http://localhost:11434", "llama3.2:1b") == 4096


def test_none_on_network_error(monkeypatch):
    _patch_ps(monkeypatch, raise_exc=ConnectionError("boom"))
    _patch_show(monkeypatch, raise_exc=ConnectionError("boom"))
    assert ollama_context_window("http://localhost:11434", "qwen3-coder:30b") is None


def test_none_on_unknown_model_404(monkeypatch):
    _patch_ps(monkeypatch, models=[])

    def fake_post(url, **kwargs):
        resp = SimpleNamespace(status_code=404)

        def _raise():
            raise RuntimeError("404 Not Found")

        resp.raise_for_status = _raise
        return resp

    monkeypatch.setattr("httpx.post", fake_post)
    assert ollama_context_window("http://localhost:11434", "nonexistent") is None
