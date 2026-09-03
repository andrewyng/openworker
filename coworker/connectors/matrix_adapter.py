"""Matrix inbound adapter — matrix-nio AsyncClient with required E2EE."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from .base import (
    BasePlatformAdapter,
    InteractionEvent,
    MessageEvent,
    MessageType,
    SendResult,
    SessionSource,
)
from .matrix_reactions import (
    PendingReaction,
    PendingReactionStore,
    reactions_for_buttons,
)
from .matrix_settings import MatrixSettings

logger = logging.getLogger("coworker.connectors.matrix")

# Sync bridge for stateless send_message tool (runs in worker threads).
_matrix_adapter: Optional["MatrixAdapter"] = None
_matrix_loop: Optional[asyncio.AbstractEventLoop] = None


def register_matrix_adapter(
    adapter: Optional["MatrixAdapter"],
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
    global _matrix_adapter, _matrix_loop
    _matrix_adapter = adapter
    _matrix_loop = loop


def send_matrix_sync(chat_id: str, text: str, thread_id: Optional[str] = None) -> SendResult:
    """Blocking outbound send via the live adapter (E2EE-aware)."""
    adapter = _matrix_adapter
    loop = _matrix_loop
    if adapter is None or loop is None:
        return SendResult(False, error="matrix adapter not connected")
    future = asyncio.run_coroutine_threadsafe(
        adapter.send(chat_id, text, thread_id=thread_id), loop
    )
    try:
        return future.result(timeout=60)
    except Exception as exc:
        return SendResult(False, error=str(exc))


def send_matrix_file_sync(
    chat_id: str,
    thread_id: Optional[str],
    filename: str,
    data: bytes,
    title: Optional[str] = None,
    comment: Optional[str] = None,
) -> SendResult:
    adapter = _matrix_adapter
    loop = _matrix_loop
    if adapter is None or loop is None:
        return SendResult(False, error="matrix adapter not connected")
    future = asyncio.run_coroutine_threadsafe(
        adapter.send_file_bytes(
            chat_id, data, filename, thread_id=thread_id, title=title, comment=comment
        ),
        loop,
    )
    try:
        return future.result(timeout=120)
    except Exception as exc:
        return SendResult(False, error=str(exc))


def _room_chat_type(room: Any, *, dm_rooms: set[str]) -> str:
    if room.room_id in dm_rooms:
        return "dm"
    if _matrix_room_is_dm(room):
        return "dm"
    return "channel"


def _matrix_room_is_dm(room: Any) -> bool:
    """True for 1:1 direct chats (summary, member count, or unnamed 2-person room)."""
    try:
        if room.member_count == 2:
            return True
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        if getattr(room, "is_group", False) and room.joined_count == 2:
            return True
    except (AttributeError, TypeError, ValueError):
        pass
    return False


def _mentions_bot(text: str, bot_user_id: Optional[str]) -> bool:
    if not bot_user_id or not text:
        return False
    return bot_user_id in text or f"@{bot_user_id.split(':')[0][1:]}" in text


def matrix_event_to_event(
    event: Any,
    *,
    room_id: str,
    bot_user_id: Optional[str],
    chat_type: str = "channel",
    chat_name: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Optional[MessageEvent]:
    """Pure mapper: Matrix m.room.message (text) -> MessageEvent."""
    sender = getattr(event, "sender", None) or (
        event.get("sender") if isinstance(event, dict) else None
    )
    if bot_user_id and sender == bot_user_id:
        return None
    body = getattr(event, "body", None)
    if body is None and isinstance(event, dict):
        body = (event.get("content") or {}).get("body")
    if not body:
        return None
    event_id = getattr(event, "event_id", None) or (
        event.get("event_id") if isinstance(event, dict) else None
    )
    source = SessionSource(
        platform="matrix",
        chat_id=room_id,
        user_id=sender,
        chat_type=chat_type,
        chat_name=chat_name,
        thread_id=thread_id,
    )
    return MessageEvent(
        text=str(body),
        source=source,
        message_id=event_id,
        mentions_me=_mentions_bot(str(body), bot_user_id),
    )


class MatrixAdapter(BasePlatformAdapter):
    platform = "matrix"

    def __init__(
        self,
        settings: MatrixSettings,
        *,
        store_path: Path,
        reaction_store: Optional[PendingReactionStore] = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.store_path = store_path
        self.reaction_store = reaction_store or PendingReactionStore()
        self._client = None
        self._sync_task: Optional[asyncio.Task] = None
        self._closing = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._joined_threads: set[tuple[str, str]] = set()
        self._dm_rooms: set[str] = set()
        self._dm_rooms_path: Optional[Path] = None
        self._lifecycle_event: dict[str, str] = {}  # room_id -> inbound event_id

    def _note_thread(self, room_id: str, thread_id: Optional[str]) -> None:
        if thread_id:
            self._joined_threads.add((room_id, thread_id))

    def _thread_active(self, room_id: str, thread_id: Optional[str]) -> bool:
        if not thread_id:
            return False
        return (room_id, thread_id) in self._joined_threads

    def _load_dm_rooms(self) -> None:
        path = self._dm_rooms_path
        if path is None or not path.is_file():
            return
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._dm_rooms.update(str(r) for r in data)
        except Exception:
            logger.debug("matrix dm_rooms load failed", exc_info=True)

    def _remember_dm_room(self, room_id: str) -> None:
        if room_id in self._dm_rooms:
            return
        self._dm_rooms.add(room_id)
        path = self._dm_rooms_path
        if path is None:
            return
        try:
            import json

            path.write_text(
                json.dumps(sorted(self._dm_rooms), indent=0) + "\n",
                encoding="utf-8",
            )
        except Exception:
            logger.debug("matrix dm_rooms save failed", exc_info=True)

    def _is_dm(self, room: Any) -> bool:
        room_id = room.room_id
        if room_id in self._dm_rooms:
            return True
        if _matrix_room_is_dm(room):
            self._remember_dm_room(room_id)
            return True
        return False

    def _should_dispatch(self, mapped: MessageEvent, room_id: str, *, is_dm: bool) -> bool:
        if is_dm:
            return True
        if room_id in self.settings.free_response_rooms:
            return True
        if mapped.mentions_me:
            return True
        if self._thread_active(room_id, mapped.source.thread_id):
            mapped.mentions_me = True
            return True
        if not self.settings.require_mention:
            return True
        return False

    async def connect(self) -> bool:
        if self.settings.e2ee_mode == "required":
            try:
                import nio.crypto  # noqa: F401
            except ImportError:
                logger.warning(
                    "matrix E2EE requires matrix-nio[e2e] and libolm — "
                    "`pip install coworker[messaging]` and install libolm "
                    "(brew install libolm / apt install libolm-dev)"
                )
                return False

        try:
            from nio import AsyncClient, AsyncClientConfig, InviteMemberEvent, RoomMessageText
            from nio.events import RoomMessage
            from nio.events.room_events import (
                ReactionEvent,
                RoomMessageAudio,
                RoomMessageFile,
                RoomMessageImage,
                RoomMessageVideo,
            )
        except ImportError:
            logger.warning(
                "matrix-nio not installed — `pip install coworker[messaging]`"
            )
            return False

        if not self.settings.homeserver_url or not self.settings.access_token:
            logger.warning("matrix: missing homeserver_url or access_token")
            return False

        self.store_path.mkdir(parents=True, exist_ok=True)
        self._dm_rooms_path = self.store_path / "dm_rooms.json"
        self._load_dm_rooms()
        user_id = self.settings.user_id or ""
        # ponytail: without store_sync_tokens, restart replays full room timelines.
        client_config = AsyncClientConfig(store_sync_tokens=True)
        self._client = AsyncClient(
            self.settings.homeserver_url,
            user_id or "@bot:local",
            store_path=str(self.store_path),
            config=client_config,
        )
        self._client.access_token = self.settings.access_token
        if user_id:
            self._client.user_id = user_id

        try:
            resp = await self._client.whoami()
            if hasattr(resp, "user_id") and resp.user_id:
                self._client.user_id = resp.user_id
            elif isinstance(resp, dict):
                self._client.user_id = resp.get("user_id") or self._client.user_id
            if hasattr(resp, "device_id") and resp.device_id:
                self._client.device_id = resp.device_id
            elif isinstance(resp, dict) and resp.get("device_id"):
                self._client.device_id = resp["device_id"]
        except Exception:
            logger.exception("matrix whoami failed")
            await self._client.close()
            self._client = None
            return False

        if hasattr(self._client, "load_store"):
            try:
                self._client.load_store()
            except Exception:
                logger.exception("matrix crypto store load failed")
                await self._client.close()
                self._client = None
                return False

        if self.settings.e2ee_mode == "required":
            try:
                from .matrix_crypto_bootstrap import (
                    MatrixCryptoBootstrapError,
                    prepare_matrix_e2ee,
                )

                await prepare_matrix_e2ee(self._client, self.settings)
            except MatrixCryptoBootstrapError as exc:
                logger.warning("matrix E2EE bootstrap failed: %s", exc)
                await self._client.close()
                self._client = None
                return False
            except Exception:
                logger.exception("matrix E2EE bootstrap failed")
                await self._client.close()
                self._client = None
                return False

        self._loop = asyncio.get_running_loop()
        register_matrix_adapter(self, self._loop)
        self._closing = False

        async def _dispatch(room, event, *, chat_type: str, thread_id: Optional[str]):
            if not self._allowed_room(room.room_id, event.sender, chat_type=chat_type):
                return
            if self.settings.ignored_user(event.sender):
                return
            mapped = matrix_event_to_event(
                event,
                room_id=room.room_id,
                bot_user_id=self._client.user_id,
                chat_type=chat_type,
                chat_name=getattr(room, "display_name", None),
                thread_id=thread_id,
            )
            if mapped is None:
                return
            is_dm = chat_type == "dm"
            if not self._should_dispatch(mapped, room.room_id, is_dm=is_dm):
                return
            media = await self._media_agent_content(room.room_id, event)
            if media is not None:
                mapped.agent_content = media
                mapped.message_type = MessageType.MEDIA
            if mapped.message_id and self.settings.lifecycle_reactions:
                await self._lifecycle_react(room.room_id, mapped.message_id, "👀")
                self._lifecycle_event[room.room_id] = mapped.message_id
            try:
                await self.handle_message(mapped)
            except Exception:
                if self.settings.lifecycle_reactions and mapped.message_id:
                    await self._lifecycle_react(room.room_id, mapped.message_id, "❌")
                raise

        async def _on_room_message(room, event):
            if self._is_dm(room):
                chat_type = "dm"
            else:
                chat_type = _room_chat_type(room, dm_rooms=self._dm_rooms)
            thread_id = None
            relates = getattr(getattr(event, "content", None), "relates_to", None)
            if relates is not None:
                thread_id = getattr(relates, "event_id", None)
            await _dispatch(room, event, chat_type=chat_type, thread_id=thread_id)

        async def _on_reaction(room, event):
            if not isinstance(event, ReactionEvent):
                return
            await self._handle_reaction(room.room_id, event)

        async def _on_invite(room, event):
            if isinstance(event, InviteMemberEvent):
                try:
                    await self._client.join(room.room_id)
                except Exception:
                    logger.debug("matrix auto-join failed for %s", room.room_id, exc_info=True)

        self._client.add_event_callback(_on_room_message, RoomMessageText)
        for cls in (RoomMessageImage, RoomMessageFile, RoomMessageAudio, RoomMessageVideo):
            self._client.add_event_callback(_on_room_message, cls)
        self._client.add_event_callback(_on_reaction, ReactionEvent)
        self._client.add_event_callback(_on_invite, InviteMemberEvent)

        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("matrix adapter connected as %s", self._client.user_id)
        return True

    async def _sync_loop(self) -> None:
        # ponytail: with a saved sync token, one full_state sync refreshes room
        # summaries/members without replaying timeline (since=token still applies).
        first = True
        while not self._closing and self._client is not None:
            try:
                full_state = first and bool(
                    getattr(self._client, "loaded_sync_token", None)
                    or getattr(self._client, "next_batch", None)
                )
                first = False
                await self._client.sync(timeout=30_000, full_state=full_state)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("matrix sync error — retrying")
                await asyncio.sleep(2)

    def _allowed_room(
        self, room_id: str, sender: Optional[str], *, chat_type: str = "channel"
    ) -> bool:
        allowed_rooms = self.settings.allowed_rooms
        if not allowed_rooms:
            return True
        if chat_type == "dm" or room_id in self._dm_rooms:
            return True
        return room_id in allowed_rooms

    async def _lifecycle_react(self, room_id: str, event_id: str, emoji: str) -> None:
        if self._client is None:
            return
        content = {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": event_id,
                "key": emoji,
            }
        }
        try:
            await self._client.room_send(
                room_id, "m.reaction", content, ignore_unverified_devices=True
            )
        except Exception:
            logger.debug("matrix lifecycle reaction %s failed", emoji, exc_info=True)

    async def _media_agent_content(self, room_id: str, event: Any) -> Any | None:
        """Download mxc media and build multimodal agent content, or None for text-only."""
        content = getattr(event, "content", None) or {}
        if isinstance(content, dict):
            url = content.get("url") or content.get("file", {}).get("url")
            body = content.get("body") or content.get("filename") or "attachment"
        else:
            url = getattr(content, "url", None)
            body = getattr(content, "body", None) or "attachment"
        if not url or not str(url).startswith("mxc://"):
            return None
        if self._client is None:
            return None
        try:
            resp = await self._client.download(url)
            data = getattr(resp, "body", None) or getattr(resp, "content", None)
            if data is None and hasattr(resp, "read"):
                data = resp.read()
            if not data:
                return None
            if len(data) > self.settings.max_media_bytes:
                return None
        except Exception:
            logger.debug("matrix media download failed", exc_info=True)
            return None
        import base64
        from mimetypes import guess_type

        mime, _ = guess_type(str(body))
        mime = mime or "application/octet-stream"
        inbound_dir = self.store_path / "inbound"
        inbound_dir.mkdir(parents=True, exist_ok=True)
        safe_name = str(body).replace("/", "_")[:120] or "attachment"
        path = inbound_dir / safe_name
        path.write_bytes(data)
        tagged = matrix_event_to_event(
            event,
            room_id=room_id,
            bot_user_id=self._client.user_id if self._client else None,
        )
        frame = tagged.tagged_text() if tagged else f"[matrix media: {safe_name}]"
        if mime.startswith("image/"):
            b64 = base64.standard_b64encode(data).decode()
            return [
                {"type": "text", "text": frame},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
        return f"{frame}\n[Attachment saved: {path}]"

    async def _handle_reaction(self, room_id: str, event: Any) -> None:
        relates = getattr(getattr(event, "content", None), "relates_to", None)
        if relates is None:
            return
        prompt_id = getattr(relates, "event_id", None)
        emoji = getattr(relates, "key", None)
        if not prompt_id or not emoji:
            return
        resolved = self.reaction_store.resolve_emoji(room_id, prompt_id, emoji)
        if resolved is None:
            return
        value, pending = resolved
        if (
            self.settings.approval_require_sender
            and pending.allowed_reactor
            and event.sender != pending.allowed_reactor
        ):
            return
        self.reaction_store.pop(room_id, prompt_id)
        await self.handle_interaction(
            InteractionEvent(
                platform="matrix",
                chat_id=room_id,
                message_id=prompt_id,
                value=value,
                user_id=getattr(event, "sender", None),
                interaction_kind="reaction",
                reaction_key=emoji,
            )
        )

    async def disconnect(self) -> None:
        self._closing = True
        register_matrix_adapter(None, None)
        if self._sync_task is not None:
            self._sync_task.cancel()
            self._sync_task = None
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    def _thread_content(self, thread_id: Optional[str]) -> dict:
        if not thread_id:
            return {}
        return {"m.relates_to": {"rel_type": "m.thread", "event_id": thread_id}}

    async def send(
        self, chat_id: str, text: str, *, thread_id: Optional[str] = None
    ) -> SendResult:
        if self._client is None:
            return SendResult(False, error="matrix not connected")
        content = {"msgtype": "m.text", "body": text[: self.settings.max_message_length]}
        content.update(self._thread_content(thread_id))
        try:
            resp = await self._client.room_send(
                chat_id, "m.room.message", content, ignore_unverified_devices=True
            )
            event_id = getattr(resp, "event_id", None)
            self._note_thread(chat_id, thread_id)
            if self.settings.auto_thread and not thread_id and event_id:
                self._note_thread(chat_id, event_id)
            pending = self._lifecycle_event.pop(chat_id, None)
            if pending and self.settings.lifecycle_reactions:
                await self._lifecycle_react(chat_id, pending, "✅")
            return SendResult(True, message_id=event_id)
        except Exception as exc:
            return SendResult(False, error=str(exc))

    async def send_interactive(
        self, chat_id: str, text: str, buttons, *, thread_id: Optional[str] = None
    ) -> SendResult:
        hint = text
        emoji_map = reactions_for_buttons(buttons)
        if emoji_map:
            keys = " ".join(emoji_map.keys())
            hint = f"{text}\n\nReact: {keys}"
        result = await self.send(chat_id, hint, thread_id=thread_id)
        if not result.ok or not result.message_id:
            return result
        self.reaction_store.register(
            PendingReaction(
                room_id=chat_id,
                prompt_event_id=result.message_id,
                emoji_map=emoji_map,
            )
        )
        return result

    async def send_file_bytes(
        self,
        chat_id: str,
        data: bytes,
        filename: str,
        *,
        thread_id: Optional[str] = None,
        title: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> SendResult:
        if self._client is None:
            return SendResult(False, error="matrix not connected")
        if len(data) > self.settings.max_media_bytes:
            return SendResult(False, error="file exceeds max_media_bytes")
        try:
            from mimetypes import guess_type

            mime, _ = guess_type(filename)
            mime = mime or "application/octet-stream"
            upload = await self._client.upload(data, mime, filename=filename)
            if hasattr(upload, "content_uri"):
                mxc = upload.content_uri
            else:
                mxc = getattr(upload, "content_uri", None) or str(upload)
            msgtype = "m.image" if mime.startswith("image/") else "m.file"
            content: dict[str, Any] = {
                "msgtype": msgtype,
                "body": title or filename,
                "url": mxc,
                "filename": filename,
            }
            if comment:
                content["body"] = f"{comment}\n{content['body']}"
            content.update(self._thread_content(thread_id))
            resp = await self._client.room_send(
                chat_id, "m.room.message", content, ignore_unverified_devices=True
            )
            return SendResult(True, message_id=getattr(resp, "event_id", None))
        except Exception as exc:
            return SendResult(False, error=str(exc))

    async def update_message(self, chat_id: str, message_id: str, text: str) -> None:
        if self._client is None or not message_id:
            return
        try:
            await self._client.room_redact(chat_id, message_id, reason=text[:50])
            await self.send(chat_id, text)
        except Exception:
            logger.debug("matrix update_message failed", exc_info=True)
