"""A corrupt on-disk JSON file must never crash a store's construction (startup).

Every file-backed store is built eagerly in ``SessionManager.__init__`` and loads its file
there, so an unreadable file used to raise straight out of the constructor and take the whole
server down. These tests pin the resilient contract for the shared helpers and for each store:
a garbage file loads as empty (and is quarantined), and a normal save round-trips atomically.
"""

from __future__ import annotations

from coworker.connections import PersonaConnectionStore, SessionConnectionStore
from coworker.inbox import InboxStore
from coworker.inbox_routing import DEFAULT_INBOX, InboxRouting
from coworker.jsonstore import read_json, write_json_atomic
from coworker.mentions import MentionSessionStore
from coworker.overrides import RiskOverrideStore
from coworker.risk import RiskClass
from coworker.subscriptions import SubscriptionStore
from coworker.unattended import UnattendedRegistry
from coworker.unrouted import UnroutedStore

CORRUPT = '{"items": [ {"broken": '  # truncated mid-object


# -- jsonstore helpers ----------------------------------------------------------
def test_read_json_missing_returns_default(tmp_path):
    assert read_json(tmp_path / "nope.json", {"a": 1}) == {"a": 1}
    assert read_json(None, []) == []


def test_read_json_valid_round_trips(tmp_path):
    p = tmp_path / "ok.json"
    write_json_atomic(p, {"x": [1, 2, 3]})
    assert read_json(p) == {"x": [1, 2, 3]}


def test_read_json_corrupt_returns_default_and_quarantines(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(CORRUPT, encoding="utf-8")
    assert read_json(p, {"fallback": True}) == {"fallback": True}
    # The bad file is moved aside, not silently clobbered or left to re-crash next boot.
    assert not p.exists()
    assert (tmp_path / "bad.json.corrupt").read_text(encoding="utf-8") == CORRUPT


def test_write_json_atomic_leaves_no_temp_file(tmp_path):
    p = tmp_path / "out.json"
    write_json_atomic(p, {"k": "v"})
    assert read_json(p) == {"k": "v"}
    # No stray sibling temp files left behind.
    assert [f.name for f in tmp_path.iterdir()] == ["out.json"]


def test_write_json_atomic_none_path_is_noop():
    write_json_atomic(None, {"anything": 1})  # must not raise


# -- every store survives a corrupt file at construction ------------------------
def _write_corrupt(path):
    path.write_text(CORRUPT, encoding="utf-8")
    return path


def test_inbox_store_survives_corrupt_file(tmp_path):
    p = _write_corrupt(tmp_path / "inbox.json")
    store = InboxStore(p)  # must not raise
    assert store.list() == []
    assert (tmp_path / "inbox.json.corrupt").exists()


def test_inbox_routing_survives_corrupt_file(tmp_path):
    p = _write_corrupt(tmp_path / "inbox_routing.json")
    routing = InboxRouting(p)
    assert routing.route_for("s1") == DEFAULT_INBOX


def test_mentions_store_survives_corrupt_file(tmp_path):
    p = _write_corrupt(tmp_path / "mentions.json")
    assert MentionSessionStore(p).all() == []


def test_subscription_store_survives_corrupt_file(tmp_path):
    p = _write_corrupt(tmp_path / "subs.json")
    assert SubscriptionStore(p).all() == []


def test_unrouted_store_survives_corrupt_file(tmp_path):
    p = _write_corrupt(tmp_path / "unrouted.json")
    assert UnroutedStore(p).list() == []


def test_unattended_registry_survives_corrupt_file(tmp_path):
    p = _write_corrupt(tmp_path / "unattended.json")
    assert UnattendedRegistry(p).sessions() == []


def test_connection_stores_survive_corrupt_file(tmp_path):
    persona = PersonaConnectionStore(_write_corrupt(tmp_path / "persona.json"))
    session = SessionConnectionStore(_write_corrupt(tmp_path / "session.json"))
    assert persona.get("p1") == {}
    assert session.get("s1") == {}


def test_override_store_survives_corrupt_file(tmp_path):
    p = _write_corrupt(tmp_path / "overrides.json")
    assert RiskOverrideStore(p).resolve("some_tool") is None


# -- round-trip after recovery: a corrupt file heals on the next save ----------
def test_store_save_after_corrupt_load_round_trips(tmp_path):
    p = _write_corrupt(tmp_path / "subs.json")
    store = SubscriptionStore(p)  # corrupt file quarantined, in-memory empty
    store.subscribe("session-1", "slack:C123")
    # A fresh store reads back exactly what was saved — the file is valid again.
    assert [(s.session_id, s.channel) for s in SubscriptionStore(p).all()] == [
        ("session-1", "slack:C123")
    ]


def test_override_store_save_round_trips(tmp_path):
    p = tmp_path / "overrides.json"
    store = RiskOverrideStore(p)
    store.set_rule("mcp__notion__*", RiskClass.READ)
    assert RiskOverrideStore(p).resolve("mcp__notion__search") == RiskClass.READ
