"""Persistent, local-only workspace for skill drafts and their evaluations.

Drafts are deliberately separate from installed skills: the only install path remains
``save_skill`` and its standard human approval card.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai

from ..secrets import state_dir
from .store import _write_skill_md, validate_name


def _now() -> float:
    return time.time()


def _safe_cases(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("Provide between zero and twenty evaluation cases.")
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError("Each evaluation case must be an object.")
        prompt = str(raw.get("prompt", "")).strip()
        if not prompt or len(prompt) > 12000:
            raise ValueError("Each evaluation needs a prompt up to 12000 characters.")
        assertions = raw.get("assertions", [])
        if not isinstance(assertions, list) or len(assertions) > 20:
            raise ValueError("An evaluation may have at most twenty assertions.")
        out.append(
            {
                "id": str(raw.get("id") or f"case-{index + 1}"),
                "name": str(raw.get("name") or f"Test {index + 1}")[:120],
                "prompt": prompt,
                "expected_output": str(raw.get("expected_output", ""))[:4000],
                "assertions": assertions,
            }
        )
    return out


class SkillDraftStore:
    """Folder-backed draft store, intentionally easy to inspect and back up."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else state_dir() / "skill-drafts"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _folder(self, draft_id: str) -> Path:
        if not draft_id or not all(c.isalnum() or c in "-_" for c in draft_id):
            raise ValueError("Invalid draft id.")
        return self.root / draft_id

    def _manifest_path(self, draft_id: str) -> Path:
        return self._folder(draft_id) / "draft.json"

    def _read(self, draft_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._manifest_path(draft_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Unknown skill draft.") from exc

    def _write(self, data: dict[str, Any]) -> None:
        path = self._manifest_path(str(data["id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def stage(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        evals: Any,
        session_id: str,
        locale: str,
        draft_id: str = "",
    ) -> dict[str, Any]:
        name = validate_name(name)
        description = description.strip()
        if not description:
            raise ValueError("A one-line skill description is required.")
        if not instructions.strip():
            raise ValueError("Skill instructions are required.")
        cases = _safe_cases(evals)
        with self._lock:
            if draft_id:
                data = self._read(draft_id)
                if data.get("session_id") != session_id:
                    raise ValueError("This draft belongs to another conversation.")
            else:
                draft_id = uuid.uuid4().hex
                data = {
                    "id": draft_id,
                    "created_at": _now(),
                    "iterations": [],
                    "reviews": [],
                }
            data.update(
                {
                    "session_id": session_id,
                    "name": name,
                    "description": description,
                    "instructions": instructions,
                    "evals": cases,
                    "locale": locale,
                    "updated_at": _now(),
                    "status": "draft",
                }
            )
            folder = self._folder(draft_id) / "candidate"
            _write_skill_md(folder, name=name, description=description, instructions=instructions)
            self._write(data)
        return self.get(draft_id)

    def get(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            return self._read(draft_id)

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in self.root.glob("*/draft.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("session_id") == session_id:
                out.append(data)
        return sorted(out, key=lambda d: d.get("updated_at", 0), reverse=True)

    def append_iteration(self, draft_id: str, iteration: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read(draft_id)
            data.setdefault("iterations", []).append(iteration)
            data["updated_at"] = _now()
            data["status"] = "evaluated"
            self._write(data)
            return data

    def add_review(self, draft_id: str, reviews: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(reviews, list):
            raise ValueError("Reviews must be a list.")
        cleaned = [
            {
                "case_id": str(item.get("case_id", ""))[:100],
                "variant": str(item.get("variant", "with_skill"))[:30],
                "feedback": str(item.get("feedback", ""))[:8000],
                "at": _now(),
            }
            for item in reviews
            if isinstance(item, dict)
        ]
        with self._lock:
            data = self._read(draft_id)
            data.setdefault("reviews", []).extend(cleaned)
            data["updated_at"] = _now()
            self._write(data)
            return data

    def prepare_trigger_optimization(
        self, draft_id: str, examples: Any, *, max_iterations: int = 5
    ) -> dict[str, Any]:
        """Persist a reviewable, bounded trigger-optimization request.

        The examples are deliberately data, not executable prompt logic.  A later
        model pass may improve the description only after the author has reviewed
        this positive/negative set.
        """
        if not isinstance(examples, list) or not examples or len(examples) > 30:
            raise ValueError("Provide one to thirty trigger examples.")
        if not 1 <= max_iterations <= 5:
            raise ValueError("Trigger optimization allows one to five iterations.")
        cleaned: list[dict[str, Any]] = []
        for item in examples:
            if not isinstance(item, dict):
                raise ValueError("Each trigger example must be an object.")
            prompt = str(item.get("prompt", "")).strip()
            if not prompt or len(prompt) > 4000 or not isinstance(item.get("should_trigger"), bool):
                raise ValueError("Each trigger example needs prompt and should_trigger.")
            cleaned.append({"prompt": prompt, "should_trigger": item["should_trigger"]})
        with self._lock:
            data = self._read(draft_id)
            data["trigger_optimization"] = {
                "examples": cleaned,
                "max_iterations": max_iterations,
                "status": "review_required",
                "updated_at": _now(),
            }
            data["updated_at"] = _now()
            self._write(data)
            return data

    def discard(self, draft_id: str, *, session_id: str) -> None:
        with self._lock:
            data = self._read(draft_id)
            if data.get("session_id") != session_id:
                raise ValueError("This draft belongs to another conversation.")
            shutil.rmtree(self._folder(draft_id))


def skill_lab_tools(
    store: SkillDraftStore,
    *,
    session_id: str,
    locale: Callable[[], str],
    run_evals: Callable[[str, int], dict[str, Any]],
) -> list[Any]:
    def stage_skill_draft(
        name: str,
        description: str,
        instructions: str,
        evals: Optional[list[dict[str, Any]]] = None,
        draft_id: str = "",
    ) -> dict[str, Any]:
        """Store a skill candidate and its test prompts without installing it."""
        try:
            return store.stage(
                name=name,
                description=description,
                instructions=instructions,
                evals=evals or [],
                session_id=session_id,
                locale=locale(),
                draft_id=draft_id,
            )
        except ValueError as exc:
            return {"error": str(exc)}

    def run_skill_evals(draft_id: str, repeats: int = 1) -> dict[str, Any]:
        """Run isolated baseline and candidate tests for a staged skill draft."""
        if repeats not in (1, 3):
            return {"error": "Repeats must be 1 or 3."}
        try:
            store.get(draft_id)
        except ValueError as exc:
            return {"error": str(exc)}
        return run_evals(draft_id, repeats)

    def read_skill_eval(draft_id: str) -> dict[str, Any]:
        """Read a staged skill draft, its comparisons, objective checks, and feedback."""
        try:
            return store.get(draft_id)
        except ValueError as exc:
            return {"error": str(exc)}

    def discard_skill_draft(draft_id: str) -> dict[str, Any]:
        """Permanently discard an uninstalled skill draft and its isolated results."""
        try:
            store.discard(draft_id, session_id=session_id)
            return {"ok": True}
        except ValueError as exc:
            return {"error": str(exc)}

    staged = ai.tool(stage_skill_draft, metadata=ai.ToolMetadata(name="stage_skill_draft", category="skills", risk_level="low", capabilities=["skill_draft"]))
    run = ai.tool(run_skill_evals, metadata=ai.ToolMetadata(name="run_skill_evals", category="skills", risk_level="medium", capabilities=["skill_evaluation"], requires_approval=True))
    read = ai.tool(read_skill_eval, metadata=ai.ToolMetadata(name="read_skill_eval", category="skills", risk_level="low", capabilities=["skill_evaluation"]))
    discard = ai.tool(discard_skill_draft, metadata=ai.ToolMetadata(name="discard_skill_draft", category="skills", risk_level="medium", capabilities=["skill_draft"], requires_approval=True))
    return [staged, run, read, discard]
