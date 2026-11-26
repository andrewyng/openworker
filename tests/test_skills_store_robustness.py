"""The skills stores: a corrupt file must not brick startup, and must not silently re-enable.

Two stores, two failure modes, both built eagerly by ``SessionManager.__init__``:

``SessionSkillStore`` (per-session skill mutes) parsed its file inside a ``try`` that
caught ``(OSError, ValueError)`` — but a file that is valid JSON and not an object
(``[]``, ``null``) fails later on ``.get`` with ``AttributeError``, which is not in
that tuple, so the constructor raised and the server did not start.

``SkillStore``'s settings file (the personal enable/disable list) is read on every
``disabled_names()`` call and written in place by ``set_enabled()``. A write
interrupted mid-way leaves a truncated file; the read then returns an empty set —
**every skill the user disabled comes back on**, with no log and no way to notice.
A file whose ``disabled`` entry is a string is worse still: iterating a string yields
characters, so a skill named ``a`` reads as disabled.

Both saves are now atomic, and both readers keep the bytes they cannot use in a
``.corrupt`` sibling (never deleted, never overwritten by a later save) with a
warning naming the reason — the shape the risk-override, connection, parked and
team stores use.
"""

from __future__ import annotations

import json
import logging

from coworker.skills.store import SessionSkillStore, SkillStore

_SESSIONS = {"sessions": {"s1": {"skill-a": True, "skill-b": False}}}
_SETTINGS = {"disabled": ["skill-a"]}


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def _corrupt_sibling(path):
    return path.with_suffix(path.suffix + ".corrupt")


# -- SessionSkillStore -----------------------------------------------------------


def test_a_good_session_file_still_loads(tmp_path, caplog):
    path = tmp_path / "session_skills.json"
    path.write_text(json.dumps(_SESSIONS), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="coworker.skills.store"):
        store = SessionSkillStore(path)
    assert store.get("s1") == {"skill-a": True, "skill-b": False}
    assert _warnings(caplog) == []


def test_valid_json_that_is_not_an_object_starts_empty_and_keeps_the_bytes(tmp_path, caplog):
    """The crash: `[]` parses, then `.get` raises AttributeError — not in the
    (OSError, ValueError) tuple the handler caught."""
    for body in ("[]", "null", '"hello"'):
        path = tmp_path / "session_skills.json"
        path.write_text(body, encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="coworker.skills.store"):
            store = SessionSkillStore(path)
        assert store.get("s1") == {}, body
        assert _corrupt_sibling(path).read_text(encoding="utf-8") == body
        assert any(str(path) in m for m in _warnings(caplog))


def test_a_torn_session_file_starts_empty_and_keeps_the_bytes(tmp_path, caplog):
    body = '{"sessions": {"s1": {"a": tr'
    path = tmp_path / "session_skills.json"
    path.write_text(body, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="coworker.skills.store"):
        store = SessionSkillStore(path)
    assert store.get("s1") == {}
    assert _corrupt_sibling(path).read_text(encoding="utf-8") == body
    assert any("unreadable" in m for m in _warnings(caplog))


def test_sessions_of_the_wrong_shape_starts_empty(tmp_path, caplog):
    path = tmp_path / "session_skills.json"
    path.write_text('{"sessions": []}', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="coworker.skills.store"):
        store = SessionSkillStore(path)
    assert store.get("s1") == {}
    assert any(str(path) in m for m in _warnings(caplog))


def test_a_quarantined_session_file_does_not_block_a_fresh_save(tmp_path):
    path = tmp_path / "session_skills.json"
    path.write_text("[]", encoding="utf-8")
    store = SessionSkillStore(path)
    store.set("s1", "skill-a", False)

    assert SessionSkillStore(path).get("s1") == {"skill-a": False}
    assert _corrupt_sibling(path).read_text(encoding="utf-8") == "[]"


def test_session_save_is_atomic_and_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "session_skills.json"
    store = SessionSkillStore(path)
    store.set("s1", "skill-a", False)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "sessions": {"s1": {"skill-a": False}}
    }
    assert not path.with_suffix(".json.tmp").exists()


# -- SkillStore settings ---------------------------------------------------------


def _settings(tmp_path, monkeypatch) -> SkillStore:
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    return SkillStore()


def test_a_good_settings_file_still_reads(tmp_path, monkeypatch, caplog):
    store = _settings(tmp_path, monkeypatch)
    store._settings_path.write_text(json.dumps(_SETTINGS), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="coworker.skills.store"):
        assert store.disabled_names() == {"skill-a"}
    assert _warnings(caplog) == []


def test_a_truncated_settings_file_is_reported_not_silently_forgotten(tmp_path, monkeypatch, caplog):
    """The bug this half exists for: a mid-write crash truncated the file, and
    `disabled_names()` returned an empty set — every skill the user disabled
    came back on, silently."""
    store = _settings(tmp_path, monkeypatch)
    store.set_enabled("skill-a", False)
    assert store.disabled_names() == {"skill-a"}

    text = store._settings_path.read_text(encoding="utf-8")
    store._settings_path.write_text(text[: len(text) // 2], encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.skills.store"):
        assert store.disabled_names() == set()
    # The bytes are kept, and the warning names the reason and where they went.
    assert _corrupt_sibling(store._settings_path).is_file()
    assert any(str(store._settings_path) in m for m in _warnings(caplog))


def test_settings_of_the_wrong_shape_is_reported_not_crashed(tmp_path, monkeypatch, caplog):
    store = _settings(tmp_path, monkeypatch)
    for body in ("[]", "null", '"hello"'):
        store._settings_path.write_text(body, encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="coworker.skills.store"):
            assert store.disabled_names() == set(), body
        assert any(str(store._settings_path) in m for m in _warnings(caplog))


def test_a_string_disabled_entry_is_not_iterated_into_characters(tmp_path, monkeypatch, caplog):
    """`for n in "notalist"` yields characters — a skill named `a` would read as
    disabled, and the next save would persist those characters as real names."""
    store = _settings(tmp_path, monkeypatch)
    store._settings_path.write_text('{"disabled": "notalist"}', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="coworker.skills.store"):
        assert store.disabled_names() == set()
    assert any(str(store._settings_path) in m for m in _warnings(caplog))


def test_a_quarantined_settings_file_does_not_block_a_fresh_save(tmp_path, monkeypatch):
    store = _settings(tmp_path, monkeypatch)
    store._settings_path.write_text("[]", encoding="utf-8")
    assert store.disabled_names() == set()

    store.set_enabled("skill-a", False)
    assert store.disabled_names() == {"skill-a"}
    assert _corrupt_sibling(store._settings_path).read_text(encoding="utf-8") == "[]"


def test_settings_save_is_atomic_and_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    store = _settings(tmp_path, monkeypatch)
    store.set_enabled("skill-a", False)
    assert json.loads(store._settings_path.read_text(encoding="utf-8")) == _SETTINGS
    assert not store._settings_path.with_suffix(".json.tmp").exists()
