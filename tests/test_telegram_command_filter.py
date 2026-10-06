"""Slash commands must reach the handler.

The adapter registered `filters.TEXT & ~filters.COMMAND`, which discards every
message beginning with "/" — and no CommandHandler was registered either, so a bot
with a documented command surface silently dropped every command sent to it. Plain
text arrived normally, which is what made it look like the commands were broken
rather than never delivered.

The distinction is Telegram's `bot_command` entity, not the leading slash: a message
typed as a command carries that entity, and that is what `filters.COMMAND` matches.
"""

from __future__ import annotations

import datetime

import pytest

telegram = pytest.importorskip("telegram")
from telegram import Chat, Message, MessageEntity, Update, User  # noqa: E402
from telegram.ext import filters  # noqa: E402

from coworker.connectors.adapters import telegram_message_to_event  # noqa: E402


def _update(text: str, *, as_command: bool) -> Update:
    entities = (
        [MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(text.split()[0]))]
        if as_command
        else []
    )
    message = Message(
        message_id=1,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=Chat(id=42, type=Chat.PRIVATE),
        from_user=User(id=7, first_name="D", is_bot=False),
        text=text,
        entities=entities,
    )
    return Update(update_id=1, message=message)


@pytest.mark.parametrize("text", ["/help", "/pending", "/status@some_bot"])
def test_commands_reach_the_handler(text):
    assert filters.TEXT.check_update(_update(text, as_command=True))
    # What the adapter used to register, and why the commands vanished.
    assert not (filters.TEXT & ~filters.COMMAND).check_update(_update(text, as_command=True))


def test_plain_text_still_reaches_the_handler():
    assert filters.TEXT.check_update(_update("approve [ow:abc123]", as_command=False))


def test_a_command_becomes_an_event_like_any_other_message():
    event = telegram_message_to_event(_update("/pending", as_command=True).effective_message)
    assert event is not None and event.text == "/pending"
