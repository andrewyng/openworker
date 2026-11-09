"""Observability golden test (Phase 1): recording events in, recording out.

The scenarios in `tests/corpora/observability/golden-phase1/` are the shared test of
recording format 1.0 (`ocw-context/docs/observability/golden/phase1/`, frozen 2026-10-31).
The Rust harness SDK runs the same files, so both engines fold the same events into the
same recording. Each scenario is replayed into a fresh recorder and compared with its
`expected-recording.json` exactly, after the one renaming the golden README describes:
step ids become `s1`, `s2`, ... by their position in the file.

The same replay through a `RecordingStore` must give back the same recording from
`recordings.db`, so nothing is lost or changed on the way to disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from coworker.observability import Recorder, RecordingStore

GOLDEN = Path(__file__).parent / "corpora" / "observability" / "golden-phase1"
SCENARIOS = [s["id"] for s in json.loads((GOLDEN / "index.json").read_text("utf-8"))["scenarios"]]


def _events(scenario: str) -> list[dict[str, Any]]:
    lines = (GOLDEN / scenario / "events.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _renamed(recording: dict[str, Any]) -> dict[str, Any]:
    """Step ids renamed s1, s2, ... in file order, with every field that holds one."""
    ids = {s["step_id"]: f"s{i}" for i, s in enumerate(recording["steps"], 1)}
    steps = []
    for s in recording["steps"]:
        s = json.loads(json.dumps(s))
        s["step_id"] = ids[s["step_id"]]
        s["parent_step_id"] = ids.get(s["parent_step_id"], s["parent_step_id"])
        if s["kind"] == "summary" and s["attrs"].get("model_step_id"):
            s["attrs"]["model_step_id"] = ids.get(
                s["attrs"]["model_step_id"], s["attrs"]["model_step_id"]
            )
        steps.append(s)
    return {"turns": recording["turns"], "steps": steps}


def _replay(events: list[dict[str, Any]], store: RecordingStore | None = None) -> Recorder:
    rec = Recorder(store=store)
    for ev in events:
        rec.emit(ev)
    return rec


def test_every_scenario_is_listed():
    folders = sorted(p.name for p in GOLDEN.iterdir() if (p / "events.jsonl").exists())
    assert sorted(SCENARIOS) == folders
    assert len(SCENARIOS) == 30


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_events_fold_to_the_expected_recording(scenario):
    expected = json.loads((GOLDEN / scenario / "expected-recording.json").read_text("utf-8"))
    got = _renamed(_replay(_events(scenario)).as_dict())
    assert got == expected


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_store_gives_back_the_same_recording(scenario, tmp_path):
    events = _events(scenario)
    in_memory = _replay(events).as_dict()
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    try:
        _replay(events, store)
        sessions = {t["session_id"] for t in in_memory["turns"]}
        for session in sessions:
            want = {
                "turns": [t for t in in_memory["turns"] if t["session_id"] == session],
                "steps": [s for s in in_memory["steps"] if s["session_id"] == session],
            }
            assert store.load_recording(session) == want
    finally:
        store.close()


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_screen_numbers(scenario):
    """`expected-numbers.json`: every token number and time bucket the screens show."""
    from coworker.observability import calculator

    expected = json.loads((GOLDEN / scenario / "expected-numbers.json").read_text("utf-8"))
    rec = _replay(_events(scenario)).as_dict()
    sessions = sorted({t["session_id"] for t in rec["turns"] if t["session_id"]}
                      | {s["session_id"] for s in rec["steps"] if s["session_id"]})

    def usage(sid=None):
        u = calculator.usage(rec, sid)
        u.pop("unreported_step_ids")  # an addition for the "≥" links (M-R19), not in the golden
        return u

    got = {"usage": usage()}
    if len(sessions) > 1:
        got["usage_by_session"] = {sid: usage(sid) for sid in sessions}
    got["buckets_by_turn"] = {t["turn_id"]: calculator.buckets(rec, t) for t in rec["turns"]}
    assert got == expected
