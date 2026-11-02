"""`recordings.db` (observability technical design §11): what the store writes, keeps and
deletes. The fold rules themselves are checked by test_observability_golden.py."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from coworker.observability import Recorder, RecordingStore, new_ulid
from coworker.observability import store as store_mod
from coworker.observability.recorder import now_ms

DAY_MS = 24 * 60 * 60 * 1000


def _turn(rec, turn_id, *, session="sess_A", at=0, mono=0):
    rec.emit({
        "type": "turn_started", "v": 1, "at_ms": at, "mono_ms": mono, "session_id": session,
        "turn_id": turn_id, "kind": "run", "trigger": "user", "first_message_id": "m1",
        "attrs": {"approval_mode": "interactive", "attended": True,
                  "reviewer": {"active": False, "shadow": False, "kind": None, "model": None}},
        "stamp": {"machine": {"kind": "this_machine"}},
    })


def _end_turn(rec, turn_id, *, session="sess_A", at=100, mono=100):
    rec.emit({"type": "turn_ended", "v": 1, "at_ms": at, "mono_ms": mono, "session_id": session,
              "turn_id": turn_id, "status": "completed", "last_message_id": "m2"})


def _model_step(rec, step_id, turn_id, *, session="sess_A", at=10, mono=10, end=60, exchange=None,
                usage=None):
    base = {"v": 1, "session_id": session, "turn_id": turn_id, "step_id": step_id}
    rec.emit({**base, "type": "step_started", "at_ms": at, "mono_ms": mono, "kind": "model",
              "name": "model", "parent_step_id": None, "call_id": None, "call_index": None,
              "shadow": False, "after_turn": False,
              "attrs": {"purpose": "main", "model": "anthropic:claude-haiku-4-5",
                        "context_window": 200000},
              "content": {}})
    attrs = {
        "usage": usage,
        "provider_facts": {"served_model": "claude-haiku-4-5-20251001",
                           "provider_request_id": "req_1"},
        "provider_meta": [{"wire": "anthropic", "request": {"max_tokens": 1024}}],
    }
    rec.emit({**base, "type": "step_ended", "at_ms": end, "mono_ms": end, "status": "ok",
              "message_id": "m2", "kind": "model", "name": "model", "parent_step_id": None,
              "call_id": None, "call_index": None, "started_at": at, "attrs": attrs,
              "content": {"exchange": exchange} if exchange else {}})


@pytest.fixture
def store(tmp_path):
    s = RecordingStore(tmp_path / "recordings.db", background=False)
    yield s
    s.close()


def test_store_lives_next_to_coworker_db(tmp_path):
    s = RecordingStore.beside(tmp_path, background=False)
    try:
        assert s.path == tmp_path / "recordings.db"
        assert s.path.exists()
    finally:
        s.close()


def test_usage_and_provider_facts_are_columns(store):
    rec = Recorder(store=store)
    _turn(rec, "t1")
    _model_step(rec, "s1", "t1", usage={"input_fresh": 5, "cache_read": 5194, "cache_write": 130,
                                        "output": 23, "reasoning": None})
    _end_turn(rec, "t1")
    store.flush()
    row = store._conn.execute("SELECT * FROM steps WHERE step_id = 's1'").fetchone()
    assert (row["purpose"], row["model"], row["usage_reported"]) == (
        "main", "anthropic:claude-haiku-4-5", 1)
    assert (row["input_fresh"], row["cache_read"], row["cache_write"], row["output"],
            row["reasoning"]) == (5, 5194, 130, 23, None)
    assert (row["served_model"], row["provider_request_id"], row["context_window"]) == (
        "claude-haiku-4-5-20251001", "req_1", 200000)
    # provider_meta is kept out of the light attrs column and comes back on load
    assert "provider_meta" not in row["attrs"]
    step = store.load_recording("sess_A")["steps"][0]
    assert step["attrs"]["provider_meta"] == [{"wire": "anthropic", "request": {"max_tokens": 1024}}]


def test_a_model_call_without_usage_is_marked_unreported(store):
    rec = Recorder(store=store)
    _turn(rec, "t1")
    _model_step(rec, "s1", "t1", usage=None)
    _end_turn(rec, "t1")
    store.flush()
    row = store._conn.execute("SELECT usage_reported, output FROM steps").fetchone()
    assert (row["usage_reported"], row["output"]) == (0, None)


def test_a_step_written_many_times_is_one_row_with_its_last_state(store):
    rec = Recorder(store=store)
    _turn(rec, "t1")
    _model_step(rec, "s1", "t1")
    rec.emit({"type": "step_updated", "v": 1, "at_ms": 70, "mono_ms": 70, "session_id": "sess_A",
              "turn_id": "t1", "step_id": "s1", "attrs": {"where": "inbox"}})
    _end_turn(rec, "t1")
    store.flush()
    rows = store._conn.execute("SELECT seq, attrs FROM steps").fetchall()
    assert len(rows) == 1
    assert rows[0]["seq"] == 1 and '"where": "inbox"' in rows[0]["attrs"]


def test_turn_seq_continues_after_a_restart(tmp_path):
    path = tmp_path / "recordings.db"
    s1 = RecordingStore(path, background=False)
    rec = Recorder(store=s1)
    _turn(rec, "t1")
    _end_turn(rec, "t1")
    s1.close()
    s2 = RecordingStore(path, background=False)
    try:
        rec2 = Recorder(store=s2)  # a new server process: nothing in memory
        _turn(rec2, "t2", at=1000, mono=5)
        _end_turn(rec2, "t2", at=1100, mono=105)
        assert [t["seq"] for t in s2.load_recording("sess_A")["turns"]] == [1, 2]
    finally:
        s2.close()


def test_pieces_are_stored_once_and_rebuilt_exactly(store):
    data = b'{"content":"Read notes.txt","role":"user"}'
    key = store.put_piece(data)
    assert store.put_piece(data) == key
    assert store.get_piece(key) == data
    assert store._conn.execute("SELECT COUNT(*) FROM exchange_pieces").fetchone()[0] == 1
    assert store._conn.execute("SELECT size FROM exchange_pieces").fetchone()[0] == len(data)


def _exchange(store, *pieces: bytes):
    keys = [store.put_piece(p) for p in pieces]
    return [{"request": {"manifest": {"messages": [{"$blob": k} for k in keys[:-1]]}},
             "response": {"body_blob": keys[-1]}}], keys


def test_deleting_a_session_deletes_its_recordings_and_unshared_pieces(store):
    rec = Recorder(store=store)
    shared = b'{"text":"system","type":"text"}'
    ex_a, keys_a = _exchange(store, shared, b"reply A")
    ex_b, keys_b = _exchange(store, shared, b"reply B")
    _turn(rec, "ta", session="sess_A")
    _model_step(rec, "sa", "ta", session="sess_A", exchange=ex_a)
    _end_turn(rec, "ta", session="sess_A")
    _turn(rec, "tb", session="sess_B")
    _model_step(rec, "sb", "tb", session="sess_B", exchange=ex_b)
    _end_turn(rec, "tb", session="sess_B")
    store.flush()

    rec.delete_session("sess_A")
    assert store.load_recording("sess_A") == {"turns": [], "steps": []}
    assert len(store.load_recording("sess_B")["steps"]) == 1
    assert store.get_piece(keys_a[1]) is None  # only sess_A used it
    assert store.get_piece(keys_b[0]) == shared  # still used by sess_B
    assert not [t for t in rec.turns.values() if t.session_id == "sess_A"]


def test_a_deleted_session_is_not_written_back_by_pending_writes(store):
    rec = Recorder(store=store)
    _turn(rec, "ta")
    _model_step(rec, "sa", "ta")  # pending, not flushed yet
    rec.delete_session("sess_A")
    store.flush()
    assert store._conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0


def test_retention_sweep_deletes_old_turns_in_batches(store, monkeypatch):
    monkeypatch.setattr(store_mod, "SWEEP_BATCH", 2)
    monkeypatch.setattr(store_mod, "SWEEP_PAUSE_S", 0)
    rec = Recorder(store=store)
    now = 100 * DAY_MS
    old_ex, old_keys = _exchange(store, b"old piece", b"old reply")
    for i in range(5):  # five turns 31 days old
        at = now - 31 * DAY_MS + i
        _turn(rec, f"old{i}", at=at, mono=i * 1000)
        _model_step(rec, f"olds{i}", f"old{i}", at=at + 1, mono=i * 1000 + 1, end=i * 1000 + 2,
                    exchange=old_ex)
        _end_turn(rec, f"old{i}", at=at + 3, mono=i * 1000 + 3)
    _turn(rec, "new", at=now - DAY_MS, mono=10_000)
    _model_step(rec, "news", "new", at=now - DAY_MS + 1, mono=10_001, end=10_002)
    _end_turn(rec, "new", at=now - DAY_MS + 3, mono=10_003)
    store.flush()

    assert store.sweep(now_ms=now) == 5
    left = store.load_recording("sess_A")
    assert [t["turn_id"] for t in left["turns"]] == ["new"]
    assert [s["step_id"] for s in left["steps"]] == ["news"]
    assert all(store.get_piece(k) is None for k in old_keys)


def test_sweep_also_ages_out_steps_with_no_turn(store):
    rec = Recorder(store=store)
    # A proxy call: no session, no turn.
    rec.emit({"type": "step_ended", "v": 1, "at_ms": 5, "mono_ms": 5, "session_id": "",
              "turn_id": "", "step_id": "p1", "status": "ok", "kind": "model", "name": "model",
              "parent_step_id": None, "call_id": None, "started_at": 1, "attrs": {}})
    assert not rec.steps  # written, and not kept in memory: no turn's end would release it
    store.flush()
    assert store._conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1
    store.sweep(now_ms=40 * DAY_MS)
    assert store._conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 0


def test_background_writer_writes_and_close_flushes(tmp_path):
    path = tmp_path / "recordings.db"
    s = RecordingStore(path)  # its writer also runs the retention sweep: use today's times
    rec = Recorder(store=s)
    now = int(time.time() * 1000)
    _turn(rec, "t1", at=now)
    _model_step(rec, "s1", "t1", at=now + 10)
    _end_turn(rec, "t1", at=now + 100)
    s.close()
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM turns").fetchone()[0] == "completed"
    finally:
        conn.close()


def test_recorder_is_safe_from_many_threads(store):
    rec = Recorder(store=store)
    _turn(rec, "t1", mono=0)

    def tools(n):
        for i in range(50):
            sid = f"x{n}_{i}"
            base = {"v": 1, "session_id": "sess_A", "turn_id": "t1", "step_id": sid}
            rec.emit({**base, "type": "step_started", "at_ms": 1, "mono_ms": 1, "kind": "tool",
                      "name": "read_file", "call_id": sid, "call_index": i})
            rec.emit({**base, "type": "step_ended", "at_ms": 2, "mono_ms": 2, "status": "ok",
                      "kind": "tool", "name": "read_file", "call_id": sid, "started_at": 1})

    threads = [threading.Thread(target=tools, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _end_turn(rec, "t1", mono=10)
    steps = store.load_recording("sess_A")["steps"]
    assert len(steps) == 400
    assert sorted(s["seq"] for s in steps) == list(range(1, 401))


def test_a_bad_event_never_raises(store):
    rec = Recorder(store=store)
    rec.emit({"type": "step_started"})  # no ids at all
    rec.emit({"type": "turn_ended", "turn_id": "unknown", "mono_ms": 0, "status": "completed"})


def test_ulids_sort_by_creation_and_never_repeat():
    ids = [new_ulid() for _ in range(5000)]
    assert len(set(ids)) == 5000
    assert ids == sorted(ids)
    assert all(len(i) == 26 for i in ids)


def test_the_step_clock_ticks_every_millisecond():
    # Windows' time.monotonic ticks every 15.6 ms; durations need millisecond steps.
    first = now_ms()[1]
    deadline = time.perf_counter() + 0.2
    while (tick := now_ms()[1]) == first and time.perf_counter() < deadline:
        pass
    assert tick - first == 1
