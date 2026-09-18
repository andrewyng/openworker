"""Tests for provider key detection + the live (read-only) Test/verify path. SDK-free: the
single httpx.get is monkeypatched so no network is touched."""

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


def _patch_post(monkeypatch, status=200, body=None, capture=None, raise_exc=None):
    def fake_post(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(
            status_code=status,
            json=lambda: body if body is not None else {},
        )

    monkeypatch.setattr("httpx.post", fake_post)


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


def test_verify_anthropic_custom_endpoint(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    # The stored endpoint is the ROOT (the SDK adds /v1) — a trailing /v1 is trimmed, not doubled.
    verify_provider_key("anthropic", api_key="sk-x", base_url="https://gw.example/anthropic/v1/")
    assert cap["url"] == "https://gw.example/anthropic/v1/models"
    assert cap["headers"]["x-api-key"] == "sk-x"


def test_verify_anthropic_gateway_without_model_list_falls_back_to_messages(monkeypatch):
    """Gateways commonly proxy /v1/messages only — a missing model list says nothing about
    the endpoint, so Test falls back to a one-token Messages call. An Anthropic-shaped 4xx
    (unknown probe model on a gateway serving its own aliases) still proves the endpoint
    speaks the API and took the key."""
    cap: dict = {}
    _patch_get(monkeypatch, status=404)
    _patch_post(
        monkeypatch,
        status=404,
        body={"type": "error", "error": {"type": "not_found_error"}},
        capture=cap,
    )
    assert verify_provider_key(
        "anthropic", api_key="sk-x", base_url="https://gw.example/anthropic"
    ) == {"ok": True}
    assert cap["url"] == "https://gw.example/anthropic/v1/messages"
    assert cap["json"]["max_tokens"] == 1  # a Test button never costs anything meaningful


def test_verify_anthropic_gateway_rejects_key_on_the_fallback(monkeypatch):
    _patch_get(monkeypatch, status=404)
    _patch_post(monkeypatch, status=401, body={"type": "error", "error": {}})
    assert verify_provider_key(
        "anthropic", api_key="sk-bad", base_url="https://gw.example/anthropic"
    ) == {"ok": False, "error": "Invalid API key."}


@pytest.mark.parametrize(
    "body",
    [
        {"message": "Not Found"},  # a plain proxy 404 page
        # The OpenAI/LiteLLM error shape: nested `error`, no top-level "type". Pasting an
        # OpenAI-compatible URL into the Anthropic endpoint box is the likely mistake, and
        # a Test that passed there would only fail later, mid-conversation.
        {"error": {"message": "Not Found", "type": "invalid_request_error"}},
    ],
)
def test_verify_anthropic_non_anthropic_host_stays_a_failure(monkeypatch, body):
    """Only Anthropic's own error shape (top-level "type": "error") counts as proof the
    endpoint speaks the Messages API."""
    _patch_get(monkeypatch, status=404)
    _patch_post(monkeypatch, status=404, body=body)
    res = verify_provider_key(
        "anthropic", api_key="sk-x", base_url="https://gw.example/wrong"
    )
    assert res["ok"] is False and "404" in res["error"]


def test_verify_anthropic_stock_404_does_not_probe_messages(monkeypatch):
    """No custom endpoint, no fallback: api.anthropic.com always serves /v1/models, so a
    404 there is a real failure — and nobody's Anthropic account gets a stray call."""
    _patch_get(monkeypatch, status=404)

    def boom(*a, **k):  # pragma: no cover - asserts the path is never taken
        raise AssertionError("stock anthropic must not POST /v1/messages")

    monkeypatch.setattr("httpx.post", boom)
    assert verify_provider_key("anthropic", api_key="sk-x")["ok"] is False


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


@pytest.mark.parametrize(
    "name,base_url,model",
    [
        (
            "ark",
            "https://ark.ap-southeast.bytepluses.com/api/v3",
            "dola-seed-evolving-latest-version",
        ),
        (
            "ark-agent-plan-cn",
            "https://ark.cn-beijing.volces.com/api/plan/v3",
            "doubao-seed-evolving",
        ),
    ],
)
def test_verify_ark_uses_non_persisted_responses_probe(
    monkeypatch, name, base_url, model
):
    """Reverse-verified probe: the captured fixture must be non-empty and provider-specific."""
    cap: dict = {}
    _patch_post(monkeypatch, status=200, capture=cap)

    assert verify_provider_key(name, api_key="ark-key") == {"ok": True}
    assert cap["url"] == base_url + "/responses"
    assert cap["headers"]["Authorization"] == "Bearer ark-key"
    assert cap["json"] == {
        "model": model,
        "input": "Reply with OK.",
        "max_output_tokens": 1,
        "store": False,
    }


def test_verify_ark_profile_endpoint_override(monkeypatch):
    cap: dict = {}
    _patch_post(monkeypatch, status=200, capture=cap)

    verify_provider_key(
        "ark",
        api_key="ark-key",
        base_url="https://gateway.example/ark/v3/",
    )

    assert cap["url"] == "https://gateway.example/ark/v3/responses"


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
