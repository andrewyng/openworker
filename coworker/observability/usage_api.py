"""The Phase 3 read endpoints, as plain functions over the store (design §4).

Each takes the `RecordingStore` (and, where a screen needs names, a lookup the server passes)
and returns the JSON the endpoint serves. All of them read the `turn_usage` ledger, so their
totals add up to the pills (gate 1): the Usage page (OB-8), the coworker page's "How it's been
doing" (OB-15), TeamView's wake split and events (OB-17) and the Scheduled view's run health
(OB-18). Tokens only: no prices anywhere.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Optional

from . import scopes
from . import signals as _signals
from .rollups import STEP_GROUPS, median

MIN_RUNS_TO_COMPARE = 10  # TD3-14


def _turn_groups(group: list[str]) -> bool:
    return not (set(group) & STEP_GROUPS)


def rollup(store: Any, *, start: int, end: int, group: list[str], days: Optional[list] = None,
           where: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """`GET /v1/observability/rollup`: the ledger grouped, the proxy line, how many turns of
    the period are not checked yet, and how far back the ledger goes (`kept_from`).
    Turn groups also get each group's median time without waiting (crash-open turns, with no
    length, are left out)."""
    groups = store.usage_rollup(group, start, end, days=days, where=where)
    turns = store.usage_turns(start, end, where=where)
    version = _signals.version()
    not_checked = sum(1 for t in turns if t["warn"] is None or t.get("signals_version") not in (None, version))
    if _turn_groups(group):
        by_key: dict[tuple, list[int]] = {}
        day_of = None
        if "day" in group and days:
            def day_of(ts: int) -> Optional[str]:  # noqa: E306
                return next((d[0] for d in days if d[1] <= ts < d[2]), None)
        for t in turns:
            if t["active_ms"] is None:
                continue
            key = []
            for g in group:
                if g == "all":
                    key.append("all")
                elif g == "day":
                    key.append(day_of(t["started_at"]) if day_of else None)
                elif g == "session":
                    key.append(t["session_id"])
                elif g == "wake_reason":
                    key.append(t.get("wake_reason") or "unknown")
                else:
                    key.append(t.get(g))
            by_key.setdefault(tuple(key), []).append(t["active_ms"])
        for gr in groups:
            k = tuple(gr["key"][g] for g in group) if group else ("all",)
            gr["median_active_ms"] = median(by_key.get(k, []))
    return {"groups": groups, "no_turn": store.outside_calls_between(start, end),
            "not_checked": not_checked, "kept_from": store.usage_kept_from(),
            "turns": len(turns), "deleted_session_turns": sum(1 for t in turns if t["session_deleted"])}


def heaviest(store: Any, *, start: int, end: int, limit: int = 20,
             title_of: Optional[Callable[[str], Optional[str]]] = None,
             persona_id: Optional[str] = None) -> dict[str, Any]:
    """`GET /v1/observability/turns?order=tokens`: the heaviest turns of the period. Each names
    its turn (`seq`, M3-R2), says when it was found open after a crash (M3-R3), and lists its
    warning checks. A deleted session's turn has no title and no link."""
    where = {"persona_id": persona_id} if persona_id else None
    turns = sorted(store.usage_turns(start, end, where=where), key=lambda t: (-t["total"], t["started_at"]))[: max(1, min(limit, 100))]
    seqs = store.turn_seqs([t["turn_id"] for t in turns]) if turns else {}
    out = []
    for t in turns:
        title = None
        if title_of is not None and not t["session_deleted"]:
            try:
                title = title_of(t["session_id"])
            except Exception:  # noqa: BLE001
                title = None
        out.append({"turn_id": t["turn_id"], "session_id": t["session_id"], "title": title,
                    "seq": seqs.get(t["turn_id"]), "session_deleted": bool(t["session_deleted"]),
                    "persona_id": t["persona_id"], "model_ids": t["models"], "total": t["total"],
                    "at_least": t["at_least"], "active_ms": t["active_ms"], "status": t["status"],
                    "found_open": t["status"] == "unknown" and t["duration_ms"] is None,
                    "warn": t["warn"], "checks": t["warn_checks"], "started_at": t["started_at"],
                    "run_id": t["run_id"], "task_id": t["task_id"]})
    return {"turns": out}


ASKED = ("unsure", "context_changed", "could_not_run")


