"""A corrupt connections file must not stop the server from starting (#204).

Both connection stores are constructed by ``SessionManager.__init__`` — so a truncated
``persona_connections.json`` / ``session_connections.json`` raised ``JSONDecodeError``
straight out of the constructor and took the whole server down with it.

These files hold *user* state: a persona's default connector set, and a session's
overrides. Degrading to "no connections" is therefore NOT neutral — a connector the user
muted comes back on. So the bytes are kept aside (never deleted, never overwritten by a
later save) and the path is logged, exactly like the risk-override store.
"""

from __future__ import annotations

import json
import logging

from coworker.connections import PersonaConnectionStore, SessionConnectionStore
from coworker.server.manager import SessionManager

TRUNCATED = '{"personas": {"p1": {"slack": tru'


def _corrupt(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TRUNCATED, encoding="utf-8")


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def test_a_truncated_persona_connections_file_degrades_instead_of_crashing(tmp_path, caplog):
    path = tmp_path / "persona_connections.json"
    _corrupt(path)

    with caplog.at_level(logging.WARNING, logger="coworker.connections"):
        store = PersonaConnectionStore(path)

    assert store.get("p1") == {}
    # The bytes are kept aside, not deleted — they are the user's only copy.
    spare = tmp_path / "persona_connections.json.corrupt"
    assert spare.is_file()
    assert spare.read_text(encoding="utf-8") == TRUNCATED
    assert any("loaded no connections" in m for m in _warnings(caplog))
    assert any(str(path) in m for m in _warnings(caplog))


def test_a_truncated_session_connections_file_degrades_instead_of_crashing(tmp_path, caplog):
    path = tmp_path / "session_connections.json"
    _corrupt(path)

    with caplog.at_level(logging.WARNING, logger="coworker.connections"):
        store = SessionConnectionStore(path)

    assert store.get("s1") == {}
    spare = tmp_path / "session_connections.json.corrupt"
    assert spare.is_file()
    assert spare.read_text(encoding="utf-8") == TRUNCATED
    assert any("loaded no connections" in m for m in _warnings(caplog))
    assert any(str(path) in m for m in _warnings(caplog))


def test_the_session_connector_extensions_store_degrades_the_same_way(tmp_path, caplog):
    # The manager builds a THIRD store of the same class for one session's extra
    # connectors — it must not be the odd one out.
    path = tmp_path / "session_connector_extensions.json"
    _corrupt(path)

    with caplog.at_level(logging.WARNING, logger="coworker.connections"):
        store = SessionConnectionStore(path)

    assert store.get("s1") == {}
    assert (tmp_path / "session_connector_extensions.json.corrupt").is_file()
    assert any(str(path) in m for m in _warnings(caplog))


def test_a_file_of_the_wrong_shape_degrades_instead_of_crashing(tmp_path, caplog):
    # Valid JSON, but not the object the store reads — `data.get(...)` would raise
    # AttributeError out of the constructor exactly like the truncated case.
    path = tmp_path / "persona_connections.json"
    path.write_text("[]", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.connections"):
        store = PersonaConnectionStore(path)

    assert store.get("p1") == {}
    assert any("not a JSON object" in m for m in _warnings(caplog))


def test_a_rows_entry_of_the_wrong_shape_degrades_instead_of_crashing(tmp_path, caplog):
    path = tmp_path / "session_connections.json"
    path.write_text('{"sessions": []}', encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.connections"):
        store = SessionConnectionStore(path)

    assert store.get("s1") == {}
    assert any("not a JSON object" in m for m in _warnings(caplog))


def test_a_quarantined_file_does_not_block_a_fresh_save(tmp_path):
    path = tmp_path / "session_connections.json"
    _corrupt(path)

    store = SessionConnectionStore(path)
    store.set("s1", "slack", True)

    # The store is usable again, and the original bytes are still there.
    assert json.loads(path.read_text(encoding="utf-8")) == {"sessions": {"s1": {"slack": True}}}
    assert (tmp_path / "session_connections.json.corrupt").read_text(encoding="utf-8") == TRUNCATED


class _Rec:
    kind = "connector"
    ref = "slack"
    tier = "core"


class _Manifest:
    recommends = [_Rec()]


def test_a_null_row_keeps_the_personas_own_empty_opinion(tmp_path):
    """A persona whose row is null keeps that (empty) row — the store does not re-seed it
    from the manifest behind the user's back. A persona with NO row still seeds."""
    path = tmp_path / "persona_connections.json"
    path.write_text('{"personas": {"p1": null}}', encoding="utf-8")

    store = PersonaConnectionStore(path)
    assert store.get("p1") == {}
    assert store.defaults_for("p1", _Manifest(), connected=set()) == {}

    fresh = PersonaConnectionStore(tmp_path / "other.json")
    assert fresh.defaults_for("p2", _Manifest(), connected=set()) == {"slack": True}


def test_the_server_starts_with_corrupt_connection_files(tmp_path):
    """The end of the story: the constructor that used to raise is the server's."""
    for name in (
        "persona_connections.json",
        "session_connections.json",
        "session_connector_extensions.json",
    ):
        _corrupt(tmp_path / name)

    mgr = SessionManager(data_dir=tmp_path)

    assert mgr.persona_connections.get("p1") == {}
    assert mgr.session_connections.get("s1") == {}
    assert mgr.session_connector_extensions.get("s1") == {}
