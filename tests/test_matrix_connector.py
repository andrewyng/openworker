"""Matrix connector unit tests — mapper, settings, reactions (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from coworker.connectors.matrix_adapter import matrix_event_to_event
from coworker.connectors.matrix_reactions import (
    APPROVAL_EMOJI,
    PendingReactionStore,
    reactions_for,
    reactions_for_buttons,
)
from coworker.connectors.matrix_settings import MatrixSettings
from coworker.interactions import Button, decode, encode
from coworker.inbox import InboxStore


def test_matrix_settings_defaults():
    s = MatrixSettings.from_profile(
        {
            "homeserver_url": "https://matrix.example.org",
            "access_token": "tok",
        }
    )
    assert s.require_mention is True
    assert s.e2ee_mode == "required"
    assert s.approval_require_sender is True
    assert s.max_media_bytes == 104_857_600


def test_reactions_for_buttons_includes_always():
    item_id = "abc123"
    buttons = [
        Button("Approve", encode(item_id, "allow")),
        Button("Deny", encode(item_id, "deny")),
    ]
    emap = reactions_for_buttons(buttons)
    assert "✅" in emap and "❌" in emap and "♾️" in emap
    assert decode(emap["♾️"]) == (item_id, "always")


def test_matrix_room_is_dm():
    from coworker.connectors.matrix_adapter import _matrix_room_is_dm

    dm = SimpleNamespace(member_count=2, is_group=True, joined_count=2)
    assert _matrix_room_is_dm(dm) is True
    channel = SimpleNamespace(member_count=10, is_group=False, joined_count=10)
    assert _matrix_room_is_dm(channel) is False
    unnamed = SimpleNamespace(member_count=0, is_group=True, joined_count=2)
    assert _matrix_room_is_dm(unnamed) is True


def test_matrix_adapter_should_dispatch_mention():
    from coworker.connectors.matrix_adapter import MatrixAdapter
    from coworker.connectors.base import MessageEvent, SessionSource

    adapter = MatrixAdapter(
        MatrixSettings.from_profile(
            {"homeserver_url": "https://h", "access_token": "t"}
        ),
        store_path=__import__("pathlib").Path("/tmp/matrix-test"),
    )
    src = SessionSource(platform="matrix", chat_id="!r:ex", chat_type="channel")
    ev = MessageEvent(text="hi", source=src, mentions_me=False)
    assert adapter._should_dispatch(ev, "!r:ex", is_dm=False) is False
    ev.mentions_me = True
    assert adapter._should_dispatch(ev, "!r:ex", is_dm=False) is True
    event = SimpleNamespace(
        sender="@alice:example.org",
        body="hello @bot:example.org",
        event_id="$e1",
    )
    mapped = matrix_event_to_event(
        event,
        room_id="!r:example.org",
        bot_user_id="@bot:example.org",
        chat_type="channel",
    )
    assert mapped is not None
    assert mapped.mentions_me is True
    assert mapped.source.platform == "matrix"
    assert mapped.source.target.startswith("matrix/")


def test_matrix_event_mapper_skips_bot():
    event = SimpleNamespace(
        sender="@bot:example.org",
        body="echo",
        event_id="$e2",
    )
    assert (
        matrix_event_to_event(
            event, room_id="!r:example.org", bot_user_id="@bot:example.org"
        )
        is None
    )


def test_reaction_store_resolve():
    store = PendingReactionStore()
    from coworker.connectors.matrix_reactions import PendingReaction

    val = encode("item1", "allow")
    store.register(
        PendingReaction(
            room_id="!r:ex",
            prompt_event_id="$prompt",
            emoji_map={"✅": val},
            allowed_reactor="@alice:ex",
        )
    )
    got = store.resolve_emoji("!r:ex", "$prompt", "✅")
    assert got is not None
    assert got[0] == val
    assert store.resolve_emoji("!r:ex", "$prompt", "❌") is None


def test_reactions_for_approval(tmp_path):
    st = InboxStore(tmp_path / "inbox.json")
    item = st.add_approval("s1", "Run tool?")
    emojis = reactions_for(item)
    assert "✅" in emojis
    assert decode(emojis["✅"]) == (item.id, "allow")
    assert decode(emojis["♾️"]) == (item.id, "always")
    assert APPROVAL_EMOJI["❌"] == "deny"


def test_reactions_for_buttons():
    btns = [
        Button("Approve", encode("x", "allow")),
        Button("Deny", encode("x", "deny")),
    ]
    em = reactions_for_buttons(btns)
    assert em["✅"] == btns[0].value
    assert em["❌"] == btns[1].value


def test_matrix_adapter_enables_sync_token_store(monkeypatch):
    """Restart must not replay history — nio needs store_sync_tokens=True."""
    from pathlib import Path

    from coworker.connectors.matrix_adapter import MatrixAdapter

    captured: dict = {}

    class FakeClient:
        user_id = "@bot:ex"
        device_id = "DEV"
        access_token = None

        async def whoami(self):
            return SimpleNamespace(user_id=self.user_id, device_id=self.device_id)

        def load_store(self):
            return None

        def add_event_callback(self, *_args, **_kwargs):
            return None

        async def close(self):
            return None

    def fake_async_client(*_args, **kwargs):
        captured["config"] = kwargs.get("config")
        return FakeClient()

    monkeypatch.setitem(
        __import__("sys").modules,
        "nio",
        SimpleNamespace(
            AsyncClient=fake_async_client,
            AsyncClientConfig=__import__("nio").AsyncClientConfig,
            InviteMemberEvent=object,
            RoomMessageText=object,
        ),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "nio.events",
        SimpleNamespace(RoomMessage=object),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "nio.events.room_events",
        SimpleNamespace(
            ReactionEvent=object,
            RoomMessageAudio=object,
            RoomMessageFile=object,
            RoomMessageImage=object,
            RoomMessageVideo=object,
        ),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "nio.crypto",
        SimpleNamespace(),
    )
    from coworker.connectors.matrix_crypto_bootstrap import prepare_matrix_e2ee

    async def noop_prepare(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "coworker.connectors.matrix_adapter.prepare_matrix_e2ee",
        noop_prepare,
        raising=False,
    )
    monkeypatch.setattr(
        "coworker.connectors.matrix_crypto_bootstrap.prepare_matrix_e2ee",
        noop_prepare,
    )

    adapter = MatrixAdapter(
        MatrixSettings.from_profile(
            {
                "homeserver_url": "https://h",
                "access_token": "t",
                "e2ee_mode": "off",
            }
        ),
        store_path=Path("/tmp/matrix-sync-token-test"),
    )

    import asyncio

    assert asyncio.run(adapter.connect()) is True
    assert captured["config"] is not None
    assert captured["config"].store_sync_tokens is True
