"""Gateway — owns the messaging adapters and routes inbound messages.

Lives inside the always-on `openworker-server` (started/stopped in its lifespan). On inbound:
enforce the per-platform allowlist, then hand the message to the registered handler (the
super-agent runner, wired in the next increment). Outbound replies go through the
`send_message` tool, not the gateway — so the gateway stays a thin inbound router here.
"""

from __future__ import annotations

import logging
from asyncio import to_thread
from collections import OrderedDict
from typing import Callable, Optional
from urllib.parse import urlparse

from ..secrets import SecretStore
from .base import (
    BasePlatformAdapter,
    InteractionEvent,
    MessageEvent,
    MessageHandler,
    SendResult,
    SessionSource,
    parse_target,
)
from .config import ConnectorSettings, is_authorized, load_settings

logger = logging.getLogger("coworker.connectors")

_RECENT_CAP = 20  # most-recent distinct senders kept for chat-ID auto-capture


class Gateway:
    def __init__(
        self,
        *,
        secrets: Optional[SecretStore] = None,
        settings: Optional[dict[str, ConnectorSettings]] = None,
        handler: Optional[MessageHandler] = None,
        reply_resolver: Optional[Callable[[MessageEvent], bool]] = None,
        interaction_handler: Optional[Callable] = None,
        on_unauthorized: Optional[Callable] = None,
    ) -> None:
        self.secrets = secrets or SecretStore()
        self.settings = (
            settings if settings is not None else load_settings(self.secrets)
        )
        self._handler = handler
        # Tried before the handler: if an inbound message is an Inbox reply (carries an
        # [ow:<id>] token), it resolves the item and is consumed — not routed as a new turn.
        self._reply_resolver = reply_resolver
        # A button click on an interactive prompt (resolves an Inbox item by id).
        self._interaction_handler = interaction_handler
        # Called (awaited) with the MessageEvent when the allow-list drops it, so the message
        # can be PARKED for one-step allow-and-deliver instead of vanishing.
        self._on_unauthorized = on_unauthorized
        self._adapters: dict[str, BasePlatformAdapter] = {}
        # Which adapters actually came up in `start()`. Registration is NOT liveness: a
        # platform stays in `_adapters` even when its listener never started (missing
        # messaging extra, rejected token, network), so anything reporting "connected" has
        # to read this instead — otherwise a bot that receives nothing still looks live.
        self._live: set[str] = set()
        # platform -> why the listener is down (surfaced on the Connectors tab).
        self._listen_errors: dict[str, str] = {}
        # In-memory recent senders for chat-ID auto-capture (identity only, never persisted).
        self._recent: "OrderedDict[tuple[str, str, str], dict]" = OrderedDict()

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    def set_reply_resolver(
        self, resolver: Optional[Callable[[MessageEvent], bool]]
    ) -> None:
        self._reply_resolver = resolver

    def register(self, adapter: BasePlatformAdapter) -> None:
        adapter.set_message_handler(self._on_inbound)
        if self._interaction_handler is not None:
            adapter.set_interaction_handler(self._on_interaction)
        self._adapters[adapter.platform] = adapter

    async def _on_interaction(self, event: InteractionEvent) -> None:
        source = SessionSource(
            platform=event.platform,
            chat_id=event.chat_id,
            user_id=event.user_id,
            user_name=event.user_name,
            chat_type="channel",
            team_id=event.team_id,
        )
        settings = self.settings.get(event.platform)
        if settings is None or not is_authorized(settings, source):
            logger.info("rejecting unauthorized interaction from %s", source.label())
            await self.reject_interaction(event)
            return
        if self._interaction_handler is not None:
            await self._interaction_handler(event)

    async def reject_interaction(
        self,
        event: InteractionEvent,
        text: str = "Only a designated approval owner can respond to this request.",
    ) -> None:
        """Best-effort private feedback for a rejected Slack button click."""
        response_url = str(event.response_url or "")
        parsed = urlparse(response_url)
        if (
            event.platform != "slack"
            or parsed.scheme != "https"
            or parsed.hostname not in {"hooks.slack.com", "hooks.slack-gov.com"}
        ):
            return

        def _post() -> None:
            import httpx

            try:
                httpx.post(
                    response_url,
                    json={"response_type": "ephemeral", "text": text},
                    timeout=10,
                )
            except Exception:
                logger.debug("Slack ephemeral interaction response failed", exc_info=True)

        await to_thread(_post)

    async def _on_inbound(self, event: MessageEvent) -> None:
        self._record_recent(event)  # capture identity even from unauthorized senders
        settings = self.settings.get(event.source.platform)
        if settings is None or not is_authorized(settings, event.source):
            logger.info("parking unauthorized inbound from %s", event.source.label())
            if self._on_unauthorized is not None:
                try:
                    await self._on_unauthorized(event)
                except Exception:
                    logger.exception("parking unauthorized inbound failed")
            return
        # An inbound reply that resolves an Inbox item (approval/answer) is consumed here, not
        # routed to the super-agent as a new turn. The suspended agent awaiting that item is
        # released automatically (InboxStore.resolve fires its waiter).
        if self._reply_resolver is not None:
            try:
                if self._reply_resolver(event):
                    return
            except Exception:
                logger.exception("inbox reply resolver failed")
        if self._handler is not None:
            await self._handler(event)

    def _record_recent(self, event: MessageEvent) -> None:
        s = event.source
        if not s.user_id:
            return
        # Ids are workspace-scoped, so the same U… in two teams is two senders.
        key = (s.platform, s.team_id or "", s.user_id)
        self._recent.pop(key, None)  # move to most-recent
        self._recent[key] = {
            "platform": s.platform,
            "user_id": s.user_id,
            "user_name": s.user_name,
            "chat_id": s.chat_id,
            "chat_type": s.chat_type,
            "target": s.target,
            "team_id": s.team_id,  # workspace (managed relay); None for socket mode
        }
        while len(self._recent) > _RECENT_CAP:
            self._recent.popitem(last=False)

    def recent_senders(self, platform: Optional[str] = None) -> list[dict]:
        """Most-recent-first list of who has messaged (for the allowlist UI)."""
        items = list(self._recent.values())[::-1]
        return [e for e in items if platform is None or e["platform"] == platform]

    async def start(self) -> list[str]:
        """Connect every enabled+registered adapter. Returns the platforms that came up.

        Every failure path records *why* in `_listen_errors` rather than only logging it.
        A connector whose credentials are saved reads as "connected" everywhere in the UI
        (that check is credentials-present, by design — it gates the outbound tools), so
        without this a listener that never started is completely invisible to the user.
        """
        live: list[str] = []
        for platform, settings in self.settings.items():
            if not settings.enabled:
                continue
            adapter = self._adapters.get(platform)
            if adapter is None:
                # Enabled with no adapter: make_adapter() couldn't build one from the saved
                # profile (e.g. Slack Socket Mode with a bot token but no app token).
                self._mark_down(
                    platform, "no listener could be started from the saved settings"
                )
                continue
            adapter.connect_error = None
            try:
                if await adapter.connect():
                    live.append(platform)
                    self._live.add(platform)
                    self._listen_errors.pop(platform, None)
                else:
                    self._mark_down(
                        platform,
                        adapter.connect_error or "the listener did not start",
                    )
            except Exception as exc:  # bad token / network — skip, don't break the server
                logger.exception("failed to connect %s adapter", platform)
                # Only the exception TYPE, never its message: this string is served over
                # REST, and library errors quote the credential they rejected verbatim
                # (python-telegram-bot's InvalidToken embeds the bot token). Adapters that
                # want to say more set a curated `connect_error` and return False instead.
                self._mark_down(
                    platform,
                    f"{type(exc).__name__} — see the server log for details",
                )
        return live

    def _mark_down(self, platform: str, reason: str) -> None:
        self._live.discard(platform)
        self._listen_errors[platform] = reason
        logger.warning("%s listener is not running: %s", platform, reason)

    def is_listening(self, platform: str) -> bool:
        """True only if this platform's inbound listener actually came up."""
        return platform in self._live

    def listen_error(self, platform: str) -> Optional[str]:
        """Why `platform`'s listener isn't running, or None when it is (or was never started)."""
        return self._listen_errors.get(platform)

    async def stop(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.disconnect()
            except Exception:
                logger.exception("error disconnecting %s adapter", adapter.platform)
        self._live.clear()
        self._listen_errors.clear()

    async def deliver(self, target: str, text: str) -> SendResult:
        """Send via a live adapter (used where the persistent connection is preferred)."""
        platform, chat_id, thread_id = parse_target(target)
        adapter = self._adapters.get(platform)
        if adapter is None:
            return SendResult(False, error=f"no adapter for {platform}")
        return await adapter.send(chat_id, text, thread_id=thread_id)

    async def deliver_interactive(self, target: str, text: str, buttons) -> SendResult:
        """Send a prompt with choice buttons (adapters without interactive support show text only)."""
        platform, chat_id, thread_id = parse_target(target)
        adapter = self._adapters.get(platform)
        if adapter is None:
            return SendResult(False, error=f"no adapter for {platform}")
        return await adapter.send_interactive(
            chat_id, text, buttons, thread_id=thread_id
        )

    async def update_message(
        self, platform: str, chat_id: str, message_id: str, text: str
    ) -> None:
        """Replace a resolved prompt's buttons with a plain-text outcome, if the adapter supports it."""
        adapter = self._adapters.get(platform)
        fn = getattr(adapter, "update_message", None)
        if fn is not None:
            await fn(chat_id, message_id, text)

    def status(self) -> list[dict]:
        out = []
        for platform, settings in self.settings.items():
            out.append(
                {
                    "platform": platform,
                    "enabled": settings.enabled,
                    # Registered ≠ running: `connected` used to be `platform in self._adapters`,
                    # which was true even when connect() failed.
                    "registered": platform in self._adapters,
                    "connected": platform in self._live,
                    "error": self._listen_errors.get(platform),
                    "allow_all": settings.allow_all,
                    "allowed_users": len(settings.allowed_users),
                }
            )
        return out
