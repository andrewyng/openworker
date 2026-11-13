"""A corrupt JSON state file must not stop the server from starting (#204).

These stores are built eagerly by ``SessionManager.__init__``, so an exception from any
of their loaders aborts startup until the user finds and deletes the file by hand. A
save interrupted mid-write leaves a truncated document, and a file can also be valid
JSON of the wrong shape. Each store should treat all of these as "start empty".
"""

from __future__ import annotations

import json

import pytest

from coworker.inbox import InboxStore
from coworker.inbox_routing import DEFAULT_INBOX, InboxRouting
from coworker.selfwake import WakeStore
from coworker.subscriptions import SubscriptionStore
from coworker.unattended import ATTENDED, UnattendedRegistry
from coworker.unrouted import UnroutedStore

_CORRUPT_DOCUMENTS = [
    pytest.param('{"items": [{"id": "abc", "ses', id="truncated"),
    pytest.param("", id="empty-file"),
    pytest.param("null", id="json-null"),
    pytest.param('"text"', id="json-string"),
]

# Valid JSON with the right top-level keys, but records the dataclass can't accept.
_BAD_RECORD = {"not_a_field": 1}
_BAD_RECORDS = {
    "inbox": {"items": [_BAD_RECORD]},
    "routing": {"bindings": [_BAD_RECORD]},
    "wakes": {"wakes": [_BAD_RECORD]},
    "subscriptions": {"subscriptions": [_BAD_RECORD]},
    "unrouted": {"items": [_BAD_RECORD]},
}


def _write(tmp_path, text):
    path = tmp_path / "state.json"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("document", _CORRUPT_DOCUMENTS)
def test_inbox_starts_empty(tmp_path, document):
    assert InboxStore(_write(tmp_path, document)).pending() == []


@pytest.mark.parametrize("document", _CORRUPT_DOCUMENTS)
def test_inbox_routing_keeps_defaults(tmp_path, document):
    routing = InboxRouting(_write(tmp_path, document))
    assert routing.route_for("session", "persona") == DEFAULT_INBOX
    assert [b["name"] for b in routing.bindings()] == [DEFAULT_INBOX]


@pytest.mark.parametrize("document", _CORRUPT_DOCUMENTS)
def test_attendance_reads_as_attended(tmp_path, document):
    assert UnattendedRegistry(_write(tmp_path, document)).attendance("session") == ATTENDED


@pytest.mark.parametrize("document", _CORRUPT_DOCUMENTS)
def test_wakes_start_empty(tmp_path, document):
    store = WakeStore(_write(tmp_path, document))
    assert store.due() == []
    assert store.stopped_sessions == set()


@pytest.mark.parametrize("document", _CORRUPT_DOCUMENTS)
def test_subscriptions_start_empty(tmp_path, document):
    assert SubscriptionStore(_write(tmp_path, document)).all() == []


@pytest.mark.parametrize("document", _CORRUPT_DOCUMENTS)
def test_unrouted_starts_empty(tmp_path, document):
    assert UnroutedStore(_write(tmp_path, document)).list() == []


def test_a_record_with_unknown_fields_does_not_raise(tmp_path):
    assert InboxStore(_write(tmp_path, json.dumps(_BAD_RECORDS["inbox"]))).pending() == []
    assert [
        b["name"]
        for b in InboxRouting(_write(tmp_path, json.dumps(_BAD_RECORDS["routing"]))).bindings()
    ] == [DEFAULT_INBOX]
    assert WakeStore(_write(tmp_path, json.dumps(_BAD_RECORDS["wakes"]))).due() == []
    assert SubscriptionStore(_write(tmp_path, json.dumps(_BAD_RECORDS["subscriptions"]))).all() == []
    assert UnroutedStore(_write(tmp_path, json.dumps(_BAD_RECORDS["unrouted"]))).list() == []


def test_a_good_record_before_a_bad_one_is_not_kept(tmp_path):
    """A half-read file must not leave a store with only some of its records."""
    wake = {"id": "w1", "session_id": "s1", "kind": "timer", "state": "pending"}
    document = {"stopped_sessions": ["s9"], "wakes": [wake, _BAD_RECORD]}
    store = WakeStore(_write(tmp_path, json.dumps(document)))
    assert store.stopped_sessions == set()
    assert store.due() == []


def test_a_save_replaces_the_corrupt_file(tmp_path):
    path = _write(tmp_path, "{not json")
    store = UnroutedStore(path)
    store.record("slack", "someone", "hello", "no route")
    assert [item["text"] for item in UnroutedStore(path).list()] == ["hello"]


def test_valid_files_still_load(tmp_path):
    path = _write(tmp_path, "{}")
    store = UnroutedStore(path)
    store.record("slack", "someone", "hello", "no route")
    assert len(UnroutedStore(path).list()) == 1
