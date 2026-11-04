"""Checks over more than one turn (signals.yaml 0.6, phase-3 design §5, TD3-17).

Phase 2's checks (`signals.py`) are pure functions of one finished turn. Phase 3 adds two
scopes, and each check here is still a pure function of what it is given, so both engines
give the same answer and `golden/phase3` can pin it:

- **series**: the ordered turns of one session, or the ordered runs of one automation
  (`lead_polling`, `failed_in_a_row`);
- **task**: every turn of one task (a board item in a team, or the session otherwise) with
  the board and journal facts about it (`scan_missing`, `scanner_failed`,
  `no_findings_without_scan`, `finding_without_evidence`, `self_verified`,
  `reviewed_before_prereq`, `same_approval_twice`).

The functions that return plain facts (`coverage_checks`, `lead_polling`, ...) are the lab's
prototypes (docs/observability/phase-3/lab/checks3.py), moved here unchanged except where a
comment says so. The `*_issues` wrappers turn those facts into issues in the shape
`signals.check_turn` gives (`{check, severity, step_id, step_seq, text, facts}`), with the
English sentence from the check's `says`.

`scanner_of` is the word rule of signals.yaml `scanners` (E17b, TD3-16): the engine records
its answer as a shell step's `attrs.scanner`.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from . import signals as _signals

# -- the scanner rule (signals.yaml `scanners`) --------------------------------------------------


def _scanners() -> dict[str, Any]:
    return _signals.signals().get("scanners") or {"list": {}, "lead_ins": [], "probe_words": []}


def scanner_of(command: str) -> Optional[dict[str, str]]:
    """The scanner a shell command runs, `{name, action}`, or None: what the engine records as
    `attrs.scanner`. Only the first word of each command part counts, after lead-ins
    (`npx`, `sudo`...), NAME=value words and a leading `python -m`, so `grep -r semgrep docs`
    is not a scan. `action` is `version` for a version or help probe, else `scan`."""
    sc_all = _scanners()
    lead_ins = set(sc_all.get("lead_ins") or [])
    probes = set(sc_all.get("probe_words") or [])
    for seg in re.split(r"&&|\|\||;|\|", command.replace("\\", "/")):
        words = [w.lower().rsplit("/", 1)[-1].removesuffix(".exe") for w in seg.split()]
        while words and (words[0] in lead_ins or "=" in words[0]):
            words = words[1:]
        if words[:2] in (["python", "-m"], ["python3", "-m"], ["py", "-m"]):
            words = words[2:]
        if not words:
            continue
        for name, sc in (sc_all.get("list") or {}).items():
            if words[0] == sc["word"] and (not sc.get("then") or words[1:2] == [sc["then"]]):
                rest = words[2:] if sc.get("then") else words[1:]
                probe = any(w in probes for w in rest[:2])
                return {"name": name, "action": "version" if probe else "scan"}
    return None


def scanner_not_found(scanner: Optional[dict], exit_code: Any, output: Any) -> bool:
    """Whether a shell step that names a scanner only showed the scanner is not installed
    (signals.yaml `scanners.not_found`, P3B-1): a not-found exit code, or a line of the output
    that names the scanner's word with a not-found phrase. Then the engine records no scan."""
    if not scanner:
        return False
    rule = _scanners().get("not_found") or {}
    if exit_code in (rule.get("exit") or []):
        return True
    if not isinstance(output, str) or not output:
        return False
    sc = (_scanners().get("list") or {}).get(scanner.get("name")) or {}
    word = str(sc.get("word") or scanner.get("name") or "").lower()
    words = {word, word.replace("-", "_")} - {""}
    phrases = [str(p).lower() for p in rule.get("phrases") or []]
    for line in output.lower().splitlines():
        if any(w in line for w in words) and any(p in line for p in phrases):
            return True
    return False


def area_word(area: str) -> str:
    return (_scanners().get("area_words") or {}).get(area, area)


def scanner_for_area(area: str) -> str:
    """The first scanner of an area, for "gitleaks did not run" (M3-R11)."""
    for name, sc in (_scanners().get("list") or {}).items():
        if sc.get("area") == area:
            return name
    return area


