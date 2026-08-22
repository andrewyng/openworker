"""Automation suggestions — what this machine's own activity says is worth scheduling.

The Automations page offers templates, but a template list is the same for everybody: it can
say "watch a repo", never "you have committed to dcode-stack 85 times this fortnight and
nothing watches it". This module turns what the server can already observe — the repos being
committed to, the servers and connectors that are connected, the cadences already covered —
into ranked suggestions that each carry the EVIDENCE for themselves.

Two rules keep it honest:
  - Never suggest something that already exists. A suggestion the user has already acted on
    reads as the machine not paying attention.
  - Every suggestion states its reason in terms of observed facts, with the numbers in it. A
    suggestion that cannot explain itself is a template, and templates live elsewhere.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# Bounds. Scanning a home directory is the expensive part of this, so it is capped hard and
# cached: suggestions are a page decoration, never worth a slow page.
_MAX_REPOS = 24
_GIT_TIMEOUT = 3.0
_ACTIVE_DAYS = 21
_CACHE_TTL = 600.0  # 10 minutes
# How much of an automation's instructions states its subject. The opening is the job's own
# description; everything after is method, budget, and shared boilerplate.
_SUBJECT_CHARS = 400


@dataclass
class RepoSignal:
    path: str
    name: str
    commits: int  # commits in the window authored by this machine's git user
    others: int  # commits by anyone else — a tracked upstream clone is mostly this
    last_days: Optional[int]  # days since the most recent own commit

    @property
    def mine(self) -> bool:
        """A clone that tracks upstream shows hundreds of commits by people who are not the
        user. Counting those as "work being done here" would suggest automations for
        somebody else's project."""
        return self.commits > 0 and self.commits >= self.others


@dataclass
class Signals:
    repos: list[RepoSignal] = field(default_factory=list)
    mcp: set[str] = field(default_factory=set)
    connectors: set[str] = field(default_factory=set)
    personas: set[str] = field(default_factory=set)
    cadences: set[str] = field(default_factory=set)  # cadences already scheduled
    # What the existing automations are ABOUT: each one's title plus the opening of its
    # instructions, lowercased. Deliberately not the whole body — shared boilerplate appended
    # to every job (a focus preamble naming each project, say) mentions everything, and
    # matching against that makes every subject look already-covered.
    task_text: str = ""
    task_count: int = 0
    inbox_pending: int = 0
    knowledge_dir: Optional[str] = None

    def has(self, *needles: str) -> bool:
        """Is something like this already scheduled? Substring match over what the existing
        automations are about — loose on purpose, because a false "already covered" costs one
        suggestion while a duplicate suggestion costs trust."""
        return any(n.lower() in self.task_text for n in needles)

    def persona(self, *ids: str) -> str:
        """The first installed persona from the preference order, else the default."""
        for pid in ids:
            if pid in self.personas:
                return pid
        return "cowork"


@dataclass
class Suggestion:
    key: str
    title: str
    blurb: str  # what it would do
    reason: str  # WHY it is suggested — the observed evidence, with numbers
    cadence: str  # daily | weekly | monthly | quarterly | yearly
    cron: str
    agent: str
    instructions: str
    requires: list[str] = field(default_factory=list)  # connectors/servers it wants live
    score: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "blurb": self.blurb,
            "reason": self.reason,
            "cadence": self.cadence,
            "cron": self.cron,
            "agent": self.agent,
            "instructions": self.instructions,
            "requires": list(self.requires),
            "score": self.score,
        }


# -- gathering -----------------------------------------------------------------------------


def _run(args: list[str], cwd: Optional[str] = None) -> str:
    try:
        out = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=_GIT_TIMEOUT
        )
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _git_identity(home: Path) -> set[str]:
    """Configured identity, if there is one. Often there is not: commits made through an agent
    carry an explicit author env and leave `git config user.email` empty, which is exactly the
    case on the machine this was written for."""
    ident = set()
    for key in ("user.name", "user.email"):
        v = _run(["git", "config", "--global", key], cwd=str(home)).strip()
        if v:
            ident.add(v.lower())
    return ident


