"""Tests for option to hide live thinking steps (#611)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class EmptyProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()


def test_hide_live_thinking_preference_default_and_toggle(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=EmptyProvider())
    # Default is False
    assert manager.hide_live_thinking() is False
    assert manager.get_settings()["hide_live_thinking"] is False

    # Toggle to True
    res = manager.set_hide_live_thinking(True)
    assert res["ok"] is True
    assert res["hide_live_thinking"] is True
    assert manager.hide_live_thinking() is True
    assert manager.get_settings()["hide_live_thinking"] is True

    # Rebuilding manager on same workspace / prefs persists preference
    manager2 = SessionManager(workspace=tmp_path, provider=EmptyProvider())
    assert manager2.hide_live_thinking() is True

    # Toggle back to False
    res2 = manager2.set_hide_live_thinking(False)
    assert res2["ok"] is True
    assert res2["hide_live_thinking"] is False
    assert manager2.hide_live_thinking() is False


def test_hide_live_thinking_rest_endpoints(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=EmptyProvider())
    client = TestClient(create_app(manager))

    # Initial settings
    resp = client.get("/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["hide_live_thinking"] is False

    # POST /v1/settings/hide-live-thinking
    resp = client.post("/v1/settings/hide-live-thinking", json={"hide_live_thinking": True})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["hide_live_thinking"] is True

    # Check updated settings
    resp = client.get("/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["hide_live_thinking"] is True

    # Turn off
    resp = client.post("/v1/settings/hide-live-thinking", json={"hide_live_thinking": False})
    assert resp.status_code == 200
    assert resp.json()["hide_live_thinking"] is False
