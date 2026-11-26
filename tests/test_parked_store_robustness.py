"""ParkedStore: one unreadable parked message must not take the queue with it.

The parked queue holds what an unauthorized sender said, kept so the owner can
allow-and-deliver instead of asking them to re-send. Two things used to lose it:

1. A file that parses but is not an object (``[]``, ``null``, ``"hi"``) raised
   ``AttributeError`` out of ``_load`` — ``SessionManager.__init__`` builds this
   store, so the server did not start. The existing ``except`` only listed
   ``(OSError, ValueError, TypeError)`` and does not catch it.
2. One record the dataclass refuses (an unknown field, a missing required one)
   aborted the list comprehension and the handler then set ``_items = []`` —
   **every** parked message was discarded, silently, with no log and no way back.

A save interrupted mid-write produces the torn file those loads then meet, so
the save is atomic too. When anything is dropped the original bytes are moved
aside to ``<name>.corrupt`` (never deleted, never overwritten by a later save)
and the warning names the reason and where the bytes went — the shape the
risk-override and connection stores use.
"""

from __future__ import annotations

import json
import logging

from coworker.connectors.parked import ParkedStore

_GOOD = {
    "platform": "slack",
    "chat_id": "C0BD7KZ1AH5",
    "user_id": "U07JK68S4BH",
    "text": "hello",
    "chat_name": "general",
    "user_name": "{{PERSON_75342fe3}}",
    "ts": 1700000000.0,
    "id": "abc123def456",
}

# Same message, plus a field a later build added: `ParkedMessage(**raw)` refuses it.
_WITH_UNKNOWN_FIELD = {**_GOOD, "id": "second0000001", "future_field": "x"}

# A record missing a required field (`text`).
_MISSING_REQUIRED = {k: v for k, v in _GOOD.items() if k != "text"}


def _write(tmp_path, body: str):
    path = tmp_path / "parked.json"
    path.write_text(body, encoding="utf-8")
    return path


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def test_a_good_file_still_loads(tmp_path, caplog):
    second = {**_GOOD, "id": "second0000001", "chat_id": "C2"}
    path = _write(tmp_path, json.dumps({"items": [_GOOD, second]}))
    with caplog.at_level(logging.WARNING, logger="coworker.connectors.parked"):
        store = ParkedStore(path)
    assert [i.id for i in store._items] == ["abc123def456", "second0000001"]
    assert _warnings(caplog) == []


def test_valid_json_that_is_not_an_object_starts_empty_and_keeps_the_bytes(tmp_path, caplog):
    for body in ("[]", "null", '"hello"'):
        path = _write(tmp_path, body)
        with caplog.at_level(logging.WARNING, logger="coworker.connectors.parked"):
            store = ParkedStore(path)
        assert store._items == [], body
        assert path.with_suffix(path.suffix + ".corrupt").read_text(encoding="utf-8") == body
        assert any(str(path) in m for m in _warnings(caplog))


def test_a_torn_file_starts_empty_and_keeps_the_bytes(tmp_path, caplog):
    body = '{"items": [{"platform": "sl'
    path = _write(tmp_path, body)
    with caplog.at_level(logging.WARNING, logger="coworker.connectors.parked"):
        store = ParkedStore(path)
    assert store._items == []
    assert path.with_suffix(path.suffix + ".corrupt").read_text(encoding="utf-8") == body
    assert any("unreadable" in m for m in _warnings(caplog))


def test_one_bad_record_does_not_discard_the_good_ones(tmp_path, caplog):
    """The bug this file exists for: 3 messages, 1 unreadable → 2 kept, not 0."""
    path = _write(
        tmp_path, json.dumps({"items": [_GOOD, _WITH_UNKNOWN_FIELD, _MISSING_REQUIRED]})
    )
    with caplog.at_level(logging.WARNING, logger="coworker.connectors.parked"):
        store = ParkedStore(path)

    assert [i.id for i in store._items] == ["abc123def456"]
    # The dropped records' bytes are preserved, not overwritten by the next save.
    spare = path.with_suffix(path.suffix + ".corrupt")
    assert spare.is_file()
    assert "second0000001" in spare.read_text(encoding="utf-8")
    assert any(str(path) in m for m in _warnings(caplog))


def test_a_quarantined_file_does_not_block_a_fresh_save(tmp_path):
    path = _write(tmp_path, '{"items": [{"platform": "sl')
    store = ParkedStore(path)
    store.park(platform="slack", chat_id="C1", user_id="U1", text="hi")

    assert json.loads(path.read_text(encoding="utf-8"))["items"][0]["text"] == "hi"
    assert path.with_suffix(path.suffix + ".corrupt").is_file()


def test_save_is_atomic_and_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "parked.json"
    store = ParkedStore(path)
    store.park(platform="slack", chat_id="C1", user_id="U1", text="hi")
    assert json.loads(path.read_text(encoding="utf-8"))["items"][0]["text"] == "hi"
    assert not path.with_suffix(".json.tmp").exists()


def test_a_failed_save_keeps_the_previous_file_and_the_in_memory_record(tmp_path, monkeypatch):
    path = _write(tmp_path, json.dumps({"items": [_GOOD]}))
    store = ParkedStore(path)

    def refuse(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(type(path), "replace", refuse)
    store.park(platform="slack", chat_id="C1", user_id="U1", text="hi")
    # Best-effort persistence: no raise into the inbound handler...
    assert len(store._items) == 2
    # ...the file on disk is still the old complete one, and no .tmp is left.
    assert json.loads(path.read_text(encoding="utf-8"))["items"] == [_GOOD]
    assert not path.with_suffix(".json.tmp").exists()
