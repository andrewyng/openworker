"""Phase 3 gate — multi-inbox routing: named bindings, route resolution, delivery + reply."""

from __future__ import annotations

from coworker.inbox import InboxStore
from coworker.inbox_routing import (
    DEFAULT_INBOX,
    InboxRouting,
    deliver,
    resolve_from_reply,
)


def test_route_precedence(tmp_path):
    r = InboxRouting(tmp_path / "routing.json")
    r.set_binding("ops", channel="slack", target="#ops-coworker")
    r.set_persona_default("ops", "ops")
    # Persona default applies...
    assert r.route_for("s1", "ops") == "ops"
    # ...unless a per-session override wins.
    r.set_session_override("s1", DEFAULT_INBOX)
    assert r.route_for("s1", "ops") == DEFAULT_INBOX
    # Unbound persona/session → default.
    assert r.route_for("s2", "cowork") == DEFAULT_INBOX


def test_bindings_persist(tmp_path):
    InboxRouting(tmp_path / "routing.json").set_binding(
        "ops", channel="telegram", target="123"
    )
    r2 = InboxRouting(tmp_path / "routing.json")
    b = r2.binding_for("ops")
    assert b.channel == "telegram" and b.target == "123"


def test_deliver_to_channel_embeds_item_id(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    routing = InboxRouting(tmp_path / "routing.json")
    routing.set_binding("ops", channel="slack", target="#ops")
    item = store.add_approval("s1", "Restart service?", body="prod web-1", inbox="ops")

    sent = {}

    def sender(channel, target, text):
        sent.update(channel=channel, target=target, text=text)

    assert deliver(item, routing.binding_for("ops"), sender) is True
    assert sent["channel"] == "slack" and sent["target"] == "#ops"
    assert f"[ow:{item.id}]" in sent["text"]  # rebrand: emits [ow:…] since 2026-07-22


def test_in_app_only_binding_delivers_nothing(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    routing = InboxRouting(tmp_path / "routing.json")
    item = store.add_approval("s1", "x", inbox=DEFAULT_INBOX)
    calls = []
    assert (
        deliver(item, routing.binding_for(DEFAULT_INBOX), lambda *a: calls.append(a))
        is False
    )
    assert calls == []


def test_inbound_reply_resolves_correct_item(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    # Current token spelling…
    ok = resolve_from_reply(f"approve [ow:{item.id}]", store.resolve)
    assert ok is True
    assert store.get(item.id).resolution == "allow"


def test_inbound_freetext_answer_to_question(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    q = store.add_question("s1", "Which region?")
    res = resolve_from_reply(f"us-east-1 [ow:{q.id}]", store.resolve)
    assert res is True and store.get(q.id).resolution == "us-east-1"


def test_reply_intent_matches_words_not_substrings(tmp_path):
    """Intent must key off standalone words. A substring test read "disapprove" as approve
    (executing a rejected action) and "no" inside note/not/cannot as deny (clobbering a
    free-text answer). Both are approval-safety regressions."""
    # "disapprove" contains the substring "approve" but must NEVER resolve to allow — that
    # inversion executed an action the user was rejecting. Anything that isn't "allow"/"always"
    # is treated as a denial by inbox_approver, so free-text here is the safe outcome.
    store = InboxStore(tmp_path / "a.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"I disapprove [ow:{item.id}]", store.resolve) is True
    assert store.get(item.id).resolution != "allow"
    # An explicit rejection word still classifies as deny.
    item2 = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"no, do not [ow:{item2.id}]", store.resolve) is True
    assert store.get(item2.id).resolution == "deny"

    # A question answer that merely contains "no"/"yes" as substrings stays free text.
    store2 = InboxStore(tmp_path / "b.json")
    for answer in ("use option A now", "that is a known issue", "cannot reach the host"):
        q = store2.add_question("s1", "What happened?")
        assert resolve_from_reply(f"{answer} [ow:{q.id}]", store2.resolve) is True
        assert store2.get(q.id).resolution == answer

    # Standalone intent words and emoji still classify.
    store3 = InboxStore(tmp_path / "c.json")
    yes = store3.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"yes [ow:{yes.id}]", store3.resolve) is True
    assert store3.get(yes.id).resolution == "allow"
    thumb = store3.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"👍 [ow:{thumb.id}]", store3.resolve) is True
    assert store3.get(thumb.id).resolution == "allow"


def test_reply_without_token_is_ignored(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    assert resolve_from_reply("random chatter", store.resolve) is None


def test_inbound_legacy_ocw_token_still_resolves(tmp_path):
    """Replies to messages sent BEFORE the @OpenWorker rename carry [ocw:…] — must keep working."""
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"deny [ocw:{item.id}]", store.resolve) is True
    assert store.get(item.id).resolution == "deny"
