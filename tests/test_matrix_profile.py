"""Matrix profile PATCH tests."""

from __future__ import annotations

from coworker.connectors.matrix_profile import matrix_settings_public, patch_matrix_settings
from coworker.secrets import SecretStore


def test_patch_matrix_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    store = SecretStore()
    store.put(
        "matrix:default",
        {
            "type": "token",
            "homeserver_url": "https://matrix.example.org",
            "access_token": "tok",
            "require_mention": True,
        },
    )
    result = patch_matrix_settings(
        store,
        {
            "require_mention": False,
            "session_scope": "room",
            "allowed_rooms": ["!a:ex", "!b:ex"],
            "lifecycle_reactions": False,
        },
    )
    assert result["ok"] is True
    settings = result["settings"]
    assert settings["require_mention"] is False
    assert settings["session_scope"] == "room"
    assert settings["allowed_rooms"] == ["!a:ex", "!b:ex"]
    assert settings["lifecycle_reactions"] is False
    pub = matrix_settings_public(store.get("matrix:default") or {})
    assert pub["homeserver_url"] == "https://matrix.example.org"
