"""Interactive prompts over messaging — buttons instead of free-text replies.

When an Inbox item is mirrored to a channel, discrete choices (approve/deny, an ask_user option)
render as **buttons**. The item id rides in each button's value, so a click resolves the exact
item — no `[ow:id]`-in-reply fragility, no thread tracking. Free-text answers aren't offered over
messaging (the user opens the app for those).

Provider-agnostic: a `Button` is `(label, value)`; each adapter renders it natively (Slack Block
Kit, Telegram inline keyboard, …). The value is opaque to the adapter — `encode`/`decode` here own
its meaning: `(item_id, resolution)`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .inbox import KIND_APPROVAL, KIND_QUESTION
from .tools.ask import option_label


@dataclass
class Button:
    label: str
    value: str  # opaque to the adapter; encode()/decode() own its meaning


def encode(item_id: str, resolution: str) -> str:
    return json.dumps({"id": item_id, "r": resolution})


def decode(value: str) -> Optional[tuple[str, str]]:
    """`(item_id, resolution)` from a button value, or None if it isn't ours."""
    try:
        d = json.loads(value)
        if isinstance(d, dict) and d.get("id"):
            return str(d["id"]), str(d.get("r", ""))
    except Exception:
        pass
    return None


def buttons_for(item) -> list[Button]:
    """The discrete-choice buttons for an Inbox item, or [] if it has none (free-text question,
    notification, …) — the caller then sends plain text with an "open the app" hint."""
    if item.kind == KIND_APPROVAL:
        return [
            Button("Approve", encode(item.id, "allow")),
            Button("Deny", encode(item.id, "deny")),
        ]
    if item.kind == KIND_QUESTION and getattr(item, "questions", None):
        # Grouped questions (OPE-51): one button row can't answer 2+ questions — send plain text
        # with the open-the-app hint instead.
        return []
    if item.kind == KIND_QUESTION and getattr(item, "options", None):
        # One button per option; the resolution IS the chosen option's label (what the agent
        # gets). Rich {label, description, …} options button as their label.
        return [
            Button(option_label(opt), encode(item.id, option_label(opt)))
            for opt in item.options
        ]
    return []
