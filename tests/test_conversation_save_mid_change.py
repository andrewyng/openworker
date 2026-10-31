"""ConversationStore.save when the message list changed in the MIDDLE, not only at the end.

`save()` used to persist by count: if the in-memory list was longer than the file it
appended `messages[existing:]`. Two code paths change the list in the middle:

* `load()` heals tool-call/result pairing (`_repair_tool_pairing`): it synthesises a
  placeholder result right after an orphaned call and moves a misplaced result up.
* `TurnEngine._repair_dangling_tool_calls` inserts a stub result right after the
  assistant message whose call never got one.

After either, appending by count wrote a shifted tail: the inserted message was never
saved and the message after it was written twice, again on every load-and-save cycle.
"""

from __future__ import annotations

import json

from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord


def _assistant_call(call_id="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}]}


def _record(sid, messages):
    return SessionRecord(session_id=sid, workspace="/w", model="m", mode="interactive", messages=messages)


def _file_lines(store, sid):
    return [json.loads(line) for line in store._file(sid).read_text(encoding="utf-8").splitlines() if line.strip()]


def test_save_after_load_repair_persists_the_placeholder_and_does_not_duplicate(tmp_path):
    store = ConversationStore(tmp_path / "state")
    sid = "abc123def456"
    # An interrupted turn left a call with no result, followed by the next user message.
    store.save(_record(sid, [{"role": "user", "content": "go"}, _assistant_call("c1"),
                             {"role": "user", "content": "next"}]))

    rec = store.load(sid)                       # heals: placeholder inserted after the call
    assert [m["role"] for m in rec.messages] == ["user", "assistant", "tool", "user"]
    rec.messages.append({"role": "assistant", "content": "answer"})
    store.save(rec)

    assert _file_lines(store, sid) == rec.messages
    # And it stays correct across another load-and-save cycle.
    again = store.load(sid)
    assert again.messages == rec.messages
    store.save(again)
    assert _file_lines(store, sid) == rec.messages
    assert [m.get("content") for m in _file_lines(store, sid)].count("next") == 1


def test_save_after_mid_history_insert_writes_the_inserted_message(tmp_path):
    store = ConversationStore(tmp_path / "state")
    sid = "abc123def457"
    msgs = [{"role": "user", "content": "go"}, _assistant_call("c1"),
            {"role": "tool", "tool_call_id": "c1", "content": "{}"}, {"role": "user", "content": "more"}]
    store.save(_record(sid, msgs))
    # Something inserts a message in the middle (as the engine's dangling-call repair does),
    # then a new message is appended.
    msgs.insert(1, {"role": "notice", "kind": "note", "text": "inserted"})
    msgs.append({"role": "assistant", "content": "done"})
    store.save(_record(sid, msgs))
    assert _file_lines(store, sid) == msgs


def test_restart_mid_approval_then_mode_switch_then_new_turn(tmp_path):
    """The sequence seen in the app: a turn parks on an approval and is saved, the app is
    quit, the reopened session switches mode (a notice is saved after the parked call),
    then a new message runs `_repair_dangling_tool_calls`, which inserts the stub between
    the call and the notice."""
    from types import SimpleNamespace

    from coworker.engine import TurnEngine

    store = ConversationStore(tmp_path / "state")
    sid = "abc123def459"
    store.save(_record(sid, [{"role": "user", "content": "run it"}, _assistant_call("c1")]))

    rec = store.load(sid)                       # trailing call: pending, left as is
    rec.messages.append({"role": "notice", "kind": "mode_switch", "text": "Discuss is on."})
    store.save(rec)

    TurnEngine._repair_dangling_tool_calls(SimpleNamespace(messages=rec.messages))
    rec.messages.append({"role": "user", "content": "never mind"})
    rec.messages.append({"role": "assistant", "content": "OK"})
    store.save(rec)

    assert _file_lines(store, sid) == rec.messages
    assert [m["role"] for m in _file_lines(store, sid)] == [
        "user", "assistant", "tool", "notice", "user", "assistant"]


def test_plain_append_still_appends(tmp_path):
    """The common path stays an append: the existing lines are not rewritten."""
    store = ConversationStore(tmp_path / "state")
    sid = "abc123def458"
    msgs = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}]
    store.save(_record(sid, msgs))
    path = store._file(sid)
    before = path.stat().st_ino if hasattr(path.stat(), "st_ino") else None
    head = path.read_text(encoding="utf-8")
    msgs.append({"role": "user", "content": "again"})
    store.save(_record(sid, msgs))
    text = path.read_text(encoding="utf-8")
    assert text.startswith(head)
    assert _file_lines(store, sid) == msgs
    if before is not None and before != 0:
        assert path.stat().st_ino == before     # same file, appended to, not replaced
