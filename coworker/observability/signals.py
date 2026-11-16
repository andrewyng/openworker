"""The Issues checks (OB-3, phase-2 design Part A): fixed rules over one finished turn.

The rules and their numbers are in `signals.yaml`, a copy of the shared file in ocw-context
(docs/observability/signals.yaml); the Rust harness reads the same file. A check is a pure
function of the turn record and its steps' `attrs` (recording format 1.2), never `content`, so
the same recording gives the same issues in both engines and in an export with metadata only
(TD2-1). The functions are the ones validated on the Phase 1 in-app recordings and the golden
scenarios (docs/observability/phase-2/lab/check_issues.py), moved here unchanged except where a
comment says so.

`check_turn` returns `{signals_version, warn, info, issues, fine, not_checked}`. An issue is
`{check, severity, step_id, step_seq, text, facts}`: `text` is the English reference sentence,
`facts` what a screen needs to say it in another language (TD2-3).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Optional

import yaml

SIGNALS_FILE = Path(__file__).with_name("signals.yaml")

# The engine's own default when a turn did not record its step limit (before format 1.2).
DEFAULT_MAX_ITERATIONS: Optional[int] = None


@functools.lru_cache(maxsize=1)
def signals() -> dict[str, Any]:
    return yaml.safe_load(SIGNALS_FILE.read_text(encoding="utf-8"))


def version() -> str:
    return str(signals().get("version"))


def total_tokens(step: dict) -> int:
    u = (step.get("attrs") or {}).get("usage") or {}
    if not u.get("reported"):
        return 0
    return sum(u.get(k) or 0 for k in ("input_fresh", "cache_read", "cache_write", "output"))


def total_input(step: dict) -> int:
    u = (step.get("attrs") or {}).get("usage") or {}
    return sum(u.get(k) or 0 for k in ("input_fresh", "cache_read", "cache_write"))


def purpose(step: dict) -> Optional[str]:
    return (step.get("attrs") or {}).get("purpose")


def issue(check: str, step: Optional[dict], text: str, **facts: Any) -> dict:
    # `words`: the facts hold every value of the sentence, so the screen can say it in the
    # reader's language (TD2-3); `text` stays the English sentence for everything else.
    return {"check": check, "step_seq": step["seq"] if step else None,
            "step_id": step["step_id"] if step else None, "text": text, "words": 1, **facts}


# -- the checks --------------------------------------------------------------------------------
# Each returns (state, issues, fine_text): state is fired | fine | not_checked.

def c_repeated_step(turn, top, all_steps, p):
    tools = [s for s in top if s["kind"] == "tool"]
    found, run = [], []

    def close():
        if len(run) >= p["min_run"]:
            # The main model step that asked for each call: the last main model step before it.
            askers = set()
            for s in run[1:]:
                prior = [m for m in top if m["kind"] == "model" and purpose(m) == "main"
                         and m["seq"] < s["seq"]]
                if prior:
                    askers.add(prior[-1]["step_id"])
            extra = sum(total_tokens(m) for m in top if m["step_id"] in askers)
            found.append(issue("repeated_step", run[-1],
                               f"{run[0]['name']} was called {len(run)} times in a row with the "
                               f"same arguments. That cost {extra:,} extra tokens.",
                               count=len(run), extra_tokens=extra, name=run[0]["name"]))

    for s in tools:
        a = s["attrs"]
        key = (s["name"], a.get("args_hash"))
        breaks = s["status"] in ("denied", "skipped") or a.get("repeat_ok") or a.get("args_hash") is None
        if run and not breaks and key == (run[-1]["name"], run[-1]["attrs"].get("args_hash")):
            run.append(s)
            continue
        close()
        run = [] if breaks else [s]
    close()
    return ("fired" if found else "fine"), found, (f"No step repeated {p['min_run']} times in a row",
                                                    {"min_run": p["min_run"]})


def c_off_plan(turn, top, all_steps, p):
    writes = [s for s in top if s["kind"] == "tool" and s["name"] == "todo_write"
              and (s["attrs"].get("plan") or {}).get("items")]
    if not writes:
        return "not_checked", [], "No plan to check against"
    found = []
    prev: dict[str, str] = {}
    for w in writes:
        items = w["attrs"]["plan"]["items"]
        now = {i["h"] for i in items}
        vanished = [h for h, st in prev.items() if st != "done" and h not in now]
        added = [h for h in now if h not in prev]
        # A new item stands in for a vanished one: the agent reworded it (V2-08).
        dropped = len(vanished) - len(added) if prev else 0
        if dropped > 0:
            found.append(issue("off_plan", w, f"A plan step was dropped before it was done "
                               f"({dropped} of {len(prev)}).", kind="dropped", dropped=dropped, of=len(prev)))
        prev = {i["h"]: i["s"] for i in items}
    # (b) acting after everything was marked done
    for idx, w in enumerate(writes):
        items = w["attrs"]["plan"]["items"]
        if not all(i["s"] == "done" for i in items):
            continue
        nxt = writes[idx + 1]["seq"] if idx + 1 < len(writes) else None
        later_adds = nxt is not None
        for s in top:
            if (s["kind"] == "tool" and s["seq"] > w["seq"] and s["name"] != "todo_write"
                    and (nxt is None or s["seq"] < nxt)
                    and s["attrs"].get("risk_class") not in ("read", None)
                    and s["status"] not in ("denied", "skipped")):
                if not later_adds:
                    found.append(issue("off_plan", s, f"It kept acting after it marked every plan "
                                       f"step done: {s['name']}.", kind="after_done", name=s["name"]))
                break
    last = writes[-1]["attrs"]["plan"]["items"]
    done = sum(1 for i in last if i["s"] == "done")
    return ("fired" if found else "fine"), found, \
        (f"No plan step was dropped ({done} of {len(last)} done)", {"done": done, "items": len(last)})


_REFUSED = {
    "user": "You refused {name}.",
    "denied_by_rule": "A rule refused {name}.",
    "reviewer_denied": "The auto-approver refused {name} and you did not allow it.",
    "unattended_auto": "{name} was refused because nobody was at the app to approve it.",
}


def c_refused(turn, top, all_steps, p):
    found = []
    for s in top:
        origin = (s["attrs"].get("decision") or {}).get("origin") if s["kind"] == "tool" else None
        # origin null: no stage decided (stopped, superseded), so nobody refused it (V2-07).
        if s["kind"] == "tool" and s["status"] == "denied" and origin:
            text = _REFUSED.get(origin, "{name} was refused.").format(name=s["name"])
            found.append(issue("refused", s, text, by=origin, name=s["name"]))
    return ("fired" if found else "fine"), found, "No actions were refused by you, a rule or the auto-approver"


def _reviewer_on(turn, live_only=False):
    r = turn.get("reviewer") or {}
    return bool(r.get("active")) or (not live_only and bool(r.get("shadow")))


def _checks(all_steps, shadow=None):
    return [s for s in all_steps if s["kind"] == "auto_approver" and (shadow is None or bool(s.get("shadow")) == shadow)]


def _call_name(all_steps, call_id):
    for s in all_steps:
        if s["kind"] == "tool" and s.get("call_id") == call_id:
            return s["name"]
    return "an action"


def c_overridden(turn, top, all_steps, p):
    if not _reviewer_on(turn, live_only=True):
        return "not_checked", [], ""
    found = [issue("overridden", s, f"The auto-approver blocked {s['name']} and you used Allow anyway.", name=s["name"])
             for s in top if s["kind"] == "tool"
             and any(x.get("stage") == "grant" and x.get("outcome") == "allow_anyway"
                     for x in (s["attrs"].get("decision") or {}).get("path", []))]
    return ("fired" if found else "fine"), found, "You did not override the auto-approver"


def c_reviewer_could_not_run(turn, top, all_steps, p):
    if not _reviewer_on(turn):
        return "not_checked", [], ""
    checks = _checks(all_steps)
    found = [issue("reviewer_could_not_run", s, f"The auto-approver could not check "
                   f"{_call_name(all_steps, s.get('call_id'))}, so it was shown to you instead.",
                   name=_call_name(all_steps, s.get("call_id")))
             for s in checks if s["attrs"].get("result") == "could_not_run"]
    live = [s for s in checks if not s.get("shadow")]
    n = lambda k: f"{k} action" + ("" if k == 1 else "s")  # noqa: E731
    if live:  # the verdicts, so "allowed everything" does not read as just "ran" (M2-R1)
        res = [s["attrs"].get("result") for s in live]
        asked = sum(r in ("unsure", "context_changed", "could_not_run") for r in res)
        fine = (f"Auto-approver checked {n(len(live))}: {res.count('allow')} allowed, "
                f"{asked} asked you, {res.count('deny')} blocked; none failed to run",
                {"variant": "live", "count": len(live), "allowed": res.count("allow"), "asked": asked,
                 "blocked": res.count("deny")})
    else:  # a shadow-only turn (M2-R5)
        fine = (f"Shadow reviewer checked {n(len(checks))}; none failed to run",
                {"variant": "shadow", "count": len(checks)})
    return ("fired" if found else "fine"), found, fine


def c_reviewer_paused(turn, top, all_steps, p):
    if not _reviewer_on(turn, live_only=True):
        return "not_checked", [], ""
    notices = [s for s in top if s["kind"] == "notice" and s["name"] == "reviewer_paused"]
    tripped = [s for s in _checks(all_steps) if (s["attrs"].get("breaker") or {}).get("tripped")]
    at = notices[0] if notices else (tripped[0] if tripped else None)
    if not at:
        return "fine", [], "Auto-approver was never paused"
    denials = max([(s["attrs"].get("breaker") or {}).get("denials_in_row") or 0 for s in tripped] or [0])
    return "fired", [issue("reviewer_paused", at, f"The auto-approver paused after {denials} "
                           f"refusals in a row. Later actions were shown to you.", denials=denials)], ""


def c_reviewer_allowed_send(turn, top, all_steps, p):
    if not _reviewer_on(turn, live_only=True):
        return "not_checked", [], ""
    found = [issue("reviewer_allowed_send", s, f"The auto-approver allowed {s['name']} without asking you. "
                   f"It sends data off the machine.", name=s["name"])
             for s in top if s["kind"] == "tool" and s["status"] == "ok"
             and (s["attrs"].get("risk_class") == "egress" or s["attrs"].get("reaches_network"))
             and (s["attrs"].get("decision") or {}).get("origin") == "reviewer"]
    return ("fired" if found else "fine"), found, "The auto-approver let nothing leave the machine without asking you"


def c_shadow_disagreed(turn, top, all_steps, p):
    if not (turn.get("reviewer") or {}).get("shadow"):
        return "not_checked", [], ""
    shadow = _checks(all_steps, shadow=True)
    compared = [s for s in shadow if s["attrs"].get("matched_user") is not None]
    found = [issue("shadow_disagreed", s, f"The shadow reviewer would have said "
                   f"{s['attrs'].get('result')} on {_call_name(all_steps, s.get('call_id'))}.",
                   result=s["attrs"].get("result"), name=_call_name(all_steps, s.get("call_id")))
             for s in compared if s["attrs"].get("matched_user") is False]
    return ("fired" if found else "fine"), found, \
        (f"The shadow reviewer agreed with you on {len(compared)} of {len(compared)} actions", {"count": len(compared)})


def c_tool_failed(turn, top, all_steps, p):
    tools = [s for s in top if s["kind"] == "tool"]
    sandboxed = ((turn.get("stamp") or {}).get("sandbox") or {}).get("enforcement") not in (None, "none")
    found = []
    for i, s in enumerate(tools):
        a = s["attrs"]
        sb = a.get("sandbox") or {}
        if sandboxed and sb.get("blocked_known") and (sb.get("blocked_count") or 0) > 0:
            continue  # blocked_by_sandbox says it, with the exit code (V2-10)
        failed = ((s["status"] == "error" and a.get("error_kind") in ("raised", None))
                  or (s["status"] == "ok" and a.get("result_error"))
                  or (a.get("exit_code") not in (None, 0)))
        if not failed:
            continue
        recovered = any(t["name"] == s["name"] and t["status"] == "ok" and not t["attrs"].get("result_error")
                        and t["attrs"].get("exit_code") in (None, 0) for t in tools[i + 1:])
        exit_part = f" with exit code {a['exit_code']}" if a.get("exit_code") not in (None, 0) else ""
        rec = f" A later {s['name']} in this turn worked." if recovered else ""
        found.append(issue("tool_failed", s, f"{s['name']} failed{exit_part}.{rec}",
                           recovered=recovered, name=s["name"], exit_code=a.get("exit_code") if exit_part else None,
                           how=("exit" if exit_part else
                                                    "result_error" if a.get("result_error") else "raised")))
    return ("fired" if found else "fine"), found, "No tool failed"


def c_model_failed(turn, top, all_steps, p):
    # Main calls only: a failed summary is context_full's business (trim, summary_failed),
    # so it is not reported twice (V2-06).
    models = [s for s in top if s["kind"] == "model" and purpose(s) == "main"]
    found = []
    for i, s in enumerate(models):
        err = (s["attrs"].get("error") or {}).get("type")
        if s["status"] != "error" or err in ("context_length", "cancelled"):
            continue
        if any(m["status"] == "ok" and purpose(m) == purpose(s) for m in models[i + 1:]):
            continue
        tail = " The turn stopped here." if turn["status"] == "error" else ""
        found.append(issue("model_failed", s, f"The model call failed ({err}).{tail}",
                           purpose=purpose(s), error_type=err, stopped=turn["status"] == "error"))
    return ("fired" if found else "fine"), found, "No model call failed"


def c_retried(turn, top, all_steps, p):
    # Not the session title: its retry is the engine's own parameter fallback (V2-05).
    models = [s for s in all_steps if s["kind"] == "model" and not s.get("shadow")
              and purpose(s) != "title"]
    models.sort(key=lambda s: s["seq"])
    found, done = [], set()
    for s in models:
        wire = [m for m in (s["attrs"].get("provider_meta") or []) if not m.get("auxiliary")]
        if s["status"] == "ok" and len(wire) > 1:
            found.append(issue("retried", s, f"The model call needed {len(wire)} tries ({purpose(s)}).",
                               attempts=len(wire), how="wire", purpose=purpose(s)))
            done.add(s["step_id"])
    for i, s in enumerate(models):
        # A too-long call that was summarised and tried again is context_full's (V2-09).
        if s["status"] != "error" or (s["attrs"].get("error") or {}).get("type") in ("cancelled", "context_length"):
            continue
        later = [m for m in models[i + 1:] if purpose(m) == purpose(s)
                 and m.get("parent_step_id") == s.get("parent_step_id")]
        ok = next((m for m in later if m["status"] == "ok"), None)
        if ok and ok["step_id"] not in done:
            tries = 1 + sum(1 for m in later if m["seq"] <= ok["seq"])
            found.append(issue("retried", ok, f"The model call needed {tries} tries ({purpose(s)}).",
                               attempts=tries, how="engine", purpose=purpose(s)))
            done.add(ok["step_id"])
    return ("fired" if found else "fine"), found, "No model call needed a second try"  # M2-R2


def c_cut_off(turn, top, all_steps, p):
    found = [issue("cut_off", s, "The model stopped at its output limit. The answer may be incomplete.")
             for s in top if s["kind"] == "model" and purpose(s) == "main"
             and s["attrs"].get("finish_reason") == "length"]
    if not found and turn["status"] == "truncated":
        n = next((s for s in top if s["kind"] == "notice" and s["name"] == "truncated"), None)
        found.append(issue("cut_off", n, "The model stopped at its output limit. The answer may be incomplete."))
    return ("fired" if found else "fine"), found, "No answers were cut off (length limit)"


def c_step_limit(turn, top, all_steps, p):
    iterations = sum(1 for s in top if s["kind"] == "model" and purpose(s) == "main")
    cap = turn.get("max_iterations") or DEFAULT_MAX_ITERATIONS
    n = next((s for s in top if s["kind"] == "notice" and s["name"] == "step_limit"), None)
    if turn["status"] == "max_iterations_exceeded" or n:
        return "fired", [issue("step_limit", n, f"The turn stopped after {iterations} steps, the limit.",
                               iterations=iterations)], ""
    return "fine", [], ((f"Stayed under the step limit ({iterations} of {cap})", {"iterations": iterations, "cap": cap})
                        if cap else (f"Stayed under the step limit ({iterations} steps)", {"iterations": iterations}))


def c_made_up_tool(turn, top, all_steps, p):
    found = []
    for s in top:
        k = s["attrs"].get("error_kind") if s["kind"] == "tool" else None
        if s["kind"] == "tool" and s["status"] == "error" and k in ("unknown_tool", "bad_arguments"):
            text = (f"The model asked for a tool that does not exist: {s['name']}." if k == "unknown_tool"
                    else f"The model sent arguments that do not fit {s['name']}.")
            found.append(issue("made_up_tool", s, text, kind=k, name=s["name"]))
    return ("fired" if found else "fine"), found, "No unknown tools or invalid arguments"


def c_context_full(turn, top, all_steps, p):
    found = []
    summaries = [s for s in all_steps if s["kind"] == "summary"]
    for s in summaries:
        if s["name"] == "trim":
            found.append(issue("context_full", s, "Older messages were cut to make room.", kind="trim"))
        elif s["attrs"].get("reason") == "overflow":
            found.append(issue("context_full", s, "The conversation became too long for the model, "
                               "so it was summarised in a hurry.", kind="overflow"))
    for s in top:
        if s["kind"] == "notice" and s["name"] == "summary_failed":
            found.append(issue("context_full", s, "Summarising failed, so the context stayed full.",
                               kind="summary_failed"))
    max_fill = 0.0
    for s in top:
        a = s["attrs"]
        if s["kind"] == "model" and purpose(s) == "main" and (a.get("usage") or {}).get("reported") \
                and a.get("context_window"):
            fill = total_input(s) / a["context_window"]
            max_fill = max(max_fill, fill)
            if fill >= p["hard_fill"]:
                found.append(issue("context_full", s, f"The context was {round(fill * 100)}% full on this call.",
                                   kind="hard_fill", fill=round(fill, 3), pct=round(fill * 100)))
    normal = sum(1 for s in summaries if s["attrs"].get("reason") == "threshold" and s["name"] != "trim")
    part = f"; summarised {normal} time(s) at the normal point" if normal else ""
    return ("fired" if found else "fine"), found, \
        (f"Context had room (at most {max(1, round(max_fill * 100))}% of the window){part}",  # M2-R7
         {"pct": max(1, round(max_fill * 100)), "normal": normal})


def c_blocked_by_sandbox(turn, top, all_steps, p):
    sb = (turn.get("stamp") or {}).get("sandbox") or {}
    if sb.get("enforcement") in (None, "none"):
        return "not_checked", [], ""
    found = [issue("blocked_by_sandbox", s, f"The sandbox blocked {s['attrs']['sandbox']['blocked_count']} "
                   f"network request(s) during {s['name']}"
                   + (f", which exited with code {s['attrs']['exit_code']}." if s["attrs"].get("exit_code") not in (None, 0) else "."),
                   count=s["attrs"]["sandbox"]["blocked_count"], name=s["name"],
                   exit_code=s["attrs"].get("exit_code") if s["attrs"].get("exit_code") not in (None, 0) else None)
             for s in top if s["kind"] == "tool" and (s["attrs"].get("sandbox") or {}).get("blocked_known")
             and ((s["attrs"].get("sandbox") or {}).get("blocked_count") or 0) > 0]
    return ("fired" if found else "fine"), found, "The sandbox blocked nothing"


def c_sandbox_failed(turn, top, all_steps, p):
    setups = [s for s in top if s["kind"] == "sandbox" and s["name"] == "setup"]
    if not setups:
        return "not_checked", [], ""
    found = [issue("sandbox_failed", s, f"The sandbox could not start at {s['attrs'].get('failed_stage')}: "
                   f"{(s['attrs'].get('error') or {}).get('type')}.",
                   stage=s["attrs"].get("failed_stage"), type=(s["attrs"].get("error") or {}).get("type"))
             for s in setups if s["status"] == "error"]
    s0 = setups[0]
    seconds = round((s0.get("duration_ms") or 0) / 1000)
    return ("fired" if found else "fine"), found, \
        (f"The sandbox started ({s0['attrs'].get('provider')}, {seconds} s)", {"provider": s0["attrs"].get("provider"), "seconds": seconds})


def c_ran_without_sandbox(turn, top, all_steps, p):
    sb = (turn.get("stamp") or {}).get("sandbox") or {}
    if not sb.get("expected"):
        # No sandbox switched on here (or format 1.1, which cannot tell): the check does not
        # run, so "Ran inside the sandbox" is never said of a machine with none (signals 0.3).
        return "not_checked", [], ""
    if sb.get("provider") != "direct":
        return "fine", [], "Ran inside the sandbox"
    acts = [s for s in top if s["kind"] == "tool" and s["status"] == "ok"
            and s["attrs"].get("risk_class") in ("exec", "egress", "write_local")]
    if not acts:
        return "fine", [], "Ran inside the sandbox"
    return "fired", [issue("ran_without_sandbox", acts[0], f"This machine has a sandbox switched on, but "
                           f"this turn ran {len(acts)} action(s) without it.", count=len(acts))], ""


def _overlap(start, end, spans):
    """How much of [start, end) the spans cover, each moment once."""
    total, cur = 0, start
    for a, b in sorted(spans):
        a, b = max(a, cur), min(b, end)
        if b > a:
            total += b - a
            cur = b
    return total


def c_waited_unwatched(turn, top, all_steps, p):
    # 0.4: besides the time after the card reached the Inbox, the wait's overlap with the
    # turn's `nobody_watching` gaps (OB-14) counts: a card left in the open session while
    # the desktop was closed.
    unwatched = [(g["started_at"], g["started_at"] + g["duration_ms"]) for g in all_steps
                 if g["kind"] == "notice" and g.get("name") == "nobody_watching" and g.get("duration_ms")]
    found = []
    for s in top:
        a = s["attrs"]
        if s["kind"] != "wait" or a.get("kind") not in ("approval", "question", "plan"):
            continue
        began = s.get("started_at") or 0
        end = began + (s.get("duration_ms") or 0)
        alone = end - (a.get("inbox_at") or began) if a.get("where") == "inbox" else 0
        alone = max(alone, _overlap(began, end, unwatched))
        if alone >= p["min_wait_ms"]:
            where = "in the Inbox " if a.get("where") == "inbox" else ""
            found.append(issue("waited_unwatched", s, f"waited {round(alone / 60000)} min {where}for an answer "
                               f"while nobody was at the app.", minutes=round(alone / 60000),
                               inbox=a.get("where") == "inbox"))
    return ("fired" if found else "fine"), found, "Nothing waited long for an answer while nobody was watching"


def _team_wake(turn):
    """The 1.3 facts the team checks read; None when the turn is not a team turn with a wake."""
    wake = turn.get("wake")
    team = (turn.get("stamp") or {}).get("team")
    return (wake, team) if wake and team else None


def c_wake_capped(turn, top, all_steps, p):  # signals 0.6 (OB-17)
    tw = _team_wake(turn)
    if tw is None:
        return "not_checked", [], "Not a team turn with its wake recorded"
    d = tw[0].get("deferred") or {}
    if d.get("by") == "cap":
        first = top[0] if top else None
        minutes = round((d.get("ms") or 0) / 60000)
        return "fired", [issue("wake_capped", first, f"The team reached its wake cap ({p.get('cap_per_hour', 60)} this hour). "
                               f"This wake waited {minutes} min before it was delivered.",
                               deferred_ms=d.get("ms"), minutes=minutes, cap_per_hour=p.get("cap_per_hour", 60))], ""
    return "fine", [], "Not held back by the wake cap"


def c_watchdog_nudged(turn, top, all_steps, p):  # signals 0.6 (OB-17)
    tw = _team_wake(turn)
    if tw is None:
        return "not_checked", [], "Not a team turn with its wake recorded"
    if tw[0].get("reason") == "watchdog":
        first = top[0] if top else None
        ids = [str(x) for x in tw[0].get("item_ids") or []]
        return "fired", [issue("watchdog_nudged", first, f"The watchdog woke the {tw[1].get('role')}: items "
                               f"{', '.join(ids)} were idle for 10 minutes.", item_ids=ids, role=tw[1].get("role"))], ""
    return "fine", [], "Not a watchdog wake"


CHECKS = {
    "repeated_step": c_repeated_step, "off_plan": c_off_plan, "refused": c_refused,
    "overridden": c_overridden, "reviewer_could_not_run": c_reviewer_could_not_run,
    "reviewer_paused": c_reviewer_paused, "reviewer_allowed_send": c_reviewer_allowed_send,
    "shadow_disagreed": c_shadow_disagreed,
    "tool_failed": c_tool_failed, "model_failed": c_model_failed, "retried": c_retried,
    "cut_off": c_cut_off, "step_limit": c_step_limit, "made_up_tool": c_made_up_tool,
    "context_full": c_context_full, "blocked_by_sandbox": c_blocked_by_sandbox, "sandbox_failed": c_sandbox_failed,
    "ran_without_sandbox": c_ran_without_sandbox, "waited_unwatched": c_waited_unwatched,
    "wake_capped": c_wake_capped, "watchdog_nudged": c_watchdog_nudged,
}


def scope_of(check: dict, spec: Optional[dict] = None) -> str:
    """A check's scope (signals 0.6): `turn`, `series` or `task`; `turn` when it does not say."""
    spec = spec or signals()
    return str(check.get("scope") or spec.get("default_scope") or "turn")


