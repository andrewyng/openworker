"""Tests for the DingTalk (钉钉) connector.

Covers the protocol pieces that are specific to DingTalk:
- the webhook HMAC signature (`_sign`)
- inbound callback payload parsing (`webhook_payload_to_event`)
- inbound ChatbotMessage parsing (stream mode)
- the outbound sender wrapper (`_send_dingtalk`)
- end-to-end `send_message` tool wiring (token JSON encoding, including stream
  session webhooks)
- adapter + descriptor registration parity with the other platforms
- Stream-mode connection lifecycle and message routing
All network calls are stubbed, so these run fully offline.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import types
from typing import Any

import pytest

from coworker.connectors import DingTalkAdapter, send_dingtalk, webhook_payload_to_event
from coworker.connectors.dingtalk import _chatbot_message_to_event
from coworker.connectors.adapters import make_adapter
from coworker.connectors.base import MessageEvent, SendResult
from coworker.connectors.descriptors import get_descriptor
from coworker.connectors.senders import _send_dingtalk
from coworker.secrets import SecretStore


# -- signing -------------------------------------------------------------------
def test_sign_is_deterministic_hmac_sha256():
    secret = "shhh"
    timestamp = "1699999999000"
    expected = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}\n{secret}".encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    # import the private fn via the module to avoid leaking it into the public API
    from coworker.connectors.dingtalk import _sign

    assert _sign(secret, timestamp) == expected
    assert _sign(secret, timestamp) == _sign(secret, timestamp)


# -- inbound payload parsing ---------------------------------------------------
def test_webhook_group_chat_strips_bot_mention():
    payload = {
        "msgtype": "text",
        "text": {"content": "@OpenWorker what is the status"},
        "conversationId": "cid-123",
        "senderStaffId": "staff-9",
        "senderNick": "Alice",
        "atUsers": [{"name": "OpenWorker"}],
    }
    ev = webhook_payload_to_event(payload)
    assert ev is not None
    assert ev.text == "what is the status"
    assert ev.source.platform == "dingtalk"
    assert ev.source.chat_id == "cid-123"
    assert ev.source.user_id == "staff-9"
    assert ev.source.user_name == "Alice"
    assert ev.source.chat_type == "group"


def test_webhook_session_push_treated_as_dm():
    payload = {
        "msgtype": "text",
        "text": {"content": "hello bot"},
        "senderNick": "Bob",
    }
    ev = webhook_payload_to_event(payload)
    assert ev is not None
    assert ev.text == "hello bot"
    assert ev.source.chat_type == "dm"


def test_webhook_non_text_is_ignored():
    assert webhook_payload_to_event({"msgtype": "picture", "text": {"content": "x"}}) is None


def test_webhook_empty_content_is_ignored():
    assert webhook_payload_to_event({"msgtype": "text", "text": {"content": "  "}}) is None


def test_webhook_only_mention_is_ignored():
    payload = {
        "msgtype": "text",
        "text": {"content": "@OpenWorker "},
        "conversationId": "cid-1",
        "atUsers": [{"name": "OpenWorker"}],
    }
    assert webhook_payload_to_event(payload) is None


# -- outbound sender -----------------------------------------------------------
def test_send_dingtalk_sender_parses_json_token(monkeypatch):
    captured = {}

    def fake_send(webhook_url, text, secret=None, msgtype="text"):
        captured["webhook_url"] = webhook_url
        captured["text"] = text
        captured["secret"] = secret
        return SendResult(True, message_id="m1")

    monkeypatch.setattr("coworker.connectors.senders.send_dingtalk", fake_send)
    token = json.dumps({"webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=ABC", "secret": "S"})
    result = _send_dingtalk(token, "cid-1", "hello")
    assert result.ok and result.message_id == "m1"
    assert captured["webhook_url"] == "https://oapi.dingtalk.com/robot/send?access_token=ABC"
    assert captured["secret"] == "S"
    assert captured["text"] == "hello"


def test_send_dingtalk_sender_rejects_bad_token():
    result = _send_dingtalk("not json", "cid-1", "hi")
    assert not result.ok and "invalid dingtalk credentials" in (result.error or "")


# -- httpx URL handling --------------------------------------------------------
def test_send_dingtalk_preserves_access_token_and_adds_sign(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeResponse:
        def json(self):
            return {"errcode": 0, "errmsg": "ok", "msg_id": "m42"}

    def fake_post(url, *, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    result = send_dingtalk(
        "https://oapi.dingtalk.com/robot/send?access_token=ABC",
        "hello",
        secret="shhh",
    )
    assert result.ok and result.message_id == "m42"
    assert captured["url"] == "https://oapi.dingtalk.com/robot/send"
    assert captured["params"]["access_token"] == "ABC"
    assert captured["params"]["timestamp"]
    assert captured["params"]["sign"]
    assert captured["json"]["msgtype"] == "text"


def test_send_dingtalk_without_secret_keeps_access_token(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeResponse:
        def json(self):
            return {"errcode": 0, "errmsg": "ok"}

    def fake_post(url, *, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    send_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=ABC", "hello")
    assert captured["url"] == "https://oapi.dingtalk.com/robot/send"
    assert captured["params"] == {"access_token": "ABC"}


def test_send_dingtalk_falls_back_to_markdown_on_300001(monkeypatch):
    calls: list[dict[str, Any]] = []

    class TextReject:
        def json(self):
            return {"errcode": 300001, "errmsg": "robot type do not match with the message"}

    class MarkdownOk:
        def json(self):
            return {"errcode": 0, "errmsg": "ok", "msg_id": "m-md"}

    def fake_post(url, *, params=None, json=None, timeout=None):
        calls.append({"url": url, "params": params, "json": json})
        if json.get("msgtype") == "text":
            return TextReject()
        return MarkdownOk()

    monkeypatch.setattr("httpx.post", fake_post)
    result = send_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=ABC", "hello")
    assert result.ok and result.message_id == "m-md"
    assert len(calls) == 2
    assert calls[0]["json"]["msgtype"] == "text"
    assert calls[1]["json"]["msgtype"] == "markdown"
    assert calls[1]["json"]["markdown"]["text"] == "hello"


# -- end-to-end tool wiring ----------------------------------------------------
def _fake_senders(record):
    def sender(token, chat_id, text, thread_id=None):
        record.append(
            {"token": token, "chat_id": chat_id, "text": text, "thread_id": thread_id}
        )
        return SendResult(True, message_id="99")

    return {"dingtalk": sender}


def test_send_message_tool_dingtalk(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "dingtalk:default",
        {"webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=ABC", "secret": "S"},
    )
    record = []
    from coworker.connectors.tools import make_send_message_tool

    tool = make_send_message_tool(secrets, senders=_fake_senders(record))
    out = tool(target="dingtalk:cid-123", text="ping")
    assert out == {"ok": True, "message_id": "99", "target": "dingtalk:cid-123"}
    assert len(record) == 1
    # the sender received the encoded JSON token, not the raw webhook string
    decoded = json.loads(record[0]["token"])
    assert decoded["webhook_url"].endswith("access_token=ABC")
    assert decoded["secret"] == "S"
    assert record[0]["chat_id"] == "cid-123"


def test_send_message_tool_dingtalk_stream_session_webhook(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "dingtalk:default",
        {
            "client_id": "dingcid",
            "client_secret": "secret",
            "session_webhooks": {"cid-123": "https://oapi.dingtalk.com/robot/send?access_token=SESSION"},
        },
    )
    record = []
    from coworker.connectors.tools import make_send_message_tool

    tool = make_send_message_tool(secrets, senders=_fake_senders(record))
    out = tool(target="dingtalk:cid-123", text="stream reply")
    assert out == {"ok": True, "message_id": "99", "target": "dingtalk:cid-123"}
    decoded = json.loads(record[0]["token"])
    assert decoded["webhook_url"].endswith("access_token=SESSION")
    assert "secret" not in decoded


def test_send_message_tool_dingtalk_missing_token(tmp_path):
    from coworker.connectors.tools import make_send_message_tool

    tool = make_send_message_tool(
        SecretStore(tmp_path / "secrets.json"), senders=_fake_senders([])
    )
    assert "error" in tool(target="dingtalk:cid-1", text="x")


# -- registration parity -------------------------------------------------------
def test_make_adapter_returns_dingtalk_adapter():
    adapter = make_adapter(
        "dingtalk",
        {"webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=ABC", "secret": "S"},
    )
    assert isinstance(adapter, DingTalkAdapter)
    assert adapter.webhook_url.endswith("access_token=ABC")
    assert adapter.secret == "S"


def test_make_adapter_stream_mode():
    adapter = make_adapter(
        "dingtalk",
        {"client_id": "dingcid", "client_secret": "shhh"},
    )
    assert isinstance(adapter, DingTalkAdapter)
    assert adapter.mode == "stream"
    assert adapter.client_id == "dingcid"
    assert adapter.client_secret == "shhh"


def test_make_adapter_prefers_stream_when_both_present():
    adapter = make_adapter(
        "dingtalk",
        {
            "client_id": "dingcid",
            "client_secret": "shhh",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=ABC",
        },
    )
    assert isinstance(adapter, DingTalkAdapter)
    assert adapter.mode == "stream"


def test_make_adapter_skips_without_credentials():
    assert make_adapter("dingtalk", {"secret": "S"}) is None


def test_descriptor_present_and_well_formed():
    d = get_descriptor("dingtalk")
    assert d is not None
    assert d.title == "DingTalk"
    assert d.auth == "webhook"
    assert d.two_way is True
    field_names = {f.key for f in d.fields}
    assert "webhook_url" in field_names and "secret" in field_names
    assert "client_id" in field_names and "client_secret" in field_names
    assert d.logo == "dingtalk"
    assert d.brand_color == "#3370ff"
    assert callable(d.validate)


# -- stream-mode lifecycle -----------------------------------------------------
def _build_fake_dingtalk_stream_module():
    """Return a minimal fake dingtalk_stream module for offline tests."""
    mod = types.ModuleType("dingtalk_stream")
    mod_chatbot = types.ModuleType("chatbot")

    class AckMessage:
        STATUS_OK = "OK"

    class FakeCredential:
        def __init__(self, client_id: str, client_secret: str):
            self.client_id = client_id
            self.client_secret = client_secret

    class FakeCallbackMessage:
        TYPE = "CALLBACK"

        def __init__(self, data: dict[str, Any]):
            self.data = data

    class FakeChatbotMessage:
        TOPIC = "/v1.0/im/bot/messages/get"

        def __init__(self, **kwargs: Any):
            self.text = kwargs.get("text")
            self.sender_staff_id = kwargs.get("sender_staff_id")
            self.sender_user_id = kwargs.get("sender_user_id")
            self.sender_nick = kwargs.get("sender_nick")
            self.conversation_id = kwargs.get("conversation_id")
            self.session_webhook = kwargs.get("session_webhook")
            self.at_users = kwargs.get("at_users", [])
            self.is_in_at_list = kwargs.get("is_in_at_list", False)
            self.chatbot_user_id = kwargs.get("chatbot_user_id")
            self.message_id = kwargs.get("message_id")
            self._raw = kwargs

        @classmethod
        def from_dict(cls, data: dict[str, Any]) -> "FakeChatbotMessage":
            return cls(
                text=TextContent(data.get("text", {}).get("content", "")),
                sender_staff_id=data.get("senderStaffId"),
                sender_user_id=data.get("senderUserId"),
                sender_nick=data.get("senderNick"),
                conversation_id=data.get("conversationId"),
                session_webhook=data.get("sessionWebhook"),
                is_in_at_list=bool(data.get("isInAtList")),
                chatbot_user_id=data.get("chatbotUserId"),
                message_id=data.get("messageId"),
                at_users=[
                    AtUser(
                        u.get("name", ""),
                        dingtalk_id=u.get("dingtalkId", ""),
                        staff_id=u.get("staffId", ""),
                    )
                    for u in data.get("atUsers", [])
                ],
            )

        def to_dict(self) -> dict[str, Any]:
            return self._raw

    class TextContent:
        def __init__(self, content: str):
            self.content = content

    class AtUser:
        def __init__(self, name: str, dingtalk_id: str = "", staff_id: str = ""):
            self.name = name
            self.dingtalk_id = dingtalk_id
            self.staff_id = staff_id

    class FakeChatbotHandler:
        async def process(self, callback: Any):
            raise NotImplementedError

    class FakeEventHandler:
        async def process(self, event: Any):
            raise NotImplementedError

    class FakeHeaders:
        def __init__(self, topic: str = None, event_type: str = None, event_id: str = None):
            self.topic = topic
            self.event_type = event_type
            self.event_id = event_id

    class FakeEventMessage:
        def __init__(self, headers: FakeHeaders = None, data: dict[str, Any] = None):
            self.headers = headers or FakeHeaders()
            self.data = data or {}

    class FakeClient:
        def __init__(self, credential: FakeCredential):
            self.credential = credential
            self.handlers: dict[str, Any] = {}
            self.callback_handler_map: dict[str, Any] = {}
            self.event_handler: Any = None
            self._stop_event = asyncio.Event()
            self._is_event_required = False

        def register_callback_handler(self, topic: str, handler: Any) -> None:
            self.handlers[topic] = handler
            self.callback_handler_map[topic] = handler

        def register_all_event_handler(self, handler: Any) -> None:
            self.event_handler = handler
            self._is_event_required = True

        async def start(self) -> None:
            await self._stop_event.wait()

        async def stop(self) -> None:
            self._stop_event.set()

    mod.AckMessage = AckMessage
    mod.ChatbotHandler = FakeChatbotHandler
    mod.EventHandler = FakeEventHandler
    mod.EventMessage = FakeEventMessage
    mod.Credential = FakeCredential
    mod.CallbackMessage = FakeCallbackMessage
    mod.ChatbotMessage = FakeChatbotMessage
    mod.DingTalkStreamClient = FakeClient
    mod.chatbot = mod_chatbot
    mod_chatbot.ChatbotMessage = FakeChatbotMessage
    mod_chatbot.ChatbotHandler = FakeChatbotHandler
    return mod


@pytest.fixture
def fake_dingtalk_stream(monkeypatch):
    mod = _build_fake_dingtalk_stream_module()
    monkeypatch.setitem(sys.modules, "dingtalk_stream", mod)
    return mod


@pytest.mark.asyncio
async def test_connect_stream_starts_client(fake_dingtalk_stream):
    adapter = DingTalkAdapter(client_id="cid", client_secret="secret")
    assert adapter.mode == "stream"

    ok = await adapter.connect()
    assert ok is True
    assert adapter._client is not None
    assert adapter._task is not None
    assert fake_dingtalk_stream.ChatbotMessage.TOPIC in adapter._client.handlers

    await adapter.disconnect()
    assert adapter._task is None or adapter._task.done()


@pytest.mark.asyncio
async def test_stream_handler_routes_message_and_saves_session_webhook(
    fake_dingtalk_stream, tmp_path
):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("dingtalk:default", {"client_id": "cid", "client_secret": "secret"})

    adapter = DingTalkAdapter(client_id="cid", client_secret="secret", secrets=secrets)
    await adapter.connect()

    received: list[MessageEvent] = []

    async def capture(ev: MessageEvent) -> None:
        received.append(ev)

    adapter.set_message_handler(capture)

    handler = adapter._client.handlers[fake_dingtalk_stream.ChatbotMessage.TOPIC]
    callback = fake_dingtalk_stream.CallbackMessage(
        {
            "senderStaffId": "staff-42",
            "senderNick": "Alice",
            "conversationId": "cid-99",
            "sessionWebhook": "https://oapi.dingtalk.com/robot/send?access_token=SESS",
            "msgtype": "text",
            "text": {"content": "hello stream"},
            "atUsers": [],
        }
    )
    status, _ = await handler.process(callback)
    assert status == "OK"

    # dispatch is thread-safe fire-and-forget onto the running loop; yield so it runs.
    await asyncio.sleep(0.01)

    assert len(received) == 1
    ev = received[0]
    assert ev.text == "hello stream"
    assert ev.source.chat_id == "cid-99"
    assert ev.source.user_id == "staff-42"
    assert adapter._session_webhooks.get("cid-99") == "https://oapi.dingtalk.com/robot/send?access_token=SESS"

    # The webhook should also be persisted to the profile so the stateless
    # send_message tool can reply later.
    profile = secrets.get("dingtalk:default")
    assert profile is not None
    assert profile.get("session_webhooks", {}).get("cid-99") == "https://oapi.dingtalk.com/robot/send?access_token=SESS"

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_send_stream_uses_session_webhook(monkeypatch, fake_dingtalk_stream):
    captured: dict[str, Any] = {}

    class FakeResponse:
        def json(self):
            return {"errcode": 0, "errmsg": "ok", "msg_id": "m-stream"}

    def fake_post(url, *, params=None, json=None, timeout=None):
        captured.update({"url": url, "params": params, "json": json})
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    adapter = DingTalkAdapter(client_id="cid", client_secret="secret")
    adapter._session_webhooks["cid-99"] = "https://oapi.dingtalk.com/robot/send?access_token=SESS"
    result = await adapter.send("cid-99", "stream reply")
    assert result.ok and result.message_id == "m-stream"
    assert captured["params"]["access_token"] == "SESS"


@pytest.mark.asyncio
async def test_send_stream_without_session_webhook_fails():
    adapter = DingTalkAdapter(client_id="cid", client_secret="secret")
    result = await adapter.send("cid-99", "stream reply")
    assert not result.ok
    assert "no session webhook" in (result.error or "").lower()


# -- inbound mention routing trigger -----------------------------------------
def _make_chatbot_msg(text: str, **kwargs: Any):
    """Build a minimal ChatbotMessage-like object for `_chatbot_message_to_event`."""

    class _Msg:
        def __init__(self, **k: Any):
            self.__dict__.update(k)

        def to_dict(self) -> dict[str, Any]:
            return {}

    at_users = kwargs.pop("at_users", [])
    return _Msg(
        text=types.SimpleNamespace(content=text),
        at_users=at_users,
        **kwargs,
    )


def test_chatbot_message_to_event_mentions_me_when_in_at_list():
    msg = _make_chatbot_msg(
        text="@OpenWorker what is the status",
        is_in_at_list=True,
        at_users=[types.SimpleNamespace(name="OpenWorker")],
        conversation_id="cid-7",
        sender_staff_id="staff-1",
        sender_nick="Alice",
        message_id="m-7",
    )
    ev = _chatbot_message_to_event(msg)
    assert ev is not None
    # The mention flag is what routes the inbound into the @-mention handler.
    assert ev.mentions_me is True
    # message_id must survive so Slack-style threading keys work uniformly.
    assert ev.message_id == "m-7"
    assert ev.source.chat_id == "cid-7"
    assert ev.source.chat_type == "group"


def test_chatbot_message_to_event_mentions_me_fallback_via_bot_id():
    # Some payloads omit is_in_at_list; the bot id in at_users must suffice.
    msg = _make_chatbot_msg(
        text="@OpenWorker hi there",
        is_in_at_list=False,
        chatbot_user_id="bot-1",
        at_users=[
            types.SimpleNamespace(name="OpenWorker", dingtalk_id="bot-1", staff_id="")
        ],
        conversation_id="cid-8",
        sender_staff_id="staff-2",
        sender_nick="Bob",
        message_id="m-8",
    )
    ev = _chatbot_message_to_event(msg)
    assert ev is not None
    assert ev.mentions_me is True
    assert ev.message_id == "m-8"


def test_chatbot_message_to_event_no_mention_flagged_false():
    msg = _make_chatbot_msg(
        text="just chatting in the channel",
        is_in_at_list=False,
        chatbot_user_id="bot-1",
        at_users=[
            types.SimpleNamespace(name="Human", dingtalk_id="h-9", staff_id="")
        ],
        conversation_id="cid-9",
        sender_staff_id="staff-3",
        sender_nick="Cara",
        message_id="m-9",
    )
    ev = _chatbot_message_to_event(msg)
    assert ev is not None
    assert ev.mentions_me is False
    assert ev.message_id == "m-9"


@pytest.mark.asyncio
async def test_stream_handler_marks_mentions_me_when_bot_tagged(
    fake_dingtalk_stream, tmp_path
):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("dingtalk:default", {"client_id": "cid", "client_secret": "secret"})

    adapter = DingTalkAdapter(client_id="cid", client_secret="secret", secrets=secrets)
    await adapter.connect()

    received: list[MessageEvent] = []

    async def capture(ev: MessageEvent) -> None:
        received.append(ev)

    adapter.set_message_handler(capture)

    handler = adapter._client.handlers[fake_dingtalk_stream.ChatbotMessage.TOPIC]
    callback = fake_dingtalk_stream.CallbackMessage(
        {
            "senderStaffId": "staff-42",
            "senderNick": "Alice",
            "conversationId": "cid-99",
            "sessionWebhook": "https://oapi.dingtalk.com/robot/send?access_token=SESS",
            "msgtype": "text",
            "text": {"content": "@OpenWorker status please"},
            "isInAtList": True,
            "messageId": "m-tag-1",
            "atUsers": [{"name": "OpenWorker"}],
        }
    )
    status, _ = await handler.process(callback)
    assert status == "OK"

    # dispatch is thread-safe fire-and-forget onto the running loop; yield so it runs.
    await asyncio.sleep(0.01)

    assert len(received) == 1
    ev = received[0]
    # The fix: a bot @-mention must be flagged so manager._route_mention fires
    # (which spawns the dedicated coworker session that replies via send_message).
    assert ev.mentions_me is True
    assert ev.message_id == "m-tag-1"

    await adapter.disconnect()
