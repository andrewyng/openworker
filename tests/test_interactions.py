"""Interactive prompts over messaging: button encoding, block rendering, and the click→resolve path."""

import asyncio
import json

from coworker.inbox import InboxStore
from coworker.interactions import (
    QUESTION_CANCELLED,
    QUESTION_INTERRUPTED,
    Button,
    buttons_for,
    decode,
    encode,
    question_answer,
)
from coworker.connectors.base import InteractionEvent
from coworker.connectors.senders import _slack_blocks
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def test_encode_decode_roundtrip():
    v = encode("abc123", "allow")
    assert decode(v) == ("abc123", "allow")
    assert decode("not json") is None
    assert decode(json.dumps({"nope": 1})) is None


def test_buttons_for_kinds(tmp_path):
    st = InboxStore(tmp_path / "inbox.json")
    appr = st.add_approval("s1", "Run `write_file`?")
    btns = buttons_for(appr)
    assert [b.label for b in btns] == ["Approve", "Deny"]
    assert decode(btns[0].value) == (appr.id, "allow")
    assert decode(btns[1].value) == (appr.id, "deny")

    q = st.add_question("s1", "Which region?", options=["us-east-1", "us-west-2"])
    qb = buttons_for(q)
    assert [b.label for b in qb] == ["us-east-1", "us-west-2", "Cancel"]
    assert decode(qb[0].value) == (q.id, "us-east-1")  # resolution IS the option text
    assert decode(qb[-1].value) == (q.id, QUESTION_CANCELLED)

    # free-text question (no options) still gets Cancel — notifications get none
    free = buttons_for(st.add_question("s1", "Say something"))
    assert [b.label for b in free] == ["Cancel"]
    assert buttons_for(st.add_notification("s1", "FYI")) == []


def test_question_answer_cancel_maps_to_error():
    assert question_answer("us-west-2") == {"answer": "us-west-2"}
    assert question_answer("") == {"answer": ""}
    assert question_answer(QUESTION_CANCELLED) == {
        "answer": "",
        "error": "cancelled by user",
    }
    assert question_answer(QUESTION_INTERRUPTED) == {
        "answer": "",
        "error": "interrupted by user",
    }


async def test_interrupt_closes_parked_question(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    ask = mgr.inbox_question_asker("s1", "cowork")
    waiter = asyncio.create_task(
        ask({"question": "Which region?", "options": ["us-east-1"]}, "call_q")
    )
    await asyncio.sleep(0)
    item = mgr.inbox.pending("s1")[0]

    waiter.cancel()
    try:
        await waiter
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("the parked question waiter should be cancelled")

    assert mgr.inbox.pending("s1") == []
    assert mgr.inbox.get(item.id).resolution == QUESTION_INTERRUPTED


async def test_free_text_question_mirror_keeps_open_app_hint(tmp_path):
    from coworker.server.manager import SessionManager

    mgr = SessionManager(workspace=tmp_path)
    delivered = {}

    class Gateway:
        async def deliver_interactive(self, target, text, buttons):
            delivered.update(target=target, text=text, buttons=buttons)

    mgr.gateway = Gateway()
    mgr.inbox_routing.set_binding(
        "default", channel="fake", target="questions"
    )
    item = mgr.inbox.add_question("s1", "What should I call it?")

    await mgr.mirror_inbox_item(item)

    assert "Open the app to answer, or cancel here." in delivered["text"]
    assert [button.label for button in delivered["buttons"]] == ["Cancel"]


def test_slack_blocks_shape():
    blocks = _slack_blocks("Run `x`?", [Button("Approve", "v1"), Button("Deny", "v2")])
    assert blocks[0]["type"] == "section"
    els = blocks[1]["elements"]
    assert [e["text"]["text"] for e in els] == ["Approve", "Deny"]
    assert [e["value"] for e in els] == ["v1", "v2"]
    assert [e["action_id"] for e in els] == ["ocw_0", "ocw_1"]
    # no buttons → just the section, no actions block
    assert len(_slack_blocks("hi", [])) == 1


def test_interaction_click_resolves_item(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    mgr.secrets.put(
        "slack:default",
        {
            "bot_token": "xoxb-test",
            "app_token": "xapp-test",
            "allowed_users": ["U_BOB"],
            "approval_owner_ids": ["U_BOB"],
        },
    )
    item = mgr.inbox.add_approval("sX", "Run `write_file`?")

    resolved: list = []

    async def fake_wait(item_id):
        # stand in for the suspended agent: record what the item resolved to
        ev = mgr.inbox._waiters.setdefault(item_id, asyncio.Event())
        await ev.wait()
        resolved.append(mgr.inbox.get(item_id).resolution)

    async def scenario():
        waiter = asyncio.create_task(fake_wait(item.id))
        await asyncio.sleep(0)  # let the waiter register
        await mgr._on_interaction(
            InteractionEvent(
                platform="slack",
                chat_id="C1",
                message_id="111.2",
                value=encode(item.id, "allow"),
                user_id="U_BOB",
                user_name="bob",
            )
        )
        await asyncio.wait_for(waiter, timeout=2)

    asyncio.run(scenario())
    assert resolved == ["allow"]
    assert mgr.inbox.get(item.id).state == "resolved"