# -- task checks: OB-16 coverage -----------------------------------------------------------------


def coverage_checks(task: dict) -> dict[str, list]:
    """`task`: {promised: [areas], steps: [tool steps with scanner, exit_code, id],
    report: {area: {ran, findings}} | None, findings: [{id, severity, evidence}],
    fix: {fixed_by, verified_by} | None}. Returns {check: [facts]} for what fired."""
    lst = _scanners().get("list") or {}
    fired: dict[str, list] = {}
    scans = [s for s in task.get("steps") or [] if (s.get("scanner") or {}).get("action") == "scan"]
    ok_areas, failed = set(), []
    for s in scans:
        sc = lst.get(s["scanner"]["name"])
        if sc is None:
            continue
        code = s.get("exit_code")
        if code in (0, None) or code in (sc.get("findings_exit") or []):
            ok_areas.add(sc["area"])
        else:
            failed.append({"scanner": s["scanner"]["name"], "exit_code": code, "step": s["id"]})
    if failed:
        fired["scanner_failed"] = failed
    missing = [a for a in task.get("promised") or [] if a not in ok_areas]
    if missing:
        fired["scan_missing"] = [{"area": a} for a in missing]
    for area, r in (task.get("report") or {}).items():
        if r.get("ran") and r.get("findings") == 0 and area not in ok_areas:
            fired.setdefault("no_findings_without_scan", []).append({"area": area})
    sev = set(((_signals.signals()["checks"].get("finding_without_evidence") or {}).get("params") or {})
              .get("severities") or ["critical", "high"])
    for f in task.get("findings") or []:
        if f.get("severity") in sev and not f.get("evidence"):
            fired.setdefault("finding_without_evidence", []).append({"finding": f["id"]})
    fix = task.get("fix")
    if fix and fix.get("verified_by") and fix["verified_by"] == fix.get("fixed_by"):
        fired["self_verified"] = [{"actor": fix["fixed_by"]}]
    return fired


def coverage_fine(task: dict) -> dict[str, str]:
    """The fine lines of the coverage checks, so an empty list reads as "checked" (§10.4)."""
    promised = task.get("promised") or []
    return {
        "scan_missing": f"Every promised scan ran ({', '.join(promised)})" if promised else "No scans promised",
        "scanner_failed": "No scanner failed",
        "no_findings_without_scan": "Every \"no findings\" has a scan behind it",
        "finding_without_evidence": "Every high finding has an evidence step",
        "self_verified": "No fix was verified by its own author",
    }


# -- team checks: OB-17 --------------------------------------------------------------------------


def lead_polling(turns: list[dict], n: Optional[int] = None) -> list[dict]:
    """Series check over one lead's turns in order: n or more timer wakes in a row with no
    board change between them (the wake's `board_seq` did not move). `turns`: [{id, wake}]."""
    if n is None:
        n = int(((_signals.signals()["checks"].get("lead_polling") or {}).get("params") or {}).get("min_run", 3))
    fired, run = [], []
    for t in turns:
        w = t.get("wake") or {}
        if w.get("reason") == "timer" and (not run or w.get("board_seq") == (run[-1].get("wake") or {}).get("board_seq")):
            run.append(t)
        else:
            if len(run) >= n:
                fired.append({"count": len(run), "from": run[0]["id"], "to": run[-1]["id"]})
            run = [t] if w.get("reason") == "timer" else []
    if len(run) >= n:
        fired.append({"count": len(run), "from": run[0]["id"], "to": run[-1]["id"]})
    return fired


def same_approval_twice(cards: list[dict]) -> list[dict]:
    """Task check over the team's approval cards in order: the same tool and args_hash asked
    again after an earlier card for it was approved. `cards`: [{tool, args_hash, session,
    outcome}]."""
    return [f for f, _card in same_approval_repeats(cards)]


