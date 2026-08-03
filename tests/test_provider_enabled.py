"""Regression suite for the provider on/off toggle (Settings ▸ Models): a configured
provider can be switched off without touching its stored key. Covers every layer the
toggle touches — REST round-trip, manager state, the composer-picker vs. per-provider
checklist model-list split, `model_ready` when the default model's own provider is
disabled, and the router's own enforcement independent of the composer's filtering.

Consolidated in one file (rather than scattered across test_server.py / test_settings.py /
test_provider_router.py) so the whole feature's regression coverage runs and reads together.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ProviderDisabledError,
    ProviderRouter,
    StreamChunk,
)
from coworker.server import SessionManager, create_app


def _client(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "state")
    return TestClient(create_app(manager))


# -- REST: POST /v1/providers/{name}/enabled -------------------------------------
def test_provider_enabled_toggle_leaves_credentials_untouched(tmp_path):
    """Disabling then re-enabling must not require re-entering the key."""
    client = _client(tmp_path)
    assert client.post(
        "/v1/providers", json={"name": "zai", "fields": {"api_key": "zk-test"}}
    ).json()["ok"]
    prov = {p["name"]: p for p in client.get("/v1/providers").json()}
    assert prov["zai"]["configured"] and prov["zai"]["enabled"]

    res = client.post("/v1/providers/zai/enabled", json={"enabled": False}).json()
    assert res == {"ok": True, "provider": "zai", "enabled": False}
    prov = {p["name"]: p for p in client.get("/v1/providers").json()}
    assert prov["zai"]["configured"] and not prov["zai"]["enabled"]

    assert client.post("/v1/providers/zai/enabled", json={"enabled": True}).json()["ok"]
    prov = {p["name"]: p for p in client.get("/v1/providers").json()}
    assert prov["zai"]["configured"] and prov["zai"]["enabled"]

    assert not client.post(
        "/v1/providers/nope/enabled", json={"enabled": False}
    ).json()["ok"]


# -- manager: set_provider_enabled / get_providers -------------------------------
def test_manager_set_provider_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(data_dir=tmp_path)
    mgr.set_provider("anthropic", {"api_key": "sk-ant-test"})
    assert {p["name"]: p for p in mgr.get_providers()}["anthropic"]["enabled"] is True

    res = mgr.set_provider_enabled("anthropic", False)
    assert res == {"ok": True, "provider": "anthropic", "enabled": False}
    provs = {p["name"]: p for p in mgr.get_providers()}
    assert provs["anthropic"]["enabled"] is False
    assert provs["anthropic"]["configured"] is True  # key untouched by the toggle

    mgr.set_provider_enabled("anthropic", True)
    assert {p["name"]: p for p in mgr.get_providers()}["anthropic"]["enabled"] is True

    assert mgr.set_provider_enabled("nope", False)["ok"] is False  # unknown provider


# -- get_settings(): composer picker (`models`) vs. checklist (`curated_models`) --
def test_disabled_provider_drops_out_of_the_model_list(tmp_path, monkeypatch):
    """A disabled provider's models disappear from the composer picker even though its
    key is still stored — `enabled` gates selectability independently of `configured`."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.set_provider("anthropic", {"api_key": "sk-ant-test"})
    manager.add_model("anthropic:claude-opus-4-8")
    assert "anthropic:claude-opus-4-8" in manager.get_settings()["models"]

    manager.set_provider_enabled("anthropic", False)
    assert "anthropic:claude-opus-4-8" not in manager.get_settings()["models"]
    # The key is untouched — re-enabling restores selectability with no re-entry.
    assert manager._provider_configured("anthropic")

    manager.set_provider_enabled("anthropic", True)
    assert "anthropic:claude-opus-4-8" in manager.get_settings()["models"]


def test_disabling_a_provider_does_not_untick_it_from_the_curated_list(tmp_path, monkeypatch):
    """Regression (found via manual browser testing after the toggle first shipped):
    Settings ▸ Models' per-provider checklist was wired to `get_settings()["models"]`, the
    SAME filtered list the composer picker uses. Once `models` also started filtering on
    `enabled`, disabling a provider made its already-ticked models render as unticked/removed
    in the checklist, even though they were never dropped from the persisted curated list.
    Fix: `get_settings()` exposes an unfiltered `curated_models` for the checklist to use;
    `models` stays filtered for the composer picker only. This test pins that split."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.set_provider("anthropic", {"api_key": "sk-ant-test"})
    manager.add_model("anthropic:claude-opus-4-8")
    assert "anthropic:claude-opus-4-8" in manager.get_settings()["curated_models"]

    manager.set_provider_enabled("anthropic", False)
    settings = manager.get_settings()
    assert "anthropic:claude-opus-4-8" not in settings["models"]  # dropped from the picker
    assert "anthropic:claude-opus-4-8" in settings["curated_models"]  # still ticked in the list


def test_disabling_the_default_models_provider_clears_model_ready(tmp_path, monkeypatch):
    """Disabling stays a pure toggle — it never auto-switches `default_model` — so the
    existing "no model connected" state (`model_ready`) is what surfaces it instead."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.set_provider("anthropic", {"api_key": "sk-ant-test"})
    manager.set_default_model("anthropic:claude-opus-4-8")
    assert manager.get_settings()["model_ready"] is True

    manager.set_provider_enabled("anthropic", False)
    settings = manager.get_settings()
    assert settings["model_ready"] is False
    assert settings["model"] == "anthropic:claude-opus-4-8"  # unchanged, not stolen back


# -- router: enforcement independent of the composer's own filtering -------------
class _Recorder(ProviderClient):
    def __init__(self, name: str):
        self.name = name
        self.models: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        return AssistantTurn(text=self.name)

    def stream(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        yield StreamChunk(turn=AssistantTurn(text=self.name))

    def capabilities(self, model):
        return ModelCapabilities()


def _patch_build(monkeypatch):
    state: dict = {"created": [], "latest": {}}

    def fake_build(name, profile, secrets):
        rec = _Recorder(name)  # a fresh client each build, so rebuilds are observable
        state["created"].append(rec)
        state["latest"][name] = rec
        return rec

    monkeypatch.setattr("coworker.providers.router.build_provider_client", fake_build)
    return state


class _FakeSecrets:
    """Minimal SecretStore stand-in: just the `.get(profile) -> dict | None` the router uses."""

    def __init__(self, profiles: dict | None = None):
        self.profiles = profiles or {}

    def get(self, key):
        return self.profiles.get(key)


def test_client_for_raises_when_provider_disabled(monkeypatch):
    state = _patch_build(monkeypatch)
    secrets = _FakeSecrets({"provider:ollama": {"enabled": False}})
    router = ProviderRouter(secrets=secrets)

    with pytest.raises(ProviderDisabledError, match="ollama"):
        router._client_for("ollama:a")
    assert state["created"] == []  # never built for a disabled provider


def test_client_for_rechecks_enabled_even_for_an_already_cached_client(monkeypatch):
    """A provider disabled after its client was built (and not yet invalidated) must still
    refuse the next call — the guard is not just a build-time check."""
    _patch_build(monkeypatch)
    secrets = _FakeSecrets({"provider:ollama": {}})
    router = ProviderRouter(secrets=secrets)
    assert router._client_for("ollama:a") is not None  # builds + caches

    secrets.profiles["provider:ollama"] = {"enabled": False}
    with pytest.raises(ProviderDisabledError):
        router._client_for("ollama:b")