def _authors(repo: Path) -> tuple[dict[str, int], dict[str, int]]:
    """(commits per author email, days since that author's last commit) within the window."""
    log = _run(
        ["git", "log", f"--since={_ACTIVE_DAYS} days ago", "--pretty=%ae|%cr", "--no-merges"],
        cwd=str(repo),
    )
    counts: dict[str, int] = {}
    last: dict[str, int] = {}
    for line in log.splitlines():
        email, _, when = line.partition("|")
        email = email.strip().lower()
        if not email:
            continue
        counts[email] = counts.get(email, 0) + 1
        if email not in last:  # git log is newest-first, so the first sighting is the latest
            last[email] = _rel_days(when) or 0
    return counts, last


def _derive_identity(per_repo: dict[Path, dict[str, int]]) -> set[str]:
    """Who owns this machine, judged by authorship rather than configuration.

    An author who DOMINATES several different repositories is the person working here; a
    tracked upstream clone has many authors and none of them dominates the user's other
    projects. Without this fallback every repo scores mine=0 on a machine with no configured
    git identity — which is the common case when commits are made through an agent — and no
    repo suggestion ever fires.
    """
    dominance: dict[str, int] = {}
    for counts in per_repo.values():
        total = sum(counts.values())
        if not total:
            continue
        top, n = max(counts.items(), key=lambda kv: kv[1])
        if n / total > 0.5:
            dominance[top] = dominance.get(top, 0) + 1
    if not dominance:
        return set()
    best = max(dominance.values())
    winners = [e for e, n in dominance.items() if n == best]
    # Dominating a single repo is not enough to be "the owner", and a tie means we cannot
    # tell — claiming one either way would mislabel every other repo.
    return set(winners) if best >= 2 and len(winners) == 1 else set()


def scan_repos(home: Path, extra: Iterable[str] = ()) -> list[RepoSignal]:
    """Repos this machine has been committing to lately. Candidates are the immediate children
    of home plus any directory the app has recently worked in — bounded on purpose, so this
    never becomes a filesystem crawl."""
    candidates: list[Path] = []
    seen: set[Path] = set()
    for cand in list(home.iterdir() if home.is_dir() else []) + [Path(e) for e in extra]:
        try:
            cand = cand.resolve()
        except OSError:
            continue
        if cand in seen or not cand.is_dir() or not (cand / ".git").exists():
            continue
        seen.add(cand)
        candidates.append(cand)
        if len(candidates) >= _MAX_REPOS:
            break

    # One `git log` per repo, then decide whose commits they are: the identity is derived from
    # the whole set, so it cannot be settled repo by repo.
    per_repo = {repo: _authors(repo) for repo in candidates}
    ident = _git_identity(home) or _derive_identity(
        {repo: counts for repo, (counts, _) in per_repo.items()}
    )

    out: list[RepoSignal] = []
    for repo, (counts, last) in per_repo.items():
        mine = sum(n for email, n in counts.items() if email in ident)
        others = sum(n for email, n in counts.items() if email not in ident)
        last_days = min((d for e, d in last.items() if e in ident), default=None)
        if mine or others:
            out.append(RepoSignal(str(repo), repo.name, mine, others, last_days))
    out.sort(key=lambda r: r.commits, reverse=True)
    return out


def _rel_days(rel: str) -> Optional[int]:
    """git's `%cr` is already human ("3 days ago"); we only need it as a number of days."""
    m = re.match(r"(\d+)\s+(second|minute|hour|day|week|month|year)", rel.strip())
    if not m:
        return 0
    n, unit = int(m.group(1)), m.group(2)
    return {"second": 0, "minute": 0, "hour": 0, "day": n, "week": n * 7,
            "month": n * 30, "year": n * 365}[unit]


def subject_text(tasks: Iterable[Any]) -> str:
    """The dedupe corpus: every task's title plus the opening of its instructions, with
    path-shaped tokens removed.

    A path is plumbing, not subject matter. Left in, `/home/me/OpenWorker/knowledge` makes the
    string "openworker" look covered by a job that has nothing to do with that repo, and the
    suggestion for it never fires.
    """
    raw = "\n".join(
        f"{t.title}\n{(t.instructions or '')[:_SUBJECT_CHARS]}" for t in tasks
    ).lower()
    return " ".join(tok for tok in raw.split() if "/" not in tok)


def cadence_of(cron: Optional[str]) -> str:
    """Which rung of the ladder a cron sits on. Anything unparseable is 'other', which never
    satisfies a ladder-gap check — better to suggest a duplicate than to assume coverage."""
    parts = (cron or "").split()
    if len(parts) != 5:
        return "other"
    _, _, dom, month, dow = parts
    if dom == "*" and dow == "*":
        return "daily"
    if dom == "*" and dow != "*":
        return "weekly"
    if dom != "*" and month == "*":
        return "monthly"
    if dom != "*" and "," in month:
        return "quarterly"
    if dom != "*" and month.isdigit():
        return "yearly"
    return "other"


