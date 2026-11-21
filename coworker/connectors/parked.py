"""Parked unauthorized messages — what an unallowed sender said, kept instead of lost.

The gateway drops inbound messages from senders not on the allow-list (closed by default).
Dropping silently made the first-contact flow clumsy: the sender had to message once just to
appear under "Recent senders", get allowed, then message AGAIN. Parking the dropped message
lets the owner see it on the connector page and resolve it in one step — dismiss it, allow
the sender, or allow AND deliver the original message (no re-send needed).

JSON-backed and capped like UnroutedStore. This IS a queue (unlike Unrouted): allow-and-deliver
re-injects the parked message through the normal inbound path.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ParkedMessage:
    platform: str  # "slack" | "telegram" | …
    chat_id: str  # channel/DM id, e.g. "C0BD7KZ1AH5"
    user_id: str  # sender id, e.g. "U07JK68S4BH"
    text: str
    chat_name: Optional[str] = None  # resolved display name (falls back to chat_id)
    user_name: Optional[str] = None  # resolved display name (falls back to user_id)
    chat_type: str = "channel"  # "channel" | "group" | "dm"
    thread_id: Optional[str] = None
    team_id: Optional[str] = None  # workspace id (managed relay); None for socket mode
    # A direct @-mention of the bot (drives the §31 mention router on re-delivery).
    mentions_me: bool = False
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class ParkedStore:
    def __init__(self, path: Optional[str | Path] = None, *, cap: int = 100) -> None:
        self.path = Path(path) if path else None
        self._cap = cap
        self._lock = threading.Lock()
        self._items: list[ParkedMessage] = []
        self._load()

    def _quarantine(self) -> Optional[Path]:
        """Move an unreadable file aside so the next save cannot overwrite the only
        copy of it. Returns the path it moved to, or None if the move failed."""
        if not self.path:
            return None
        spare = self.path.with_suffix(self.path.suffix + ".corrupt")
        try:
            self.path.replace(spare)
        except OSError:
            return None
        return spare

    def _refuse_file(self, reason: str, items: list[ParkedMessage]) -> list[ParkedMessage]:
        """Keep what could be read, move the file aside, and say so out loud.

        Parked messages are what an unauthorized sender said, kept so the owner can
        allow-and-deliver rather than asking them to re-send — dropping one is NOT
        neutral. So the bytes are moved aside (never deleted, never overwritten by a
        later save) and the reason is logged, the way the risk-override and
        connection stores do it.
        """
        message = f"{self.path}: {reason}; kept {len(items)} parked message(s)"
        spare = self._quarantine()
        if spare is not None:
            message += f" (original kept at {spare})"
        logger.warning(message)
        return items

    def _load(self) -> None:
        if not (self.path and self.path.is_file()):
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as e:
            # A torn file must never block startup: SessionManager builds this store.
            self._items = self._refuse_file(f"unreadable ({type(e).__name__})", [])
            return
        if not isinstance(data, dict):
            # Valid JSON of the wrong shape (`[]`, `null`, a string): `data.get` would
            # raise AttributeError out of the constructor the same way.
            self._items = self._refuse_file("not a JSON object", [])
            return
        raw_items = data.get("items")
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            self._items = self._refuse_file("'items' is not a JSON array", [])
            return

        # A single record this build cannot represent (an unknown field a later
        # build wrote, a required one missing) used to abort the whole comprehension
        # and take every other parked message with it. Skip just that record.
        items: list[ParkedMessage] = []
        dropped: list[str] = []
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                dropped.append(f"item {index}: not a JSON object")
                continue
            try:
                items.append(ParkedMessage(**raw))
            except (TypeError, ValueError) as e:
                dropped.append(f"item {index}: {type(e).__name__}")
        if dropped:
            detail = "; ".join(dropped[:3])
            if len(dropped) > 3:
                detail += f"; … and {len(dropped) - 3} more"
            self._items = self._refuse_file(
                f"dropped {len(dropped)} of {len(raw_items)} parked message(s) ({detail})",
                items,
            )
            return
        self._items = items

    def _save(self) -> None:
        if not self.path:
            return
        # Write a sibling .tmp and rename it over the real file, so a crash mid-write
        # leaves the previous complete file instead of a truncated one — the same
        # shape mentions.py uses.
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps({"items": [asdict(i) for i in self._items]}, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            # Persistence is best-effort; memory stays authoritative for this process.
            logger.warning("could not save %s: %s", self.path, exc)
            try:
                temporary.unlink()
            except OSError:
                pass

    def park(self, **fields) -> ParkedMessage:
        item = ParkedMessage(**fields)
        with self._lock:
            self._items.append(item)
            if len(self._items) > self._cap:
                self._items = self._items[-self._cap :]
            self._save()
        return item

    def list(self, platform: Optional[str] = None) -> list[dict]:
        with self._lock:
            return [
                asdict(i)
                for i in reversed(self._items)  # newest first
                if platform is None or i.platform == platform
            ]

    def pop(self, item_id: str) -> Optional[ParkedMessage]:
        with self._lock:
            for i, item in enumerate(self._items):
                if item.id == item_id:
                    del self._items[i]
                    self._save()
                    return item
        return None
