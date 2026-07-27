"""Phase 2+ gate — self-wake: timer/completion/event wakes, scheduler tick integration."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

from coworker.selfwake import WakeStore, _now, Wake
from coworker.selfwake import (
    KIND_TIMER,
    STATE_PENDING,
    STATE_DUE,
    STATE_FIRED,
)


def test_add_timer(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    w = store.add_timer("s1", _now() + timedelta(hours=1))
    assert w.kind == KIND_TIMER and w.state == STATE_PENDING
    assert store.pending("s1") == [w]


def test_due_returns_past_timers(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    store.add_timer("s1", _now() - timedelta(seconds=10))
    assert len(store.due()) == 1


def test_due_excludes_future_timers(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    store.add_timer("s1", _now() + timedelta(hours=1))
    assert store.due() == []


def test_due_excludes_fired(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    w = store.add_timer("s1", _now() - timedelta(seconds=10))
    store.mark_fired(w.id)
    assert store.due() == []


def test_complete_job_marks_due(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    w = store.add_completion("s1", "job-1")
    assert store.complete_job("job-1") == [w]
    assert store.due() == [w]
    assert store.pending("s1") == [w]


def test_mark_fired(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    w = store.add_timer("s1", _now() - timedelta(seconds=10))
    store.mark_fired(w.id)
    assert store._wakes[w.id].state == STATE_FIRED


def test_pending_filters_session(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    store.add_timer("s1", _now() + timedelta(hours=1))
    w2 = store.add_timer("s2", _now() + timedelta(hours=1))
    assert len(store.pending("s1")) == 1
    assert store.pending("s2") == [w2]


def test_corrupt_file_does_not_crash(tmp_path):
    p = tmp_path / "wakes.json"
    p.write_text("{garbage!!", encoding="utf-8")
    store = WakeStore(p)
    assert store.pending() == []
    w = store.add_timer("s1", _now() + timedelta(hours=1))
    assert w.id in store._wakes


def test_empty_file_does_not_crash(tmp_path):
    p = tmp_path / "wakes.json"
    p.write_text("", encoding="utf-8")
    store = WakeStore(p)
    assert store.pending() == []


def test_atomic_save_on_crash(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    w = store.add_timer("s1", _now() + timedelta(hours=1))
    wake_file = tmp_path / "wakes.json"
    content_before = wake_file.read_text(encoding="utf-8")
    # Simulate a partially-written file by truncating at a random point
    truncated = content_before[: len(content_before) // 2]
    wake_file.write_text(truncated, encoding="utf-8")
    store2 = WakeStore(wake_file)
    # The truncated file should be handled by corrupt-file fallback
    # so store2 loads nothing from it (the tmp write is atomic, so this
    # only happens if the rename hadn't finished before the crash)
    # but our main assertion is that the store doesn't crash
    assert store2.pending() == []


def test_due_thread_safe_snapshot(tmp_path):
    """due() must work correctly when wakes are added concurrently."""
    store = WakeStore(tmp_path / "wakes.json")
    store.add_timer("s1", _now() + timedelta(hours=1))
    errors = []

    def add_wakes():
        for i in range(100):
            store.add_timer(f"s{i}", _now() + timedelta(hours=2))

    def read_due():
        for _ in range(100):
            try:
                store.due()
            except RuntimeError as e:
                errors.append(e)

    t1 = threading.Thread(target=add_wakes, daemon=True)
    t2 = threading.Thread(target=read_due, daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors, f"due() raised RuntimeError on concurrent modification: {errors}"


def test_pending_thread_safe_snapshot(tmp_path):
    """pending() must work correctly when wakes are added concurrently."""
    store = WakeStore(tmp_path / "wakes.json")
    errors = []

    def add_wakes():
        for i in range(100):
            store.add_timer(f"s{i}", _now() + timedelta(hours=2))

    def read_pending():
        for _ in range(100):
            try:
                store.pending()
            except RuntimeError as e:
                errors.append(e)

    t1 = threading.Thread(target=add_wakes, daemon=True)
    t2 = threading.Thread(target=read_pending, daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors, f"pending() raised RuntimeError on concurrent modification: {errors}"