def same_approval_repeats(cards: list[dict]) -> list[tuple[dict, dict]]:
    """`same_approval_twice` with the card that asked again: (finding, repeat card). Both the
    Issues tab and TeamView's event line point at that card, the second ask (P3B-4)."""
    approved: dict[tuple, dict] = {}
    fired = []
    for c in cards:
        if c.get("args_hash") is None:
            continue  # arguments that never parsed match nothing (build: not in the prototype)
        k = (c["tool"], c["args_hash"])
        if k in approved:
            fired.append(({"tool": c["tool"], "first_by": approved[k]["session"], "again_by": c["session"]}, c))
        if c.get("outcome") == "approved":
            approved.setdefault(k, c)
    return fired


def reviewed_before_prereq(events: list[dict], links: Iterable[tuple[Any, Any]]) -> list[dict]:
    """Board check: an item moved to review or done while an item that blocks it was not done.
    `events`: item_transitioned rows in seq order ({seq, item, to}); `links`: (prerequisite, item)."""
    links = [tuple(x) for x in links]
    state: dict[Any, str] = {}
    fired = []
    for e in events:
        if e["to"] in ("review", "done"):
            for pre, item in links:
                if item == e["item"] and state.get(pre) != "done":
                    fired.append({"item": item, "prerequisite": pre, "at_seq": e["seq"]})
        state[e["item"]] = e["to"]
    return fired


def wake_split(turns: list[dict]) -> dict[str, dict[str, int]]:
    """TeamView's "By wake reason" (§11.5): wakes, tokens, cache reads and input per reason.
    `turns`: [{wake: {reason}, tokens, cache_read, input, role?}]. Each turn has one reason,
    so the split adds up to the team total."""
    out: dict[str, dict[str, Any]] = {}
    for t in turns:
        reason = ((t.get("wake") or {}).get("reason")) or "unknown"
        r = out.setdefault(reason, {"wakes": 0, "tokens": 0, "cache_read": 0, "input": 0,
                                    "by_role": {}})
        r["wakes"] += 1
        r["tokens"] += t.get("tokens") or 0
        r["cache_read"] += t.get("cache_read") or 0
        r["input"] += t.get("input") or 0
        role = t.get("role")
        if role:
            b = r["by_role"].setdefault(role, {"wakes": 0, "tokens": 0})
            b["wakes"] += 1
            b["tokens"] += t.get("tokens") or 0
    return out


# -- automations: OB-18 --------------------------------------------------------------------------

FAILING_CHECKS = ("tool_failed", "model_failed", "sandbox_failed")


def run_health(task_run: dict, turn: Optional[dict], *, now_ms: Optional[int] = None) -> str:
    """TD3-21: `failed` if the run says so, or its own turn ended in error, or a warning of
    a failing kind fired on it; `skipped`; `unknown` when no turn was recorded; else `ok`.
    `turn`: {status, warn_checks, ended_at?} or None. A run still `running` whose turn ended
    more than `stuck_after_ms` ago did not finish, and counts as failed (build: the
    prototype took no clock; the stuck rule is §12.4's)."""
    if task_run.get("status") == "skipped":
        return "skipped"
    if task_run.get("status") == "error":
        return "failed"
    if turn is None:
        return "unknown"
    if turn.get("status") in ("error", "unknown", "max_iterations_exceeded"):
        return "failed"
    if set(turn.get("warn_checks") or []) & set(FAILING_CHECKS):
        return "failed"
    if task_run.get("status") == "running" and turn.get("ended_at") is not None and now_ms is not None:
        stuck = int(((_signals.signals()["checks"].get("failed_in_a_row") or {}).get("params") or {})
                    .get("stuck_after_ms", 600_000))
        if now_ms - turn["ended_at"] > stuck:
            return "failed"
    return "ok"


def failed_in_a_row(runs: list[dict], n: Optional[int] = None) -> Optional[dict]:
    """Series check over one automation's runs, newest last ({health, error}). Skipped runs
    are left out. Fires when the n most recent runs all failed."""
    if n is None:
        n = int(((_signals.signals()["checks"].get("failed_in_a_row") or {}).get("params") or {}).get("min_run", 3))
    done = [r for r in runs if r.get("health") != "skipped"]
    if len(done) >= n and all(r.get("health") == "failed" for r in done[-n:]):
        count = 0
        for r in reversed(done):
            if r.get("health") != "failed":
                break
            count += 1
        return {"count": count, "last_error": done[-1].get("error")}
    return None


