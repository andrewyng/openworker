"""Phase 3 code review before merging (4 Nov), one test per fix: the rating export keeps moving
past 100 (P3B-2), the same approval asked twice points at the repeat in both views (P3B-4), a
team's numbers are read once and kept until something changes (P3B-5), a ledger error keeps the
recorded steps (P3B-6), and a resumed automation turn is the run's (P3B-7). P3B-3 (the offline
machine's cached Usage answer) is in test_remote_home.py."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from coworker.observability import RecordingStore, rollups, scopes, signals, usage_api
from coworker.observability import export as ex
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord
from coworker.teams.model import Actor, Role
from coworker.teams.registry import TeamWorker


def _turn(tid, sid, seq, at, *, automation=None, status="completed"):
    t = {"turn_id": tid, "session_id": sid, "seq": seq, "kind": "run", "trigger": "user", "started_at": at,
         "duration_ms": 1000, "status": status, "incomplete": False, "approval_mode": "interactive",
         "attended": True, "reviewer": None, "stamp": {"machine": {"kind": "this_machine", "id": None}},
         "first_message_id": None, "last_message_id": None, "schema_version": 1, "wake": {"reason": "user"}}
    if automation:
        t["automation"] = automation
    return t


def _step(tid, sid, n, at, *, kind="model", name="model", call_id=None, attrs=None):
    return {"step_id": f"{tid}-s{n}", "turn_id": tid, "session_id": sid, "parent_step_id": None, "seq": n,
            "kind": kind, "name": name, "started_at": at, "duration_ms": 100, "status": "ok",
            "call_id": call_id, "call_index": None, "message_id": None, "shadow": False, "after_turn": False,
            "attrs": attrs if attrs is not None else
            {"purpose": "main", "model": "model-x",
             "usage": {"reported": True, "input_fresh": 10, "cache_read": 0, "cache_write": 0, "output": 1}},
            "content": {}}


def _write(store, turns, steps):
    for t in turns:
        store.write_turn(t)
    for s in steps:
        store.write_step(s)
    store.flush()


# -- P3B-2 ----------------------------------------------------------------------------------------
def test_ratings_keep_going_out_after_the_first_100(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    for i in range(130):
        store.put_feedback({"turn_id": f"T{i:03d}", "session_id": "s", "rating": "up", "reasons": [],
                            "note": None, "auto_approved": []})
    sent = []

    def post(url, body, headers, timeout):
        sent.append(json.loads(body)["traceId"])
        return 200, ""

    cfg = ex.ExportConfig(destination=ex.Destination(kind="langfuse", host="http://lf.test"), headers={},
                          include={}, since_ms=0)
    exp = ex.Exporter(store, cfg, post=post, secret_values=lambda: [])
    exp.flush_once()
    assert len(sent) == 100  # one batch
    exp.flush_once()
    assert len(sent) == 130 and len(set(sent)) == 130  # the next batch is the other 30, not the first 100 again
    exp.flush_once()
    assert len(sent) == 130  # nothing left
    # a rating changed after all that still goes out
    store.put_feedback({"turn_id": "T000", "session_id": "s", "rating": "down", "reasons": [],
                        "note": None, "auto_approved": []})
    exp.flush_once()
    assert len(sent) == 131
    store.close()


# -- P3B-4 ----------------------------------------------------------------------------------------
def _approval(store, tid, sid, seq, at, call_id, args_hash, outcome):
    """A turn with one tool step and its approval card (wait step)."""
    _write(store, [_turn(tid, sid, seq, at)], [
        _step(tid, sid, 1, at, kind="tool", name="run_shell", call_id=call_id, attrs={"args_hash": args_hash}),
        _step(tid, sid, 2, at + 1, kind="wait", name="approval",
              attrs={"kind": "approval", "for_call_id": call_id, "outcome": outcome}),
    ])


def test_the_same_approval_twice_points_at_the_repeat_in_both_views(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    _approval(store, "L1", "lead-s", 1, 1_000, "c1", "h-same", "approved")
    # maya's first card is for the same tool with other arguments: not a repeat
    _approval(store, "M1", "maya-s", 1, 2_000, "c2", "h-other", "denied")
    # her second card asks again what the lead already approved: the repeat
    _approval(store, "M2", "maya-s", 2, 3_000, "c3", "h-same", "approved")
    cards = store.team_approval_cards(["lead-s", "maya-s"])
    [issue] = scopes.same_approval_issues(cards)
    assert issue["turn_id"] == "M2" and issue["step_id"] == "M2-s2"
    events = usage_api.team_events(store, lead_session="lead-s", worker_sessions={"maya": "maya-s"},
                                   board_events=[], links=[])
    [ev] = [e for e in events if e["kind"] == "same_approval_twice"]
    assert ev["turn_id"] == issue["turn_id"] == "M2"
    # the golden check's own answer is unchanged
    assert scopes.same_approval_twice(cards) == [{"tool": "run_shell", "first_by": "lead-s", "again_by": "maya-s"}]
    store.close()


# -- P3B-6 ----------------------------------------------------------------------------------------
def test_a_ledger_error_keeps_the_recorded_steps(tmp_path, monkeypatch):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    real = rollups.ledger_rows

    def ledger_rows(turn, *args, **kwargs):
        if turn["turn_id"] == "BAD":
            raise RuntimeError("ledger bug")
        return real(turn, *args, **kwargs)

    monkeypatch.setattr(rollups, "ledger_rows", ledger_rows)
    good, bad = _turn("GOOD", "s1", 1, 1_000), _turn("BAD", "s2", 1, 1_000)
    for t in (good, bad):
        store.write_turn(t)
        store.write_step(_step(t["turn_id"], t["session_id"], 1, 1_000))
        store.write_issues(t["turn_id"], {"signals_version": signals.version(), "warn": 0, "info": 0,
                                          "issues": [], "fine": []}, seen=True)
    store.flush()  # one batch with both turns: the ledger error must not throw it away
    for sid, tid in (("s1", "GOOD"), ("s2", "BAD")):
        rec = store.load_recording(sid)
        assert [t["turn_id"] for t in rec["turns"]] == [tid]
        assert [s["step_id"] for s in rec["steps"]] == [f"{tid}-s1"]
    # the other turn's ledger rows were still worked out
    assert [t["turn_id"] for t in store.usage_turns(0, 2**62)] == ["GOOD"]
    store.close()


# -- P3B-5 ----------------------------------------------------------------------------------------
def test_a_team_summary_reads_once_and_works_out_again_only_on_a_change(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path / "repo", data_dir=tmp_path / "data")
    manager.session_store.save(SessionRecord(session_id="lead-session", workspace=str(tmp_path / "repo"),
                                             model="sample", mode="interactive", agent="swe-lead"))
    team = manager.teams.create(space=manager._board_space("lead-session"), lead_session="lead-session",
                                lead_actor="lead",
                                workers=[TeamWorker(actor="sam", persona="swe-worker", session_id="worker-session")])
    store = manager.recordings_store
    _write(store, [_turn("W1", "worker-session", 1, 1_000)],
           [_step("W1", "worker-session", 1, 1_000),
            _step("W1", "worker-session", 2, 1_100, kind="tool", name="transition", call_id="t1", attrs={})])

    reads = {"team_turns": 0, "load_recording": 0}
    for name in reads:
        real = getattr(store, name)

        def counted(*a, _real=real, _name=name, **k):
            reads[_name] += 1
            return _real(*a, **k)

        monkeypatch.setattr(store, name, counted)
    # the hand-off step is one indexed read, not the whole recording
    assert manager._handoff_step("worker-session", 2_000) == {"step_id": "W1-s2", "seq": 2, "turn_id": "W1"}
    assert manager._handoff_step("worker-session", 0) is None  # before the step
    assert reads["load_recording"] == 0

    first = manager.team_summary(team.team_id)
    assert first["wake_explained"] == {"turns": 0, "explained": 0}
    after_first = reads["team_turns"]
    assert after_first == 1  # once for the whole summary, not once per item and per gate
    again = manager.team_summary(team.team_id)
    assert reads["team_turns"] == after_first  # nothing changed: the kept answer
    assert again["wakes"] == first["wakes"] and again["events"] == first["events"]
    # a member's new turn is a change
    _write(store, [_turn("W2", "worker-session", 2, 5_000)], [_step("W2", "worker-session", 1, 5_000)])
    manager.team_summary(team.team_id)
    assert reads["team_turns"] == after_first + 1
    # so is a board change
    manager.team_store.create_item(team.space, Actor(id="lead", role=Role.LEAD), title="New", criteria="Done")
    manager.team_summary(team.team_id)
    assert reads["team_turns"] == after_first + 2
    # the task checks skip a rerun when nothing changed since the last one
    manager._scope_team_tasks(team.team_id)
    n = reads["team_turns"]
    manager._scope_team_tasks(team.team_id)
    assert reads["team_turns"] == n
    manager.observability.close()


# -- P3B-7 ----------------------------------------------------------------------------------------
class _Resumable:
    def __init__(self):
        self.kwargs = None

    async def resume(self, **kwargs):
        self.kwargs = kwargs
        if False:
            yield None


def _resume(manager, session_id):
    engine = _Resumable()
    manager.get_engine = lambda sid, *a, **k: engine
    manager.save = lambda *a, **k: None
    asyncio.run(manager._durable_resume(SimpleNamespace(id=None, session_id=session_id, tool_call_id="c1",
                                                        resolved_by="")))
    return engine.kwargs


def test_a_resumed_automation_turn_is_tagged_with_its_run(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path / "repo", data_dir=tmp_path / "data")
    store = manager.recordings_store
    run = {"task_id": "task-1", "run_id": "run-1", "trigger": "schedule"}
    # the run's own turn stopped at an approval; the app restarted; the card is answered
    _write(store, [_turn("R1", "__run__run-1", 1, 1_000, automation=run, status="unknown")],
           [_step("R1", "__run__run-1", 1, 1_000)])
    got = _resume(manager, "__run__run-1")
    assert got["automation"] == run and got["wake"]["reason"] == "answer"
    # a follow-up typed into the run's session later is not the run's, nor is its resume (TD3-23)
    _write(store, [_turn("R2", "__run__run-1", 2, 9_000, status="unknown")], [_step("R2", "__run__run-1", 1, 9_000)])
    assert _resume(manager, "__run__run-1")["automation"] is None
    # an ordinary session's resume carries none
    _write(store, [_turn("S1", "plain", 1, 1_000, automation=run)], [_step("S1", "plain", 1, 1_000)])
    assert _resume(manager, "plain")["automation"] is None
    manager.observability.close()