def approver(store: Any, *, start: int, end: int, persona_id: Optional[str] = None) -> dict[str, Any]:
    """`GET /v1/observability/approver`: the Auto-approve strip (§7.3). Live checks by result
    (asked you includes could-not-run: the card falls back to asking), overridden tool
    steps, the median check time, and the checks' tokens with shadow checks included, as
    the pill counts them. One line per reviewer kind used in the period."""
    steps = store.approver_steps(start, end, persona_id=persona_id)
    live = [s for s in steps if s["kind"] == "auto_approver" and not s["shadow"] and s["parent_step_id"] is None]
    shadow = [s for s in steps if s["kind"] == "auto_approver" and s["shadow"]]
    overrides = [s for s in steps if s["kind"] == "tool" and any(
        x.get("stage") == "grant" and x.get("outcome") == "allow_anyway"
        for x in (s["attrs"].get("decision") or {}).get("path") or [])]
    # T3-6: an override counts for the reviewer its turn ran with (the one whose block was
    # let through); the step itself names none, as no reviewer is asked again.
    overridden_by: dict[str, int] = {}
    for s in overrides:
        k = (s.get("turn_reviewer") or {}).get("kind") or "unknown"
        overridden_by[k] = overridden_by.get(k, 0) + 1

    def counts(rows: list[dict]) -> dict[str, Any]:
        res = [r["attrs"].get("result") for r in rows]
        return {"checks": len(rows), "allowed": res.count("allow"), "asked": sum(r in ASKED for r in res),
                "blocked": res.count("deny"), "could_not_run": res.count("could_not_run"),
                "median_ms": median([r["duration_ms"] for r in rows if r["duration_ms"] is not None])}

    by_kind: dict[str, list] = {}
    for s in live:
        by_kind.setdefault((s["attrs"].get("reviewer") or {}).get("kind") or "unknown", []).append(s)
    tokens = sum(g["total"] for g in store.usage_rollup(["purpose"], start, end,
                                                        where={"persona_id": persona_id} if persona_id else None)
                 if g["key"]["purpose"] == "auto_approver")
    tokens_by = store.approver_tokens_by_reviewer(start, end, persona_id=persona_id)
    return {**counts(live), "overridden": len(overrides), "tokens": tokens, "shadow_checks": len(shadow),
            "by_reviewer": [{"kind": k, **counts(v), "overridden": overridden_by.get(k, 0),
                             "tokens": tokens_by.get(k, 0)} for k, v in sorted(by_kind.items())],
            "shown": bool(live)}


# -- OB-15: how a coworker has been doing ------------------------------------------------------


def persona_doing(store: Any, persona_id: str, *, start: int, end: int, current_fp: Optional[str] = None,
                  min_runs: int = MIN_RUNS_TO_COMPARE) -> dict[str, Any]:
    """`GET /v1/personas/{id}/doing`: one row per fingerprint. A run is the turns of one
    session under one fingerprint (TD3-12), so the runs add up to the coworker's share of the
    Usage page. Fewer than `min_runs` runs: no medians, "too few runs to compare"."""
    turns = store.usage_turns(start, end, where={"persona_id": persona_id})
    kept_from = store.usage_kept_from()
    models_of_turn = {t["turn_id"]: t["models"] for t in turns}
    runs: dict[tuple, dict[str, Any]] = {}
    for t in turns:
        k = (t["persona_fp"], t["session_id"])
        r = runs.setdefault(k, {"fp": t["persona_fp"], "turns": [], "automation": False})
        r["turns"].append(t)
        r["automation"] = r["automation"] or bool(t["run_id"])
    rows: dict[Optional[str], dict[str, Any]] = {}
    for (fp, _sid), r in runs.items():
        row = rows.setdefault(fp, {"fp": fp, "runs": [], "first": None, "last": None})
        row["runs"].append(r)
        for t in r["turns"]:
            row["first"] = t["started_at"] if row["first"] is None else min(row["first"], t["started_at"])
            row["last"] = t["started_at"] if row["last"] is None else max(row["last"], t["started_at"])
    versions = {v["fp"]: v for v in store.persona_versions(persona_id)}
    out = []
    for fp, row in sorted(rows.items(), key=lambda kv: -(kv[1]["last"] or 0)):
        rr = row["runs"]
        all_turns = [t for r in rr for t in r["turns"]]
        checked = [t for t in all_turns if t["warn"] is not None]
        models = sorted({m for t in all_turns for m in models_of_turn.get(t["turn_id"], [])})
        enough = len(rr) >= min_runs
        out.append({
            "fp": fp, "current": fp is not None and fp == current_fp, "unknown_version": fp is None,
            "first_seen": row["first"], "last_seen": row["last"],
            # V3-10: when the first turn is the oldest the ledger keeps, it is "before" that day.
            "before_kept": kept_from is not None and row["first"] is not None and row["first"] <= kept_from,
            "runs": len(rr), "automation_runs": sum(1 for r in rr if r["automation"]),
            "turns": len(all_turns), "checked": len(checked), "with_warn": sum(1 for t in checked if t["warn"]),
            "not_checked": len(all_turns) - len(checked),
            "median_tokens": median([sum(t["total"] for t in r["turns"]) for r in rr]) if enough else None,
            "at_least": any(t["at_least"] for t in all_turns),
            "approvals_per_run": (sum(t["approvals"] for t in all_turns) / len(rr)) if enough else None,
            "up": sum(1 for t in all_turns if t["rating"] == "up"),
            "down": sum(1 for t in all_turns if t["rating"] == "down"),
            "too_few": not enough, "models": models,
            "first_seen_version": (versions.get(fp) or {}).get("first_seen"),
        })
    # "What changed" between each version and the one before it (by first sighting).
    ordered = [v for v in store.persona_versions(persona_id)]
    changes = {}
    for prev, cur in zip(ordered, ordered[1:]):
        changes[cur["fp"]] = {"from": prev["fp"], **diff_versions(prev, cur, store)}
    for row in out:
        row["changed"] = changes.get(row["fp"])
    # model line: "model unchanged", "model changed: a -> b", or several models
    for i, row in enumerate(out):
        older = out[i + 1] if i + 1 < len(out) else None
        if len(row["models"]) > 1:
            row["model_line"] = {"kind": "several", "models": row["models"]}
        elif older is not None and row["models"] and older["models"] and row["models"] != older["models"]:
            row["model_line"] = {"kind": "changed", "from": older["models"][-1], "to": row["models"][0]}
        else:
            row["model_line"] = {"kind": "unchanged", "models": row["models"]}
    return {"persona_id": persona_id, "start": start, "end": end, "kept_from": kept_from,
            "min_runs": min_runs, "rows": out,
            "total": sum(t["total"] for t in turns), "runs": sum(r["runs"] for r in out)}


