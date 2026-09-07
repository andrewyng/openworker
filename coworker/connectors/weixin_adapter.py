"""WeChat ClawBot adapter — long-poll iLink getupdates into Gateway MessageEvents."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    SessionSource,
)
from . import weixin_ilink as ilink

logger = logging.getLogger(__name__)


def weixin_message_to_event(msg: dict[str, Any]) -> Optional[MessageEvent]:
    """Map an iLink inbound message to a Gateway MessageEvent (DM-only v1)."""
    # message_type 1 = user; skip bot echoes
    mtype = msg.get("message_type")
    if mtype is not None and int(mtype) != 1:
        return None
    user_id = str(msg.get("from_user_id") or "").strip()
    if not user_id:
        return None
    text = ilink.extract_text(msg)
    if not text:
        return None
    ctx = msg.get("context_token") or ""
    if ctx:
        ilink.context_token_store().put(user_id, str(ctx))
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="weixin",
            chat_id=user_id,
            user_id=user_id,
            user_name=user_id.split("@")[0] if "@" in user_id else user_id,
            chat_type="dm",
        ),
        message_id=str(msg.get("client_id") or msg.get("msg_id") or "") or None,
        message_type=MessageType.TEXT,
        raw=msg,
        mentions_me=True,  # ClawBot DMs are always addressed to the bot
    )


class WeixinAdapter(BasePlatformAdapter):
    platform = "weixin"

    def __init__(
        self,
        bot_token: str,
        *,
        base_url: Optional[str] = None,
        poll_interval: float = 0.5,
    ) -> None:
        super().__init__()
        self.bot_token = bot_token
        self.base_url = (base_url or "").rstrip("/") or None
        self._poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._closing = False
        self._buf = ""

    async def connect(self) -> bool:
        if not self.bot_token:
            return False
        self._closing = False
        self._task = asyncio.create_task(self._poll_loop(), name="weixin-ilink-poll")
        logger.info("weixin adapter polling (iLink ClawBot)")
        return True

    async def disconnect(self) -> None:
        self._closing = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def send(
        self, chat_id: str, text: str, *, thread_id: Optional[str] = None
    ) -> SendResult:
        _ = thread_id
        ctx = ilink.context_token_store().get(chat_id)
        result = await asyncio.to_thread(
            ilink.send_text,
            self.bot_token,
            chat_id,
            text,
            ctx or "",
            base_url=self.base_url,
        )
        if result.get("ok"):
            return SendResult(True, message_id=str(result.get("message_id") or "") or None)
        return SendResult(False, error=result.get("error") or "weixin send failed")

    async def _poll_loop(self) -> None:
        while not self._closing:
            try:
                data = await asyncio.to_thread(
                    ilink.get_updates,
                    self.bot_token,
                    self._buf,
                    base_url=self.base_url,
                )
                self._buf = data.get("get_updates_buf") or self._buf
                for msg in data.get("msgs") or []:
                    if not isinstance(msg, dict):
                        continue
                    event = weixin_message_to_event(msg)
                    if event is not None:
                        await self.handle_message(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("weixin getupdates failed")
                await asyncio.sleep(max(2.0, self._poll_interval))
                continue
            await asyncio.sleep(self._poll_interval)
