"""Tests for provider key detection + the live Test/verify path. SDK-free: httpx is
monkeypatched so no network is touched."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.providers import detect_provider, verify_provider_key


# -- detect_provider ------------------------------------------------------------
@pytest.mark.parametrize(
    "key,expected",
    [
        ("sk-ant-api03-abc", "anthropic"),
        ("sk-or-v1-abc", "openrouter"),
        ("AIzaSyAbc123", "gemini"),
        ("sk-proj-abc", "openai"),
        ("sk_live_abc", "openai"),
        ("", None),
        ("   ", None),
        ("nonsense", None),
    ],
)
def test_detect_provider(key, expected):
    assert detect_provider(key) == expected


# -- verify_provider_key: status-code mapping + per-provider request shape -------
def _patch_get(monkeypatch, status=200, capture=None, raise_exc=None):
    def fake_get(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr("httpx.get", fake_get)


def _patch_openai_compat(
    monkeypatch,
    *,
    models_status=200,
    completions_status=200,
    model_id="demo",
    capture=None,
    models_exc=None,
    completions_exc=None,
):
    """Stub GET /models + POST /chat/completions for the OpenAI-compat verify path."""

    def fake_get(url, **kwargs):
        if capture is not None:
            capture["models_url"] = url
            capture["models_headers"] = kwargs.get("headers")
        if models_exc is not None:
            raise models_exc
        body = {"data": [{"id": model_id}]} if model_id else {"data": []}
        return SimpleNamespace(
            status_code=models_status, json=lambda: body
        )

    def fake_post(url, **kwargs):
        if capture is not None:
            capture["completions_url"] = url
            capture["completions_json"] = kwargs.get("json")
        if completions_exc is not None:
            raise completions_exc
        return SimpleNamespace(status_code=completions_status)

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)


def test_verify_openai_ok(monkeypatch):
    cap: dict = {}
    _patch_openai_compat(monkeypatch, capture=cap)
    assert verify_provider_key("openai", api_key="sk-x") == {"ok": True}
    assert cap["models_url"] == "https://api.openai.com/v1/models"
    assert cap["models_headers"]["Authorization"] == "Bearer sk-x"
    assert cap["completions_url"] == "https://api.openai.com/v1/chat/completions"
    assert cap["completions_json"]["model"] == "demo"
    assert cap["completions_json"]["max_tokens"] == 1


def test_verify_openai_custom_endpoint(monkeypatch):
    cap: dict = {}
    _patch_openai_compat(monkeypatch, capture=cap)
    verify_provider_key(
        "openai", api_key="sk-x", base_url="https://gw.example/openai/v1/"
    )
    # trailing slash trimmed; /models then /chat/completions on the custom endpoint
    assert cap["models_url"] == "https://gw.example/openai/v1/models"
    assert cap["completions_url"] == "https://gw.example/openai/v1/chat/completions"


def test_verify_bad_key_is_invalid(monkeypatch):
    _patch_openai_compat(monkeypatch, models_status=401)
    assert verify_provider_key("openai", api_key="sk-bad") == {
        "ok": False,
        "error": "Invalid API key.",
    }


def test_verify_rejects_endpoint_missing_v1_when_completions_404(monkeypatch):
    """#431: GET /models can succeed at a root that has no /chat/completions."""
    _patch_openai_compat(monkeypatch, completions_status=404)
    res = verify_provider_key(
        "qwen", api_key="placeholder", base_url="http://127.0.0.1:1234"
    )
    assert res["ok"] is False
    assert "/v1" in res["error"]
    assert "chat completions" in res["error"].lower()


def test_verify_anthropic_headers(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("anthropic", api_key="sk-ant-x")
    assert cap["url"] == "https://api.anthropic.com/v1/models"
    assert cap["headers"]["x-api-key"] == "sk-ant-x"
    assert "anthropic-version" in cap["headers"]


def test_verify_gemini_key_param(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("gemini", api_key="AIza-x")
    assert cap["params"]["key"] == "AIza-x"


def test_verify_ollama_uses_v1_models_no_key(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("ollama", base_url="http://localhost:11434")
    assert cap["url"] == "http://localhost:11434/v1/models"
    assert "headers" not in cap  # keyless


def test_verify_network_error_is_clean(monkeypatch):
    _patch_get(monkeypatch, raise_exc=ConnectionError("boom"))
    res = verify_provider_key("openai", api_key="sk-x")
    assert res["ok"] is False
    assert "Couldn't reach" in res["error"]


def test_verify_unexpected_status(monkeypatch):
    _patch_get(monkeypatch, status=500)
    res = verify_provider_key("anthropic", api_key="sk-ant-x")
    assert res["ok"] is False
    assert "500" in res["error"]
