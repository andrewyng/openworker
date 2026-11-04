"""Observability Phase 3 golden test: rollups over many turns, and the series and task checks.

`tests/corpora/observability/golden-phase3/` is a copy of ocw-context
docs/observability/golden/phase3 (recording format 1.3, signals.yaml 0.7). The Rust harness runs
the same files, so both engines must give the same totals for the same recordings and the same
issues for the same team, security task or automation.

Rollup scenarios (S3-01 to S3-15) go through the app's own path: every turn and step is written
to a `RecordingStore`, with its stored Issues result; the store works out the `turn_usage`
ledger at the write (E24a); the screens' queries run over that ledger (`usage_rollup`). Every
grouping, the pills, the turn counts, the proxy line and the Auto-approve strip must equal
`expected-rollup.json`, and the hand-worked edge values in `index.json`.

Check scenarios (S3-40 to S3-62) feed their input to `observability.scopes` (series and task
checks) and to `signals.check_turn` (the turn checks `wake_capped` and `watchdog_nudged`)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from coworker.observability import RecordingStore, rollups, scopes, signals

GOLDEN = Path(__file__).parent / "corpora" / "observability" / "golden-phase3"
INDEX = json.loads((GOLDEN / "index.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((Path(__file__).parent / "corpora/observability/recording-format.schema.json").read_text("utf-8"))
DAYS = [tuple(d) for d in json.loads((GOLDEN / "vectors" / "days-europe-london.json").read_text("utf-8"))["days"]]
GROUPINGS = [g.split("+") for g in INDEX["groupings"]]
ALL = (0, 2**62)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _key(values) -> str:
    return "|".join("null" if v is None else str(v) for v in values)


def _stored_issues(counts):
    """The stored Issues result a turn would have: `issues.json` gives only its counts."""
    if counts is None:
        return None
    return {"signals_version": signals.version(), "warn": counts["warn"], "info": counts["info"],
            "issues": [{"check": c, "severity": "warn"} for c in counts.get("checks", [])]
            + [{"check": "x", "severity": "warn"}] * (counts["warn"] - len(counts.get("checks", [])))
            + [{"check": "y", "severity": "info"}] * counts["info"]}


def _store_with(tmp_path: Path, rec: dict, issues: dict) -> RecordingStore:
    store = RecordingStore(tmp_path / "recordings.db", background=False, retention_days=None)
    for t in rec["turns"]:
        store.write_turn(t)
    for s in rec["steps"]:
        store.write_step(s)
    for t in rec["turns"]:
        res = _stored_issues(issues.get(t["turn_id"]))
        if res is not None:
            store.write_issues(t["turn_id"], res, seen=True)
        store.refresh_usage(t["turn_id"])
    store.flush()
    return store


def _validator():
    return jsonschema.Draft202012Validator({**SCHEMA, "$ref": "#/$defs/recording"})


ROLLUPS = [s["id"] for s in INDEX["scenarios"] if s["kind"] == "rollup" and s["id"] != "S3-11"]
CHECKS = [s for s in INDEX["scenarios"] if s["kind"] != "rollup"]


def test_the_corpus_is_the_one_this_engine_targets():
    assert INDEX["format"] == "1.3"
    assert INDEX["signals_version"] == signals.version()
    assert len(INDEX["scenarios"]) == 26


@pytest.mark.parametrize("sid", ROLLUPS)
def test_rollup_scenario_through_the_ledger(sid, tmp_path):
    rec, issues = _load(GOLDEN / sid / "recording.json"), _load(GOLDEN / sid / "issues.json")
    want = _load(GOLDEN / sid / "expected-rollup.json")
    errs = list(_validator().iter_errors(rec))
    assert not errs, errs[0].message
    store = _store_with(tmp_path, rec, issues)
    try:
        # each turn's own numbers (the reference the ledger is built from)
        steps_of: dict = {}
        for s in rec["steps"]:
            steps_of.setdefault(s["turn_id"], []).append(s)
        got_turns = {}
        for t in rec["turns"]:
            n = rollups.turn_numbers(t, steps_of.get(t["turn_id"], []), _stored_issues(issues.get(t["turn_id"])))
            got_turns[t["turn_id"]] = {
                "total": n["total"], "at_least": n["at_least"], "unreported": n["unreported"],
                "failed": n["failed"], "over_limit": n["over_limit"], "waiting_ms": n["waiting_ms"],
                "active_ms": n["active_ms"], "approvals": len(n["approvals"]),
                "approval_calls": sorted(n["approvals"]), "warn": n["warn"], "live_checks": len(n["checks"])}
        assert got_turns == want["turns"]
        # every grouping, one GROUP BY each over the ledger
        for g in GROUPINGS:
            rows = store.usage_rollup(g, *ALL, days=DAYS if "day" in g else None)
            got = {_key([r["key"][k] for k in g]): r["total"] for r in rows if r["total"]}
            assert got == want["rollups"]["+".join(g)], "+".join(g)
        # the pills: each session's total, and their sum is the page's total
        pills = {r["key"]["session"]: r["total"] for r in store.usage_rollup(["session"], *ALL)}
        assert {k: v for k, v in pills.items() if k in want["pills"]} == want["pills"]
        assert sum(want["pills"].values()) == want["rollups"]["all"].get("all", 0)
        # turns, checked, with a warning
        for g in (["all"], ["day"], ["persona_id"], ["persona_fp"]):
            rows = store.usage_rollup(g, *ALL, days=DAYS if g == ["day"] else None)
            got = {_key([r["key"][k] for k in g]): {"turns": r["turns"], "checked": r["checked"],
                                                    "with_warn": r["with_warn"]} for r in rows}
            assert got == want["turn_counts"]["+".join(g)], "+".join(g)
        assert store.outside_calls_between(*ALL) == want["no_turn"]
        res: dict = {}
        for s in store.approver_steps(*ALL):
            if s["kind"] == "auto_approver" and not s["shadow"] and s["parent_step_id"] is None:
                r = s["attrs"].get("result") or "unknown"
                res[r] = res.get(r, 0) + 1
        aa = {r["key"]["purpose"]: r["total"] for r in store.usage_rollup(["purpose"], *ALL)}.get("auto_approver", 0)
        assert {"by_result": dict(sorted(res.items())), "tokens": aa} == want["approver"]
        # the hand-worked values (phase-3/scenarios.md)
        sc = next(s for s in INDEX["scenarios"] if s["id"] == sid)
        if "hand" in sc:
            assert _hand(sid, store, got_turns) == sc["hand"]
    finally:
        store.close()


def _hand(sid: str, store: RecordingStore, turns: dict) -> dict:
    t = list(turns.values())
    day = {r["key"]["day"]: r for r in store.usage_rollup(["day"], *ALL, days=DAYS)}
    purpose = {r["key"]["purpose"]: r["total"] for r in store.usage_rollup(["purpose"], *ALL)}
    if sid == "S3-02":
        return {k: day.get(k, {}).get("total", 0) for k in ("2026-10-12", "2026-10-13")}
    if sid == "S3-03":
        return {"turn_total": t[0]["total"], "day": next(k for k, v in day.items() if v["total"])}
    if sid == "S3-04":
        nt = store.outside_calls_between(*ALL)
        return {"no_turn_total": nt["total"], "no_turn_calls": nt["calls"]}
    if sid == "S3-05":
        return {"unreported": t[0]["unreported"], "total": t[0]["total"]}
    if sid == "S3-06":
        return {"failed": t[0]["failed"], "total": t[0]["total"], "unreported": t[0]["unreported"]}
    if sid == "S3-07":
        return {"auto_approver_tokens": purpose.get("auto_approver", 0), "live_checks": t[0]["live_checks"]}
    if sid == "S3-08":
        return {"total": t[0]["total"], "helper": purpose.get("helper", 0)}
    if sid == "S3-09":
        return {"total": t[0]["total"], "active_ms": t[0]["active_ms"]}
    if sid == "S3-10":
        return {k: day.get(k, {}).get("turns", 0) for k in ("2026-10-25", "2026-10-26")}
    if sid == "S3-13":
        return {"approvals": len({c for x in t for c in x["approval_calls"]})}
    if sid == "S3-14":
        r = store.usage_rollup(["all"], *ALL)[0]
        return {"turns": r["turns"], "checked": r["checked"], "with_warn": r["with_warn"]}
    if sid == "S3-15":
        return {"active_ms": t[0]["active_ms"]}
    raise AssertionError(sid)


def test_a_deleted_session_keeps_its_counts_in_the_ledger(tmp_path):
    spec = _load(GOLDEN / "S3-11" / "scenario.json")
    base = spec["base"]
    rec, issues = _load(GOLDEN / base / "recording.json"), _load(GOLDEN / base / "issues.json")
    store = _store_with(tmp_path, rec, issues)
    try:
        e = spec["expected"]
        assert store.usage_rollup(["all"], *ALL)[0]["total"] == e["total_before"]
        store.delete_session(spec["delete_session"])
        # the steps lose the session ...
        left = store._conn.execute(
            "SELECT SUM(COALESCE(input_fresh,0)+COALESCE(cache_read,0)+COALESCE(cache_write,0)+COALESCE(output,0))"
            " FROM steps WHERE kind = 'model' AND usage_reported = 1").fetchone()[0]
        assert (left, e["total_before"] - left) == (e["total_after_steps"], e["removed"])
        # ... the ledger keeps it, marked deleted
        assert store.usage_rollup(["all"], *ALL)[0]["total"] == e["total_from_ledger"]
        gone = [t for t in store.usage_turns(*ALL) if t["session_id"] == spec["delete_session"]]
        assert gone and all(t["session_deleted"] == 1 for t in gone)
    finally:
        store.close()


def _check(kind: str, inp: dict, tmp_path: Path) -> dict:
    """What this engine gives for one check scenario, in the shape of `expected.json`."""
    rec = inp.get("recording") or {"turns": [], "steps": []}
    if kind == "team":
        store = _store_with(tmp_path, rec, {})
        try:
            split = {}
            for r in store.usage_rollup(["wake_reason"], *ALL, where={"team_id": "team-1"}):
                split[r["key"]["wake_reason"]] = {"wakes": r["turns"], "tokens": r["total"],
                                                  "cache_read": r["cache_read"],
                                                  "input": r["input_fresh"] + r["cache_read"] + r["cache_write"]}
            team_total = store.usage_rollup(["all"], *ALL, where={"team_id": "team-1"})[0]["total"]
        finally:
            store.close()
        turns = rec["turns"]
        lead = [{"id": t["turn_id"], "wake": t["wake"]} for t in turns if t["stamp"]["team"]["role"] == "lead"]
        turn_checks: dict = {}
        for t in turns:
            res = signals.check_turn(t, [s for s in rec["steps"] if s["turn_id"] == t["turn_id"]])
            for i in res["issues"]:
                if i["check"] in ("wake_capped", "watchdog_nudged"):
                    turn_checks.setdefault(i["check"], []).append(t["turn_id"])
        known = sum(1 for t in turns if t["wake"]["reason"] != "unknown")
        return {"team_turns": len(turns),
                "explained_1_2": sum(1 for t in turns if t["trigger"] == "user" or t.get("woken_by")),
                "explained_1_3": known, "gate_95": known / len(turns) >= 0.95,
                "by_reason": dict(sorted(split.items())), "team_total": team_total,
                "lead_polling": scopes.lead_polling(lead), "turn_checks": dict(sorted(turn_checks.items()))}
    if kind == "approvals":
        tools = {s["call_id"]: s for s in rec["steps"] if s["kind"] == "tool"}
        cards = [{"tool": tools[s["call_id"]]["name"], "args_hash": tools[s["call_id"]]["attrs"]["args_hash"],
                  "session": s["session_id"], "outcome": s["attrs"].get("outcome")}
                 for s in sorted(rec["steps"], key=lambda x: x["started_at"]) if s["kind"] == "wait"]
        return {"same_approval_twice": scopes.same_approval_twice(cards)}
    if kind == "board":
        return {"reviewed_before_prereq": scopes.reviewed_before_prereq(inp["events"], inp["links"])}
    if kind == "coverage":
        t = rec["turns"][0]
        task = {"promised": t["stamp"].get("persona_coverage") or [], **inp["task"],
                "steps": [{"id": s["step_id"], "scanner": s["attrs"].get("scanner"),
                           "exit_code": s["attrs"].get("exit_code")} for s in rec["steps"] if s["kind"] == "tool"]}
        return scopes.coverage_checks(task)
    if kind == "automation":
        by_run = {t["automation"]["run_id"]: t for t in rec["turns"] if t.get("automation")}
        iss = inp.get("issues") or {}
        health = []
        for r in inp["runs"]:
            t = by_run.get(r["run_id"])
            summary = None if t is None else {"status": t["status"],
                                              "warn_checks": (iss.get(t["turn_id"]) or {}).get("checks", [])}
            # A run with no recorded turn reads from its run record only (§12.5).
            health.append(scopes.run_health(r, summary) if t is not None or r["status"] != "ok" else "ok")
        runs = [{"health": h, "error": r.get("error")} for h, r in zip(health, inp["runs"])]
        return {"health": health, "failed_in_a_row": scopes.failed_in_a_row(runs)}
    raise AssertionError(kind)


@pytest.mark.parametrize("sc", CHECKS, ids=[s["id"] for s in CHECKS])
def test_check_scenario(sc, tmp_path):
    d = GOLDEN / sc["id"]
    inp, want = _load(d / "input.json"), _load(d / "expected.json")
    if inp.get("recording"):
        errs = list(_validator().iter_errors(inp["recording"]))
        assert not errs, errs[0].message
    got = json.loads(json.dumps(_check(sc["kind"], inp, tmp_path)))
    assert got == want
    if sc["kind"] == "automation":
        fired = want["failed_in_a_row"] is not None
    elif sc["kind"] == "team":
        fired = True
    else:
        fired = any(want.values())
    assert (sc.get("should") == "fire") == fired


def test_scanner_vectors():
    for case in _load(GOLDEN / "vectors" / "scanners.json")["cases"]:
        assert scopes.scanner_of(case["command"]) == case["scanner"], case["command"]


def test_scanner_not_found_vectors():
    """signals 0.7 `scanners.not_found` (P3B-1): the scanner the engine records after the step
    ran, by the same function the engine calls."""
    v = _load(GOLDEN / "vectors" / "scanner-not-found.json")
    assert len(v["cases"]) == 10
    for case in v["cases"]:
        got = scopes.recorded_scanner(case["command"], exit_code=case["exit_code"], output=case["output"])
        assert got == case["scanner"], case["shows"]
        assert (got is None) == case["not_installed"], case["shows"]
        # the message may come in the result's error instead of its output
        assert scopes.recorded_scanner(case["command"], exit_code=case["exit_code"], output="",
                                       error=case["output"]) == case["scanner"], case["shows"]


def test_day_vectors_read_as_the_client_sends_them():
    v = _load(GOLDEN / "vectors" / "days-europe-london.json")
    days = rollups.parse_days(json.dumps(v["days"]))
    assert days == DAYS and len(days) == 30
    dst = next(d for d in days if d[0] == "2026-10-25")
    assert (dst[2] - dst[1]) / 3_600_000 == v["dst_day"]["hours"] == 25
