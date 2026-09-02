"""MatrixSessionStore tests."""

from __future__ import annotations

from coworker.connectors.matrix_sessions import MatrixSessionStore


def test_matrix_session_store_per_user(tmp_path):
    path = tmp_path / "sessions.json"
    store = MatrixSessionStore(path)
    store.set("!r:ex", "sess-a", user_id="@alice:ex")
    store.set("!r:ex", "sess-b", user_id="@bob:ex")
    assert store.get("!r:ex", user_id="@alice:ex") == "sess-a"
    assert store.get("!r:ex", user_id="@bob:ex") == "sess-b"
    store.remove_session("sess-a")
    assert store.get("!r:ex", user_id="@alice:ex") is None
