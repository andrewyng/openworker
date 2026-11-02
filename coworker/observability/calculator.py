"""Every number the Phase 1 screens show, from a recording (technical design §6.3, §7.1).

Works on the recording JSON (`{"turns": [...], "steps": [...]}`, recording format 1.0), as
the store's `load_recording()` and the recorder's `as_dict()` give it. The server computes
the numbers once, so the token pill, the popover and later pages always agree. The shared
golden test's `expected-numbers.json` checks these functions.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

BUCKET_ORDER = ["waiting", "tools", "model", "auto_approver", "summary"]


# -- tokens (§6.3) -------------------------------------------------------------------------------
def input_total(u: dict[str, Any]) -> int:
    """Fresh input plus cache read and cache write; a part not reported adds nothing."""
    return (u.get("input_fresh") or 0) + (u.get("cache_read") or 0) + (u.get("cache_write") or 0)


def step_total(step: dict[str, Any]) -> Optional[int]:
    u = (step.get("attrs") or {}).get("usage") or {}
    if not u.get("reported"):
        return None
    return input_total(u) + (u.get("output") or 0)


def usage(recording: dict[str, Any], session_id: Optional[str] = None,
          turn_id: Optional[str] = None) -> dict[str, Any]:
    """The session's token numbers: the pill, the "≥" marker, failed and over-limit calls,
    by purpose, by model, cache share. With `turn_id`, the same over one turn (its
    `after_turn` steps included)."""
    steps = recording.get("steps") or []
    models = [s for s in steps if s.get("kind") == "model"
              and (session_id is None or s.get("session_id") == session_id)
              and (turn_id is None or s.get("turn_id") == turn_id)
              and (s.get("attrs") or {}).get("purpose") != "proxy"]  # proxy calls are in no pill
    total = unreported = failed = overflow = 0
    by_purpose: dict[str, int] = {}
    by_model: dict[str, int] = {}
    cache_read = inp = 0
    unreported_ids: list[str] = []
    for s in models:
        attrs = s.get("attrs") or {}
        err = attrs.get("error")
        if err and s.get("shadow"):
            continue  # a failed shadow check is never shown as a failure (M-17)
        if err:
            if err.get("type") == "context_length":
                overflow += 1  # its own line, not a failed call (F-5)
            else:
                failed += 1  # V-02
            continue
        t = step_total(s)
        if t is None:
            unreported += 1  # only completed model steps count toward "≥" (V-01)
            unreported_ids.append(s.get("step_id"))
            continue
        total += t
        p = attrs.get("purpose", "main")
        by_purpose[p] = by_purpose.get(p, 0) + t
        key = attrs.get("model", "?") + ("" if p == "main" else f" ({p})")
        by_model[key] = by_model.get(key, 0) + t
        u = attrs["usage"]
        if u.get("cache_read") is not None:  # a call with no cache count stays out of the share
            cache_read += u["cache_read"]
            inp += input_total(u)
    reported_parents = {s.get("parent_step_id") for s in models
                        if ((s.get("attrs") or {}).get("usage") or {}).get("reported")}
    not_reported_checks = sum(
        1 for s in steps
        if s.get("kind") == "auto_approver"
        and ((s.get("attrs") or {}).get("reviewer") or {}).get("kind") not in (None, "session_model")
        and s.get("step_id") not in reported_parents
        and (session_id is None or s.get("session_id") == session_id)
        and (turn_id is None or s.get("turn_id") == turn_id))
    return {"pill": total, "at_least": unreported > 0, "unreported_calls": unreported,
            "failed_calls": failed, "overflow_calls": overflow, "by_purpose": by_purpose,
            "by_model": by_model, "cache_share": round(cache_read / inp, 3) if inp else None,
            "auto_approver_checks_not_reported": not_reported_checks,
            "unreported_step_ids": unreported_ids}


# -- time buckets (§7.1) ---------------------------------------------------------------------------
def _merge(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(i for i in intervals if i[1] > i[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _subtract(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    for s, e in a:
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
        if cur < e:
            out.append((cur, e))
    return out


def _length(iv: list[tuple[int, int]]) -> int:
    return sum(b - a for a, b in iv)


def _bucket_of(step: dict[str, Any]) -> Optional[str]:
    if step.get("shadow") or step.get("after_turn") or step.get("status") == "unknown":
        return None
    kind = step.get("kind")
    if kind == "wait":
        return "waiting"
    if kind == "tool":
        return "tools"
    if kind == "model" and (step.get("attrs") or {}).get("purpose") in ("main", "helper"):
        return "model"
    if kind == "auto_approver":
        return "auto_approver"
    if kind == "summary":
        return "summary"
    return None


def buckets(recording: dict[str, Any], turn: dict[str, Any], now: Optional[int] = None) -> dict[str, Any]:
    """Where the turn's time went: each bucket is the union of its steps' time, overlaps go
    to the first bucket in the order waiting, tools, model, auto-approver, summary; Other is
    the rest, shown when it is 1% of the total or more (M-R5)."""
    start = turn["started_at"]
    end_of_turn = (start + turn["duration_ms"] if turn.get("duration_ms") is not None
                   else (now if now is not None else start))
    steps = [s for s in recording.get("steps") or [] if s.get("turn_id") == turn["turn_id"]]
    # A step still open is counted up to the turn's end (or now, for a running turn; TD-12).
    spans = [(s, s["started_at"], s["started_at"] + s["duration_ms"] if s.get("duration_ms") is not None
              else end_of_turn) for s in steps]
    raw = {b: _merge((a, e) for s, a, e in spans if _bucket_of(s) == b) for b in BUCKET_ORDER}
    taken: list[tuple[int, int]] = []
    out: dict[str, Any] = {}
    for b in BUCKET_ORDER:
        own = _subtract(raw[b], taken)
        out[b] = _length(own)
        taken = _merge(taken + own)
    total = end_of_turn - start
    out["total"] = total
    out["other"] = total - _length(taken)
    out["other_shown"] = bool(total) and out["other"] > 0 and out["other"] >= 0.01 * total
    return out
