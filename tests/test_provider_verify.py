"""Tests for provider key detection + the live Test/verify path. SDK-free: httpx.get — and
httpx.post, which only NVIDIA NIM needs — are monkeypatched so no network is touched."""

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
        ("nvapi-abc123", "nvidia"),
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


def test_verify_openai_ok(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("openai", api_key="sk-x") == {"ok": True}
    assert cap["url"] == "https://api.openai.com/v1/models"
    assert cap["headers"]["Authorization"] == "Bearer sk-x"


def test_verify_openai_custom_endpoint(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key(
        "openai", api_key="sk-x", base_url="https://gw.example/openai/v1/"
    )
    # trailing slash trimmed, /models appended to the custom endpoint
    assert cap["url"] == "https://gw.example/openai/v1/models"


def test_verify_bad_key_is_invalid(monkeypatch):
    _patch_get(monkeypatch, status=401)
    assert verify_provider_key("openai", api_key="sk-bad") == {
        "ok": False,
        "error": "Invalid API key.",
    }


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


# -- NVIDIA NIM: the one provider a list-models probe can't verify ---------------
def _patch_post(monkeypatch, status=200, capture=None):
    def fake_post(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture.update(kwargs)
        return SimpleNamespace(status_code=status)

    def refuse_get(*_a, **_kw):  # see the docstring below
        raise AssertionError("NVIDIA verify must not GET /models — it is unauthenticated")

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", refuse_get)


def test_verify_nvidia_posts_a_one_token_completion(monkeypatch):
    """NIM's /v1/models is PUBLIC — 200 for any key, including an empty one — so a
    list-models probe would greenlight a typo. Auth is only enforced on inference, hence
    the POST. The GET stub above fails the test if this ever regresses to the generic
    branch."""
    from coworker.providers.registry import get_descriptor

    cap: dict = {}
    _patch_post(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("nvidia", api_key="nvapi-x") == {"ok": True}
    assert cap["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer nvapi-x"
    # Asserted against the descriptor, not a literal: NIM model ids rotate, and the point
    # is that verify names the *recommended* model — a bad id 404s before auth is checked.
    assert cap["json"]["model"] == get_descriptor("nvidia").recommended_model
    assert cap["json"]["max_tokens"] == 1


def test_verify_nvidia_bad_key_is_invalid(monkeypatch):
    """NIM answers 403 on a bad key (401 with no header at all); both must read as a key
    problem, not as an unreachable server."""
    _patch_post(monkeypatch, status=403)
    assert verify_provider_key("nvidia", api_key="nvapi-bad") == {
        "ok": False,
        "error": "Invalid API key.",
    }


def test_verify_nvidia_custom_endpoint(monkeypatch):
    """The descriptor's help text promises a self-hosted NIM container works — only true
    if the override actually reaches the request."""
    cap: dict = {}
    _patch_post(monkeypatch, status=200, capture=cap)
    verify_provider_key(
        "nvidia", api_key="nvapi-x", base_url="http://localhost:8000/v1/"
    )
    assert cap["url"] == "http://localhost:8000/v1/chat/completions"


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
