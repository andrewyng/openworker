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


def test_interactive_tools_and_steps_closed_by_a_stop_fit_the_schema(tmp_path):
    # Round-2 in-app check: interactive tools (plan, question, folder) had no `decision`, and
    # a step closed at its turn's end kept none of its kind's end fields.
    import asyncio as _asyncio
    from coworker.permissions import Mode

    engine, rec = waits._engine(tmp_path, [waits._call("p1", "propose_plan", {"plan": "do it"}),
                                           AssistantTurn(text="x")], mode=Mode.PLAN)

    async def never(*a):
        await _asyncio.sleep(30)

    engine.plan_approver = never

    async def go():
        async def stop_soon():
            await _asyncio.sleep(0.3)
            engine.request_interrupt()
        task = _asyncio.create_task(stop_soon())
        [ev async for ev in engine.run("plan", trigger="user")]
        await task

    _asyncio.run(go())
    [plan] = [s for s in rec.as_dict()["steps"] if s["name"] == "propose_plan"]
    assert plan["attrs"]["decision"] == {"path": [], "origin": None}
    _check(rec.as_dict())


def test_a_model_step_still_open_at_the_turns_end_fits_the_schema():
    from coworker.observability import Recorder

    rec = Recorder()
    base = {"v": 1, "session_id": "s", "turn_id": "t"}
    rec.emit({**base, "type": "turn_started", "at_ms": 1000, "mono_ms": 0, "kind": "run", "trigger": "user",
              "first_message_id": "m1", "stamp": {},
              "attrs": {"approval_mode": "interactive", "attended": True,
                        "reviewer": {"active": False, "shadow": False, "kind": None, "model": None}}})
    rec.emit({**base, "type": "step_started", "step_id": "x", "at_ms": 1010, "mono_ms": 10, "kind": "model",
              "name": "model", "parent_step_id": None, "call_id": None, "call_index": None, "shadow": False,
              "after_turn": False, "content": {},
              "attrs": {"purpose": "title", "provider": "openai", "provider_title": "OpenAI", "model": "openai:model-a",
                        "stream": False, "context_window": None, "tools_offered": 0, "tools_hash": None}})
    rec.emit({**base, "type": "turn_ended", "at_ms": 1100, "mono_ms": 100, "status": "completed", "last_message_id": "m2"})
    [step] = rec.as_dict()["steps"]
    assert step["status"] == "interrupted" and step["attrs"]["usage"] == {"reported": False}
    _check(rec.as_dict())
