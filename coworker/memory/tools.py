"""Memory tools — the agent's explicit paths into memory.

`remember` saves a new fact; `memory_update` / `memory_forget` revise or retire one by
the [#id] shown in the known-memories block, so corrections replace stale facts instead
of piling up next to them. `memory_read` fetches full bodies by id — the retrieval half
of index mode (MEMORY-SPEC §7); registered always, harmless in full mode.

`on_saved` is the save-notice hook (spec §5.1): the manager passes a callback that pushes
a memory_saved event to the session's surface so it can render "I'll remember that — …
[Undo]" inline in the transcript. It fires for `memory_update` too — the
update-don't-duplicate rule means many saves arrive as edits to an existing memory, and
those were invisible (owner-hit 2026-07-28) — carrying the previous text so Undo can put
it back. Failures in the callback never fail the write.
"""

from __future__ import annotations

from typing import Callable, Optional

import aisuite as ai

from .base import MemoryItem, MemoryStore, Scope

_SCOPES = {s.value for s in Scope}

_META = dict(category="memory", risk_level="low", capabilities=["remember"])


def memory_tools(
    store: MemoryStore,
    *,
    workspace: Optional[str],
    on_saved: Optional[Callable[[MemoryItem, Optional[str]], None]] = None,
    saving_enabled: Optional[Callable[[], bool]] = None,
) -> list:
    """The agent's memory tools.

    `saving_enabled` is a LIVE callable checked on each write, so the Settings switch
    applies to conversations already running — in BOTH directions (owner-hit
    2026-07-28: off kept saving, then on kept refusing). The registry is fixed at
    build, so the write tools are always registered and refuse when saving is off;
    `memory_read` never gates (off = stop learning, not amnesia).
    """

    def _saving_off() -> bool:
        return saving_enabled is not None and not saving_enabled()

    _OFF_ERROR = (
        "Saving memories is turned off in the user's Settings (they can turn it back "
        "on in Settings ▸ Memory). Nothing was saved — tell the user plainly instead "
        "of implying you remembered it."
    )

    def _announce(item: MemoryItem, previous: Optional[str]) -> None:
        """Surface the write to the user (§5.1). Best-effort: the notice is never worth
        failing a write that already succeeded."""
        if on_saved is None:
            return
        try:
            on_saved(item, previous)
        except Exception:
            pass
    def remember(content: str, summary: str = "", scope: str = "workspace") -> dict:
        """Save a durable memory (a fact or preference) to recall in future sessions.
        Check the known-memories list first: if one already covers this, use
        memory_update instead of saving a near-duplicate.

        Args:
            content (str): The thing to remember, with the why.
            summary (str): One-line gist (15 words max) shown in compact listings.
            scope (str): "global" (facts about the user — applies everywhere) or
                "workspace" (facts about this project only).
        """
        if _saving_off():
            return {"saved": False, "error": _OFF_ERROR}
        chosen = Scope(scope) if scope in _SCOPES else Scope.WORKSPACE
        if chosen is Scope.SESSION:  # dead scope (spec §3): never save to it
            chosen = Scope.WORKSPACE
        item = store.add(
            content,
            scope=chosen,
            summary=summary.strip() or None,
            workspace=workspace if chosen is Scope.WORKSPACE else None,
        )
        _announce(item, None)
        return {"id": item.id, "scope": item.scope.value, "saved": True}

    def memory_read(memory_ids: list[int]) -> dict:
        """Read the full content of memories by id (use when the known-memories list
        shows only a one-line summary and you need the details before acting).

        Args:
            memory_ids (list[int]): The [#id]s to fetch.
        """
        found, missing = [], []
        for mid in memory_ids:
            item = store.get(int(mid))
            if item is None:
                missing.append(int(mid))
            else:
                found.append(
                    {"id": item.id, "scope": item.scope.value, "content": item.content}
                )
        result: dict = {"memories": found}
        if missing:
            result["missing"] = missing
        return result

    def memory_update(memory_id: int, content: str, summary: str = "") -> dict:
        """Rewrite an existing memory with corrected or refined content.

        Args:
            memory_id (int): The memory's id, from the [#id] in the known-memories list.
            content (str): The full corrected memory text (replaces the old text).
            summary (str): Corrected one-line gist (15 words max).
        """
        if _saving_off():
            return {"updated": False, "error": _OFF_ERROR}
        # Captured BEFORE the write so the user's Undo can restore the old wording.
        existing = store.get(memory_id)
        previous = existing.content if existing is not None else None
        item = store.update(memory_id, content, summary=summary.strip() or None)
        if item is None:
            return {"updated": False, "error": f"no memory with id {memory_id}"}
        _announce(item, previous)
        return {"updated": True, "id": item.id}

    def memory_forget(memory_id: int) -> dict:
        """Delete a memory that turned out to be wrong or is no longer true.

        Args:
            memory_id (int): The memory's id, from the [#id] in the known-memories list.
        """
        if _saving_off():
            return {"deleted": False, "error": _OFF_ERROR}
        if store.delete(memory_id):
            return {"deleted": True, "id": memory_id}
        return {"deleted": False, "error": f"no memory with id {memory_id}"}

    return [
        ai.tool(fn, metadata=ai.ToolMetadata(**_META))
        for fn in (remember, memory_read, memory_update, memory_forget)
    ]