def _skill_version(data: Optional[bytes]) -> Optional[str]:
    if not data:
        return None
    for line in data.decode("utf-8", "replace").splitlines()[:40]:
        s = line.strip()
        if s.lower().startswith("version:"):
            return s.split(":", 1)[1].strip().strip("\"'") or None
    return None


def diff_versions(prev: dict[str, Any], cur: dict[str, Any], store: Any) -> dict[str, Any]:
    """What changed between two kept versions of a coworker, from their file lists: files
    added, removed and edited (with the line change), and a skill's own `version:` when its
    SKILL.md says one (§9.4)."""
    a, b = prev["files"], cur["files"]
    edits = []
    for path in sorted(set(a) | set(b)):
        if path in a and path in b and a[path]["sha256"] == b[path]["sha256"]:
            continue
        e: dict[str, Any] = {"path": path, "kind": "added" if path not in a else "removed" if path not in b else "edited"}
        if e["kind"] == "edited":
            e["lines"] = b[path]["lines"] - a[path]["lines"]
        if path.endswith("SKILL.md"):
            e["skill"] = path.split("/")[1] if path.count("/") >= 2 else path
            e["from_version"] = _skill_version(store.persona_file(prev["persona_id"], prev["fp"], path)) if path in a else None
            e["to_version"] = _skill_version(store.persona_file(cur["persona_id"], cur["fp"], path)) if path in b else None
        edits.append(e)
    return {"files": edits}


def version_diff(store: Any, persona_id: str, fp_from: str, fp_to: str) -> dict[str, Any]:
    """"Show diff": both versions' text of each changed file (unified diff), local only."""
    import difflib

    vs = {v["fp"]: v for v in store.persona_versions(persona_id)}
    if fp_from not in vs or fp_to not in vs:
        return {"error": "version not kept"}
    files = []
    for e in diff_versions(vs[fp_from], vs[fp_to], store)["files"]:
        old = store.persona_file(persona_id, fp_from, e["path"]) or b""
        new = store.persona_file(persona_id, fp_to, e["path"]) or b""
        lines = list(difflib.unified_diff(old.decode("utf-8", "replace").splitlines(),
                                          new.decode("utf-8", "replace").splitlines(),
                                          fromfile=f"{fp_from}/{e['path']}", tofile=f"{fp_to}/{e['path']}", lineterm=""))
        files.append({**e, "diff": "\n".join(lines[:2000])})
    return {"from": fp_from, "to": fp_to, "files": files}


