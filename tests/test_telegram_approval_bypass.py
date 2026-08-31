"""Approval ownership must hold on EVERY transport, not just the Slack lane.

The owner check for protected items (approval/directory/plan) used to run only when
platform == "slack". A Telegram reply or button therefore resolved protected items with no
owner or channel-binding validation, and items mirrored to Slack were resolvable from
Telegram. These tests pin the cross-transport enforcement. Questions stay answerable by any
allow-listed member.
"""

from __future__ import annotations

import asyncio

from coworker.connectors.base import InteractionEvent, MessageEvent, SessionSource
from coworker.interactions import encode
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager


class NoTurnsProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no model turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path) -> SessionManager:
    return SessionManager(data_dir=tmp_path / "data", provider=NoTurnsProvider())


def _tg_reply(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="telegram", chat_id="99", user_id="tg-user", user_name="TG"
        ),
    )


def test_telegram_reply_cannot_resolve_a_protected_approval(tmp_path):
    manager = _manager(tmp_path)
    approval = manager.inbox.add_approval("s1", "Run it?")
    consumed = manager._resolve_inbox_reply(_tg_reply(f"approve [ow:{approval.id}]"))
    # The token was recognized (consumed), but the protected item must NOT be resolved.
    assert consumed is True
    assert manager.inbox.get(approval.id).state == "pending"


def test_telegram_button_cannot_resolve_a_protected_approval(tmp_path):
    manager = _manager(tmp_path)

    class GatewayStub:
        def __init__(self):
            self.rejections = []

        async def reject_interaction(self, event, text=""):
            self.rejections.append(getattr(event, "user_id", None))

        async def update_message(self, *args):
            raise AssertionError("a refused interaction must not update the message")

    gateway = GatewayStub()
    manager.gateway = gateway
    approval = manager.inbox.add_approval("s1", "Run it?")

    asyncio.run(
        manager._on_interaction(
            InteractionEvent(
                platform="telegram",
                chat_id="99",
                message_id="1",
                value=encode(approval.id, "allow"),
                user_id="tg-user",
                user_name="TG",
            )
        )
    )
    assert manager.inbox.get(approval.id).state == "pending"
    assert gateway.rejections == ["tg-user"]


def test_telegram_reply_can_still_answer_a_question(tmp_path):
    # Questions are not protected — an allow-listed member may answer from any transport.
    manager = _manager(tmp_path)
    question = manager.inbox.add_question("s1", "Which region?", options=["A", "B"])
    consumed = manager._resolve_inbox_reply(_tg_reply(f"A [ow:{question.id}]"))
    assert consumed is True
    assert manager.inbox.get(question.id).resolution == "A"


def test_slack_bound_item_is_not_resolvable_from_telegram(tmp_path):
    # Cross-transport: an item whose inbox is bound to Slack must not be resolved by a
    # Telegram reply, even though Slack's own owner check would apply on the Slack lane.
    manager = _manager(tmp_path)
    manager.inbox_routing.set_binding("ops", channel="slack", target="T1:C1")
    approval = manager.inbox.add_approval("s1", "Deploy?", inbox="ops")
    consumed = manager._resolve_inbox_reply(_tg_reply(f"approve [ow:{approval.id}]"))
    assert consumed is True
    assert manager.inbox.get(approval.id).state == "pending"
