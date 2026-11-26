"""MentionSessionStore: a corrupt mention_threads.json must not brick server startup.

A save interrupted mid-write (crash, SIGKILL, full disk) leaves a truncated JSON
document, and a field added to ``MentionThread`` makes every older record fail to
construct. Both used to raise straight out of ``__init__``, and the store is built
eagerly by ``SessionManager.__init__`` — so ``openworker-server`` died on every start
until the user found and deleted the file by hand. ``ParkedStore`` in the same
constructor already treats a corrupt file as "start empty"; the other JSON stores
listed in #204 still do not.

#709: the guard also has to cover a file that is valid JSON but not an object, and
the save has to be atomic so it stops producing the torn file in the first place.
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


def test_one_unreadable_record_does_not_discard_the_readable_ones(tmp_path):
    """The granularity bug: a single record the dataclass refuses — a field a newer
    build added, a missing required field, a non-object entry — must not take every
    OTHER thread's grant with it. The records around it stay, only that one is
    skipped."""
    bad_extra = {**_RECORD, "field_from_a_newer_build": "x"}
    other = {**_RECORD, "thread_target": "slack:C9999:1700.000200", "session_id": "zzz999"}
    path = _write(tmp_path, json.dumps({"threads": [_RECORD, bad_extra, other]}))
    store = MentionSessionStore(path)
    # Before the fix the one refused record aborted the comprehension and the
    # except-clause wiped _threads to [] — every readable grant was lost too.
    assert sorted(t.thread_target for t in store.all()) == [
        "slack:C0123:1700.000100", "slack:C9999:1700.000200"]
    assert store.get("slack:C0123:1700.000100") == "abc123def456"


def test_a_non_object_record_is_skipped_not_fatal(tmp_path):
    # A `threads` list with a bare string / list / null inside used to be the same
    # one-bad-record aborts-everything shape, through AttributeError instead of TypeError.
    path = _write(
        tmp_path,
        json.dumps({"threads": [_RECORD, "junk", ["not", "a", "record"], None]}))
    store = MentionSessionStore(path)
    assert [t.thread_target for t in store.all()] == ["slack:C0123:1700.000100"]


def test_load_survives_valid_json_that_is_not_an_object(tmp_path):
    # `[]`, `null` and a bare string all parse, then fail on `.get` (#709).
    for body in ("[]", "null", '"hello"'):
        path = _write(tmp_path, body)
        assert MentionSessionStore(path).all() == []


def test_save_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "mention_threads.json"
    store = MentionSessionStore(path)
    store.set(_RECORD["thread_target"], _RECORD["session_id"], _RECORD["channel"])
    assert json.loads(path.read_text(encoding="utf-8")) == {"threads": [_RECORD]}
    assert not path.with_suffix(".json.tmp").exists()


def test_save_failure_keeps_the_previous_file_and_the_in_memory_record(tmp_path, monkeypatch):
    path = _write(tmp_path, json.dumps({"threads": [_RECORD]}))
    store = MentionSessionStore(path)

    def refuse(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(type(path), "replace", refuse)
    # The failed save must not raise into the caller (a Slack message handler)...
    store.set("slack:C0123:1700.000200", "sid2", "slack:C0123")
    # ...memory still has both records, the file on disk is the old complete one,
    # and no .tmp is left beside it.
    assert len(store.all()) == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"threads": [_RECORD]}
    assert not path.with_suffix(".json.tmp").exists()
