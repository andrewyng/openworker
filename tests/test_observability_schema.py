"""The engine's own recordings fit the shared schema (recording-format.schema.json).

The golden test checks the recorder against the shared scenarios; this checks the other
half: that what the real engine records (turns, model, tool, wait, auto-approver, summary,
context and notice steps, stamps, decision paths, exchanges) has exactly the shape of
recording format 1.x, the same file the Rust SDK is built against.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from coworker import compaction as _compaction
from coworker.engine import ApprovalOutcome
from coworker.providers import AssistantTurn
from coworker.providers.base import TokenUsage

SCHEMA = json.loads((Path(__file__).parent / "corpora/observability/recording-format.schema.json").read_text("utf-8"))
waits = importlib.import_module("test_observability_waits")
reviews = importlib.import_module("test_observability_reviewer_helper")
stamps = importlib.import_module("test_observability_stamps_context")


def _check(recording):
    validator = jsonschema.validators.validator_for(SCHEMA)(SCHEMA)
    errors = sorted(validator.iter_errors(recording), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message[:300]}" for e in errors[:8])


def test_approved_card_session_grant_and_low_risk(tmp_path):
    engine, rec = waits._engine(tmp_path, [
        waits._call("c1", "run_shell", {"command": "pytest"}),
        waits._call("c2", "run_shell", {"command": "pytest"}),
        waits._call("c3", "look", {"path": "x"}),
        AssistantTurn(text="done", usage=TokenUsage(input=10, output=2)),
    ], answer=ApprovalOutcome.ALWAYS_COMMAND)
    waits._run(engine)
    _check(rec.as_dict())


def test_denied_card(tmp_path):
    engine, rec = waits._engine(tmp_path, [waits._call("c1", "run_shell", {"command": "rm -rf b"}),
                                           AssistantTurn(text="ok")], answer=ApprovalOutcome.DENY)
    waits._run(engine)
    _check(rec.as_dict())


def test_summary_step(tmp_path, monkeypatch):
    engine, rec = waits._engine(tmp_path, [AssistantTurn(text="sum", usage=TokenUsage(input=9, output=1)),
                                           AssistantTurn(text="hi", usage=TokenUsage(input=5, output=1)),
                                           AssistantTurn(text="next")])

    def fake(messages, *, provider, model, **kw):
        provider.complete(model=model, messages=[{"role": "user", "content": "summarize"}])
        return SimpleNamespace(boundary_index=0, carries_outside=False, transcript_path=None)

    monkeypatch.setattr(_compaction, "build_state", fake)
    waits._run(engine)  # a turn, so the summary has one to belong to

    async def go():
        await engine._compact_now()

    asyncio.run(go())
    waits._run(engine, "again")
    _check(rec.as_dict())


@pytest.mark.parametrize("verdict", ["allow", "deny"])
def test_live_auto_approver_checks(tmp_path, verdict):
    engine, rec = reviews._engine(tmp_path, [reviews._call("c1", "run_shell", {"command": "make"}),
                                             AssistantTurn(text="ok")],
                                  [json.dumps({"verdict": verdict, "reason": "r"})])
    reviews._run(engine)
    _check(rec.as_dict())


def test_shadow_check_and_could_not_run(tmp_path):
    engine, rec = reviews._engine(tmp_path, [reviews._call("c1", "run_shell", {"command": "make"}),
                                             AssistantTurn(text="ok")],
                                  [json.dumps({"verdict": "allow", "reason": "fine"})], shadow=True)
    reviews._run(engine)
    _check(rec.as_dict())
    engine, rec = reviews._engine(tmp_path, [reviews._call("c1", "run_shell", {"command": "make"}),
                                             AssistantTurn(text="ok")], [RuntimeError("no key")])
    reviews._run(engine)
    _check(rec.as_dict())


def test_explore_helper(tmp_path):
    rec_holder = {}
    original = reviews.Recorder

    class Keep(original):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            rec_holder["rec"] = self

    reviews.Recorder = Keep
    try:
        reviews.test_explore_helper_steps_are_nested_under_the_explore_step(tmp_path)
    finally:
        reviews.Recorder = original
    _check(rec_holder["rec"].as_dict())


def test_context_notices_stamps_and_sandbox_facts(tmp_path):
    engine, rec = stamps._engine(tmp_path, [stamps._call("c1", "look", {"path": "x"}), AssistantTurn(text="ok")],
                                 blocks=["Folders: /w"])
    stamps._run(engine)
    _check(rec.as_dict())
    engine, rec = stamps._engine(tmp_path, [RuntimeError("boom")])
    stamps._run(engine)
    _check(rec.as_dict())
