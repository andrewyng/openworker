"""Telegram approval owners (OPE-217 / #712), the follow-up to #567.

#567 made `_actor_owns_protected_item` refuse every Telegram reply because Telegram had no
approval-owner model. These tests pin the model that replaces that blanket refusal:

- the reply must arrive in the chat the item's inbox is bound to;
- with owners configured, the sender must be one of them;
- with no owners and a private-chat binding, the one allowed human in that chat is the
  owner (a Telegram DM's chat id is the user's id), so single-user bots need no setup;
- with no owners and a group binding, nobody may approve until owners are chosen;
- a refused tagged reply gets a one-line answer instead of silence.
"""

from __future__ import annotations

import asyncio

from coworker.connectors.base import InteractionEvent, MessageEvent, SessionSource
from coworker.connectors.gateway import APPROVAL_OWNER_REQUIRED
from coworker.interactions import encode
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager

OWNER = "8894780782"  # bound DM: chat id == user id
OTHER = "8883353200"  # allowed to message, never an owner
GROUP = "-1001234567890"


class NoTurnsProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no model turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


class GatewayStub:
    """Records outbound text so the refusal reply can be asserted."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.rejections: list = []
        self.settings: dict = {}

    async def deliver(self, target, text):
        self.sent.append((target, text))

    async def reject_interaction(self, event, text=""):
        self.rejections.append(getattr(event, "user_id", None))

    async def update_message(self, *args):
        raise AssertionError("a refused interaction must not update the message")


def _manager(tmp_path, *, owners=None, allowed=(OWNER, OTHER), bind_to=OWNER) -> SessionManager:
    manager = SessionManager(data_dir=tmp_path / "data", provider=NoTurnsProvider())
    profile = {"bot_token": "123:abc", "enabled": True, "allowed_users": list(allowed)}
    if owners is not None:
        profile["approval_owner_ids"] = list(owners)
    manager.secrets.put("telegram:default", profile)
    if bind_to is not None:
        manager.inbox_routing.set_binding("default", channel="telegram", target=bind_to)
    return manager


def _reply(text: str, *, user_id: str, chat_id: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="telegram",
            chat_id=chat_id,
            user_id=user_id,
            user_name="someone",
            chat_type="group" if chat_id.startswith("-") else "dm",
        ),
    )


def _approve(manager, item, *, user_id, chat_id) -> bool:
    return manager._resolve_inbox_reply(
        _reply(f"approve [ow:{item.id}]", user_id=user_id, chat_id=chat_id)
    )


# -- no owners configured, bound to the owner's private chat ------------------------------

def test_bound_dm_user_approves_without_configuring_owners(tmp_path):
    manager = _manager(tmp_path)
    item = manager.inbox.add_approval("s1", "Run it?")
    assert _approve(manager, item, user_id=OWNER, chat_id=OWNER) is True
    assert manager.inbox.get(item.id).state == "resolved"
    assert manager.inbox.get(item.id).resolution == "allow"


def test_allowed_non_owner_replying_from_their_own_dm_is_refused(tmp_path):
    # The #519 scenario: allowed to message, replies from a different chat than the bound one.
    manager = _manager(tmp_path)
    item = manager.inbox.add_approval("s1", "Run it?")
    consumed = _approve(manager, item, user_id=OTHER, chat_id=OTHER)
    assert consumed is True  # the tag was recognised, so the message is not routed as a turn
    assert manager.inbox.get(item.id).state == "pending"


def test_owner_replying_from_another_chat_is_refused(tmp_path):
    manager = _manager(tmp_path)
    item = manager.inbox.add_approval("s1", "Run it?")
    assert _approve(manager, item, user_id=OWNER, chat_id=GROUP) is True
    assert manager.inbox.get(item.id).state == "pending"


# -- owners configured -------------------------------------------------------------------

def test_configured_owner_approves_and_allowed_non_owner_is_refused(tmp_path):
    manager = _manager(tmp_path, owners=[OWNER])
    item = manager.inbox.add_approval("s1", "Run it?")
    assert _approve(manager, item, user_id=OTHER, chat_id=OWNER) is True
    assert manager.inbox.get(item.id).state == "pending"
    assert _approve(manager, item, user_id=OWNER, chat_id=OWNER) is True
    assert manager.inbox.get(item.id).state == "resolved"


def test_group_binding_with_owners_lets_only_owners_approve(tmp_path):
    manager = _manager(tmp_path, owners=[OWNER], bind_to=GROUP)
    item = manager.inbox.add_approval("s1", "Run it?")
    assert _approve(manager, item, user_id=OTHER, chat_id=GROUP) is True
    assert manager.inbox.get(item.id).state == "pending"
    assert _approve(manager, item, user_id=OWNER, chat_id=GROUP) is True
    assert manager.inbox.get(item.id).state == "resolved"


def test_group_binding_without_owners_refuses_everyone(tmp_path):
    manager = _manager(tmp_path, bind_to=GROUP)
    item = manager.inbox.add_approval("s1", "Run it?")
    for user in (OWNER, OTHER):
        assert _approve(manager, item, user_id=user, chat_id=GROUP) is True
        assert manager.inbox.get(item.id).state == "pending"


def test_in_app_only_inbox_is_not_resolvable_from_telegram(tmp_path):
    manager = _manager(tmp_path, bind_to=None)
    item = manager.inbox.add_approval("s1", "Run it?")
    assert _approve(manager, item, user_id=OWNER, chat_id=OWNER) is True
    assert manager.inbox.get(item.id).state == "pending"


def test_questions_stay_answerable_by_any_allowed_member(tmp_path):
    manager = _manager(tmp_path)
    question = manager.inbox.add_question("s1", "Which region?", options=["A", "B"])
    assert manager._resolve_inbox_reply(
        _reply(f"A [ow:{question.id}]", user_id=OTHER, chat_id=OTHER)
    ) is True
    assert manager.inbox.get(question.id).resolution == "A"


# -- owner list editing -------------------------------------------------------------------

def test_adding_an_owner_implies_allowed_and_removal_keeps_allowed(tmp_path):
    manager = _manager(tmp_path, allowed=())
    assert manager.set_telegram_approval_owner("42", add=True, display_name="Rohit")["ok"]
    profile = manager.secrets.get("telegram:default")
    assert profile["approval_owner_ids"] == ["42"]
    assert profile["allowed_users"] == ["42"]
    assert manager.set_telegram_approval_owner("42", add=False)["ok"]
    profile = manager.secrets.get("telegram:default")
    assert profile["approval_owner_ids"] == []
    assert profile["allowed_users"] == ["42"]


def test_owner_edit_requires_a_connected_telegram(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=NoTurnsProvider())
    result = manager.set_telegram_approval_owner("42", add=True)
    assert not result["ok"]


# -- refusal feedback ---------------------------------------------------------------------

def test_refused_text_reply_gets_a_one_line_answer(tmp_path):
    manager = _manager(tmp_path)
    gateway = GatewayStub()
    manager.gateway = gateway
    item = manager.inbox.add_approval("s1", "Run it?")

    async def run():
        # The resolver is called synchronously from inside the gateway's loop in production.
        assert _approve(manager, item, user_id=OTHER, chat_id=OTHER) is True
        await asyncio.sleep(0)  # let the scheduled send run

    asyncio.run(run())
    assert manager.inbox.get(item.id).state == "pending"
    assert gateway.sent == [(f"telegram:{OTHER}", APPROVAL_OWNER_REQUIRED)]


def test_accepted_reply_sends_no_refusal(tmp_path):
    manager = _manager(tmp_path)
    gateway = GatewayStub()
    manager.gateway = gateway
    item = manager.inbox.add_approval("s1", "Run it?")

    async def run():
        assert _approve(manager, item, user_id=OWNER, chat_id=OWNER) is True
        await asyncio.sleep(0)

    asyncio.run(run())
    assert manager.inbox.get(item.id).state == "resolved"
    assert gateway.sent == []


def test_refused_button_click_on_telegram_gets_a_plain_reply(tmp_path):
    manager = _manager(tmp_path)
    gateway = GatewayStub()
    manager.gateway = gateway
    item = manager.inbox.add_approval("s1", "Run it?")
    asyncio.run(
        manager._on_interaction(
            InteractionEvent(
                platform="telegram",
                chat_id=OTHER,
                message_id="1",
                value=encode(item.id, "allow"),
                user_id=OTHER,
                user_name="someone",
            )
        )
    )
    assert manager.inbox.get(item.id).state == "pending"
    assert gateway.sent == [(f"telegram:{OTHER}", APPROVAL_OWNER_REQUIRED)]
    assert gateway.rejections == [OTHER]  # the generic reject path still runs (no-op off Slack)
