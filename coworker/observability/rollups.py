"""Rollups over many turns (Phase 3, OB-8, OB-15, OB-17, OB-18; tracing-spec §6 "Rollups").

The rule (TD3-1): **a step belongs to its turn, a turn belongs to one period by its
`started_at`, and a rollup is a sum of whole turns.** So a turn is never split between two
days, coworkers or machines, and every grouping adds up to the same total: the sum of the
pills.

Two parts:
- `turn_numbers()` works out one turn's numbers from its record and steps, by the Phase 1
  counting rule (`calculator.py`): total = fresh + cache read + cache write + output over
  model steps that reported usage; a completed call without usage makes it "at least";
  failed and over-limit calls are counted on their own. It is the lab's reference
  (docs/observability/phase-3/lab/rollups.py `turn_numbers`), moved here over step dicts.
- `ledger_rows()` turns those numbers into the `turn_usage` rows (attributes.yaml Part 3):
  one per turn × purpose × model, content-free. The store keeps them 90 days and keeps them
  when a session is deleted (TD3-3, TD3-5). `GROUPS` and `rollup_sql()` are the queries the
  screens run over them, with the viewer's day boundaries (TD3-2).

Built differently from attributes.yaml Part 3, on purpose: the turn-level columns (`warn`,
`info`, `approvals`, `rating`, `warn_checks`) are copied onto every row of the turn instead of
the first row only, so a purpose or model group can count its own turns checked and with a
warning with one query. Row `first = 1` marks the one row turn-level sums read.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

TOKEN_PARTS = ("input_fresh", "cache_read", "cache_write", "output")


def _union_ms(intervals: Iterable[tuple[int, int]]) -> int:
    """Length of the union of [start, end) intervals: overlapping time counts once."""
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def median(values: list[float]) -> Optional[float]:
    v = sorted(values)
    if not v:
        return None
    n = len(v)
    return float(v[n // 2]) if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def machine_key(stamp: Optional[dict]) -> str:
    """The machine a turn ran on: `stamp.machine.id`, else its kind (the desktop's own
    machine has no id until it joins a controller), else `unknown`."""
    m = (stamp or {}).get("machine") or {}
    return m.get("id") or m.get("kind") or "unknown"


def _usage(step: dict) -> Optional[dict]:
    u = (step.get("attrs") or {}).get("usage")
    if not isinstance(u, dict):
        return None
    return u if u.get("reported", True) is not False else None


def turn_numbers(turn: dict, steps: list[dict], issues: Optional[dict] = None) -> dict[str, Any]:
    """One turn's numbers (the reference every rollup adds up). `issues` is the turn's stored
    Issues result (`{warn, info, issues: [...]}`), or None when it was never checked."""
    t0 = turn.get("started_at") or 0
    dur = turn.get("duration_ms")
    t1 = t0 + dur if dur is not None else None
    out: dict[str, Any] = {
        "total": 0, **{p: 0 for p in TOKEN_PARTS}, "reasoning": 0, "cache_read_known_input": 0,
        "calls": 0, "unreported": 0, "failed": 0, "over_limit": 0, "by_pm": {},
        "waiting_ms": 0, "active_ms": None, "approvals": set(), "checks": [],
        "auto_approved": [], "asked": set(),
    }
    waits: list[tuple[int, int]] = []
    for s in steps:
        kind = s.get("kind")
        a = s.get("attrs") or {}
        if kind == "model":
            key = (a.get("purpose"), a.get("model"))
            pm = out["by_pm"].setdefault(key, {"total": 0, **{p: 0 for p in TOKEN_PARTS}, "reasoning": 0,
                                               "cache_read_known_input": 0, "calls": 0, "unreported": 0,
                                               "failed": 0, "over_limit": 0})
            err = (a.get("error") or {}).get("type") if isinstance(a.get("error"), dict) else None
            if s.get("status") == "error":
                k = "over_limit" if err == "context_length" else "failed"
                out[k] += 1
                pm[k] += 1
            u = _usage(s)
            if u is not None:
                vals = {p: int(u.get(p) or 0) for p in TOKEN_PARTS}
                tot = sum(vals.values())
                for target in (out, pm):
                    target["total"] += tot
                    target["calls"] += 1
                    target["reasoning"] += int(u.get("reasoning") or 0)
                    for p in TOKEN_PARTS:
                        target[p] += vals[p]
                    if u.get("cache_read") is not None:
                        target["cache_read_known_input"] += vals["input_fresh"] + vals["cache_read"] + vals["cache_write"]
            elif s.get("status") == "ok":
                out["unreported"] += 1
                pm["unreported"] += 1
        elif kind == "wait":
            # a card counts once per tool call, even when a crash left its wait open and the
            # resume turn shows it again (TD3-12)
            if a.get("kind") == "approval" and a.get("for_call_id"):
                out["approvals"].add(a["for_call_id"])
            if s.get("duration_ms") is not None:
                lo, hi = s.get("started_at") or 0, (s.get("started_at") or 0) + s["duration_ms"]
                if t1 is not None:
                    lo, hi = max(lo, t0), min(hi, t1)
                waits.append((lo, hi))
        elif kind == "auto_approver" and not s.get("shadow") and s.get("parent_step_id") is None:
            out["checks"].append({"result": a.get("result"), "ms": s.get("duration_ms"), "call_id": s.get("call_id"),
                                  "reviewer": (a.get("reviewer") or {}).get("kind")})
        elif kind == "tool" and s.get("parent_step_id") is None:
            origin = (a.get("decision") or {}).get("origin")
            if origin == "reviewer":
                out["auto_approved"].append(s.get("step_id"))
    out["waiting_ms"] = _union_ms(waits)
    if dur is not None:
        out["active_ms"] = dur - out["waiting_ms"]
    if issues is None:
        out["warn"] = out["info"] = None
        out["warn_checks"] = []
    else:
        lst = issues.get("issues") or []
        out["warn"] = int(issues["warn"]) if issues.get("warn") is not None else sum(
            1 for i in lst if i.get("severity") == "warn")
        out["info"] = int(issues["info"]) if issues.get("info") is not None else sum(
            1 for i in lst if i.get("severity") == "info")
        out["warn_checks"] = sorted({i.get("check") for i in lst if i.get("severity") == "warn" and i.get("check")})
    out["at_least"] = out["unreported"] > 0
    return out


LEDGER_COLUMNS = (
    "turn_id", "purpose", "model", "first", "session_id", "session_deleted", "started_at", "trigger",
    "wake_reason", "persona_id", "persona_fp", "machine", "team_id", "team_role", "task_id", "run_id",
    "automation_trigger", "status", "duration_ms", "waiting_ms",
    "input_fresh", "cache_read", "cache_write", "output", "reasoning", "cache_read_known_input",
    "calls", "unreported", "failed", "over_limit", "warn", "info", "warn_checks", "approvals",
    "auto_approved", "live_checks", "rating", "signals_version", "updated_at",
)


def ledger_rows(turn: dict, steps: list[dict], issues: Optional[dict] = None, *,
                persona_id: Optional[str] = None, rating: Optional[str] = None,
                now_ms: int = 0) -> list[dict[str, Any]]:
    """The `turn_usage` rows of one turn: one per purpose × model of its model steps (a turn
    with none gets one row with both null). Ids, counts and times only, never `content`.
    `persona_id` stands in when the stamp lacks it (a 1.2 turn: the session's coworker)."""
    n = turn_numbers(turn, steps, issues)
    stamp = turn.get("stamp") or {}
    wake = turn.get("wake") or {}
    auto = turn.get("automation") or {}
    team = stamp.get("team") or {}
    base = {
        "turn_id": turn["turn_id"], "session_id": turn.get("session_id"), "session_deleted": 0,
        "started_at": turn.get("started_at"), "trigger": turn.get("trigger"),
        "wake_reason": wake.get("reason"), "persona_id": stamp.get("persona_id") or persona_id,
        "persona_fp": stamp.get("persona_fp"), "machine": machine_key(stamp),
        "team_id": team.get("team_id"), "team_role": team.get("role"),
        "task_id": auto.get("task_id"), "run_id": auto.get("run_id"), "automation_trigger": auto.get("trigger"),
        "status": turn.get("status"), "duration_ms": turn.get("duration_ms"), "waiting_ms": n["waiting_ms"],
        "warn": n["warn"], "info": n["info"], "warn_checks": json.dumps(n["warn_checks"]),
        "approvals": len(n["approvals"]), "auto_approved": len(n["auto_approved"]),
        "live_checks": len(n["checks"]), "rating": rating,
        "signals_version": (issues or {}).get("signals_version"), "updated_at": now_ms,
    }
    pms = sorted(n["by_pm"].items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")) or [
        ((None, None), {"total": 0, **{p: 0 for p in TOKEN_PARTS}, "reasoning": 0, "cache_read_known_input": 0,
                        "calls": 0, "unreported": 0, "failed": 0, "over_limit": 0})]
    rows = []
    for i, ((purpose, model), v) in enumerate(pms):
        rows.append({**base, "purpose": purpose, "model": model, "first": 1 if i == 0 else 0,
                     **{k: v[k] for k in (*TOKEN_PARTS, "reasoning", "cache_read_known_input", "calls",
                                          "unreported", "failed", "over_limit")}})
    return rows


# -- the queries ----------------------------------------------------------------------------------
# What each `group` name means, over the ledger (alias u). Every expression is a turn's own
# field, never a step's start time, so a turn is never split (TD3-1).
GROUPS = {
    "all": "'all'",
    "session": "u.session_id",
    "trigger": "u.trigger",
    "persona_fp": "u.persona_fp",
    "persona_id": "u.persona_id",
    "machine": "u.machine",
    "purpose": "COALESCE(u.purpose, 'unattributed')",
    "model": "COALESCE(u.model, 'unknown')",
    "wake_reason": "COALESCE(u.wake_reason, 'unknown')",
    "team_role": "u.team_role",
    "team_id": "u.team_id",
    "run_id": "u.run_id",
    "task_id": "u.task_id",
    "status": "u.status",
    "day": "d.label",
}
# Groups inside a turn (a turn has many purposes and models); the rest group whole turns.
STEP_GROUPS = {"purpose", "model"}

_TOTAL = "u.input_fresh + u.cache_read + u.cache_write + u.output"


def rollup_sql(group: list[str], *, days: Optional[list] = None, where: str = "") -> tuple[str, int]:
    """One GROUP BY over the ledger. Returns (sql, number of key columns). The parameters are
    the day boundaries (label, start, end) flattened, then `from`, `to`, then `where`'s own."""
    for g in group:
        if g not in GROUPS:
            raise ValueError(f"unknown group {g!r}")
    keys = ", ".join(GROUPS[g] for g in group) or "'all'"
    with_days = ""
    join = ""
    if days:
        with_days = "WITH d(label, start, end) AS (VALUES " + ", ".join("(?, ?, ?)" for _ in days) + ") "
        join = " JOIN d ON u.started_at >= d.start AND u.started_at < d.end"
    sql = (
        f"{with_days}SELECT {keys}, SUM({_TOTAL}), SUM(u.input_fresh), SUM(u.cache_read), SUM(u.cache_write),"
        " SUM(u.output), SUM(u.reasoning), SUM(u.cache_read_known_input), SUM(u.calls), SUM(u.unreported),"
        " SUM(u.failed), SUM(u.over_limit), COUNT(DISTINCT u.turn_id),"
        " COUNT(DISTINCT CASE WHEN u.warn IS NOT NULL THEN u.turn_id END),"
        " COUNT(DISTINCT CASE WHEN u.warn > 0 THEN u.turn_id END),"
        " COUNT(DISTINCT CASE WHEN u.run_id IS NOT NULL THEN u.turn_id END),"
        " SUM(CASE WHEN u.first = 1 THEN u.approvals ELSE 0 END)"
        f" FROM turn_usage u{join} WHERE u.started_at >= ? AND u.started_at < ?{where}"
        f" GROUP BY {keys}"
    )
    return sql, max(1, len(group))


ROLLUP_FIELDS = ("total", "input_fresh", "cache_read", "cache_write", "output", "reasoning",
                 "cache_read_known_input", "calls", "unreported", "failed", "over_limit", "turns", "checked",
                 "with_warn", "automation_turns", "approvals")


def day_params(days: Optional[list]) -> list:
    out: list = []
    for d in days or []:
        out += [str(d[0]), int(d[1]), int(d[2])]
    return out


def parse_days(raw: Any) -> list[tuple[str, int, int]]:
    """The client's day boundaries: `[[label, start, end], ...]` as JSON text or a list.
    Each is a half-open [start, end) in epoch ms, the viewer's own local day (TD3-2)."""
    if raw in (None, ""):
        return []
    val = json.loads(raw) if isinstance(raw, str) else raw
    out = []
    for d in val or []:
        label, a, b = d
        if int(b) <= int(a):
            raise ValueError("a day must end after it starts")
        out.append((str(label), int(a), int(b)))
    return out