# -- OB-17: team wake split and events ---------------------------------------------------------------


def team_wakes(store: Any, session_ids: Iterable[str], *, start: int = 0, end: int = 2**62) -> list[dict[str, Any]]:
    """TeamView's "By wake reason" (§11.5): wakes, tokens and cache-read share per reason, lead
    and workers together, each split by role. Reads the ledger of every member session, so its
    total equals the sum of their pills (gate 1)."""
    sids = list(dict.fromkeys(session_ids))
    out: dict[str, dict[str, Any]] = {}
    for g in store.usage_rollup(["wake_reason", "team_role"], start, end, where={"session_id": sids}):
        reason = g["key"]["wake_reason"]
        r = out.setdefault(reason, {"reason": reason, "wakes": 0, "total": 0, "cache_read": 0, "input": 0,
                                    "at_least": False, "by_role": {}})
        r["wakes"] += g["turns"]
        r["total"] += g["total"]
        r["cache_read"] += g["cache_read"]
        r["input"] += g["input_fresh"] + g["cache_read"] + g["cache_write"]
        r["at_least"] = r["at_least"] or g["unreported"] > 0
        role = g["key"]["team_role"] or "unknown"
        b = r["by_role"].setdefault(role, {"wakes": 0, "total": 0})
        b["wakes"] += g["turns"]
        b["total"] += g["total"]
    return sorted(out.values(), key=lambda r: (-r["total"], r["reason"]))


def team_events(store: Any, *, lead_session: str, worker_sessions: dict[str, str],
                board_events: list[dict[str, Any]], links: list[tuple[int, int]], limit: int = 50,
                turns: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """"Team events that used to be silent" (§11.4), newest first: cap hits, long deferrals,
    watchdog nudges, lead polling, the same approval asked twice and an item reviewed before
    its prerequisite. Each names the session and turn whose Steps it opens. `turns`: the
    members' turns when the caller has read them already (P3B-5)."""
    sessions = {lead_session: "lead", **{sid: actor for actor, sid in worker_sessions.items()}}
    rows = store.team_turns(list(sessions)) if turns is None else turns
    events: list[dict[str, Any]] = []
    for t in rows:
        wake = t.get("wake") or {}
        d = wake.get("deferred") or {}
        who = sessions.get(t["session_id"], "?")
        base = {"session_id": t["session_id"], "turn_id": t["turn_id"], "seq": t["seq"], "at": t["started_at"], "who": who}
        if d.get("by") == "cap":
            events.append({**base, "kind": "wake_capped", "severity": "warn", "deferred_ms": d.get("ms")})
        elif d.get("by") in ("busy", "stopped") and (d.get("ms") or 0) >= 60_000:
            events.append({**base, "kind": "deferred", "severity": "info", "by": d["by"], "deferred_ms": d.get("ms")})
        if wake.get("reason") == "watchdog":
            events.append({**base, "kind": "watchdog_nudged", "severity": "info", "item_ids": wake.get("item_ids") or []})
    lead_turns = [t for t in rows if t["session_id"] == lead_session]
    by_id = {t["turn_id"]: t for t in lead_turns}
    for f in scopes.lead_polling([{"id": t["turn_id"], "wake": t.get("wake")} for t in lead_turns]):
        t = by_id[f["to"]]
        events.append({"session_id": lead_session, "turn_id": t["turn_id"], "seq": t["seq"], "at": t["started_at"],
                       "who": "lead", "kind": "lead_polling", "severity": "warn", "count": f["count"]})
    cards = store.team_approval_cards(list(sessions))
    for f, c in scopes.same_approval_repeats(cards):  # the repeat card, as the Issues tab (P3B-4)
        events.append({"session_id": f["again_by"], "turn_id": c.get("turn_id"), "seq": c.get("seq"),
                       "at": c.get("at"), "who": sessions.get(f["again_by"], "?"), "kind": "same_approval_twice",
                       "severity": "warn", "tool": f["tool"], "first_by": sessions.get(f["first_by"], f["first_by"]),
                       "again_by": sessions.get(f["again_by"], f["again_by"])})
    transitions = [{"seq": e["seq"], "item": e.get("item_id"), "to": (e.get("payload") or {}).get("to"),
                    "at": e.get("ts")} for e in board_events if e.get("kind") == "item_transitioned"]
    for f in scopes.reviewed_before_prereq(transitions, links):
        e = next((x for x in transitions if x["seq"] == f["at_seq"]), {})
        events.append({"kind": "reviewed_before_prereq", "severity": "warn", "item": f["item"],
                       "prerequisite": f["prerequisite"], "to": e.get("to"), "at": _ts_ms(e.get("at")),
                       "board_seq": f["at_seq"]})
    for e in board_events:
        if e.get("kind") == "wake_cap_reached":
            p = e.get("payload") or {}
            events.append({"kind": "wake_cap_reached", "severity": "warn", "at": _ts_ms(e.get("ts")),
                           "hour": p.get("hour"), "count": p.get("count"), "held": p.get("held") or [],
                           "board_seq": e.get("seq")})
    events.sort(key=lambda e: -(e.get("at") or 0))
    return events[:limit]


def _ts_ms(ts: Any) -> Optional[int]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts * 1000) if ts < 10**11 else int(ts)
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


