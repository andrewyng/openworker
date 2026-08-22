"""Brain tools — `brain_recall` and `brain_note`.

The read path an agent actually calls. Without these the brain is a directory nobody opens:
the scheduled jobs would keep writing threads and every session would keep starting cold.

`brain_recall` answers "what do I already know about this?" from the durable threads first and
the dated corpus second. `brain_note` is the write side — one entry appended to one thread,
with the state line updated when the situation has changed. Both are deliberately small: an
agent that has to think about a schema will not use them.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

from ..brain.recall import recall as do_recall
from ..brain.threads import Thread, brain_dir, load, load_all, save, slugify

_RECALL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "brain_recall",
        "description": (
            "Search everything this machine has recorded before — durable subject threads (what "
            "is true NOW plus how it got there) and the dated reports the scheduled automations "
            "have written. Call this BEFORE researching or answering anything that might have "
            "come up before: it is how you avoid re-deriving what is already known, and how you "
            "learn that a past conclusion has since been superseded. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you want to know about — a subject, project, or question.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max threads and max corpus excerpts to return (default 6).",
                },
            },
            "required": ["query"],
        },
    },
}

_NOTE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "brain_note",
        "description": (
            "Record something durable against a subject thread, so a future session recalls it. "
            "Use for findings that will still matter in months — a decision and its reasoning, a "
            "result, a state change. Not for chatter. Pass `now` ONLY when the current state of "
            "the subject has actually changed: it replaces the thread's standing summary, which "
            "is what stops old claims being retrieved as current."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "thread": {
                    "type": "string",
                    "description": (
                        "Thread id or title. An unknown one creates a new thread — check "
                        "brain_recall first so you extend the existing subject instead of "
                        "forking a near-duplicate of it."
                    ),
                },
                "entry": {
                    "type": "string",
                    "description": "One sentence: what happened, with the specifics.",
                },
                "now": {
                    "type": "string",
                    "description": "Optional. The subject's new current state, replacing the old one.",
                },
                "source": {
                    "type": "string",
                    "description": "Optional path or URL the entry came from.",
                },
                "state": {
                    "type": "string",
                    "description": "Optional lifecycle: active, quiet, parked or resolved.",
                },
            },
            "required": ["thread", "entry"],
        },
    },
}


def brain_tools(base: Optional[Path] = None) -> list:
    root = Path(base) if base else None

    def brain_recall(query: str, limit: int = 6) -> dict[str, Any]:
        n = limit if isinstance(limit, int) and 0 < limit <= 25 else 6
        out = do_recall(query, base=root, limit=n).as_dict()
        # `_display` is the engine's GUI sidecar: it is lifted onto the event and the stored
        # message and stripped from every provider feed, so the model never sees it and it costs
        # no tokens. The rail needs it because the live tool_finished event carries only a
        # 300-character preview of the result — enough to show one thread id out of three — while
        # a recall over this corpus returns ~9KB.
        out["_display"] = {"threads": [t["id"] for t in out.get("threads", [])], "mode": "read"}
        return out

    def brain_note(
        thread: str,
        entry: str,
        now: str = "",
        source: str = "",
        state: str = "",
    ) -> dict[str, Any]:
        if not (entry or "").strip():
            return {"error": "entry is required"}
        base_dir = root or brain_dir()
        key = (thread or "").strip()
        if not key:
            return {"error": "thread is required"}

        # Match an existing thread by id or title before creating one: a brain that forks a new
        # subject on every rephrasing loses exactly the continuity it exists to provide.
        tid = slugify(key)
        found = load(tid, base_dir)
        if found is None:
            found = next(
                (t for t in load_all(base_dir) if t.title.strip().lower() == key.lower()), None
            )
        created = found is None
        if found is None:
            if not tid:
                return {"error": f"cannot derive a thread id from {thread!r}"}
            found = Thread(id=tid, title=key)

        found.add(entry, when=date.today().isoformat(), source=source or "")
        if now.strip():
            found.now = now.strip()
        if state.strip().lower() in {"active", "quiet", "parked", "resolved"}:
            found.state = state.strip().lower()
        path = save(found, base_dir)
        return {
            "ok": True,
            "thread": found.id,
            "created": created,
            "state": found.state,
            "entries": len(found.history),
            "path": str(path),
            # The CANONICAL id, which the `thread` argument is not: callers pass an id or a
            # title, and slugifying a title ("OpenScienceLab / openEvolve — Phase 2") produces
            # something that matches no file. A rail keyed on the argument would invent threads.
            "_display": {"threads": [found.id], "mode": "written", "created": created},
        }

    brain_recall.__name__ = "brain_recall"
    brain_recall.__doc__ = _RECALL_SCHEMA["function"]["description"]
    brain_recall.__coworker_schema__ = _RECALL_SCHEMA
    brain_note.__name__ = "brain_note"
    brain_note.__doc__ = _NOTE_SCHEMA["function"]["description"]
    brain_note.__coworker_schema__ = _NOTE_SCHEMA
    return [brain_recall, brain_note]
