"""The 4 Nov fix-list batch, engine side (manual testing of Phase 1 and 2):
F-1 the sandbox note is recorded only when its text was added, F-5 every line of your
instructions counts as a rule, F-4 model calls from outside any session are counted."""

from __future__ import annotations

import importlib
import time

from coworker.observability import Recorder, RecordingStore
from coworker.observability.engine_hooks import TurnRecording
from coworker.providers import AssistantTurn

base = importlib.import_module("test_observability_stamps_context")
memory = importlib.import_module("test_memory")


class _Workspace:
    def __init__(self, text):
        self.text = text
        self.started = True

    def context(self):
        return self.text

    def describe(self):
        return {"provider": "x", "enforcement": "full"}


def _note(tmp_path, workspace):
    engine, rec = base._engine(tmp_path, [AssistantTurn(text="ok")])
    engine.sandbox_workspace = workspace
    base._run(engine)
    [ctx] = [s for s in rec.as_dict()["steps"] if s["kind"] == "context"]
    return ctx["attrs"]["per_turn"]["sandbox_note"]


def test_the_sandbox_note_is_recorded_only_when_its_text_was_added(tmp_path):
    assert _note(tmp_path, _Workspace("Commands in this session run in a sandbox.")) is True
    # A workspace that describes itself but adds no note (no sandbox, or runner-local):
    assert _note(tmp_path, _Workspace("")) is False
    nothing = type("Direct", (), {"started": True, "describe": lambda self: {"provider": "direct"}})()
    assert _note(tmp_path, nothing) is False


def test_every_line_of_your_instructions_counts_as_a_rule(tmp_path):
    from coworker.agent import build_code_engine

    for text, want in (("Use DD-MM-YYYY for dates\nNo tables", 2), ("- one\n- two\n\n  - three", 3), ("", 0)):
        engine = build_code_engine(workspace=tmp_path, provider=memory._StubProvider(), memory_store=None,
                                   user_rules=lambda t=text: t)
        try:
            assert engine.context_facts["rules"] == want, text
        finally:
            engine.executor.close()


def test_model_calls_from_outside_any_session_are_counted(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    rec = Recorder(store=store)
    obs = TurnRecording(rec, lambda: "")
    step = obs.start_step(kind="model", name="model", attrs={"purpose": "proxy"}, turn_id=None)
    obs.end_step(step, attrs={"usage": {"reported": True, "input_fresh": 100, "cache_read": 20, "cache_write": None,
                                        "output": 30}})
    store.flush()
    assert store.outside_calls(since_ms=0) == {"calls": 1, "tokens": 150}
    assert store.outside_calls(since_ms=int(time.time() * 1000) + 60_000) == {"calls": 0, "tokens": 0}
    store.close()
