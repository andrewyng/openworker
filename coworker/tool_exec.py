"""Durable tool-execution records — the crash window between a side-effecting tool call
and its conversation result (#443).

A turn approves a call, the adapter applies the side effect (an email goes out), and the
process dies before `_record_result` checkpoints the `role="tool"` message. Durable
resume then sees an unanswered call, finds the already-resolved approval, and runs the
adapter again: at-least-once delivery where the approval model promises once.

These records close that window. The engine writes `dispatched` immediately before the
adapter runs and `succeeded` (with the result snapshot) immediately after it returns —
both BEFORE the conversation result exists. On resume a `succeeded` record reconstructs
the tool message from the snapshot instead of calling the adapter; a `dispatched` record
with no completion means the outcome is unknown, and an unknown side effect is never
silently replayed.

Keyed by `(session_id, tool_call_id)` — internal identity, never a model-visible
argument (issue #443 invariant 4). One JSON file beside `inbox.json`, atomic
tmp-and-replace, thread-safe: `_execute_sync` writes from worker threads.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Cap the file so a long-lived install cannot grow it without bound. Records are tiny
# (state + tool name + a bounded result snapshot); the oldest keys evict first.
_MAX_RECORDS = 2000


class ToolExecutionStore:
    """`tool_executions.json` — durable per-call execution state for side-effecting tools."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        if self.path and self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                raw = saved.get("records") or {}
                self._records = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
            except Exception:  # noqa: BLE001 - a corrupt file must not brick startup
                self._records = {}

    @staticmethod
    def _key(session_id: str, tool_call_id: str) -> str:
        return f"{session_id}:{tool_call_id}"

    def _save(self) -> None:  # caller holds the lock
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"records": self._records}, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def _put(self, session_id: str, tool_call_id: str, record: dict[str, Any]) -> None:
        key = self._key(session_id, tool_call_id)
        with self._lock:
            self._records[key] = record
            while len(self._records) > _MAX_RECORDS:
                self._records.pop(next(iter(self._records)))
            try:
                self._save()
            except Exception:  # noqa: BLE001 - best-effort durability, never fails a call
                pass

    def dispatched(self, session_id: str, tool_call_id: str, tool: str) -> None:
        """The adapter is about to run — written BEFORE it is invoked."""
        self._put(
            session_id,
            tool_call_id,
            {"state": "dispatched", "tool": tool, "ts": time.time()},
        )

    def succeeded(
        self, session_id: str, tool_call_id: str, tool: str, status: str, result: Any
    ) -> None:
        """The adapter returned — written immediately after, before the conversation
        result is appended. `succeeded` names the issue's state model: the CALL
        completed; `status` carries whether the tool itself was ok."""
        self._put(
            session_id,
            tool_call_id,
            {
                "state": "succeeded",
                "tool": tool,
                "status": status,
                "result": result,
                "ts": time.time(),
            },
        )

    def get(self, session_id: str, tool_call_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            rec = self._records.get(self._key(session_id, tool_call_id))
            return dict(rec) if rec is not None else None
