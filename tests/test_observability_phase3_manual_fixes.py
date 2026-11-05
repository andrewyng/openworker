"""Phase 3 manual testing (5 Nov), the server side of rows T3-6 and T3-10: the Auto-approve table
counts "overridden" per reviewer, and the Ratings card's numbers."""

from __future__ import annotations

import time

from coworker.observability import RecordingStore, usage_api

# Turns an hour old: a store with the background writer sweeps on its first tick.
RECENT = int(time.time() * 1000) - 3_600_000


def _turn(tid, sid, seq, at, reviewer_kind=None):
    return {"turn_id": tid, "session_id": sid, "seq": seq, "kind": "run", "trigger": "user", "started_at": at,
            "duration_ms": 1000, "status": "completed", "incomplete": False, "approval_mode": "auto_approve",
            "attended": True, "reviewer": {"kind": reviewer_kind, "active": True, "shadow": False} if reviewer_kind else None,
            "stamp": {"machine": {"kind": "this_machine", "id": None}}, "first_message_id": None,
            "last_message_id": None, "schema_version": 1, "wake": {"reason": "user"}}


def _step(tid, sid, n, at, kind, attrs):
    return {"step_id": f"{tid}-s{n}", "turn_id": tid, "session_id": sid, "parent_step_id": None, "seq": n,
            "kind": kind, "name": kind, "started_at": at, "duration_ms": 100, "status": "ok", "call_id": None,
            "call_index": None, "message_id": None, "shadow": False, "after_turn": False, "attrs": attrs,
            "content": {}}


def _check(result, kind):
    return {"result": result, "reviewer": {"kind": kind}}


OVERRIDE = {"decision": {"path": [{"stage": "auto_approver", "outcome": "deny"}, {"stage": "grant", "outcome": "allow_anyway"}]}}


def test_overridden_is_counted_per_reviewer(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    turns = [_turn("A", "s1", 1, RECENT, "session_model"), _turn("B", "s2", 1, RECENT + 10, "typesafe_jev"),
             _turn("C", "s2", 2, RECENT + 20, "typesafe_jev"), _turn("D", "s3", 1, RECENT + 30, "openai_decisions")]
    steps = [
        _step("A", "s1", 1, RECENT, "auto_approver", _check("allow", "session_model")),
        _step("A", "s1", 2, RECENT + 1, "auto_approver", _check("allow", "session_model")),
        _step("B", "s2", 1, RECENT + 10, "auto_approver", _check("deny", "typesafe_jev")),
        # the person let Jev's block through, twice
        _step("C", "s2", 1, RECENT + 20, "tool", OVERRIDE),
        _step("C", "s2", 2, RECENT + 21, "tool", OVERRIDE),
        _step("D", "s3", 1, RECENT + 30, "auto_approver", _check("unsure", "openai_decisions")),
        # the reviewers' own model calls: tokens per reviewer
        _step("A", "s1", 3, RECENT + 2, "model", {"purpose": "auto_approver", "model": "model-x", "usage": {
            "reported": True, "input_fresh": 300, "cache_read": 0, "cache_write": 0, "output": 20}}),
        _step("D", "s3", 2, RECENT + 31, "model", {"purpose": "auto_approver", "model": "model-y", "usage": {
            "reported": True, "input_fresh": 50, "cache_read": 0, "cache_write": 0, "output": 5}}),
        _step("A", "s1", 4, RECENT + 3, "model", {"purpose": "main", "model": "model-x", "usage": {
            "reported": True, "input_fresh": 9000, "cache_read": 0, "cache_write": 0, "output": 100}}),
    ]
    for t in turns:
        store.write_turn(t)
    for s in steps:
        store.write_step(s)
    store.flush()
    got = usage_api.approver(store, start=RECENT - 1, end=RECENT + 1000)
    assert got["overridden"] == 2  # the total, as before
    rows = {r["kind"]: r for r in got["by_reviewer"]}
    assert set(rows) == {"session_model", "typesafe_jev", "openai_decisions"}
    assert rows["typesafe_jev"]["overridden"] == 2
    assert rows["session_model"]["overridden"] == 0 and rows["openai_decisions"]["overridden"] == 0
    assert rows["session_model"]["checks"] == 2 and rows["openai_decisions"]["asked"] == 1
    # each reviewer's own tokens; main work is not in them
    assert rows["session_model"]["tokens"] == 320 and rows["openai_decisions"]["tokens"] == 55
    assert rows["typesafe_jev"]["tokens"] == 0
    store.close()
