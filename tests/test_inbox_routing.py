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


def test_freetext_answer_containing_decision_substrings_stays_freetext(tmp_path):
    """Decision intent comes from the LEADING word only — a free-text answer that merely
    contains "no"/"yes" as a substring or mid-sentence word must not flip to deny/allow."""
    store = InboxStore(tmp_path / "inbox.json")
    for answer in (
        "I have no preference — use us-east-1",
        "yesterday's numbers look fine",
        "the northern region",
    ):
        q = store.add_question("s1", "Which region?")
        assert resolve_from_reply(f"{answer} [ow:{q.id}]", store.resolve) is True
        assert store.get(q.id).resolution == answer


def test_negated_approval_reply_does_not_allow(tmp_path):
    """"I cannot approve this yet" used to resolve as ALLOW (substring match, allow words
    checked first). It must fall through to free text, which the approver maps to deny."""
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"I cannot approve this yet [ow:{item.id}]", store.resolve)
    assert store.get(item.id).resolution == "I cannot approve this yet"


def test_leading_decision_word_and_emoji_still_resolve(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    for reply, expected in (
        ("Yes, go ahead", "allow"),
        ("No.", "deny"),
        ("👍", "allow"),
        ("❌ too risky", "deny"),
        ("allow", "allow"),
        ("reject", "deny"),
    ):
        item = store.add_approval("s1", "Deploy?", inbox="ops")
        assert resolve_from_reply(f"{reply} [ow:{item.id}]", store.resolve) is True
        assert store.get(item.id).resolution == expected


def test_decision_word_after_token_still_resolves(tmp_path):
    """The [ow:…] token may lead the reply (e.g. a quoted redelivery) — intent is parsed
    from the text with the token stripped, wherever it sits."""
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"[ow:{item.id}] approve", store.resolve) is True
    assert store.get(item.id).resolution == "allow"


def test_reply_without_token_is_ignored(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    assert resolve_from_reply("random chatter", store.resolve) is None


def test_inbound_legacy_ocw_token_still_resolves(tmp_path):
    """Replies to messages sent BEFORE the @OpenWorker rename carry [ocw:…] — must keep working."""
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"deny [ocw:{item.id}]", store.resolve) is True
    assert store.get(item.id).resolution == "deny"