# -- OB-18: automation run health ---------------------------------------------------------------------


def automation_runs(store: Any, task_id: str, runs: list[dict[str, Any]], *, now_ms: int,
                    titles: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Each run of an automation gains `{turns, total, at_least, active_ms, warn, checks,
    health}` from its own turns (`turn.automation`, TD3-23), and the automation gains the
    `failed_in_a_row` banner (TD3-21). A run whose turn was never recorded reads tokens "—"
    and its health from the run record only. Before 1.3, a run's own turns are the turns of
    its session that started before the run finished (a follow-up typed later is left out)."""
    titles = titles or {}
    by_run: dict[str, list[dict]] = {}
    for t in store.usage_turns(0, 2**62, where={"task_id": task_id}):
        by_run.setdefault(t["run_id"], []).append(t)
    sessions = [r.get("session_id") for r in runs if r.get("session_id")]
    legacy: dict[str, list[dict]] = {}
    for t in store.usage_turns(0, 2**62, where={"session_id": sessions}) if sessions else []:
        if t["run_id"] is None:
            legacy.setdefault(t["session_id"], []).append(t)
    seqs = store.turn_seqs([t["turn_id"] for ts in list(by_run.values()) + list(legacy.values()) for t in ts])
    out = []
    for r in runs:
        own = by_run.get(r.get("run_id")) or []
        if not own:
            started = int((r.get("started_at") or 0) * 1000)
            finished = int(r["finished_at"] * 1000) if r.get("finished_at") else None
            own = [t for t in legacy.get(r.get("session_id"), [])
                   if t["started_at"] >= started - 5000 and (finished is None or t["started_at"] <= finished)]
        warn_checks = sorted({c for t in own for c in t["warn_checks"]})
        last = own[-1] if own else None
        summary = None if last is None else {
            "status": next((t["status"] for t in own if t["status"] in ("error", "unknown", "max_iterations_exceeded")),
                           last["status"]),
            "warn_checks": warn_checks,
            "ended_at": (last["started_at"] + last["duration_ms"]) if last["duration_ms"] is not None else None}
        health = scopes.run_health(r, summary, now_ms=now_ms)
        did_not_finish = (r.get("status") == "running" and health == "failed")
        out.append({**r, "turns": len(own), "total": sum(t["total"] for t in own) if own else None,
                    "at_least": any(t["at_least"] for t in own),
                    "active_ms": sum(t["active_ms"] or 0 for t in own) if own else None,
                    "duration_ms": sum(t["duration_ms"] or 0 for t in own) if own else (
                        int((r["finished_at"] - r["started_at"]) * 1000) if r.get("finished_at") else None),
                    "warn": sum(t["warn"] or 0 for t in own) if own else None,
                    "checks": warn_checks, "check_titles": [titles.get(c, c) for c in warn_checks],
                    "health": "did_not_finish" if did_not_finish else health,
                    "first_turn_id": own[0]["turn_id"] if own else None,
                    "first_turn_seq": seqs.get(own[0]["turn_id"]) if own else None})
    ordered = sorted(out, key=lambda x: x.get("started_at") or 0)
    series = [{"health": "failed" if x["health"] in ("failed", "did_not_finish") else x["health"],
               "error": x.get("error") or (x["check_titles"][0] if x["check_titles"] else None)} for x in ordered]
    banner = scopes.failed_in_a_row(series)
    if banner is not None:
        last_failed = next(x for x in reversed(ordered) if x["health"] in ("failed", "did_not_finish"))
        banner = {**banner, "run_id": last_failed.get("run_id"), "session_id": last_failed.get("session_id"),
                  "turn_id": last_failed.get("first_turn_id")}
    return {"runs": out, "failed_in_a_row": banner}


def json_or(text: Optional[str], default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
