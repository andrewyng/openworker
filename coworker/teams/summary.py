"""Read-only Team View projections. Never a wake source or an agent transcript tool."""

from __future__ import annotations

import json
import math
from datetime import datetime
from statistics import median

from .store import ITEM_STATUS, WORKER_WAITING

KINDS = ("input", "cache_read", "output", "cache_write")


def stamp(value) -> float:
    try:
        number = (
            float(value)
            if isinstance(value, (float, int))
            else datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        )
        return number if math.isfinite(number) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


def tokens(usage) -> dict:
    result = {}
    for key in KINDS:
        try:
            result[key] = max(0, int((usage or {}).get(key) or 0))
        except (ValueError, TypeError, OverflowError):
            result[key] = 0
    return result


def add_tokens(rows) -> dict:
    return {key: sum(row.get(key, 0) for row in rows) for key in KINDS}


def compact_usage_points(points, buckets=120):
    """Bound the optional chart payload, preserving every recorded token by role."""
    points = sorted(points, key=lambda p: p["ts"])
    if len(points) <= buckets:
        return points
    start, end = points[0]["ts"], points[-1]["ts"]
    width = max((end - start) / buckets, 0.001)
    grouped = {}
    for point in points:
        bucket = min(buckets - 1, int((point["ts"] - start) / width))
        key = (point["role"], bucket)
        row = grouped.setdefault(
            key,
            {
                "role": point["role"],
                "ts": min(end, start + (bucket + 1) * width),
                "tokens": 0,
            },
        )
        row["tokens"] += point["tokens"]
    return sorted(grouped.values(), key=lambda p: p["ts"])


def all_events(store, space):
    """Read every page, not just the first 500 events of a long-running team."""
    cursor = 0
    while True:
        page = store.events(space, since_seq=cursor, limit=2000)
        if not page:
            return
        yield from page
        cursor = page[-1]["seq"]


def waiting_items(events, inbox, items):
    current = {i["id"]: i for i in items}
    result = {}
    for event in events:
        if event["kind"] != WORKER_WAITING:
            continue
        p = event.get("payload") or {}
        item = current.get(event.get("item_id"))
        prompt = inbox.get(p.get("prompt_id", ""))
        if (
            item
            and prompt
            and prompt.state == "pending"
            and item["state"] not in ("done", "canceled")
            and item["assignee"] == event["actor"]
            and prompt.session_id == p.get("session_id")
        ):
            result[item["id"]] = {
                "prompt_id": prompt.id,
                "tool": p.get("tool", ""),
                "preview": p.get("preview", ""),
                "session_id": prompt.session_id,
            }
    return result


def breakdown(intervals, start, end):
    """Partition wall time; overlapping parallel calls count once, not N times."""
    clipped = [
        (max(start, a), min(end, b), kind)
        for a, b, kind in intervals
        if b > start and a < end and b > a
    ]
    edges = sorted(
        {start, end, *(a for a, _, _ in clipped), *(b for _, b, _ in clipped)}
    )
    out = dict(model_ms=0, tool_ms=0, waited_ms=0, queued_ms=0)
    for a, b in zip(edges, edges[1:]):
        active = {kind for x, y, kind in clipped if x < b and y > a}
        kind = next(
            (k for k in ("waited_ms", "tool_ms", "model_ms") if k in active),
            "queued_ms",
        )
        out[kind] += max(0, (b - a) * 1000)
    return {k: round(v) for k, v in out.items()}


def active_windows(events, end):
    """Assignment to review/done, including rework; no inference from prose."""
    windows, start, actor = [], None, ""
    for event in events:
        ts = stamp(event["ts"])
        p = event.get("payload") or {}
        if event["kind"] == "item_assigned":
            if start is not None:
                windows.append((start, ts, actor))
            actor, start = p.get("assignee", ""), ts
        elif event["kind"] == "item_transitioned":
            if p.get("to") in ("review", "done", "canceled") and start is not None:
                windows.append((start, ts, actor))
                start = None
            elif p.get("to") == "in_progress" and start is None and actor:
                start = ts
    if start is not None:
        windows.append((start, end, actor))
    return windows


