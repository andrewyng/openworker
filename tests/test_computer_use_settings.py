import json

from fastapi.testclient import TestClient

from coworker.server import manager as manager_module
from coworker.server.app import create_app
from coworker.server.manager import SessionManager


def test_computer_use_is_disabled_and_empty_by_default(tmp_path):
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    settings = client.get("/v1/settings/computer-use").json()
    assert settings["enabled"] is False
    assert settings["allowed_programs"] == []


def test_computer_use_settings_persist_program_allowlist(tmp_path, monkeypatch):
    reloads = []
    monkeypatch.setattr(
        manager_module,
        "reset_computer_use_permissions",
        lambda: reloads.append(True)
        or {"driver_installed": True, "driver_reloaded": True},
    )
    editor = tmp_path / "editor.exe"
    editor.write_bytes(b"MZ")
    data_dir = tmp_path / "data"
    client = TestClient(create_app(SessionManager(data_dir=data_dir)))

    saved = client.post(
        "/v1/settings/computer-use",
        json={
            "enabled": True,
            "allowed_programs": [{"name": "Editor", "path": str(editor)}],
        },
    ).json()
    assert saved["ok"] is True
    assert saved["driver_reloaded"] is True
    assert saved["allowed_programs"] == [
        {"name": "Editor", "path": str(editor), "available": True}
    ]
    assert reloads == [True]
    prefs = json.loads((data_dir / "prefs.json").read_text())
    assert prefs["computer_use_enabled"] is True
    assert prefs["computer_use_programs"] == [
        {"name": "Editor", "path": str(editor)}
    ]
    reborn = SessionManager(data_dir=data_dir)
    assert reborn.computer_use_settings()["allowed_programs"][0]["path"] == str(
        editor
    )


def test_computer_use_settings_reject_command_interpreters(tmp_path, monkeypatch):
    monkeypatch.setattr(
        manager_module,
        "reset_computer_use_permissions",
        lambda: {"driver_installed": False, "driver_reloaded": False},
    )
    command = tmp_path / "cmd.exe"
    command.write_bytes(b"MZ")
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))

    result = client.post(
        "/v1/settings/computer-use",
        json={
            "enabled": True,
            "allowed_programs": [{"name": "Command Prompt", "path": str(command)}],
        },
    ).json()
    assert result["ok"] is False
    assert "cannot be allowed" in result["error"]
