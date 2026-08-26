"""Todo / plan tool — a structured task list the agent maintains and the UI renders.

Most of the "organized agent" feel in interactive work. Low risk, auto-approved. The list
is held in a `TodoList` the surface can read; `todo_write` replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import aisuite as ai

_STATUSES = {"pending", "in_progress", "done"}

# Explicit schema — the array-of-objects shape can't be auto-generated reliably, and
# providers reject a bare `list` annotation. Registered via `__coworker_schema__`.
#
# The parameter is `todos`, NOT `items`: a top-level argument key named "items" shadows
# minijinja's `.items()` map method in at least one hosted chat template (Together's
# GLM-5.2, 2026-07-21 — "object is not callable"), 400-ing every request that replays
# the call. Any key name that isn't a minijinja map method is safe; never rename back.
_TODO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "todo_write",
        "description": "Replace the task list. Provide the full list of todos each call.",
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done"],
                            },
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["todos"],
        },
    },
}


@dataclass
class TodoList:
    items: list[dict] = field(default_factory=list)


def todo_tools(todo: TodoList) -> list:
    def todo_write(todos: list = None, items: list = None) -> dict:
        """Replace the task list. Each todo is an object with `content` and a `status`
        of pending, in_progress, or done."""
        # `items` stays accepted (models that free-style the old name; queued replays).
        normalized = []
        for entry in (todos if todos is not None else items) or []:
            if isinstance(entry, dict):
                status = entry.get("status", "pending")
                if status == "completed":  # common model alias for our "done"
                    status = "done"
                normalized.append(
                    {
                        "content": str(entry.get("content", "")),
                        "status": status if status in _STATUSES else "pending",
                    }
                )
            else:
                normalized.append({"content": str(entry), "status": "pending"})
        todo.items = normalized
        return {"count": len(normalized), "todos": normalized}

    wrapped = ai.tool(
        todo_write,
        metadata=ai.ToolMetadata(
            category="planning",
            risk_level="low",
            capabilities=["todo"],
        ),
    )
    wrapped.__coworker_schema__ = _TODO_SCHEMA
    return [wrapped]


# How far a run may travel on a list nobody has rewritten before the model is asked to
# refresh it. Measured against the failure below: eight tool calls is more than any single
# plan item took in the runs this was sized on, and well short of the seventy-six that
# produced it.
_STALE_AFTER = 8

_STALE_NOTICE = (
    "Your task list is out of date: you have run several tools since you last wrote it and "
    "it still shows unfinished items. Call todo_write now with the FULL list — mark "
    "everything you have finished as done, and exactly one item as in_progress. The user's "
    "progress panel renders the last list you wrote and nothing else, so a list you leave "
    "behind is the run reporting itself as stalled."
)


def stale_plan_notice(todo: Optional[TodoList], messages: list[dict]) -> str:
    """The per-turn nudge to rewrite a plan the run has moved past — "" when it is current.

    Reaches the model through the engine's ephemeral `<system-context>` block (agent.py), so
    it is never persisted, never replayed into the transcript, and disappears on the round
    trip after the list is rewritten.

    The failure it exists for: a five-item plan written at the top of a 101-call turn and
    rewritten once, at call 24. The remaining 76 calls finished items 2-5 and committed them,
    and the model never said so. The Progress panel can only render the last list it was
    given, so it showed "item 2 in progress, 3-5 pending" for the rest of the run and after
    it ended. Nothing in the loop had ever asked for an update: the persona instructions say
    it once, at a point the model passed seventy calls ago.

    Deliberately count-free. The block it lands in is appended to the last user message, so a
    text that changed on every round trip would invalidate the prompt cache on every one;
    this one changes twice per plan, when it appears and when it clears.
    """
    if todo is None or not todo.items:
        return ""  # no plan at all — a persona without the tool, or a run that never planned
    if all(item.get("status") == "done" for item in todo.items):
        return ""  # nothing left to move; a finished list is not a stale one
    calls = 0
    for message in reversed(messages):
        batch = message.get("tool_calls") or []
        if any((c.get("function") or {}).get("name") == "todo_write" for c in batch):
            # Calls the model issued in the SAME batch as the write did not age the list — it
            # emitted them together, plan first and work after. Counting them let one wide
            # parallel batch trip the threshold on the round trip right after a fresh rewrite,
            # telling the model the list it had just written was out of date.
            return _STALE_NOTICE if calls >= _STALE_AFTER else ""
        calls += len(batch)
    # No todo_write left in the visible history — compaction dropped it, or this is a resumed
    # thread. The list still came from somewhere, so age it by everything that has run since.
    return _STALE_NOTICE if calls >= _STALE_AFTER else ""
