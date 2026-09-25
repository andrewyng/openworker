"""A default permission mode affects new sessions without widening existing ones."""

from fastapi.testclient import TestClient

from coworker.permissions import Mode
from coworker.server.app import create_app
from coworker.server.manager import SessionManager


def test_default_mode_round_trip_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    manager = SessionManager(data_dir=data_dir)
    client = TestClient(create_app(manager))

    assert client.get("/v1/settings").json()["default_mode"] == "interactive"
    saved = client.post("/v1/settings/default-mode", json={"mode": "discuss"}).json()
    assert saved["ok"] and saved["default_mode"] == "discuss"
    assert manager.mode is Mode.DISCUSS
    assert SessionManager(data_dir=data_dir).mode is Mode.DISCUSS


def test_default_mode_rejects_bypass_and_unknown_values(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    for value in ("auto", "bypass-approvals", "plan", "custom", "unknown", ""):
        result = manager.set_default_mode(value)
        assert not result["ok"]
        assert manager.mode is Mode.INTERACTIVE
    assert "default_mode" not in manager._prefs


def test_auto_approve_default_respects_feature_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    assert not manager.set_default_mode("auto-approve")["ok"]
    manager.set_auto_approve(True)
    assert manager.set_default_mode("auto-approve")["ok"]
    assert manager.mode is Mode.AUTO_APPROVE
    manager.set_auto_approve(False)
    assert manager.mode is Mode.INTERACTIVE
    assert manager.get_settings()["default_mode"] == "interactive"
    manager.set_auto_approve(True)
    assert manager.mode is Mode.AUTO_APPROVE


def test_config_mode_is_used_without_preference(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data", mode=Mode.DISCUSS)
    assert manager.default_mode() is Mode.DISCUSS
    assert manager.set_default_mode("interactive")["ok"]
    assert manager.mode is Mode.INTERACTIVE


def test_default_change_only_applies_to_new_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    existing = manager.get_engine("existing", agent="chat")
    assert existing is not None and existing.permissions.mode is Mode.INTERACTIVE

    manager.set_default_mode("discuss")
    assert manager.get_engine("existing", agent="chat") is existing
    assert existing.permissions.mode is Mode.INTERACTIVE
    fresh = manager.get_engine("fresh", agent="chat")
    assert fresh is not None and fresh.permissions.mode is Mode.DISCUSS
