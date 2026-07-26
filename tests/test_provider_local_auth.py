"""Optional API key for Ollama / authenticated local OpenAI-compatible servers.

Some local runtimes that speak Ollama's OpenAI-compatible API (oMLX, a gated reverse proxy
in front of `ollama serve`) sit behind a small auth layer. These tests pin the contract:
an empty key keeps today's keyless behavior exactly, and a stored key is used for both the
runtime client and the Test/Detect probe.

Hermetic: no network — the OpenAI SDK constructor and `httpx.get` are monkeypatched.
"""

from __future__ import annotations

from types import SimpleNamespace

from coworker.providers.registry import (
    build_provider_client,
    get_descriptor,
    verify_provider_key,
)


# -- descriptor ----------------------------------------------------------------
def test_ollama_exposes_an_optional_secret_api_key_field():
    d = get_descriptor("ollama")
    assert d is not None
    # Stays keyless: the gallery must keep showing Ollama as usable with no key.
    assert d.needs_key is False
    field = next((f for f in d.fields if f.key == "api_key"), None)
    assert field is not None, "ollama should offer an optional api_key field"
    assert field.secret is True
    assert field.required is False


# -- runtime client ------------------------------------------------------------
def _capture_openai(monkeypatch):
    captured: dict = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    import coworker.providers.registry as reg

    monkeypatch.setattr(reg, "OpenAIProvider", fake_openai)
    return captured


def test_build_ollama_uses_the_stored_key_when_present(monkeypatch):
    captured = _capture_openai(monkeypatch)
    build_provider_client(
        "ollama", {"base_url": "http://box:11434", "api_key": "sk-local-123"}, secrets=None
    )
    assert captured["api_key"] == "sk-local-123"
    assert captured["base_url"] == "http://box:11434/v1"


def test_build_ollama_keeps_the_placeholder_when_no_key(monkeypatch):
    captured = _capture_openai(monkeypatch)
    build_provider_client("ollama", {"base_url": "http://box:11434"}, secrets=None)
    # Ollama ignores the key but the OpenAI SDK requires a non-empty string.
    assert captured["api_key"] == "ollama"


def test_build_ollama_ignores_a_blank_key(monkeypatch):
    captured = _capture_openai(monkeypatch)
    build_provider_client("ollama", {"api_key": "   "}, secrets=None)
    assert captured["api_key"] == "ollama"


# -- verify (Test / Detect) ----------------------------------------------------
def _capture_httpx(monkeypatch, status: int = 200):
    calls: list[dict] = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return SimpleNamespace(status_code=status)

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_verify_ollama_sends_bearer_when_a_key_is_given(monkeypatch):
    calls = _capture_httpx(monkeypatch)
    out = verify_provider_key(
        "ollama", api_key="sk-local-123", base_url="http://localhost:11434"
    )
    assert out["ok"] is True
    assert calls[0]["url"] == "http://localhost:11434/v1/models"
    assert calls[0]["headers"] == {"Authorization": "Bearer sk-local-123"}


def test_verify_ollama_stays_keyless_without_a_key(monkeypatch):
    calls = _capture_httpx(monkeypatch)
    verify_provider_key("ollama", base_url="http://localhost:11434")
    # Unchanged from today: no auth header at all.
    assert "headers" not in calls[0]


# -- persistence ---------------------------------------------------------------
def _manager(tmp_path):
    from coworker.server.manager import SessionManager

    return SessionManager(workspace=tmp_path)


def test_set_provider_stores_the_optional_key(tmp_path):
    mgr = _manager(tmp_path)
    mgr.set_provider("ollama", {"base_url": "http://box:11434", "api_key": "sk-local-123"})
    profile = mgr.secrets.get("provider:ollama")
    assert profile["api_key"] == "sk-local-123"


def test_resaving_without_the_key_does_not_wipe_it(tmp_path):
    """The GUI masks saved secrets and submits them blank on a re-save / Detect.

    An optional secret must therefore survive an empty submit, exactly like a required one —
    otherwise editing the server URL silently drops the key.
    """
    mgr = _manager(tmp_path)
    mgr.set_provider("ollama", {"base_url": "http://box:11434", "api_key": "sk-local-123"})
    mgr.set_provider("ollama", {"base_url": "http://other:11434", "api_key": ""})
    profile = mgr.secrets.get("provider:ollama")
    assert profile["api_key"] == "sk-local-123"
    assert profile["base_url"] == "http://other:11434"


def test_non_secret_optional_fields_can_still_be_cleared(tmp_path):
    mgr = _manager(tmp_path)
    mgr.set_provider("ollama", {"base_url": "http://box:11434"})
    mgr.set_provider("ollama", {"base_url": ""})
    profile = mgr.secrets.get("provider:ollama")
    assert "base_url" not in profile


def test_verify_provider_falls_back_to_the_stored_key(tmp_path, monkeypatch):
    calls = _capture_httpx(monkeypatch)
    mgr = _manager(tmp_path)
    mgr.set_provider("ollama", {"base_url": "http://box:11434", "api_key": "sk-local-123"})
    mgr.verify_provider("ollama", {})
    assert calls[-1]["headers"] == {"Authorization": "Bearer sk-local-123"}
