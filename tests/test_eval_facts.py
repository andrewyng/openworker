"""The reviewer eval carries facts (OPE-235) and plan approval (OPE-237).

The engine hands the reviewer fixed-vocabulary fact lines. An eval row whose correct
answer depends on a fact must give the reviewer the same lines, or it grades the
reviewer against input it never saw. Rows say `facts: [...]`; the harness passes them
the way the engine does (only when there are any); every line must be one the engine
can produce. Plan approval reaches the reviewer only as a fact line, so the same field
covers it, and the corpus pairs each plan row with the same action without the plan.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_reviewer as ev  # noqa: E402
from coworker import review_facts as rf  # noqa: E402
from coworker.reviewer import Verdict  # noqa: E402


def test_the_vocabulary_lists_every_line_the_engine_can_produce():
    assert {
        rf.EXTERNAL_READ,
        rf.DESTINATION_NAMED,
        rf.DESTINATION_NOT_NAMED,
        rf.DESTINATION_UNKNOWN,
        rf.SECRET_INVOLVED,
        rf.PLAN_STEP,
        "target file: new",
        "target file: has uncommitted changes",
    } <= rf.FACT_VOCABULARY


def test_rows_with_facts_load_and_use_only_engine_lines():
    rows = [r for r in ev.load_corpus("reviewer_actions") if r.facts]
    assert rows, "the corpus has fact rows"
    for row in rows:
        assert set(row.facts) <= rf.FACT_VOCABULARY, row.id


def test_a_fact_the_engine_cannot_produce_is_rejected(tmp_path, monkeypatch):
    bad = {
        "id": "x-1", "user_request": "r", "setup": {}, "action": {"tool": "run_shell", "arguments": {}},
        "correct": "allow", "facts": ["the user said it is fine"],
    }
    (tmp_path / "bad.jsonl").write_text(json.dumps(bad) + "\n", encoding="utf-8")
    monkeypatch.setattr(ev, "CORPUS_DIR", tmp_path)
    with pytest.raises(ValueError, match="unknown fact"):
        ev.load_corpus("bad")


def test_facts_reach_the_reviewer_only_when_a_row_has_them():
    seen: list[dict] = []

    class _Reviewer:
        known_world = ""

        async def review(self, **kw):
            seen.append(kw)
            return Verdict("unsure", "x")

    rows = ev.load_corpus("reviewer_actions")
    with_facts = next(r for r in rows if r.facts)
    without = next(r for r in rows if not r.facts)
    asyncio.run(ev.review_row(_Reviewer(), with_facts, stub=False))
    asyncio.run(ev.review_row(_Reviewer(), without, stub=False))
    assert seen[0]["facts"] == with_facts.facts
    assert "facts" not in seen[1]  # as the engine: no empty facts argument


def _plan_rows():
    return [r for r in ev.load_corpus("reviewer_actions") if "plan-approval" in r.tags]


def test_every_approved_plan_row_has_a_twin_without_the_plan():
    rows = _plan_rows()
    approved = [r for r in rows if rf.PLAN_STEP in r.facts]
    assert len(approved) >= 2
    for row in approved:
        twins = [
            r for r in rows
            if r.action == row.action and r.user_request == row.user_request and rf.PLAN_STEP not in r.facts
        ]
        assert twins, f"{row.id} has no twin without the plan"
        assert row.correct == "allow"
        assert all(t.correct == "ask" for t in twins)


def test_actions_outside_the_plan_are_never_labelled_allow():
    outside = [r for r in _plan_rows() if "plan-outside" in r.tags]
    assert len(outside) >= 3
    assert all(r.correct in ("ask", "deny") for r in outside)


def test_the_validator_accepts_the_corpus_with_facts():
    import validate_layered_corpora as vl

    vl.validate_all()


def test_there_is_no_line_saying_an_action_is_outside_the_plan():
    # A plan only ever adds evidence for its own steps (see test_plan_approval.py).
    assert not hasattr(rf, "PLAN_NOT_STEP")
    assert not any("plan" in f and "not" in f for f in rf.FACT_VOCABULARY)
    outside = [r for r in _plan_rows() if "plan-outside" in r.tags]
    assert all(rf.PLAN_STEP not in r.facts for r in outside)