def make_summary(manager, team, now):
    board = manager.session_board(team.lead_session)
    events = list(all_events(manager.team_store, team.space))
    actors = {team.lead_actor, *(w.actor for w in team.workers)}
    # A project board can be shared. A team view includes its filings/assignments,
    # not another lead's unrelated backlog (the board itself remains unchanged).
    items = [
        i for i in board["items"] if i["creator"] in actors or i["assignee"] in actors
    ]
    by_item = {i["id"]: [] for i in items}
    for e in events:
        if e.get("item_id") in by_item:
            by_item[e["item_id"]].append(e)
    waits = waiting_items(events, manager.inbox, items)
    session_ids = {team.lead_session, *(w.session_id for w in team.workers)}
    prompts = [
        p
        for p in manager.inbox.list()
        if p.session_id in session_ids and p.kind != "notification"
    ]
    # The lead decision and the original worker approval are one human interruption.
    pending_ids = {p.id for p in prompts if p.state == "pending"}
    forwarded = {
        (p.data.get("arguments") or {}).get("call_id")
        for p in prompts
        if p.data.get("tool") == "decide_worker_call"
        # Declining the lead's proposal need not answer the original worker.
        # That still-pending request must become visible again.
        and (p.state == "pending" or (p.data.get("arguments") or {}).get("call_id") not in pending_ids)
    }
    asks = []
    pending_requests = []
    by_session = {w.session_id: w.actor for w in team.workers}
    represented = {w["prompt_id"] for w in waits.values()}
    for p in prompts:
        if p.id in forwarded:
            continue
        kind = (
            "staffing"
            if p.data.get("members")
            else (
                "reviews"
                if p.kind == "plan"
                else (
                    "questions"
                    if p.kind == "question"
                    else (
                        "grants"
                        if p.kind in ("connector", "directory", "tool")
                        else "approvals"
                    )
                )
            )
        )
        asks.append(
            {
                "id": p.id,
                "kind": kind,
                "state": p.state,
                "waited_s": max(
                    0,
                    (stamp(p.resolved_at) if p.resolved_at else now)
                    - stamp(p.created_at),
                ),
            }
        )
        if p.state == "pending":
            original = manager.inbox.get((p.data.get("arguments") or {}).get("call_id", "")) if p.data.get("tool") == "decide_worker_call" else p
            pending_requests.append({
                "id": p.id, "session_id": p.session_id,
                "worker": by_session.get(original.session_id if original else p.session_id, team.lead_actor),
                "title": p.title, "kind": kind,
                "represented_by_task": bool(original and original.id in represented),
            })
    ask_groups = {
        k: {
            "answered": sum(a["kind"] == k and a["state"] == "resolved" for a in asks),
            "waiting": sum(a["kind"] == k and a["state"] == "pending" for a in asks),
        }
        for k in ("approvals", "questions", "reviews", "staffing", "grants")
    }
    answered = [a["waited_s"] for a in asks if a["state"] == "resolved"]
    members = [(team.lead_actor, "lead", team.lead_session)] + [
        (w.actor, w.persona, w.session_id) for w in team.workers
    ]
    workers, usage_points, records = [], [], {}
    for actor, role, sid in members:
        record = manager.session_store.load(sid)
        engine = manager._engines.get(sid)
        messages = (
            list(engine.messages)
            if engine is not None
            else (record.messages if record else [])
        )
        records[actor] = messages
        usage = [
            tokens(m["usage"])
            for m in messages
            if m.get("role") == "assistant" and isinstance(m.get("usage"), dict)
        ]
        # A persisted session's usage may be available before its full transcript.
        total = add_tokens(
            usage or [tokens(u) for u in (record.usage if record else {}).values()]
        )
        step = None
        for m in messages:
            if m.get("role") == "assistant" and isinstance(m.get("usage"), dict):
                ts = stamp((m.get("timing") or {}).get("model_started") or m.get("ts"))
                if ts:
                    usage_points.append(
                        {
                            "ts": ts,
                            "role": role,
                            "tokens": sum(tokens(m["usage"]).values()),
                        }
                    )
            for call in m.get("tool_calls") or []:
                if (call.get("function") or {}).get("name") == "todo_write":
                    try:
                        a = json.loads(call["function"].get("arguments") or "{}")
                        todos = a.get("todos", a.get("items", []))
                        if isinstance(todos, list) and todos:
                            step = {
                                "done": sum(
                                    x.get("status") == "done"
                                    for x in todos
                                    if isinstance(x, dict)
                                ),
                                "total": len(todos),
                            }
                    except (TypeError, ValueError):
                        pass
        workers.append(
            {
                "actor": actor,
                "role": role,
                "session_id": sid,
                "model": record.model if record else "",
                "workspace": record.workspace if record else "",
                "running": manager.is_running(sid),
                "tokens": total,
                "usage_partial": any(
                    m.get("role") == "assistant" and not m.get("usage")
                    for m in messages
                ),
                "step": step,
                "items": [i["id"] for i in items if i["assignee"] == actor],
            }
        )
    windows = {i["id"]: active_windows(by_item[i["id"]], now) for i in items}
    worker_by_actor = {w["actor"]: w for w in workers}
    all_item_times = []
    for item in items:
        story = by_item[item["id"]]
        activity = [e for e in story if e["kind"] != ITEM_STATUS]
        item["updated"] = (
            stamp(activity[-1]["ts"]) if activity else stamp(item["created_ts"])
        )
        item["last_event"] = activity[-1]["kind"] if activity else "item_created"
        item["waiting"] = waits.get(item["id"])
        blockers = [l for l in item.get("links", []) if l["kind"] == "blocked_by"]
        item["group"] = (
            "waiting"
            if item["waiting"] or (item["state"] == "blocked" and not blockers)
            else (
                "review"
                if item["state"] == "review"
                else (
                    "working"
                    if item["state"] == "in_progress"
                    else (
                        "done"
                        if item["state"] == "done"
                        else "canceled" if item["state"] == "canceled" else "queued"
                    )
                )
            )
        )
        worker = worker_by_actor.get(item["assignee"])
        item["worker_session"] = worker["session_id"] if worker else None
        active_assigned = [
            i
            for i in items
            if i["assignee"] == item["assignee"]
            and i["state"] not in ("review", "done", "canceled")
        ]
        item["step"] = (
            worker["step"]
            if worker and len(active_assigned) == 1 and item in active_assigned
            else None
        )
        item["pr"] = next(
            (
                ref
                for ref in item["refs"]
                if ref.startswith(("https://", "http://")) and "/pull/" in ref
            ),
            None,
        )
        started = stamp(item["created_ts"])
        closed = next(
            (
                stamp(e["ts"])
                for e in reversed(story)
                if e["kind"] == "item_transitioned"
                and e["payload"].get("to") in ("done", "canceled")
            ),
            now,
        )
        if item["state"] not in ("done", "canceled"):
            closed = now
        item["elapsed_s"] = max(0, closed - started)
        intervals, attributed_usage = [], []
        ambiguous = False
        for a, b, actor in windows[item["id"]]:
            overlaps = [
                (x, y)
                for other, ws in windows.items()
                if other != item["id"]
                for x, y, who in ws
                if who == actor and x < b and y > a
            ]
            for m in records.get(actor, []):
                ts = stamp((m.get("timing") or {}).get("model_started") or m.get("ts"))
                # Multiple assignments: don't attribute a single call to two tasks.
                if any(x <= ts < y for x, y in overlaps):
                    ambiguous = True
                    continue
                if a <= ts < b and m.get("role") == "assistant" and m.get("usage"):
                    attributed_usage.append(tokens(m["usage"]))
                timing = m.get("timing") or {}
                for kind in ("model_ms", "tool_ms", "waited_ms"):
                    start = stamp(timing.get(kind.replace("_ms", "_started")))
                    duration = timing.get(kind)
                    if start and isinstance(duration, (float, int)) and duration >= 0:
                        stop = start + duration / 1000
                        if stop <= a or start >= b:
                            continue
                        cuts = sorted(
                            {
                                max(a, start),
                                min(b, stop),
                                *(
                                    max(a, start, x)
                                    for x, y in overlaps
                                    if x < min(b, stop) and y > max(a, start)
                                ),
                                *(
                                    min(b, stop, y)
                                    for x, y in overlaps
                                    if x < min(b, stop) and y > max(a, start)
                                ),
                            }
                        )
                        for x, y in zip(cuts, cuts[1:]):
                            if x < y and not any(
                                ox < y and oy > x for ox, oy in overlaps
                            ):
                                intervals.append((x, y, kind))
        for event in story:
            if event["kind"] == WORKER_WAITING:
                p = manager.inbox.get(event["payload"].get("prompt_id", ""))
                if p:
                    intervals.append(
                        (
                            stamp(p.created_at),
                            stamp(p.resolved_at) if p.resolved_at else now,
                            "waited_ms",
                        )
                    )
        item["tokens"] = add_tokens(attributed_usage)
        item["usage_partial"] = ambiguous or any(
            m.get("role") == "assistant" and not m.get("usage")
            for _, _, actor in windows[item["id"]]
            for m in records.get(actor, [])
        )
        item["timing_partial"] = ambiguous or any(
            m.get("role") == "assistant" and not m.get("timing")
            for _, _, actor in windows[item["id"]]
            for m in records.get(actor, [])
        )
        item["timing"] = breakdown(intervals, started, closed) if intervals else None
        if item["timing"]:
            all_item_times.append(item["timing"])
    lead = workers[0]
    workers = workers[1:]
    by_kind = add_tokens([lead["tokens"], *(w["tokens"] for w in workers)])
    counts = {
        g: sum(i["group"] == g for i in items)
        for g in ("waiting", "review", "working", "queued", "done")
    }
    active = [i for i in items if i["group"] != "canceled"]
    end = (
        max((stamp(i["created_ts"]) + i["elapsed_s"] for i in active), default=now)
        if active and all(i["group"] == "done" for i in active)
        else now
    )
    elapsed = max(0, end - stamp(team.created_at))
    timing = {
        k: sum(v[k] for v in all_item_times)
        for k in ("model_ms", "tool_ms", "waited_ms", "queued_ms")
    }
    return {
        "team_id": team.team_id,
        "space": team.space,
        "lead_session": team.lead_session,
        "items": items,
        "workers": workers,
        "lead": lead,
        "counts": counts,
        "pending_requests": pending_requests,
        "totals": {
            "done": counts["done"],
            "total": len(active),
            "elapsed_s": elapsed,
            "tokens": sum(by_kind.values()),
            "tokens_by_kind": by_kind,
            "asks_answered": len(answered),
            "asks_waiting": sum(a["state"] == "pending" for a in asks),
        },
        "timing": timing if all_item_times else None,
        "timing_partial": any(i["timing_partial"] for i in items),
        "usage_partial": any(
            m.get("role") == "assistant" and not m.get("usage")
            for messages in records.values()
            for m in messages
        ),
        "asks": ask_groups,
        "median_answer_s": median(answered) if answered else None,
        "usage_points": compact_usage_points(usage_points),
        "generated_at": now,
    }
