"""Interactive prompts over messaging — buttons instead of free-text replies.

When an Inbox item is mirrored to a channel, discrete choices (approve/deny, an ask_user option)
render as **buttons**. The item id rides in each button's value, so a click resolves the exact
item — no `[ow:id]`-in-reply fragility, no thread tracking. Free-text answers aren't offered over
messaging (the user opens the app for those) — but every question still gets a Cancel button so
the user can decline without opening the app.

Provider-agnostic: a `Button` is `(label, value)`; each adapter renders it natively (Slack Block
Kit, Telegram inline keyboard, …). The value is opaque to the adapter — `encode`/`decode` here own
its meaning: `(item_id, resolution)`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from .inbox import KIND_APPROVAL, KIND_QUESTION

# Reserved Inbox resolution for declining an ask_user prompt. Not a user-visible option label —
# the UI/Slack button says "Cancel", and question_answer() maps this to an empty answer + error
# so the agent doesn't treat the word "cancelled" as a real reply (and so it can't collide with
# an option the model offered).
QUESTION_CANCELLED = "__cancelled__"
QUESTION_INTERRUPTED = "__interrupted__"


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


def question_answer(resolution: str) -> dict[str, Any]:
    """Map an Inbox question resolution to the ask_user tool result the engine expects."""
    if resolution == QUESTION_CANCELLED:
        return {"answer": "", "error": "cancelled by user"}
    if resolution == QUESTION_INTERRUPTED:
        return {"answer": "", "error": "interrupted by user"}
    return {"answer": resolution or ""}


def buttons_for(item) -> list[Button]:
    """The discrete-choice buttons for an Inbox item, or [] if it has none (notification, …) —
    the caller then sends plain text with an "open the app" hint. Questions always include
    Cancel so the user can decline without typing an answer."""
    if item.kind == KIND_APPROVAL:
        return [
            Button("Approve", encode(item.id, "allow")),
            Button("Deny", encode(item.id, "deny")),
        ]
    if item.kind == KIND_QUESTION:
        opts = list(getattr(item, "options", None) or [])
        # Option buttons first; resolution IS the chosen option text (what the agent gets).
        # Cancel last — HIG secondary action, reserved sentinel so it never looks like an answer.
        return [Button(opt, encode(item.id, opt)) for opt in opts] + [
            Button("Cancel", encode(item.id, QUESTION_CANCELLED))
        ]
    return []
