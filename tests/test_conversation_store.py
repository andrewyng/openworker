"""ConversationStore: session id path-traversal hardening.

A session id becomes a filename ("<id>.jsonl"); ids arrive from client-controlled
surfaces (the /ws/session/{id} route, REST paths), so a crafted id must never let a
write or read escape the conversations/ directory.
"""

from __future__ import annotations

import pytest

from coworker.conversations import ConversationStore, is_safe_session_id
from coworker.sessions import SessionRecord


def test_is_safe_session_id():
    # Every id shape the app actually generates is accepted.
    for ok in (
        "0123456789abcdef0123456789abcdef",  # uuid4().hex
        "abc123def456",  # uuid4().hex[:12]
        "__run__run-abcdef1234",  # automation run thread
        "__task__task-0123456789",  # automation task thread
    ):
        assert is_safe_session_id(ok), ok
    # Anything that could escape a single path component is rejected.
    for bad in (
        "../evil",
        "../../etc/passwd",
        "a/b",
        "a\\b",
        "..",
        ".",
        "with space",
        "dot.dot",
        "",
        "x" * 129,  # over the length cap
    ):
        assert not is_safe_session_id(bad), bad


def test_save_rejects_traversal_id_without_writing_outside(tmp_path):
    """The verified vuln: saving a record with '../evil' used to create 'evil.jsonl'
    OUTSIDE the conversations dir. It must raise and write nothing."""
    store = ConversationStore(tmp_path / "state")
    rec = SessionRecord(
        session_id="../evil",
        workspace=str(tmp_path),
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": "hi"}],
    )
    with pytest.raises(ValueError):
        store.save(rec)
    # Nothing landed outside conversations/ (the previous behavior wrote here).
    assert not (tmp_path / "state" / "evil.jsonl").exists()
    assert list((tmp_path / "state" / "conversations").glob("*.jsonl")) == []


def test_load_of_unknown_or_unsafe_id_is_none_not_crash(tmp_path):
    store = ConversationStore(tmp_path / "state")
    # A normal missing id: no DB row, returns None (never touches the filesystem).
    assert store.load("deadbeef") is None
    # An unsafe id also has no DB row, so load short-circuits to None before any file IO.
    assert store.load("../evil") is None


def test_round_trip_with_valid_id_still_works(tmp_path):
    store = ConversationStore(tmp_path / "state")
    rec = SessionRecord(
        session_id="abc123def456",
        workspace=str(tmp_path),
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": "hello"}],
    )
    store.save(rec)
    loaded = store.load("abc123def456")
    assert loaded is not None
    assert loaded.messages[0]["content"] == "hello"
    assert (tmp_path / "state" / "conversations" / "abc123def456.jsonl").is_file()
