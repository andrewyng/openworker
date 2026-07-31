"""Matrix emoji reactions for Inbox prompts — pending registry + emoji maps."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from ..interactions import Button, encode
from ..inbox import KIND_APPROVAL, KIND_QUESTION

APPROVAL_EMOJI: dict[str, str] = {
    "✅": "allow",
    "♾️": "always",
    "❌": "deny",
}

NUMBER_EMOJI = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")


@dataclass
class PendingReaction:
    room_id: str
    prompt_event_id: str
    emoji_map: dict[str, str]  # emoji -> encoded value
    allowed_reactor: Optional[str] = None


class PendingReactionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], PendingReaction] = {}

    def register(self, pending: PendingReaction) -> None:
        key = (pending.room_id, pending.prompt_event_id)
        with self._lock:
            self._pending[key] = pending

    def lookup(
        self, room_id: str, prompt_event_id: str
    ) -> Optional[PendingReaction]:
        with self._lock:
            return self._pending.get((room_id, prompt_event_id))

    def pop(self, room_id: str, prompt_event_id: str) -> Optional[PendingReaction]:
        key = (room_id, prompt_event_id)
        with self._lock:
            return self._pending.pop(key, None)

    def resolve_emoji(
        self, room_id: str, relates_to_event_id: str, emoji: str
    ) -> Optional[tuple[str, PendingReaction]]:
        pending = self.lookup(room_id, relates_to_event_id)
        if pending is None:
            return None
        value = pending.emoji_map.get(emoji)
        if value is None:
            return None
        return value, pending


def reactions_for(item) -> dict[str, str]:
    """Build emoji map for an Inbox item mirrored to Matrix."""
    emoji_map: dict[str, str] = {}
    if item.kind == KIND_APPROVAL:
        for emoji, resolution in APPROVAL_EMOJI.items():
            emoji_map[emoji] = encode(item.id, resolution)
    elif item.kind == KIND_QUESTION and getattr(item, "options", None):
        for i, opt in enumerate(item.options):
            if i >= len(NUMBER_EMOJI):
                break
            emoji_map[NUMBER_EMOJI[i]] = encode(item.id, opt)
    return emoji_map


def reactions_for_buttons(buttons: list[Button]) -> dict[str, str]:
    """Map emoji keys to button values for interactive Matrix prompts."""
    out: dict[str, str] = {}
    for i, btn in enumerate(buttons):
        if btn.label == "Approve":
            out["✅"] = btn.value
            # Hermes-style approve-always (♾️) — same item id, resolution "always".
            try:
                import json

                d = json.loads(btn.value)
                if isinstance(d, dict) and d.get("id"):
                    out["♾️"] = encode(str(d["id"]), "always")
            except Exception:
                pass
        elif btn.label == "Deny":
            out["❌"] = btn.value
        elif i < len(NUMBER_EMOJI):
            out[NUMBER_EMOJI[i]] = btn.value
    return out
