"""Read-only, structured specialists used by the supervisor tool.

They deliberately use a fresh TurnEngine in PLAN mode rather than Deep Agents' built-in
filesystem tools, so OpenWork's root and permission boundaries remain authoritative.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..events import EventType
from ..tools.subagent import build_explorer_engine
from .store import RuntimeStore


@dataclass(frozen=True)
class Specialist:
    id: str
    title: str
    instructions: str


class SpecialistRegistry:
    def __init__(self) -> None:
        self._items = {
            "researcher": Specialist("researcher", "Researcher", "Find evidence and cite files."),
            "reviewer": Specialist("reviewer", "Reviewer", "Identify correctness, security, and regression risks."),
            "planner": Specialist("planner", "Planner", "Break the task into safe, ordered implementation steps."),
        }

    def get(self, name: str) -> Specialist | None:
        return self._items.get(name)

    def names(self) -> list[str]:
        return sorted(self._items)


class DelegationBroker:
    def __init__(self, store: RuntimeStore, emit: Callable[[str, str, dict[str, Any]], None] | None = None) -> None:
        self.store = store
        self.registry = SpecialistRegistry()
        self.emit = emit
        self._active: dict[str, Any] = {}
        self._lock = threading.RLock()

    def _update(self, record: dict[str, Any], event: str) -> None:
        self.store.save_delegation(record)
        if self.emit:
            self.emit(record["session_id"], event, {"delegation": record})

    def run(
        self, *, session_id: str, workspace: str | Path, provider: Any, model: str,
        task: str, specialist: str = "researcher", model_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec = self.registry.get(specialist)
        if spec is None:
            return {"error": f"Unknown specialist '{specialist}'. Available: {', '.join(self.registry.names())}"}
        delegation_id = uuid.uuid4().hex
        with self._lock:
            if len(self._active) >= 2:
                return {"error": "At most two read-only specialists may run concurrently."}
            self._active[delegation_id] = None  # reserve the bounded concurrency slot
        record: dict[str, Any] = {
            "id": delegation_id, "session_id": session_id, "specialist": spec.id,
            "title": spec.title, "task": task, "status": "running", "started_at": time.time(),
            "tools": ["read_file", "grep", "git_status", "git_diff", "git_log"],
        }
        self._update(record, "delegation_started")
        engine = build_explorer_engine(
            workspace=workspace, provider=provider, model=model, model_settings=model_settings,
        )
        engine.messages[0]["content"] += f"\n\nSpecialist role: {spec.instructions}"
        with self._lock:
            self._active[delegation_id] = engine

        async def drive() -> tuple[str, str]:
            report, status = "", "unknown"
            async for event in engine.run(task):
                if event.type is EventType.ASSISTANT_MESSAGE and event.data.get("text"):
                    report = event.data["text"]
                    record["progress"] = "drafting report"
                    self._update(record, "delegation_progress")
                elif event.type is EventType.TURN_END:
                    status = event.data.get("status", status)
                elif event.type is EventType.ERROR:
                    status = "failed"
                    record["error"] = event.data.get("error", "specialist failed")
            return report, status

        try:
            report, status = asyncio.run(drive())
        finally:
            with self._lock:
                self._active.pop(delegation_id, None)
        record["finished_at"] = time.time()
        if status == "completed" and report:
            record.update({"status": "completed", "result": {
                "summary": report, "findings": [report], "evidence_refs": [],
                "confidence": "medium", "risks": [], "recommended_next_step": "Review the evidence and continue.",
            }})
            self._update(record, "delegation_completed")
            return record["result"] | {"delegation_id": delegation_id}
        record["status"] = "cancelled" if status == "interrupted" else "failed"
        self._update(record, "delegation_failed")
        return {"delegation_id": delegation_id, "error": record.get("error") or f"specialist stopped ({status})"}

    def steer(self, session_id: str, delegation_id: str, text: str) -> dict[str, Any]:
        record = self.store.get_delegation(delegation_id)
        if not record or record.get("session_id") != session_id:
            return {"ok": False, "error": "Delegation not found."}
        with self._lock:
            engine = self._active.get(delegation_id)
        if engine is None:
            return {"ok": False, "error": "Delegation is no longer running."}
        engine.queue_steering(text)
        record["steering"] = text
        self._update(record, "delegation_progress")
        return {"ok": True, "delegation": record}

    def cancel(self, session_id: str, delegation_id: str) -> dict[str, Any]:
        record = self.store.get_delegation(delegation_id)
        if not record or record.get("session_id") != session_id:
            return {"ok": False, "error": "Delegation not found."}
        with self._lock:
            engine = self._active.get(delegation_id)
        if engine is None:
            return {"ok": False, "error": "Delegation is no longer running."}
        engine.request_interrupt()
        record["status"] = "cancelling"
        self._update(record, "delegation_progress")
        return {"ok": True, "delegation": record}
