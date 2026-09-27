"""Cloud-first subscriptions (connectors-across-machines spec §3.3): which door
the box / desktop uses, and how the broker's answers map to the route's."""

from __future__ import annotations

import httpx
import pytest

from coworker import subscription_sync as sync
from coworker.config import Config
from coworker.secrets import SecretStore


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def cfg():
    return Config(cloud_base_url="https://api.test")


def _capture(monkeypatch, status=200, body=None, raise_error=False):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        if raise_error:
            raise httpx.ConnectError("down")
        return _Resp(status, body if body is not None else {"ok": True, "moved_from": []})

    monkeypatch.setattr(sync.httpx, "post", fake_post)
    return calls


def test_box_registers_by_machine_credential(tmp_path, monkeypatch, cfg):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("slack:default", {
        "mode": "relay", "machine_credential": "mc_abc", "broker_user_id": "u1",
        "connection_id": "conn1",
    })
    calls = _capture(monkeypatch)
    out = sync.register(secrets, cfg, source="slack:T1/C1", session_id="s1", title="Ops")
    assert out == {"ok": True, "registered": True, "moved_from": []}
    (call,) = calls
    assert call["url"] == "https://api.test/v1/machine/subscriptions"
    assert call["headers"] == {"Authorization": "Bearer mc_abc"}
    assert call["json"] == {"user_id": "u1", "connection_id": "conn1", "source": "slack:T1/C1",
                            "session_id": "s1", "title": "Ops", "move": False}


def test_desktop_registers_by_session_token(tmp_path, monkeypatch, cfg):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("github:default", {"mode": "relay"})
    monkeypatch.setattr(sync, "fresh_access_token", lambda s, c: "jwt-1")
    calls = _capture(monkeypatch)
    out = sync.register(secrets, cfg, source="github:acme/site", session_id="s1", move=True)
    assert out["ok"] and out["registered"]
    (call,) = calls
    assert call["url"] == "https://api.test/v1/subscriptions"
    assert call["headers"] == {"Authorization": "Bearer jwt-1"}
    assert call["json"]["move"] is True and "user_id" not in call["json"]


def test_local_only_when_nothing_to_register(tmp_path, monkeypatch, cfg):
    secrets = SecretStore(tmp_path / "s.json")
    calls = _capture(monkeypatch)
    # Socket-Mode Slack: the broker has no relay for it.
    secrets.put("slack:default", {"bot_token": "xoxb", "app_token": "xapp"})
    assert sync.register(secrets, cfg, source="slack:C1", session_id="s") == {"ok": True, "registered": False}
    # Telegram is not a managed inbound connector at all.
    assert sync.register(secrets, cfg, source="telegram:123", session_id="s") == {"ok": True, "registered": False}
    # Relay mode but signed out on the desktop: local-only, honest.
    secrets.put("slack:default", {"mode": "relay"})
    monkeypatch.setattr(sync, "fresh_access_token", lambda s, c: None)
    assert sync.register(secrets, cfg, source="slack:C1", session_id="s") == {"ok": True, "registered": False}
    assert calls == []


def test_held_and_failures_map_to_route_answers(tmp_path, monkeypatch, cfg):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("slack:default", {"mode": "relay", "machine_credential": "mc", "broker_user_id": "u", "connection_id": "c"})
    held = {"detail": {"error": "held", "held_by": {"session_id": "other", "title": "Lead", "machine_id": "m-a"},
                       "also": [], "move_allowed": False}}
    _capture(monkeypatch, status=409, body=held)
    out = sync.register(secrets, cfg, source="slack:C1", session_id="s")
    assert out["ok"] is False and out["error"] == "held"
    assert out["held_by"]["title"] == "Lead" and out["move_allowed"] is False
    # An older broker without the route: local-only, exactly as before.
    _capture(monkeypatch, status=404, body={"detail": "Not Found"})
    assert sync.register(secrets, cfg, source="slack:C1", session_id="s") == {"ok": True, "registered": False}
    _capture(monkeypatch, status=401, body={"detail": "unauthorized"})
    out = sync.register(secrets, cfg, source="slack:C1", session_id="s")
    assert out["ok"] is False and out["error"] == "unauthorized"
    _capture(monkeypatch, raise_error=True)
    out = sync.register(secrets, cfg, source="slack:C1", session_id="s")
    assert out["ok"] is False and "could not be reached" in out["error"]


def test_remove_and_orphan_are_best_effort(tmp_path, monkeypatch, cfg):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("slack:default", {"mode": "relay", "machine_credential": "mc", "broker_user_id": "u", "connection_id": "c"})
    calls = _capture(monkeypatch)
    sync.remove(secrets, cfg, source="slack:C1", session_id="s")
    sync.report_orphan(secrets, cfg, source="slack:C1")
    assert [c["url"].rsplit("/", 1)[1] for c in calls] == ["remove", "orphan"]
    assert calls[0]["json"]["session_id"] == "s"
    _capture(monkeypatch, raise_error=True)
    sync.remove(secrets, cfg, source="slack:C1", session_id="s")  # never raises


# --- mirror refresh (UX-049 4b) ---------------------------------------------------


def test_pull_partitions_rows_by_this_machine(tmp_path, monkeypatch, cfg):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("slack:default", {"mode": "relay", "machine_credential": "mc", "broker_user_id": "u", "connection_id": "c"})
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/machine/subscriptions"):
            return _Resp(200, {"machine_id": "m-a", "subscriptions": [
                {"connector": "slack", "source": "slack:C1", "machine_id": "m-a", "session_id": "s1"},
                {"connector": "slack", "source": "slack:C2", "machine_id": "m-b", "session_id": "s9"},
                {"connector": "github", "source": "github:o/r", "machine_id": "m-a", "session_id": "s1"},
            ]})
        return _Resp(200, {"people": [{"connector": "slack", "scope": "T1", "member": "U_BOB", "name": "Bob"}]})

    monkeypatch.setattr(sync.httpx, "get", fake_get)
    out = sync.pull(secrets, cfg)
    assert [r["source"] for r in out["subscriptions"]] == ["slack:C1"]
    assert [r["source"] for r in out["elsewhere"]] == ["slack:C2"]
    assert out["people"]["slack"][0]["member"] == "U_BOB"
    assert sorted(u.rsplit("/", 1)[1] for u in calls) == ["people", "subscriptions"]
    # A desktop (no machine credential) pulls nothing.
    secrets.put("slack:default", {"mode": "relay"})
    assert sync.pull(secrets, cfg) == {"subscriptions": [], "elsewhere": [], "people": {}}
