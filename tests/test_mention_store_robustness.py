"""MentionSessionStore: a corrupt mention_threads.json must not brick server startup.

An append interrupted mid-write (crash, SIGKILL, full disk) leaves a truncated JSON
document, and a field added to ``MentionThread`` makes every older record fail to
construct. Both raise straight out of ``__init__``, and the store is built eagerly by
``SessionManager.__init__`` — so ``openworker-server`` dies on every start until the user
finds and deletes the file by hand. Every sibling store in that constructor already treats
a corrupt file as "start empty" (``ParkedStore``, ``ChannelBuffer``, the people
directory); this one was the outlier.
"""

from __future__ import annotations

import json

from coworker.mentions import MentionSessionStore

_RECORD = {
    "thread_target": "slack:C0123:1700.000100",
    "session_id": "abc123def456",
    "channel": "slack:C0123",
}


def _write(tmp_path, text):
    path = tmp_path / "mention_threads.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_reads_a_valid_file(tmp_path):
    path = _write(tmp_path, json.dumps({"threads": [_RECORD]}))
    store = MentionSessionStore(path)
    assert store.get(_RECORD["thread_target"]) == _RECORD["session_id"]


def test_load_survives_a_torn_thread_file(tmp_path):
    path = _write(tmp_path, '{"threads": [\n  {"thread_target": "slack:C0123:17')
    store = MentionSessionStore(path)
    assert store.all() == []
    # Recovery: the next write replaces the unreadable file instead of dying again.
    store.set("slack:C0123:1700.000200", "sid", "slack:C0123")
    assert MentionSessionStore(path).all() == store.all()


def test_load_survives_a_record_with_a_missing_field(tmp_path):
    older = {k: v for k, v in _RECORD.items() if k != "channel"}
    path = _write(tmp_path, json.dumps({"threads": [older]}))
    assert MentionSessionStore(path).all() == []
