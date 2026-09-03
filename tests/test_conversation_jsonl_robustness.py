"""ConversationStore: a corrupt line in a .jsonl must not brick session load.

An append interrupted mid-write (crash, full disk) leaves one malformed trailing line.
load() must skip it and return the recoverable history, not raise on every open.
"""

from __future__ import annotations

from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord


def _seed(store: ConversationStore, sid: str, n: int) -> None:
    store.save(
        SessionRecord(
            session_id=sid,
            workspace="/tmp",
            model="m",
            mode="interactive",
            messages=[{"role": "user", "content": f"m{i}"} for i in range(n)],
        )
    )


def test_load_skips_a_corrupt_trailing_line(tmp_path):
    store = ConversationStore(tmp_path / "state")
    sid = "abc123def456"
    _seed(store, sid, 2)

    # Simulate a torn write: append a truncated JSON line to the session's log.
    jsonl = tmp_path / "state" / "conversations" / f"{sid}.jsonl"
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write('{"role": "user", "content": "unterm\n')  # no closing brace/quote

    loaded = store.load(sid)  # must not raise
    assert loaded is not None
    # The two good messages survive; the corrupt line is dropped.
    assert [m["content"] for m in loaded.messages] == ["m0", "m1"]


def test_load_skips_a_corrupt_middle_line(tmp_path):
    store = ConversationStore(tmp_path / "state")
    sid = "def456abc123"
    jsonl = tmp_path / "state" / "conversations" / f"{sid}.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text(
        '{"role": "user", "content": "first"}\n'
        "not json at all\n"
        '{"role": "assistant", "content": "third"}\n',
        encoding="utf-8",
    )
    # Register the session in the index so load() reaches the .jsonl.
    store._conn.execute(
        "INSERT INTO sessions (session_id, workspace, model, mode, title, n_msgs) "
        "VALUES (?, '/tmp', 'm', 'interactive', 't', 2)",
        (sid,),
    )
    store._conn.commit()

    loaded = store.load(sid)
    assert loaded is not None
    assert [m["content"] for m in loaded.messages] == ["first", "third"]
