"""Crash-safety of ConversationStore's shrink rewrite (write-side atomicity).

`save()` appends new messages on the common path, but when a turn *reduces* the message
count (context compaction / summarization) it rewrites the whole ``.jsonl``. That rewrite
must be atomic: a crash partway through must not truncate or erase the existing history —
the most valuable data the app holds.

This is the write-side complement to read-side corrupt-line tolerance: prevent the
truncated file rather than cope with one after the fact.
"""

from __future__ import annotations

import os

import pytest

from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord


def _rec(sid: str, n: int) -> SessionRecord:
    return SessionRecord(
        session_id=sid,
        workspace="/w",
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": f"msg-{i}"} for i in range(n)],
    )


def test_shrink_rewrite_preserves_history_when_write_crashes(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    sid = "sess1"

    # Persist a 5-message history via the append path.
    store.save(_rec(sid, 5))
    assert len(store.load(sid).messages) == 5

    # Force a crash partway through the shrink rewrite (5 -> 2): the write fails after the
    # first line. A non-atomic in-place open(..., "w") truncates the real file at open() and
    # the crash then erases the history; an atomic tmp-then-replace leaves the original
    # untouched because the swap never happens.
    import coworker.conversations as conv

    real_open = open
    writes = {"n": 0}

    class _CrashingFile:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()
            return False

        def write(self, s):
            writes["n"] += 1
            if writes["n"] >= 2:
                raise OSError("simulated crash mid-write")
            return self._fh.write(s)

    def crashing_open(file, mode="r", *args, **kwargs):
        fh = real_open(file, mode, *args, **kwargs)
        if "w" in mode and os.path.basename(str(file)).startswith(sid):
            return _CrashingFile(fh)
        return fh

    monkeypatch.setattr(conv, "open", crashing_open, raising=False)

    with pytest.raises(OSError):
        store.save(_rec(sid, 2))

    monkeypatch.undo()

    # The interrupted shrink must not have destroyed the existing history.
    assert len(store.load(sid).messages) == 5


def test_shrink_rewrite_persists_reduced_history(tmp_path):
    """The (non-crash) shrink path still rewrites the log to exactly the reduced set."""
    store = ConversationStore(tmp_path)
    sid = "sess2"

    store.save(_rec(sid, 5))
    assert len(store.load(sid).messages) == 5

    store.save(_rec(sid, 3))  # shrink 5 -> 3
    reloaded = store.load(sid)
    assert [m["content"] for m in reloaded.messages] == ["msg-0", "msg-1", "msg-2"]

    # No leftover temp file next to the conversation log.
    assert not (tmp_path / "conversations" / f"{sid}.tmp").exists()