# -- facts -> issues --------------------------------------------------------------------------------


def _issue(check: str, text: str, step: Optional[dict] = None, **facts: Any) -> dict:
    spec = _signals.signals()["checks"].get(check) or {}
    return {"check": check, "severity": spec.get("severity", "warn"),
            "step_id": (step or {}).get("step_id"), "step_seq": (step or {}).get("seq"),
            "turn_id": (step or {}).get("turn_id"), "text": text,
            "facts": {"words": 1, **facts}}


def coverage_issues(task: dict, *, handoff: Optional[dict] = None,
                    steps_by_id: Optional[dict[str, dict]] = None) -> list[dict]:
    """The coverage checks of one task as issues, each pointing at the hand-off step (a
    failed scan points at its own step), with signals.yaml's sentences (M3-R11 words)."""
    steps_by_id = steps_by_id or {}
    out = []
    for check, facts in coverage_checks(task).items():
        for f in facts:
            if check == "scan_missing":
                a = f["area"]
                out.append(_issue(check, f"{scanner_for_area(a)} did not run: {area_word(a)} scan skipped.",
                                  handoff, area=a, scanner=scanner_for_area(a)))
            elif check == "scanner_failed":
                out.append(_issue(check, f"{f['scanner']} failed with exit code {f['exit_code']}.",
                                  steps_by_id.get(f["step"]) or handoff, **f))
            elif check == "no_findings_without_scan":
                w = area_word(f["area"])
                out.append(_issue(check, f"The report says no {w} problems were found, but no {w} scan ran. "
                                  "It should say we could not look, not that nothing is there.", handoff, **f))
            elif check == "finding_without_evidence":
                sev = next((x.get("severity") for x in task.get("findings") or [] if x.get("id") == f["finding"]), "high")
                out.append(_issue(check, f"Finding #{f['finding']} ({sev}) has no evidence step.", handoff,
                                  severity_of_finding=sev, **f))
            elif check == "self_verified":
                item = (task.get("fix") or {}).get("item")
                out.append(_issue(check, f"The fix for #{item} was verified by the worker that made it."
                                  if item is not None else "A fix was verified by the worker that made it.",
                                  handoff, item=item, **f))
    return out


def lead_polling_issues(turns: list[dict], first_steps: Optional[dict[str, dict]] = None) -> list[dict]:
    first_steps = first_steps or {}
    return [_issue("lead_polling", f"The lead woke on its timer {f['count']} times with no board change.",
                   first_steps.get(f["to"]), **f)
            for f in lead_polling(turns)]


def failed_in_a_row_issue(runs: list[dict], last_step: Optional[dict] = None) -> Optional[dict]:
    f = failed_in_a_row(runs)
    if f is None:
        return None
    err = f.get("last_error") or "unknown"
    return _issue("failed_in_a_row", f"Failed {f['count']} runs in a row. Last error: {err}", last_step, **f)


def same_approval_issues(cards: list[dict]) -> list[dict]:
    """Each repeat as an issue on the card that asked again (P3B-4), not the first one."""
    return [_issue("same_approval_twice",
                   f"Same approval asked twice: {f['tool']} ({f['first_by']}, then {f['again_by']}).",
                   card.get("step"), **f)
            for f, card in same_approval_repeats(cards)]


def reviewed_before_prereq_issues(events: list[dict], links: Iterable[tuple[Any, Any]]) -> list[dict]:
    return [_issue("reviewed_before_prereq",
                   f"Item #{f['item']} went to {next((e['to'] for e in events if e['seq'] == f['at_seq']), 'review')} "
                   f"before its prerequisite #{f['prerequisite']} was done.", None, **f)
            for f in reviewed_before_prereq(events, links)]


SERIES_CHECKS = ("lead_polling", "failed_in_a_row")
TASK_CHECKS = ("scan_missing", "scanner_failed", "no_findings_without_scan", "finding_without_evidence",
               "self_verified", "reviewed_before_prereq", "same_approval_twice")
