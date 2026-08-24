"""DingTalk (钉钉) connector.

Supports two mutually exclusive modes:

1. **Group-bot webhook** (outbound-only by default): send messages to a DingTalk
   group via a webhook URL that contains an access_token, optionally signed with
   a bot secret. This is the easiest way to get notifications into a group, but
   DingTalk custom group bots do *not* deliver inbound @-mentions to a callback
   URL unless the bot is upgraded.

2. **Enterprise (stream) mode** (two-way): create an enterprise-internal app +
   robot in the DingTalk open platform, choose "Stream" as the message-receiving
   mode, and provide the app's ClientId + ClientSecret. The connector opens a
   WebSocket to DingTalk's Stream gateway, receives messages without a public IP,
   and replies via the per-conversation ``sessionWebhook`` that accompanies every
   inbound push.

Inbound messages in stream mode arrive through the dingtalk-stream SDK; the
legacy HTTP webhook route (``/v1/connectors/dingtalk/webhook``) is kept for
backwards compatibility with group-bot callbacks that some enterprise bots can
also emit.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .base import BasePlatformAdapter, MessageEvent, SendResult, SessionSource

logger = logging.getLogger("coworker.connectors")

_DINGTALK_API_BASE = "https://oapi.dingtalk.com"
_TIMEOUT = 30.0


def _sign(secret: str, timestamp: str) -> str:
    """DingTalk group-bot signature: base64(hmac_sha256(timestamp + '\n' + secret))."""
    msg = f"{timestamp}\n{secret}".encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), msg, digestmod=hashlib.sha256).digest()
    return base64.b64encode(mac).decode("utf-8")


def _build_dingtalk_payload(text: str, msgtype: str) -> dict[str, Any]:
    """Build a DingTalk message payload for the given msgtype."""
    payload: dict[str, Any] = {"msgtype": msgtype}
    if msgtype == "markdown":
        payload["markdown"] = {"title": text.split("\n", 1)[0][:64], "text": text}
    else:
        payload["text"] = {"content": text}
    return payload


def _send_once(
    webhook_url: str,
    text: str,
    secret: Optional[str],
    msgtype: str,
) -> SendResult:
    """Single attempt to send a DingTalk group-bot message."""
    # httpx drops an existing query string when params={} is passed, so we extract
    # every query param (especially access_token) into the explicit params dict and
    # post to the bare path.
    parsed = urlparse(webhook_url)
    params: dict[str, str] = {}
    for key, values in parse_qs(parsed.query).items():
        if values:
            params[key] = values[-1]
    url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    if secret:
        timestamp = str(int(time.time() * 1000))
        params["timestamp"] = timestamp
        params["sign"] = _sign(secret, timestamp)

    payload = _build_dingtalk_payload(text, msgtype)

    try:
        import httpx

        resp = httpx.post(url, params=params, json=payload, timeout=_TIMEOUT)
        data = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))

    if data.get("errcode") == 0:
        return SendResult(True, message_id=str(data.get("msg_id") or ""))
    return SendResult(
        False,
        error=f"dingtalk {data.get('errcode')}: {data.get('errmsg', 'send failed')}",
    )


def _is_msgtype_error(error: str) -> bool:
    """True if the error indicates the bot rejected the msgtype."""
    err = error.lower()
    return (
        "300001" in error
        or "450001" in error
        or "robot type" in err
        or "not match" in err
        or "不支持的消息类型" in err
        or "msgtype" in err
    )


def send_dingtalk(
    webhook_url: str,
    text: str,
    secret: Optional[str] = None,
    msgtype: str = "text",
) -> SendResult:
    """Send a message through a DingTalk group-bot or session webhook.

    If the bot rejects the default ``text`` message type (common with bots that
    only accept markdown/actionCard), automatically fall back to ``markdown``.
    """
    result = _send_once(webhook_url, text, secret, msgtype)
    if not result.ok and msgtype == "text" and _is_msgtype_error(result.error or ""):
        return _send_once(webhook_url, text, secret, "markdown")
    return result


def _strip_at(text: str, at_users: list[dict[str, Any]]) -> str:
    """Remove '@机器人' text that DingTalk prepends to the message content."""
    for at in at_users or []:
        name = at.get("name") or at.get("nick") or ""
        if not name:
            continue
        # DingTalk prefixes "@机器人名 " or embeds "@机器人名".
        for pat in (f"@{name} ", f"@{name}"):
            if text.startswith(pat):
                text = text[len(pat) :]
                break
    return text.strip()


def webhook_payload_to_event(payload: dict[str, Any]) -> Optional[MessageEvent]:
    """Convert a DingTalk inbound callback payload into a MessageEvent.

    Handles group-chat robot callbacks (conversationId + senderStaffId) and
    session-webhook pushes (senderNick). Falls back gracefully when fields are
    missing.
    """
    msgtype = payload.get("msgtype") or payload.get("msgType")
    if msgtype != "text":
        # Only plain text inbound is supported in v1.
        return None

    text_obj = payload.get("text") or {}
    text = str(text_obj.get("content") or "").strip()
    if not text:
        return None

    # Strip the leading @bot mention if present.
    at_users = payload.get("atUsers") or payload.get("atUserIds") or []
    text = _strip_at(text, at_users)
    if not text:
        return None

    sender_id = str(
        payload.get("senderStaffId")
        or payload.get("senderUserId")
        or payload.get("sender")
        or ""
    )
    sender_name = payload.get("senderNick") or payload.get("senderName") or sender_id
    conversation_id = str(payload.get("conversationId") or "")
    chat_type = "group" if conversation_id else "dm"

    source = SessionSource(
        platform="dingtalk",
        chat_id=conversation_id,
        user_id=sender_id,
        user_name=sender_name,
        chat_type=chat_type,
        thread_id=None,
    )
    return MessageEvent(text=text, source=source, raw=payload)


def _chatbot_message_to_event(msg: Any) -> Optional[MessageEvent]:
    """Convert a dingtalk-stream ChatbotMessage into a MessageEvent."""
    text_content = getattr(msg, "text", None)
    text = str(getattr(text_content, "content", "") or "").strip()
    if not text:
        return None

    chatbot_user_id = str(getattr(msg, "chatbot_user_id", "") or "")
    at_users_raw = getattr(msg, "at_users", None) or []
    at_users: list[dict[str, Any]] = []
    mentions_me = bool(getattr(msg, "is_in_at_list", False))
    for at in at_users_raw:
        name = getattr(at, "name", "") or ""
        if name:
            at_users.append({"name": name})
        # Some payloads don't set is_in_at_list; fall back to matching the bot id.
        if chatbot_user_id and not mentions_me:
            at_id = str(
                getattr(at, "dingtalk_id", "")
                or getattr(at, "staff_id", "")
                or ""
            )
            if at_id and at_id == chatbot_user_id:
                mentions_me = True

    text = _strip_at(text, at_users)
    if not text:
        return None

    sender_id = str(
        getattr(msg, "sender_staff_id", "")
        or getattr(msg, "sender_user_id", "")
        or ""
    )
    sender_name = getattr(msg, "sender_nick", "") or sender_id
    conversation_id = str(getattr(msg, "conversation_id", "") or "")
    chat_type = "group" if conversation_id else "dm"
    message_id = str(getattr(msg, "message_id", "") or "")

    source = SessionSource(
        platform="dingtalk",
        chat_id=conversation_id,
        user_id=sender_id,
        user_name=sender_name,
        chat_type=chat_type,
        thread_id=None,
    )
    return MessageEvent(
        text=text,
        source=source,
        message_id=message_id,
        mentions_me=mentions_me,
        raw=msg.to_dict(),
    )


class _DingTalkStreamHandler:
    """Bridge between dingtalk-stream's ChatbotHandler and our BasePlatformAdapter.

    We do not inherit from ChatbotHandler at import time to keep the module
    importable when dingtalk-stream is absent. The adapter creates the real
    subclass dynamically inside connect().
    """

    def __init__(self, adapter: DingTalkAdapter) -> None:
        self.adapter = adapter

    def _make_handler_class(self) -> type:
        import dingtalk_stream

        outer = self

        class Handler(dingtalk_stream.ChatbotHandler):
            async def process(self, callback: dingtalk_stream.CallbackMessage):
                # dingtalk-stream may drive `process` from its event loop OR a worker
                # thread, and its upstream only logs str(e) — which hides the trace.
                # Wrap everything so the real stack lands in the backend log, and
                # always return an ack tuple so one bad message can't kill the stream.
                try:
                    data = getattr(callback, "data", {}) or {}
                    topic = getattr(callback.headers, "topic", None) if hasattr(callback, "headers") else None
                    outer.adapter._last_inbound_at = time.time()
                    outer.adapter._inbound_count += 1
                    logger.info(
                        "dingtalk raw callback: topic=%s data_keys=%s",
                        topic,
                        sorted(data.keys()) if isinstance(data, dict) else type(data).__name__,
                    )

                    try:
                        msg = dingtalk_stream.ChatbotMessage.from_dict(data)
                    except Exception as exc:
                        logger.warning("dingtalk failed to parse ChatbotMessage: %s", exc, exc_info=True)
                        return dingtalk_stream.AckMessage.STATUS_OK, "OK"

                    msgtype = getattr(msg, "msgtype", None)
                    text_obj = getattr(msg, "text", None) or {}
                    raw_text = str(getattr(text_obj, "content", "") or "").strip()
                    conversation_id = str(getattr(msg, "conversation_id", "") or "")
                    sender_nick = getattr(msg, "sender_nick", "") or ""
                    logger.info(
                        "dingtalk parsed: msgtype=%s conversation_id=%s sender=%s text=%r",
                        msgtype,
                        conversation_id,
                        sender_nick,
                        raw_text,
                    )

                    event = _chatbot_message_to_event(msg)
                    if event is None:
                        logger.info(
                            "dingtalk callback produced no MessageEvent "
                            "(msgtype=%s conversation_id=%s text=%r)",
                            msgtype,
                            conversation_id,
                            raw_text,
                        )
                        return dingtalk_stream.AckMessage.STATUS_OK, "OK"

                    # Surface the conversation id so the operator can copy it into the
                    # Add-channel field (`dingtalk:<conversationId>`). It only exists in
                    # the inbound message, never in the open-platform console.
                    outer.adapter._last_conversation_id = event.source.chat_id
                    logger.info(
                        "dingtalk inbound: conversationId=%s sender=%s(%s) chat_type=%s "
                        "mentions_me=%s message_id=%s text=%r",
                        event.source.chat_id,
                        event.source.user_name,
                        event.source.user_id,
                        event.source.chat_type,
                        event.mentions_me,
                        event.message_id,
                        event.text,
                    )

                    # Remember the conversation-specific reply webhook. Persist it so
                    # the stateless send_message tool can reply outside the adapter.
                    session_webhook = str(getattr(msg, "session_webhook", "") or "").strip()
                    if session_webhook:
                        outer.adapter._set_session_webhook(
                            event.source.chat_id, session_webhook
                        )

                    # Dispatch the agent turn off the SDK's hot path. run_coroutine_threadsafe
                    # is safe whether process was awaited in the loop or run on a thread.
                    loop = outer.adapter._loop
                    if loop is not None:
                        future = asyncio.run_coroutine_threadsafe(outer.adapter.handle_message(event), loop)
                        def _on_done(fut):
                            try:
                                fut.result()
                            except Exception as exc:
                                logger.exception("dingtalk inbound dispatch failed: %s", exc)
                        future.add_done_callback(_on_done)
                    else:
                        logger.warning(
                            "dingtalk adapter has no event loop captured; cannot dispatch inbound"
                        )
                    return dingtalk_stream.AckMessage.STATUS_OK, "OK"
                except Exception:
                    logger.exception("dingtalk stream process failed")
                    return dingtalk_stream.AckMessage.STATUS_OK, "OK"

        class EventHandler(dingtalk_stream.EventHandler):
            async def process(self, event: dingtalk_stream.EventMessage):
                try:
                    logger.info(
                        "dingtalk event: topic=%s event_type=%s event_id=%s data_keys=%s",
                        event.headers.topic,
                        event.headers.event_type,
                        event.headers.event_id,
                        sorted(event.data.keys()) if isinstance(event.data, dict) else type(event.data).__name__,
                    )
                except Exception:
                    logger.exception("dingtalk event handler failed")
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"

        return Handler, EventHandler


class DingTalkAdapter(BasePlatformAdapter):
    platform = "dingtalk"

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        secret: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        secrets: Any = None,
    ) -> None:
        super().__init__()
        self.webhook_url = webhook_url
        self.secret = secret
        self.client_id = client_id
        self.client_secret = client_secret
        self._secrets = secrets
        # In-memory cache of conversation -> sessionWebhook for stream-mode replies.
        self._session_webhooks: dict[str, str] = {}
        # Stream mode runtime state.
        self._client: Any = None
        self._handler: Any = None
        self._task: Optional[asyncio.Task] = None
        self._closing = False
        # Captured event loop, used to dispatch inbound agent turns thread-safely.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Diagnostics surfaced by /v1/connectors/dingtalk/status.
        self._connection_state: str = "idle"  # idle | connecting | connected | error
        self._connected_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._last_inbound_at: Optional[float] = None
        self._last_conversation_id: Optional[str] = None
        self._inbound_count: int = 0

    @property
    def mode(self) -> str:
        """'stream' if enterprise credentials are present, otherwise 'webhook'."""
        if self.client_id and self.client_secret:
            return "stream"
        return "webhook"

    def _profile_key(self) -> str:
        return "dingtalk:default"

    def _load_profile(self) -> dict[str, Any]:
        if self._secrets is None:
            return {}
        return self._secrets.get(self._profile_key()) or {}

    def _save_session_webhooks(self) -> None:
        """Persist the in-memory session webhook cache back to the profile."""
        if self._secrets is None or not self._session_webhooks:
            return
        profile = self._load_profile()
        webhooks = {
            k: v
            for k, v in self._session_webhooks.items()
            if v
        }
        if not webhooks:
            return
        profile["session_webhooks"] = webhooks
        self._secrets.put(self._profile_key(), profile)

    def _set_session_webhook(self, chat_id: str, webhook: str) -> None:
        """Store a per-conversation reply webhook (stream mode)."""
        if not chat_id or not webhook:
            return
        self._session_webhooks[chat_id] = webhook
        self._save_session_webhooks()

    def status(self) -> dict[str, Any]:
        """Runtime diagnostics for the DingTalk status endpoint."""
        import asyncio

        task_state: Optional[str] = None
        if self._task is not None:
            if self._task.done():
                task_state = "done"
                if self._task.cancelled():
                    task_state = "cancelled"
                elif self._task.exception():
                    task_state = "failed"
            else:
                task_state = "running"
        return {
            "mode": self.mode,
            "state": self._connection_state,
            "task_state": task_state,
            "connected_at": self._connected_at,
            "last_inbound_at": self._last_inbound_at,
            "last_conversation_id": self._last_conversation_id,
            "inbound_count": self._inbound_count,
            "session_webhooks": len(self._session_webhooks),
            "client_id_prefix": self.client_id[:8] if self.client_id else None,
            "last_error": self._last_error,
        }

    async def connect(self) -> bool:
        if self.mode == "stream":
            return await self._connect_stream()
        return bool(self.webhook_url)

    def _on_stream_task_done(self, task: asyncio.Task) -> None:
        """Log unexpected stream client exits so operators can diagnose disconnects."""
        if task.cancelled():
            self._connection_state = "cancelled"
            logger.info("dingtalk stream task cancelled")
            return
        exc = task.exception()
        if exc is not None:
            self._connection_state = "error"
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "dingtalk stream task exited with error: %s", self._last_error, exc_info=exc
            )
        else:
            self._connection_state = "disconnected"
            logger.info("dingtalk stream task ended")

    async def _run_stream_client(self) -> None:
        """Wrap the SDK start() call to catch and log connection errors."""
        if self._client is None:
            return
        try:
            logger.info("dingtalk stream client starting websocket handshake")
            await self._client.start()
        except asyncio.CancelledError:
            # Expected during shutdown; the SDK's reconnect loop catches
            # CancelledError and loops forever, so we exit here.
            if not self._closing:
                self._connection_state = "error"
                self._last_error = "stream task cancelled unexpectedly"
                logger.warning("dingtalk stream task cancelled unexpectedly")
            raise
        except Exception as exc:
            if not self._closing:
                self._connection_state = "error"
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("dingtalk stream client error: %s", self._last_error, exc_info=True)
            raise

    async def _connect_stream(self) -> bool:
        try:
            import dingtalk_stream
        except ImportError:
            self._connection_state = "error"
            self._last_error = "dingtalk-stream SDK not installed"
            logger.warning(
                "dingtalk-stream not installed — run `pip install dingtalk-stream`"
            )
            return False

        self._connection_state = "connecting"
        logger.info(
            "dingtalk stream connecting: client_id=%s... mode=stream",
            self.client_id[:6] if self.client_id else "<none>",
        )

        credential = dingtalk_stream.Credential(self.client_id, self.client_secret)
        self._client = dingtalk_stream.DingTalkStreamClient(credential)
        # Capture the running loop so the inbound dispatch is thread-safe even if
        # dingtalk-stream invokes our handler from a worker thread.
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None

        # Build the handler dynamically so the class only inherits from
        # dingtalk_stream.ChatbotHandler when the SDK is available.
        bridge = _DingTalkStreamHandler(self)
        handler_class, event_handler_class = bridge._make_handler_class()
        # IMPORTANT: do NOT assign to `self._handler` — that field is owned by
        # BaseAdapter and points at `gateway._on_inbound` (set via
        # `set_message_handler` during `Gateway.register`). Overwriting it with
        # the dingtalk_stream ChatbotHandler instance breaks inbound dispatch:
        # `await self._handler(event)` raises `'Handler' object is not callable`.
        self._stream_callback_handler = handler_class()

        topic = dingtalk_stream.chatbot.ChatbotMessage.TOPIC
        self._client.register_callback_handler(topic, self._stream_callback_handler)
        logger.info("dingtalk stream callback handler registered for topic=%s", topic)

        # Catch-all event handler so we can see connection-level / lifecycle events
        # and log any event topic that arrives.
        self._client.register_all_event_handler(event_handler_class())
        logger.info("dingtalk stream catch-all event handler registered")

        # Log the exact subscription list that will be sent to the gateway.
        subscriptions: list[dict[str, str]] = []
        if self._client._is_event_required:
            subscriptions.append({"type": "EVENT", "topic": "*"})
        for t in self._client.callback_handler_map.keys():
            subscriptions.append({"type": "CALLBACK", "topic": t})
        logger.info("dingtalk stream subscriptions: %s", subscriptions)

        self._closing = False
        self._task = asyncio.create_task(self._run_stream_client())
        self._task.add_done_callback(self._on_stream_task_done)
        # The SDK's start() is blocking; we treat "task spawned" as connected
        # because the handshake happens inside start(). The watchdog/task_done
        # logging will surface any failure.
        self._connection_state = "connected"
        self._connected_at = asyncio.get_event_loop().time()
        logger.info("dingtalk adapter connected (stream mode)")
        return True

    async def disconnect(self) -> None:
        if self.mode != "stream":
            return
        self._closing = True
        self._connection_state = "disconnecting"
        logger.info("dingtalk adapter disconnecting (stream mode)")

        if self._client is not None:
            # The SDK does not expose a stop() method; close the open websocket
            # so the async-for in start() raises ConnectionClosedError and the
            # task yields. Without this the SDK's reconnect loop catches
            # CancelledError and sleeps/reconnects forever.
            try:
                ws = getattr(self._client, "websocket", None)
                if ws is not None and hasattr(ws, "close"):
                    await ws.close(code=1001, reason="shutdown")
                    logger.debug("dingtalk stream websocket closed")
            except Exception:
                logger.debug("dingtalk stream websocket close failed", exc_info=True)

        if self._task is not None:
            self._task.cancel()
            # The SDK's start() catches CancelledError in its reconnect loop and
            # reconnects forever, so a plain `await self._task` can hang. Use
            # asyncio.wait (not wait_for) with a timeout: it returns whether or
            # not the task respects cancellation, and Uvicorn will reap the
            # process once lifespan shutdown completes.
            done, pending = await asyncio.wait({self._task}, timeout=5.0)
            if self._task in pending:
                logger.warning(
                    "dingtalk stream task did not finish within 5s; leaving it behind"
                )
            try:
                # Re-raise cancellation / surface any stored exception if the
                # task actually finished in time.
                if self._task in done:
                    self._task.result()
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connection_state = "idle"
        logger.info("dingtalk adapter disconnected")

    async def send(
        self, chat_id: str, text: str, *, thread_id: Optional[str] = None
    ) -> SendResult:
        if self.mode == "stream":
            # Stream replies must use the per-conversation sessionWebhook that
            # arrived with the inbound message.
            webhook = self._session_webhooks.get(chat_id) if chat_id else None
            if not webhook:
                return SendResult(
                    False,
                    error="dingtalk stream: no session webhook for this conversation yet",
                )
            return await asyncio.to_thread(send_dingtalk, webhook, text)

        # Webhook mode: fixed group-bot URL.
        return await asyncio.to_thread(
            send_dingtalk, self.webhook_url, text, self.secret
        )

    def receive_webhook(self, payload: dict[str, Any]) -> Optional[MessageEvent]:
        """Called by the FastAPI route when DingTalk pushes a message.

        Used by group-bot callbacks and as a fallback for enterprise bots that
        also emit HTTP callbacks.
        """
        event = webhook_payload_to_event(payload)
        if event is not None:
            session_webhook = str(payload.get("sessionWebhook") or "").strip()
            if session_webhook:
                self._set_session_webhook(event.source.chat_id, session_webhook)
            return event
        return None