# -- the catalogue -------------------------------------------------------------------------
# Each rule reads the signals and either returns a Suggestion or None. Order here is not
# priority — `score` is.

_LADDER = [
    ("weekly", "daily", 3), ("monthly", "weekly", 1),
    ("quarterly", "monthly", 1), ("yearly", "quarterly", 1),
]
_LADDER_SPEC = {
    "weekly": ("30 6 * * 1", "Weekly review", "the week's daily output"),
    "monthly": ("0 7 1 * *", "Monthly report", "the month's weekly reviews"),
    "quarterly": ("30 7 1 1,4,7,10 *", "Quarterly review", "the quarter's monthly reports"),
    "yearly": ("0 8 1 1 *", "Yearly review", "the year's quarterly reviews"),
}


def _ladder_rules(s: Signals) -> list[Suggestion]:
    out = []
    for rung, below, need in _LADDER:
        if rung in s.cadences or below not in s.cadences:
            continue
        below_n = sum(1 for c in [below] if c in s.cadences)
        if below_n < 1 or (rung == "weekly" and s.task_count < need):
            continue
        cron, title, reads = _LADDER_SPEC[rung]
        out.append(
            Suggestion(
                key=f"ladder-{rung}",
                title=title,
                blurb=f"Summarise {reads} — what moved, what is still open, what needs a decision.",
                reason=(
                    f"You have {rung.replace('ly','')}-level work running but nothing reads it back: "
                    f"{s.task_count} automations write reports and none of them roll up to {rung}."
                ),
                cadence=rung,
                cron=cron,
                agent=s.persona("repo-ops", "ops", "cowork"),
                requires=[],
                score=70 if rung == "weekly" else 40,
                instructions=(
                    f"Read {reads} and write one report. Lead with what changed, then what is "
                    "still open, then at most three decisions waiting on me. Every claim must "
                    "trace to a file you read this run — no summarising from memory, and no "
                    "new research: this is a report about my reports."
                ),
            )
        )
    return out


def _repo_rules(s: Signals) -> list[Suggestion]:
    out = []
    for r in s.repos:
        if not r.mine or r.commits < 5:
            continue
        if s.has(r.name):
            continue
        out.append(
            Suggestion(
                key=f"repo-health-{r.name}",
                title=f"Repo health — {r.name}",
                blurb="What landed, what is uncommitted, what looks stale — as a written report.",
                reason=(
                    f"{r.commits} of your commits to {r.name} in the last {_ACTIVE_DAYS} days"
                    + (f", most recent {r.last_days} days ago" if r.last_days else "")
                    + ", and no automation mentions it."
                ),
                cadence="weekly",
                cron="0 6 * * 1",
                agent=s.persona("repo-ops", "ops", "cowork"),
                requires=[],
                score=min(90, 40 + r.commits),
                instructions=(
                    f"Inspect the repository at {r.path}: what landed this week, what is still "
                    "uncommitted, which branches have gone stale, and whether the tests pass. "
                    "Read-only commands only. Write the report to a file and name it in your "
                    "summary."
                ),
            )
        )
    # A repo that WAS active and went quiet is a different, quieter signal.
    quiet = [r for r in s.repos if r.mine and r.last_days and r.last_days >= 14]
    if quiet and not s.has("went quiet", "stalled", "dormant"):
        names = ", ".join(r.name for r in quiet[:3])
        out.append(
            Suggestion(
                key="quiet-projects",
                title="Went-quiet check",
                blurb="Projects with no commits lately — still alive, parked, or forgotten?",
                reason=f"{len(quiet)} repo(s) you were committing to have gone quiet: {names}.",
                cadence="monthly",
                cron="0 8 1 * *",
                agent=s.persona("repo-ops", "ops", "cowork"),
                requires=[],
                score=35,
                instructions=(
                    "For each repository I have committed to in the last 90 days but not the "
                    "last 14, report: when it stopped, what the last commits were doing, and "
                    "whether anything was left half-finished. Ask me one question per repo: "
                    "resume, park, or archive."
                ),
            )
        )
    return out


