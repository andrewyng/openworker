"""OB-14: gaps in the timeline (phase-2 design §11, recording format 1.2): the machine
offline from its controller, nobody watching the session, the machine asleep, and the
app restarted mid-turn. Each is a notice; the first three have a duration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from coworker.observability import Recorder, RecordingStore, gaps, signals
from coworker.observability.engine_hooks import TurnRecording
from coworker.observability.export_spans import Masker, build_turn_spans
from coworker.server.manager import SessionManager

SCHEMA = json.loads((Path(__file__).parent / "corpora/observability/recording-format.schema.json").read_text("utf-8"))


def _check(recording):
    validator = jsonschema.validators.validator_for(SCHEMA)(SCHEMA)
    errors = sorted(validator.iter_errors(recording), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message[:300]}" for e in errors[:8])


@pytest.fixture(autouse=True)
def _no_machine_conditions():
    gaps._machine.clear()
    yield
    gaps._machine.clear()


def _recording(session="sess_g"):
    rec = Recorder()
    obs = TurnRecording(rec, lambda: session)
    return rec, obs


def _begin(obs):
    return obs.begin_turn(kind="run", trigger="user", attrs={
        "approval_mode": "ask", "attended": True,
        "reviewer": {"active": False, "shadow": False, "kind": None, "model": None}}, stamp={}, first_message_id=None)


def _gaps(rec, name):
    return [s for s in rec.as_dict()["steps"] if s["kind"] == "notice" and s["name"] == name]


# -- nobody watching (E15b-2) ------------------------------------------------------------------


def test_nobody_watching_mid_turn_is_a_band_from_leaving_to_coming_back():
    rec, obs = _recording()
    _begin(obs)
    start = obs._turn_started_ms
    obs.condition("nobody_watching", True, at_ms=start + 1000)
    obs.condition("nobody_watching", False, at_ms=start + 61_000)
    obs.end_turn("completed")
    [gap] = _gaps(rec, "nobody_watching")
    assert (gap["started_at"], gap["duration_ms"], gap["status"], gap["attrs"]) == (start + 1000, 60_000, "ok", {})
    assert gap["parent_step_id"] is None


def test_a_gap_still_on_is_cut_at_each_turns_start_and_end():
    rec, obs = _recording()
    obs.condition("nobody_watching", True, at_ms=1)  # left long before the turn: a schedule ran it
    import time

    t1 = _begin(obs)
    time.sleep(0.01)
    obs.end_turn("completed")
    t2 = _begin(obs)
    time.sleep(0.01)
    obs.end_turn("completed")
    one, two = _gaps(rec, "nobody_watching")
    assert one["turn_id"] == t1 and two["turn_id"] == t2
    turns = {t["turn_id"]: t for t in rec.as_dict()["turns"]}
    assert one["started_at"] == turns[t1]["started_at"]  # not before its turn
    assert one["started_at"] + one["duration_ms"] <= turns[t1]["started_at"] + turns[t1]["duration_ms"] + 1
    _check(rec.as_dict())


def test_a_condition_outside_any_turn_writes_nothing():
    rec, obs = _recording()
    obs.condition("nobody_watching", True)
    obs.condition("nobody_watching", False)
    assert rec.as_dict()["steps"] == []


def test_only_the_crossings_between_none_and_one_viewer_count():
    told = []
    fake = SimpleNamespace(_session_clients={}, _engines={})
    fake._note_watching = lambda sid, watching: told.append((sid, watching))
    register = SessionManager.register_session_client.__get__(fake)
    unregister = SessionManager.unregister_session_client.__get__(fake)
    a, b = object(), object()
    register("s1", a)
    register("s1", b)  # a second window: no row
    unregister("s1", a)
    unregister("s1", b)
    assert told == [("s1", True), ("s1", False)]


def test_the_manager_tells_the_engines_recording():
    rec, obs = _recording("s1")
    fake = SimpleNamespace(_engines={"s1": SimpleNamespace(_obs=obs)})
    note = SessionManager._note_watching.__get__(fake)
    _begin(obs)
    note("s1", False)
    note("s1", True)
    obs.end_turn("completed")
    assert len(_gaps(rec, "nobody_watching")) <= 1  # a band only if any time passed
    note("missing", False)  # no engine: nothing, no error


# -- the controller link (E15b-1) --------------------------------------------------------------


def test_machine_offline_reaches_every_running_turn(tmp_path):
    rec1, obs1 = _recording("a")
    rec2, obs2 = _recording("b")
    _begin(obs1)
    _begin(obs2)
    start = max(obs1._turn_started_ms, obs2._turn_started_ms)
    gaps.machine_condition("controller_offline", True, at_ms=start + 10, attrs={"reason": "ping_timeout"})
    gaps.machine_condition("controller_offline", False, at_ms=start + 420_010)
    obs1.end_turn("completed")
    obs2.end_turn("completed")
    for rec in (rec1, rec2):
        [gap] = _gaps(rec, "controller_offline")
        assert gap["duration_ms"] == 420_000 and gap["attrs"] == {"reason": "ping_timeout"}
        _check(rec.as_dict())


def test_an_engine_made_while_offline_hears_it():
    gaps.machine_condition("controller_offline", True, at_ms=5, attrs={"reason": "failed"})
    rec, obs = _recording()
    _begin(obs)
    gaps.machine_condition("controller_offline", False, at_ms=obs._turn_started_ms + 1_000)
    [gap] = _gaps(rec, "controller_offline")
    assert gap["started_at"] == obs._turn_started_ms and gap["attrs"] == {"reason": "failed"}


def test_the_lost_moment_survives_a_restart(tmp_path):
    gaps.link_lost(tmp_path, "closed")
    saved = json.loads((tmp_path / "controller-link-lost.json").read_text("utf-8"))
    assert saved["reason"] == "closed"
    gaps._machine.clear()  # the app restarts: the memory is gone, the file is not
    rec, obs = _recording()
    _begin(obs)
    import time
    time.sleep(0.01)  # the link comes back a moment after the turn started
    gaps.link_back(tmp_path)
    [gap] = _gaps(rec, "controller_offline")
    assert gap["attrs"] == {"reason": "closed"}
    assert not (tmp_path / "controller-link-lost.json").exists()
    gaps.link_back(tmp_path)  # a welcome with nothing lost: nothing
    assert len(_gaps(rec, "controller_offline")) == 1


def test_link_reasons():
    assert gaps.link_reason(None) == "closed"
    assert gaps.link_reason(TimeoutError("keepalive ping timeout")) == "ping_timeout"
    assert gaps.link_reason(OSError("connection refused")) == "failed"


# -- the machine asleep (E15b-3) ---------------------------------------------------------------


def _ev(typ, **kw):
    return {"v": 1, "type": typ, "session_id": "sess_c", "turn_id": "t1", **kw}


def _step(step_id, at, mono, end_at, end_mono):
    return [
        _ev("step_started", step_id=step_id, at_ms=at, mono_ms=mono, kind="tool", name="look",
            parent_step_id=None, call_id=step_id, call_index=0, shadow=False, after_turn=False,
            attrs={}, content={}),
        _ev("step_ended", step_id=step_id, at_ms=end_at, mono_ms=end_mono, status="ok", message_id=None,
            attrs={}, content={}, kind="tool", name="look", parent_step_id=None, call_id=step_id,
            call_index=0, shadow=False, after_turn=False, started_at=at),
    ]


def test_a_sleep_mid_turn_is_a_clock_jump_and_later_steps_follow_the_wall_clock():
    rec = Recorder()
    rec.emit(_ev("turn_started", at_ms=1_000, mono_ms=0, kind="run", trigger="user", first_message_id=None,
                 attrs={}, stamp={}))
    for ev in _step("a", 1_100, 100, 1_200, 200):
        rec.emit(ev)
    # The machine slept 60 s: the wall clock moved on, the monotonic clock did not.
    for ev in _step("b", 61_300, 300, 61_400, 400):
        rec.emit(ev)
    rec.emit(_ev("turn_ended", at_ms=61_500, mono_ms=500, status="completed", last_message_id=None))
    d = rec.as_dict()
    by = {s["name"]: s for s in d["steps"]}
    jump = by["clock_jump"]
    assert jump["started_at"] == 1_200 and jump["duration_ms"] == 60_000
    assert jump["attrs"] == {"skipped_ms": 60_000}
    assert by["look"]["started_at"] in (1_100, 61_300)
    starts = sorted(s["started_at"] for s in d["steps"] if s["kind"] == "tool")
    assert starts == [1_100, 61_300]  # the second step is placed by the wall clock again
    assert d["turns"][0]["duration_ms"] == 60_500  # the turn's length counts the sleep
    assert [s["name"] for s in d["steps"]] == ["look", "clock_jump", "look"]


def test_small_drift_is_not_a_jump():
    rec = Recorder()
    rec.emit(_ev("turn_started", at_ms=1_000, mono_ms=0, kind="run", trigger="user", first_message_id=None,
                 attrs={}, stamp={}))
    for ev in _step("a", 4_000, 100, 4_100, 200):  # 2.9 s ahead: within the 5 s allowed
        rec.emit(ev)
    assert not [s for s in rec.as_dict()["steps"] if s["name"] == "clock_jump"]


# -- the app restarted (E15b-4) ----------------------------------------------------------------


def _open_turn(path):
    store = RecordingStore(path, background=False)
    rec = Recorder(store=store)
    rec.emit(_ev("turn_started", at_ms=1_000, mono_ms=0, kind="run", trigger="user", first_message_id=None,
                 attrs={}, stamp={}))
    store.flush()
    return store


def _gap_attrs(path):
    s = RecordingStore(path, background=False)
    try:
        assert s.close_open_turns() == 1
        [gap] = [x for x in s.load_recording("sess_c")["steps"] if x["name"] == "recording_gap"]
        return gap["attrs"]
    finally:
        s.close()


def test_a_turn_cut_by_a_crash_says_restart(tmp_path):
    path = tmp_path / "recordings.db"
    first = RecordingStore(path, background=False)
    first.close_open_turns()  # this run owns the folder
    first._conn.close()  # killed: never closed properly
    s = _open_turn(path)
    s._conn.close()
    attrs = _gap_attrs(path)
    assert attrs["cause"] == "restart" and isinstance(attrs["until"], int)


def test_a_turn_cut_by_closing_the_app_says_stopped(tmp_path):
    path = tmp_path / "recordings.db"
    first = RecordingStore(path, background=False)
    first.close_open_turns()
    rec = Recorder(store=first)
    rec.emit(_ev("turn_started", at_ms=1_000, mono_ms=0, kind="run", trigger="user", first_message_id=None,
                 attrs={}, stamp={}))
    first.close()  # a clean stop while the turn ran
    assert _gap_attrs(path)["cause"] == "stopped"


def test_with_no_word_of_the_last_run_the_cause_is_unknown(tmp_path):
    path = tmp_path / "recordings.db"
    s = _open_turn(path)
    s.close()  # not the owner: leaves no word
    assert _gap_attrs(path)["cause"] == "unknown"


# -- what reads the gaps -----------------------------------------------------------------------


def test_a_gap_is_its_own_span_and_other_notices_stay_events():
    rec, obs = _recording()
    _begin(obs)
    start = obs._turn_started_ms
    obs.condition("nobody_watching", True, at_ms=start)
    obs.condition("nobody_watching", False, at_ms=start + 5_000)
    obs.instant_step(kind="notice", name="interrupted", status="ok")
    obs.end_turn("interrupted")
    d = rec.as_dict()
    turn = d["turns"][0]
    spans = build_turn_spans(turn, d["steps"], sw={}, dialect="otel", get_piece=lambda k: None, masker=Masker())
    [gap] = [s for s in spans if s["name"] == "openworker.gap.nobody_watching"]
    assert gap["end_ms"] - gap["start_ms"] == 5_000
    [root] = [s for s in spans if s["name"] == "invoke_agent coworker"]
    assert [e["name"] for e in root["events"]] == ["openworker.notice.interrupted"]


def test_a_card_left_in_the_session_while_nobody_watched_is_an_issue():
    turn = {"turn_id": "t1", "started_at": 0, "duration_ms": 1_300_000, "status": "completed", "stamp": {},
            "reviewer": None, "attended": True}
    wait = {"step_id": "w", "kind": "wait", "name": "approval", "status": "ok", "parent_step_id": None,
            "shadow": False, "after_turn": False, "started_at": 0, "duration_ms": 1_200_000, "seq": 1,
            "attrs": {"kind": "approval", "where": "session", "outcome": "approved"}}
    gap = {"step_id": "g", "kind": "notice", "name": "nobody_watching", "status": "ok", "parent_step_id": None,
           "shadow": False, "after_turn": False, "started_at": 100_000, "duration_ms": 1_000_000, "seq": 2, "attrs": {}}
    found = signals.check_turn(turn, [wait, gap])
    [i] = [x for x in found["issues"] if x["check"] == "waited_unwatched"]
    assert "17 min for an answer" in i["text"] and "Inbox" not in i["text"]
    quiet = signals.check_turn(turn, [wait])
    assert not [x for x in quiet["issues"] if x["check"] == "waited_unwatched"]
