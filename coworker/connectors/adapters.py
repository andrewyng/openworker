"""Real inbound adapters — Telegram (long-poll) and Slack (Socket Mode).

The heavy SDKs are **lazy-imported inside `connect()`** so the module imports without them
and they're optional extras. Outbound reuses the stateless senders. The raw-event → MessageEvent
mappers are pure functions (testable with plain objects/dicts, no SDK).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from typing import Any, Optional
from urllib.parse import quote

from .base import (
    BasePlatformAdapter,
    InteractionEvent,
    MessageEvent,
    MessageType,
    SendResult,
    SessionSource,
)
from .senders import (
    _feishu_api_base,
    _patch_feishu_message,
    _feishu_tenant_token,
    _send_feishu,
    _send_feishu_interactive,
    _send_slack,
    _send_slack_interactive,
    _send_telegram,
)
from .feishu_cards import prompt_card, resolved_card, submitted_card

logger = logging.getLogger("coworker.connectors")

# Slack encodes an @-mention in message text as `<@U0123>` (legacy: `<@U0123|name>`) — a token,
# not the display name. Resolved at ingestion so every surface (parked cards, transcripts, the
# channel buffer) shows "@name" instead of the raw id.
_SLACK_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")


# -- pure mappers --------------------------------------------------------------
def telegram_message_to_event(msg: Any) -> Optional[MessageEvent]:
    text = getattr(msg, "text", None)
    if not text:
        return None
    chat = msg.chat
    user = getattr(msg, "from_user", None)
    chat_type = (
        "dm"
        if str(getattr(chat, "type", "private")).lower().endswith("private")
        else "group"
    )
    thread = getattr(msg, "message_thread_id", None)
    source = SessionSource(
        platform="telegram",
        chat_id=str(chat.id),
        user_id=str(user.id) if user else None,
        user_name=getattr(user, "full_name", None) if user else None,
        chat_type=chat_type,
        thread_id=str(thread) if thread else None,
    )
    return MessageEvent(
        text=text, source=source, message_id=str(getattr(msg, "message_id", ""))
    )


def slack_event_to_event(
    event: dict, bot_user_id: Optional[str]
) -> Optional[MessageEvent]:
    # Skip bot echoes / message edits / joins etc. (reply-loop guard).
    if event.get("bot_id") or event.get("subtype"):
        return None
    if bot_user_id and event.get("user") == bot_user_id:
        return None
    text = event.get("text") or ""
    if not text:
        return None
    chat_type = "dm" if event.get("channel_type") == "im" else "channel"
    source = SessionSource(
        platform="slack",
        chat_id=str(event.get("channel", "")),
        user_id=event.get("user"),
        chat_type=chat_type,
        thread_id=event.get("thread_ts"),
    )
    # Mention detection runs on the RAW text (the `<@U…>` token form, legacy `<@U…|name>`
    # included) — callers rewrite mentions to @display-name only after mapping.
    mentions_me = bool(
        bot_user_id and re.search(rf"<@{re.escape(bot_user_id)}(?:\|[^>]*)?>", text)
    )
    return MessageEvent(
        text=text, source=source, message_id=event.get("ts"), mentions_me=mentions_me
    )


def _as_dict(value: Any) -> Any:
    """Best-effort conversion for lark-oapi model objects into plain dicts."""
    if isinstance(value, dict):
        return {k: _as_dict(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_dict(v) for v in value]
    for attr in ("to_dict", "model_dump"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return _as_dict(fn())
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        return {
            k.lstrip("_"): _as_dict(v)
            for k, v in vars(value).items()
            if not callable(v) and not k.startswith("__")
        }
    return value


def _first_sender_id(sender_id: dict) -> Optional[str]:
    for key in ("open_id", "user_id", "union_id"):
        value = sender_id.get(key)
        if value:
            return str(value)
    return None


def _feishu_text_from_content(content: Any) -> str:
    if not content:
        return ""
    data = _feishu_content_data(content)
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        text = data.get("text")
        if isinstance(text, str):
            return text
        post_text = _feishu_text_from_post(data)
        if post_text:
            return post_text
        # Post/rich text messages arrive as nested title/content blocks. Keep a compact text view.
        if isinstance(data.get("title"), str):
            return data["title"]
    return ""


def _feishu_content_data(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _feishu_text_from_post(data: dict) -> str:
    locale = data.get("zh_cn") or data.get("en_us") or data
    if not isinstance(locale, dict):
        return ""
    blocks = locale.get("content")
    if not isinstance(blocks, list):
        return ""
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, list):
            continue
        parts: list[str] = []
        for item in block:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag") or "")
            if tag in {"text", "md"}:
                parts.append(str(item.get("text") or ""))
            elif tag == "a":
                label = str(item.get("text") or item.get("href") or "")
                href = str(item.get("href") or "")
                parts.append(f"{label} ({href})" if href and href != label else label)
            elif tag == "at":
                parts.append(str(item.get("user_name") or item.get("user_id") or ""))
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _feishu_attachments_from_message(message: dict) -> list[dict[str, Any]]:
    message_type = str(message.get("message_type") or "")
    data = _feishu_content_data(message.get("content"))
    if not isinstance(data, dict):
        return []
    message_id = str(message.get("message_id") or "")
    if message_type == "image":
        key = str(data.get("image_key") or "")
        if not key:
            return []
        return [
            {
                "platform": "feishu",
                "type": "image",
                "resource_type": "image",
                "key": key,
                "filename": str(
                    data.get("file_name") or data.get("name") or f"{key}.png"
                ),
                "message_id": message_id,
            }
        ]
    if message_type in {"file", "audio", "media", "video"}:
        key = str(data.get("file_key") or "")
        if not key:
            return []
        return [
            {
                "platform": "feishu",
                "type": message_type,
                "resource_type": "file",
                "key": key,
                "filename": str(data.get("file_name") or data.get("name") or key),
                "message_id": message_id,
            }
        ]
    return []


def _feishu_attachment_text(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return ""
    names = [
        str(a.get("filename") or a.get("key") or "attachment") for a in attachments
    ]
    return "User sent attachment: " + ", ".join(names)


def feishu_event_to_event(event: Any) -> Optional[MessageEvent]:
    data = _as_dict(event)
    if isinstance(data, dict) and "event" in data:
        data = data.get("event") or {}
    if not isinstance(data, dict):
        return None
    message = data.get("message") or {}
    if not isinstance(message, dict):
        return None
    message_type = str(message.get("message_type") or "text")
    if message_type not in (
        "text",
        "post",
        "file",
        "image",
        "audio",
        "media",
        "video",
    ):
        return None
    text = _feishu_text_from_content(message.get("content"))
    attachments = _feishu_attachments_from_message(message)
    if not text and attachments:
        text = _feishu_attachment_text(attachments)
    if not text:
        return None
    sender = data.get("sender") or {}
    sender_id = sender.get("sender_id") if isinstance(sender, dict) else {}
    sender_id = sender_id if isinstance(sender_id, dict) else {}
    user_id = _first_sender_id(sender_id)
    chat_id = str(message.get("chat_id") or "")
    if not chat_id:
        return None
    chat_type_raw = str(message.get("chat_type") or "").lower()
    chat_type = "dm" if chat_type_raw in {"p2p", "private", "dm"} else "channel"
    source = SessionSource(
        platform="feishu",
        chat_id=chat_id,
        user_id=user_id,
        user_name=user_id,
        chat_name=message.get("chat_name") or chat_id,
        chat_type=chat_type,
        thread_id=message.get("thread_id") or None,
    )
    return MessageEvent(
        text=text,
        source=source,
        message_id=message.get("message_id"),
        message_type=MessageType.MEDIA if attachments else MessageType.TEXT,
        raw=event,
        attachments=attachments,
    )


def feishu_card_action_to_interaction(event: Any) -> Optional[InteractionEvent]:
    data = _as_dict(event)
    if isinstance(data, dict) and "event" in data:
        data = data.get("event") or {}
    if not isinstance(data, dict):
        return None
    action = data.get("action") or {}
    context = data.get("context") or {}
    operator = data.get("operator") or {}
    if not isinstance(action, dict) or not isinstance(context, dict):
        return None
    raw_value = action.get("value")
    value = ""
    if isinstance(raw_value, dict):
        for key in ("ocw_value", "value", "v"):
            if raw_value.get(key):
                value = str(raw_value.get(key))
                break
    elif raw_value is not None:
        value = str(raw_value)
    if not value:
        return None
    chat_id = str(context.get("open_chat_id") or context.get("chat_id") or "")
    if not chat_id:
        return None
    user_id = _first_sender_id(operator if isinstance(operator, dict) else {})
    return InteractionEvent(
        platform="feishu",
        chat_id=chat_id,
        message_id=str(context.get("open_message_id") or "") or None,
        value=value,
        user_id=user_id,
        user_name=user_id,
    )


# -- adapters ------------------------------------------------------------------
class TelegramAdapter(BasePlatformAdapter):
    platform = "telegram"

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token
        self._app = None

    async def connect(self) -> bool:
        try:
            from telegram.ext import Application, MessageHandler, filters
        except ImportError:
            logger.warning(
                "python-telegram-bot not installed — `pip install coworker[messaging]`"
            )
            return False

        self._app = Application.builder().token(self.token).build()

        async def _on_update(update, _context):
            event = telegram_message_to_event(update.effective_message)
            if event is not None:
                await self.handle_message(event)

        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, _on_update)
        )
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram adapter polling")
        return True

    async def disconnect(self) -> None:
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._app = None

    async def send(
        self, chat_id: str, text: str, *, thread_id: Optional[str] = None
    ) -> SendResult:
        return _send_telegram(self.token, chat_id, text, thread_id)


class SlackAdapter(BasePlatformAdapter):
    platform = "slack"

    # Watchdog cadence: how often to check the live Socket Mode connection and force a reconnect
    # if it has silently died. `start_async()` sleeps forever, so a dead socket looks alive to us
    # unless we poll the client's own is_connected(). Overridable for tests.
    _WATCHDOG_INTERVAL = 20.0

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        *,
        watchdog_interval: Optional[float] = None,
        auto_reconnect: bool = True,
    ) -> None:
        super().__init__()
        self.bot_token = bot_token
        self.app_token = app_token
        self._app = None
        self._socket = None
        self._task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._closing = False
        self._reconnects = (
            0  # observable: how many times the watchdog revived the connection
        )
        self._watchdog_interval = (
            watchdog_interval
            if watchdog_interval is not None
            else self._WATCHDOG_INTERVAL
        )
        # slack_sdk's own reconnect stays on in production (seamless on Slack's graceful cycling);
        # tests turn it off so the watchdog is the sole, deterministic recovery path.
        self._auto_reconnect = auto_reconnect
        self._bot_user_id: Optional[str] = None
        self._name_cache: dict[str, str] = (
            {}
        )  # user_id → display name (resolved once via users.info)
        self._channel_cache: dict[str, str] = (
            {}
        )  # chat_id → channel name (resolved once via conversations.info)

    async def connect(self) -> bool:
        try:
            from slack_bolt.adapter.socket_mode.async_handler import (
                AsyncSocketModeHandler,
            )
            from slack_bolt.async_app import AsyncApp
            from slack_sdk.web.async_client import AsyncWebClient
        except ImportError:
            logger.warning(
                "slack-bolt not installed — `pip install coworker[messaging]`"
            )
            return False

        # Base-URL override so tests (and the FakeSlack harness) can redirect every Web API
        # call — auth.test/users.info/conversations.info/chat.update AND Socket Mode's
        # apps.connections.open, which the handler issues on this same client. Default is the
        # real Slack API. See platform/docs/FAKE-SLACK-SPEC.md.
        base_url = os.environ.get("SLACK_API_URL", "https://slack.com/api/")
        client = AsyncWebClient(token=self.bot_token, base_url=base_url)
        self._app = AsyncApp(client=client)
        try:
            auth = await self._app.client.auth_test()
            self._bot_user_id = auth.get("user_id")
        except Exception:
            logger.exception("slack auth_test failed")
            return False

        @self._app.event("message")
        async def _on_message(event, _say):
            mapped = slack_event_to_event(event, self._bot_user_id)
            if mapped is not None:
                # Slack message events carry only the user id; resolve a friendly name so recent
                # senders / the allow-list don't read "unknown".
                if not mapped.source.user_name:
                    mapped.source.user_name = await self._display_name(
                        mapped.source.user_id
                    )
                # ...and a friendly channel/DM name so the GUI card shows "#ocw-test", not "C…".
                if not mapped.source.chat_name:
                    mapped.source.chat_name = await self._channel_name(
                        mapped.source.chat_id
                    )
                # ...and rewrite <@U…> mention tokens in the text to @name ("@ocw hi", not
                # "<@U0BDKMA4DFF> hi").
                mapped.text = await self._resolve_mentions(mapped.text)
                await self.handle_message(mapped)

        # Button clicks on interactive prompts (action_id `ocw_*`). Socket mode delivers these over
        # the same connection — no public endpoint, just "Interactivity" enabled in the Slack app.
        import re as _re

        @self._app.action(_re.compile(r"^ocw_"))
        async def _on_action(ack, body):
            await ack()
            actions = body.get("actions") or [{}]
            value = actions[0].get("value", "")
            user = body.get("user") or {}
            channel = (body.get("channel") or {}).get("id", "")
            ts = (body.get("message") or {}).get("ts")
            await self.handle_interaction(
                InteractionEvent(
                    platform="slack",
                    chat_id=str(channel),
                    message_id=ts,
                    value=str(value),
                    user_id=user.get("id"),
                    user_name=user.get("username") or user.get("name"),
                    response_url=body.get("response_url"),
                )
            )

        self._closing = False
        self._socket = AsyncSocketModeHandler(self._app, self.app_token)
        self._socket.client.auto_reconnect_enabled = self._auto_reconnect
        self._task = asyncio.create_task(self._socket.start_async())
        # Supervise the connection: start_async() sleeps forever even if the socket dies, so poll
        # the client's real state and force a reconnect if it drops (the silent-stall fix).
        self._watchdog_task = asyncio.create_task(self._watchdog())
        logger.info("slack adapter connected (socket mode) as %s", self._bot_user_id)
        return True

    async def _watchdog(self) -> None:
        """Reconnect the Socket Mode connection if it silently dies. slack_sdk maintains the socket
        in background tasks and normally auto-reconnects, but it can give up after a transient
        error during Slack's periodic connection cycling — leaving a dead socket that never
        recovers. We poll is_connected() and re-open a fresh endpoint when it's down."""
        # Let the initial connect settle before the first check.
        while not self._closing:
            try:
                await asyncio.sleep(self._watchdog_interval)
            except asyncio.CancelledError:
                break
            if self._closing or self._socket is None:
                break
            client = getattr(self._socket, "client", None)
            try:
                alive = bool(client and client.is_connected())
            except Exception:
                alive = False
            if alive:
                continue
            logger.warning(
                "slack socket mode connection down — reconnecting (watchdog)"
            )
            try:
                await client.connect_to_new_endpoint(force=True)
                self._reconnects += 1
                logger.info(
                    "slack socket mode reconnected (watchdog, #%d)", self._reconnects
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("slack watchdog reconnect failed — will retry")

    async def _display_name(self, uid: Optional[str]) -> Optional[str]:
        """Resolve a user id to a display name via users.info, cached. Best-effort: None on failure
        (the caller falls back to the id)."""
        if not uid:
            return None
        if uid in self._name_cache:
            return self._name_cache[uid]
        try:
            info = await self._app.client.users_info(user=uid)
            u = info.get("user") or {}
            prof = u.get("profile") or {}
            name = (
                prof.get("display_name")
                or prof.get("real_name")
                or u.get("real_name")
                or u.get("name")
            )
        except Exception:
            name = None
        if name:
            self._name_cache[uid] = name
        return name

    async def _resolve_mentions(self, text: str) -> str:
        """Rewrite `<@U…>` mention tokens to `@display-name` (cached users.info, same cache as
        sender names). Best-effort: an id that won't resolve (missing scope, deleted user)
        keeps its token."""
        out = text
        for uid in set(_SLACK_MENTION_RE.findall(text or "")):
            name = await self._display_name(uid)
            if name:
                out = re.sub(rf"<@{re.escape(uid)}(?:\|[^>]*)?>", f"@{name}", out)
        return out

    async def _channel_name(self, chat_id: Optional[str]) -> Optional[str]:
        """Resolve a channel/DM id to a display name via conversations.info, cached. Best-effort:
        None on failure (the caller falls back to the id). Mirrors `_display_name`."""
        if not chat_id:
            return None
        if chat_id in self._channel_cache:
            return self._channel_cache[chat_id]
        try:
            info = await self._app.client.conversations_info(channel=chat_id)
            chan = info.get("channel") or {}
            name = chan.get("name") or chan.get("name_normalized")
        except Exception:
            name = None
        if name:
            self._channel_cache[chat_id] = name
        return name

    async def resolve_user_name(self, user_id: Optional[str]) -> Optional[str]:
        """Public §2.1 wrapper over the cached user-name resolution."""
        return await self._display_name(user_id)

    async def resolve_channel_name(self, chat_id: Optional[str]) -> Optional[str]:
        """Public §2.1 wrapper over the cached channel-name resolution."""
        return await self._channel_name(chat_id)

    async def disconnect(self) -> None:
        self._closing = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._socket is not None:
            try:
                await self._socket.close_async()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def send(
        self, chat_id: str, text: str, *, thread_id: Optional[str] = None
    ) -> SendResult:
        # The stateless senders use blocking httpx; offload so an outbound from the event loop
        # (e.g. mirror_inbox_item / _on_interaction, which await this directly) never blocks the
        # server loop on the Slack round-trip.
        return await asyncio.to_thread(
            _send_slack, self.bot_token, chat_id, text, thread_id
        )

    async def send_interactive(
        self, chat_id: str, text: str, buttons, *, thread_id: Optional[str] = None
    ) -> SendResult:
        return await asyncio.to_thread(
            _send_slack_interactive, self.bot_token, chat_id, text, buttons, thread_id
        )

    async def update_message(self, chat_id: str, message_id: str, text: str) -> None:
        """Replace a resolved prompt's buttons with a plain-text outcome ("✅ Approved by …")."""
        if self._app is None or not message_id:
            return
        try:
            await self._app.client.chat_update(
                channel=chat_id, ts=message_id, text=text, blocks=[]
            )
        except Exception:
            logger.debug("slack chat_update failed", exc_info=True)


class FeishuAdapter(BasePlatformAdapter):
    platform = "feishu"

    def __init__(self, profile: dict) -> None:
        super().__init__()
        self.app_id = str(profile.get("app_id") or "")
        self.app_secret = str(profile.get("app_secret") or "")
        self.base_url = str(profile.get("base_url") or "")
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._closing = False
        self._user_cache: dict[str, str] = {}
        self._chat_cache: dict[str, str] = {}
        self._identity_lock = threading.Lock()

    async def connect(self) -> bool:
        try:
            import lark_oapi as lark
        except ImportError:
            logger.warning("lark-oapi not installed — `pip install coworker[messaging]`")
            return False

        self._loop = asyncio.get_running_loop()

        def _on_message(data: Any) -> None:
            event = feishu_event_to_event(data)
            if event is None or self._loop is None or self._closing:
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_feishu_message(event), self._loop
            )

        def _on_card_action(data: Any):
            event = feishu_card_action_to_interaction(data)
            if event is not None and self._loop is not None and not self._closing:
                asyncio.run_coroutine_threadsafe(
                    self._handle_feishu_interaction(event), self._loop
                )
            try:
                from lark_oapi.event.callback.model.p2_card_action_trigger import (
                    P2CardActionTriggerResponse,
                )

                return P2CardActionTriggerResponse(
                    {
                        "toast": {"type": "success", "content": "已收到操作"},
                        "card": {
                            "type": "raw",
                            "data": submitted_card("你的选择已提交，正在继续执行。"),
                        },
                    }
                )
            except Exception:
                return {}

        builder = lark.EventDispatcherHandler.builder(
            "", ""
        ).register_p2_im_message_receive_v1(_on_message)
        register_card_action = getattr(builder, "register_p2_card_action_trigger", None)
        if callable(register_card_action):
            builder = register_card_action(_on_card_action)
        else:
            logger.warning(
                "lark-oapi does not support card action events; Feishu approval "
                "buttons will require the UI fallback"
            )
        handler = builder.build()
        self._client = lark.ws.Client(self.app_id, self.app_secret, event_handler=handler)

        def _run() -> None:
            thread_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(thread_loop)
            try:
                ws_client_module = getattr(getattr(lark, "ws", None), "client", None)
                if ws_client_module is not None and hasattr(ws_client_module, "loop"):
                    ws_client_module.loop = thread_loop
                self._client.start()
            except Exception:
                if not self._closing:
                    logger.exception("feishu websocket client stopped unexpectedly")
            finally:
                try:
                    thread_loop.close()
                finally:
                    asyncio.set_event_loop(None)

        self._closing = False
        self._thread = threading.Thread(target=_run, name="feishu-ws", daemon=True)
        self._thread.start()
        logger.info("feishu adapter websocket started")
        return True

    async def _handle_feishu_message(self, event: MessageEvent) -> None:
        try:
            await asyncio.to_thread(self._enrich_event, event)
        except Exception:
            logger.debug("feishu identity enrichment failed", exc_info=True)
        await self.handle_message(event)

    async def _handle_feishu_interaction(self, event: InteractionEvent) -> None:
        user_id = str(event.user_id or "")
        if user_id and (not event.user_name or event.user_name == user_id):
            try:
                name = await asyncio.to_thread(self._resolve_user_name, user_id)
            except Exception:
                logger.debug("feishu interaction identity enrichment failed", exc_info=True)
                name = None
            if name:
                event.user_name = name
        await self.handle_interaction(event)

    def _enrich_event(self, event: MessageEvent) -> None:
        source = event.source
        user_id = str(source.user_id or "")
        if user_id and (not source.user_name or source.user_name == user_id):
            name = self._resolve_user_name(user_id)
            if name:
                source.user_name = name
        chat_id = str(source.chat_id or "")
        if chat_id and (not source.chat_name or source.chat_name == chat_id):
            name = self._resolve_chat_name(chat_id)
            if name:
                source.chat_name = name

    def _resolve_user_name(self, user_id: str) -> Optional[str]:
        with self._identity_lock:
            cached = self._user_cache.get(user_id)
        if cached:
            return cached
        data = self._feishu_get(
            f"/open-apis/contact/v3/users/{quote(user_id, safe='')}",
            params={"user_id_type": "open_id"},
        )
        user = (data.get("data") or {}).get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict):
            return None
        name = (
            user.get("name")
            or user.get("nickname")
            or user.get("en_name")
            or user.get("email")
        )
        if not name:
            return None
        name = str(name)
        with self._identity_lock:
            self._user_cache[user_id] = name
        return name

    def _resolve_chat_name(self, chat_id: str) -> Optional[str]:
        with self._identity_lock:
            cached = self._chat_cache.get(chat_id)
        if cached:
            return cached
        data = self._feishu_get(f"/open-apis/im/v1/chats/{quote(chat_id, safe='')}")
        body = data.get("data") if isinstance(data, dict) else None
        if not isinstance(body, dict):
            return None
        name = body.get("name") or body.get("chat_name")
        if not name:
            return None
        name = str(name)
        with self._identity_lock:
            self._chat_cache[chat_id] = name
        return name

    def _feishu_get(self, path: str, *, params: Optional[dict[str, str]] = None) -> dict:
        import httpx

        base_url = _feishu_api_base(self.base_url)
        token, err = _feishu_tenant_token(self.app_id, self.app_secret, base_url)
        if err or not token:
            return {}
        try:
            resp = httpx.get(
                f"{base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            data = resp.json()
        except Exception:
            return {}
        if resp.status_code >= 400 or data.get("code") not in (0, None):
            logger.debug(
                "feishu identity lookup failed: path=%s status=%s code=%s msg=%s",
                path,
                resp.status_code,
                data.get("code"),
                data.get("msg") or data.get("error"),
            )
            return {}
        return data

    async def disconnect(self) -> None:
        self._closing = True
        client = self._client
        self._client = None
        if client is not None:
            for name in ("stop", "close"):
                fn = getattr(client, name, None)
                if callable(fn):
                    try:
                        await asyncio.to_thread(fn)
                    except Exception:
                        logger.debug("feishu websocket %s failed", name, exc_info=True)
                    break
        self._thread = None

    async def send(
        self, chat_id: str, text: str, *, thread_id: Optional[str] = None
    ) -> SendResult:
        return await asyncio.to_thread(
            _send_feishu, self._token_bundle(), chat_id, text, thread_id
        )

    async def send_interactive(
        self, chat_id: str, text: str, buttons, *, thread_id: Optional[str] = None
    ) -> SendResult:
        return await asyncio.to_thread(
            _send_feishu_interactive,
            self._token_bundle(),
            chat_id,
            prompt_card(text, buttons),
        )

    async def update_message(self, chat_id: str, message_id: str, text: str) -> None:
        if not message_id:
            return
        result = await asyncio.to_thread(
            _patch_feishu_message,
            self._token_bundle(),
            message_id,
            resolved_card(text),
        )
        if not result.ok:
            logger.debug("feishu interactive card update failed: %s", result.error)

    def _token_bundle(self) -> str:
        return json.dumps(
            {
                "app_id": self.app_id,
                "app_secret": self.app_secret,
                "base_url": self.base_url,
            }
        )


def _load_slack_teams(secrets) -> dict[str, dict]:
    """Per-team bot tokens for managed relay, from `slack:team:<team_id>` profiles
    (written by the managed OAuth install). Returns {team_id: {bot_token, bot_user_id}}.
    """
    teams: dict[str, dict] = {}
    if secrets is None:
        return teams
    for entry in secrets.status():
        prof = entry.get("profile", "")
        if not prof.startswith("slack:team:"):
            continue
        team_id = prof[len("slack:team:") :]
        data = secrets.get(prof) or {}
        if data.get("bot_token"):
            teams[team_id] = {
                "bot_token": data["bot_token"],
                "bot_user_id": data.get("bot_user_id"),
            }
    return teams


def make_adapter(
    platform: str,
    profile: dict,
    *,
    secrets=None,
    token_provider=None,
    relay_url: Optional[str] = None,
    relay_hub=None,
    github_token_client=None,
) -> Optional[BasePlatformAdapter]:
    """Build the adapter for a connected platform from its SecretStore profile.

    Slack supports two mutually-exclusive modes, the user's choice:
    - `mode == "relay"` → managed cloud relay (`SlackRelayAdapter`): needs the
      cloud sign-in `token_provider` + `relay_url`; per-team tokens come from
      `slack:team:*` profiles. No manual tokens.
    - otherwise → Socket Mode (`SlackAdapter`): manual bot + app tokens, one
      workspace.

    Relay adapters share ONE cloud socket: pass the same `relay_hub` to every
    relay-mode platform (the caller owns it); without one, each adapter builds
    its own (fine for a single relay platform).
    """
    if platform == "telegram" and profile.get("bot_token"):
        return TelegramAdapter(profile["bot_token"])
    if platform == "slack":
        if profile.get("mode") == "relay":
            if not (relay_url and token_provider):
                logger.warning(
                    "slack managed-relay configured but relay endpoint / sign-in unavailable "
                    "— sign in and set cloud_relay_ws_url; skipping"
                )
                return None
            from .relay_client import SlackRelayAdapter

            return SlackRelayAdapter(
                relay_url,
                token_provider,
                teams=_load_slack_teams(secrets),
                hub=relay_hub,
            )
        if profile.get("bot_token") and profile.get("app_token"):
            return SlackAdapter(profile["bot_token"], profile["app_token"])
    if platform == "feishu" and profile.get("app_id") and profile.get("app_secret"):
        return FeishuAdapter(profile)
    if platform == "github" and profile.get("mode") == "relay":
        if not (relay_url and token_provider):
            logger.warning(
                "github managed-relay configured but relay endpoint / sign-in "
                "unavailable — sign in and set cloud_relay_ws_url; skipping"
            )
            return None
        from .github_installs import list_installs
        from .github_relay import GitHubRelayAdapter
        from .relay_client import RelayHub

        hub = relay_hub or RelayHub(relay_url, token_provider)
        installs = (
            {iid: prof for iid, prof in list_installs(secrets)} if secrets else {}
        )
        return GitHubRelayAdapter(
            hub, installs=installs, token_client=github_token_client
        )
    return None