def check_turn(turn: dict, steps: list[dict], spec: Optional[dict] = None) -> dict:
    """Every check over one finished turn. Pure: reads the turn and its steps' attrs only."""
    spec = spec or signals()
    steps = sorted(steps, key=lambda s: s.get("seq") or 0)
    usable = [s for s in steps if not s.get("after_turn")]
    top = [s for s in usable if s.get("parent_step_id") is None and not s.get("shadow")]
    out: dict[str, Any] = {"signals_version": str(spec.get("version")), "issues": [], "fine": [],
                           "not_checked": []}
    for cid, check in spec["checks"].items():
        if scope_of(check, spec) != "turn":
            continue  # series and task checks run over more than one turn (`scopes.py`)
        fn = CHECKS.get(cid)
        if fn is None:
            continue  # a check this app version does not know yet: left out, never guessed
        try:
            state, found, fine = fn(turn, top, usable, check.get("params") or {})
        except Exception:  # noqa: BLE001 - one broken check never hides the others
            state, found, fine = "not_checked", [], ""
        for f in found:
            out["issues"].append({
                "check": cid, "severity": check["severity"], "step_id": f.get("step_id"),
                "step_seq": f.get("step_seq"), "text": f.get("text", ""),
                "facts": {k: v for k, v in f.items() if k not in ("check", "step_id", "step_seq", "text")},
            })
        if state == "fired":
            continue
        fine_facts: dict[str, Any] = {}
        if isinstance(fine, tuple):
            fine, fine_facts = fine
        (out["fine"] if state == "fine" else out["not_checked"]).append(
            {"check": cid, "text": fine, "facts": {"words": 1, **fine_facts}})
    out["warn"] = sum(1 for i in out["issues"] if i["severity"] == "warn")
    out["info"] = sum(1 for i in out["issues"] if i["severity"] == "info")
    return out