def _capability_rules(s: Signals) -> list[Suggestion]:
    out = []
    if "qdrant" in s.mcp and not s.has("qdrant", "knowledge base", "corpus", "embed"):
        out.append(
            Suggestion(
                key="kb-ingest",
                title="Knowledge base — ingest what the others write",
                blurb="Store each day's findings in the vector store so they can be retrieved later.",
                reason=(
                    "A Qdrant server is connected but nothing writes to it — "
                    f"{s.task_count} automations produce reports that are never indexed."
                ),
                cadence="daily",
                cron="0 6 * * *",
                agent=s.persona("repo-ops", "ops", "cowork"),
                requires=["qdrant"],
                score=75,
                instructions=(
                    "Read the reports the other automations wrote in the last 24 hours, pull out "
                    "the discrete findings, and store each one in the vector store with metadata "
                    "for job, date and source path. Check for a near-duplicate before storing — "
                    "a noisy corpus retrieves worse than a small clean one. Store only what you "
                    "actually read in a file today."
                ),
            )
        )
    if s.inbox_pending >= 3 and not s.has("inbox"):
        out.append(
            Suggestion(
                key="inbox-digest",
                title="Inbox digest",
                blurb="One summary of what is waiting on you, instead of a queue you scroll.",
                reason=f"{s.inbox_pending} items are sitting unanswered in the Inbox.",
                cadence="daily",
                cron="0 17 * * *",
                agent=s.persona("cowork", "fastchat"),
                requires=[],
                score=50,
                instructions=(
                    "Summarise the items waiting in my Inbox: who or what is asking, how long it "
                    "has waited, and what answering costs. Group by whether I can answer in one "
                    "line or need to sit down with it."
                ),
            )
        )
    for conn in sorted(s.connectors):
        if s.has(conn):
            continue
        out.append(
            Suggestion(
                key=f"connector-{conn}",
                title=f"Weekly {conn.title()} digest",
                blurb=f"What changed in {conn.title()} this week, summarised into one report.",
                reason=f"{conn.title()} is connected but no automation uses it.",
                cadence="weekly",
                cron="0 7 * * 1",
                agent=s.persona("cowork", "ops"),
                requires=[conn],
                score=30,
                instructions=(
                    f"Summarise the week's activity in {conn.title()}: what changed, what needs a "
                    "response, and what I have not looked at. Write it to a file."
                ),
            )
        )
    return out


def _perspective_rules(s: Signals) -> list[Suggestion]:
    if s.task_count < 4 or s.has("outside view", "devil", "counter-argument", "alternative approach"):
        return []
    return [
        Suggestion(
            key="outside-view",
            title="Outside view — argue against the current approach",
            blurb="Alternatives, prior art, and the case against what you are building.",
            reason=(
                f"All {s.task_count} of your automations report on work you are already doing. "
                "None of them tells you what you are not considering."
            ),
            cadence="weekly",
            cron="45 5 * * 3",
            agent=s.persona("research", "cowork"),
            requires=[],
            score=60,
            instructions=(
                "Take the problem I am currently working on and attack the way I am solving it. "
                "Find at least two alternative approaches, the prior art that already solved it, "
                "and the real arguments against my current path — search for the criticism "
                "explicitly. Close with one thing worth abandoning and one cheap experiment that "
                "would show the current approach is wrong. Never leave either blank."
            ),
        )
    ]


RULES = [_ladder_rules, _repo_rules, _capability_rules, _perspective_rules]


def suggest(signals: Signals, limit: int = 6) -> list[Suggestion]:
    """Ranked suggestions, strongest evidence first. Duplicates of existing automations are
    already filtered by the rules themselves — that check lives next to the evidence so a new
    rule cannot forget it."""
    out: list[Suggestion] = []
    for rule in RULES:
        out.extend(rule(signals))
    out.sort(key=lambda s: (-s.score, s.title))
    return out[:limit]


# -- caching -------------------------------------------------------------------------------


class SuggestionCache:
    """The repo scan shells out to git, so the whole signal set is cached. The page polls."""

    def __init__(self, ttl: float = _CACHE_TTL) -> None:
        self.ttl = ttl
        self._at = 0.0
        self._value: list[dict[str, Any]] = []

    def get(self, build) -> list[dict[str, Any]]:
        now = time.time()
        if now - self._at < self.ttl:
            return self._value
        self._value = build()
        self._at = now
        return self._value

    def invalidate(self) -> None:
        self._at = 0.0
