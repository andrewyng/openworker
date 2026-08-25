"""Skill Creator draft persistence and safe API boundaries."""

from __future__ import annotations

import asyncio

from coworker.skills.lab import SkillDraftStore
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager
from coworker.sessions import SessionRecord


class _Provider(ProviderClient):
    def __init__(self):
        self.turns = [AssistantTurn(text="baseline output"), AssistantTurn(text="candidate output")]

    def complete(self, **_kwargs):
        return self.turns.pop(0)

    def capabilities(self, _model):
        return ModelCapabilities()


def test_draft_roundtrip_review_and_discard(tmp_path):
    store = SkillDraftStore(tmp_path / "drafts")
    draft = store.stage(
        name="weekly-report",
        description="Create a weekly report when asked.",
        instructions="Write a concise report.",
        evals=[{"id": "basic", "prompt": "Write this week's report", "assertions": []}],
        session_id="session-1",
        locale="pt-BR",
    )
    assert (tmp_path / "drafts" / draft["id"] / "candidate" / "SKILL.md").is_file()
    assert store.get(draft["id"])["locale"] == "pt-BR"
    store.add_review(draft["id"], [{"case_id": "basic", "variant": "with_skill", "feedback": "Good."}])
    assert store.get(draft["id"])["reviews"][0]["feedback"] == "Good."
    optimized = store.prepare_trigger_optimization(
        draft["id"],
        [{"prompt": "Prepare an incident report", "should_trigger": True}],
    )
    assert optimized["trigger_optimization"]["status"] == "review_required"
    store.discard(draft["id"], session_id="session-1")
    try:
        store.get(draft["id"])
    except ValueError:
        pass
    else:  # pragma: no cover - clearer failure than a context manager here
        raise AssertionError("discarded draft was still available")


def test_draft_rejects_reserved_name_and_other_session(tmp_path):
    store = SkillDraftStore(tmp_path / "drafts")
    try:
        store.stage(
            name="skill-creator", description="x", instructions="x", evals=[], session_id="s", locale="en-US"
        )
    except ValueError as exc:
        assert "built-in" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("reserved name was accepted")

    draft = store.stage(
        name="ok", description="d", instructions="i", evals=[], session_id="s1", locale="en-US"
    )
    try:
        store.discard(draft["id"], session_id="s2")
    except ValueError as exc:
        assert "another conversation" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("another session discarded the draft")


def test_isolated_evaluation_keeps_baseline_without_candidate(tmp_path):
    provider = _Provider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.session_store.save(
        SessionRecord(session_id="s1", workspace=str(tmp_path), model="test", mode="interactive", agent="cowork")
    )
    draft = manager.skill_drafts.stage(
        name="reports", description="Create reports.", instructions="Be concise.",
        evals=[{"id": "case", "prompt": "Create a report", "assertions": [{"type": "contains", "value": "output"}]}],
        session_id="s1", locale="en-US",
    )
    asyncio.run(manager._run_skill_evals_async("s1", draft["id"], 1))
    saved = manager.skill_drafts.get(draft["id"])
    runs = saved["iterations"][0]["runs"]
    assert [run["variant"] for run in runs] == ["baseline", "with_skill"]
    assert runs[0]["output"] == "baseline output"
    assert runs[1]["output"] == "candidate output"
    assert all(run["files"] == [] for run in runs)
