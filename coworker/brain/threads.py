"""Threads — the brain's identity-over-time layer.

The scheduled automations produce a dated report per run. That is a good archive and a poor
memory: "openEvolve Phase 2" written in August and the same subject written in November are
unrelated strings, so anything retrieving from the pile gets a bag of sentences rather than a
history. A *thread* is one durable subject with a stable id, a CURRENT STATE line, and a dated
history beneath it.

The state line is what makes supersession explicit. An archive where "X is true" (August) and
"X is false" (November) retrieve equally well is worse than no memory at all — it grows more
confidently wrong with age. Here the state line is the answer and the history is the evidence
for how it got there, so a stale claim can only be read as history.

Format — YAML frontmatter + markdown, the same shape as SKILL.md and a persona manifest:

    ---
    id: openevolve-phase-2
    title: OpenEvolve Phase 2
    state: active
    updated: 2026-08-22
    tags: [opensciencelab, optimization]
    ---
    **Now:** one sentence that is true today.

    ## History
    - 2026-08-22 — what happened, and where it is recorded. (source: path)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

VALID_STATES = {"active", "quiet", "parked", "resolved"}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# One thread's history is a running record, not a log: past a few dozen entries it stops being
# readable and the older detail belongs in the dated reports it points at.
MAX_HISTORY = 40


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")[:64]
    return slug if _ID_RE.match(slug) else ""


# Words that name nothing on their own, dropped before two references are compared. Kept tiny
# on purpose: this decides whether two strings are the SAME SUBJECT, not whether they are
# relevant to each other.
_FILLER = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by"}
# "OpenEvolve Phase Two" and "OpenEvolve Phase 2" are one subject, and on a machine whose
# durable subjects are literally named "Phase 2" the difference is a spelling, not a meaning.
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


def identity_words(text: str) -> frozenset[str]:
    """The words that NAME a subject, normalised so two spellings of one name come out equal:
    case, punctuation, separators, word order, filler and "Two" vs "2" all stop mattering."""
    words = [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]
    return frozenset(_NUMBER_WORDS.get(w, w) for w in words if w not in _FILLER)


def find_thread(key: str, threads: list["Thread"]) -> Optional["Thread"]:
    """The existing thread `key` names, or None if it names a new one.

    A brain that forks a subject on every rephrasing loses exactly the continuity it exists to
    provide. Matching on the id or the byte-exact title alone did that: "OpenScienceLab /
    openEvolve — Phase 2", "opensciencelab Phase 2", "Phase 2 — OpenEvolve" and "OpenEvolve
    Phase Two" produced four files with four state lines, and recall answered with whichever of
    them happened to score highest — on this machine, the stalest one.

    In falling confidence:
      1. the id or the title, verbatim (or the id the title slugifies to);
      2. the same identity words, in any order or spelling;
      3. one name naming the subject with fewer (or more) words than the other, but ONLY when
         exactly one thread fits and the shorter name is more than one word. Two candidates
         means the reference is genuinely ambiguous, and merging two subjects is worse than
         forking one — a thread that mixes subjects can no longer have a true state line.

    Nothing here reads a thread's body: a thread IS its name, and a passing mention in the
    history is not identity.
    """
    key = (key or "").strip()
    if not key:
        return None
    lower = key.lower()
    slug = slugify(key)
    for t in threads:
        if t.id == key or (slug and t.id == slug) or t.title.strip().lower() == lower:
            return t

    want = identity_words(key)
    if not want:
        return None
    named = [(t, (identity_words(t.id), identity_words(t.title))) for t in threads]
    exact = {t.id: t for t, names in named if want in names}
    if exact:
        return next(iter(exact.values())) if len(exact) == 1 else None
    partial = {
        t.id: t
        for t, names in named
        for n in names
        if n and min(len(n), len(want)) > 1 and (n <= want or want <= n)
    }
    return next(iter(partial.values())) if len(partial) == 1 else None


def near_threads(key: str, threads: list["Thread"]) -> list[str]:
    """Ids of threads whose name overlaps `key` without being it. Returned when a note creates a
    thread anyway, so a fork the matcher declined to resolve is visible instead of silent."""
    want = identity_words(key)
    if not want:
        return []
    out = []
    for t in threads:
        shared = (identity_words(t.id) | identity_words(t.title)) & want
        # Two words in common, or the whole reference echoed — one shared word is a coincidence
        # ("phase"), and a hint that fires on every thread is a hint nobody reads.
        if len(shared) > 1 or shared == want:
            out.append(t.id)
    return out


def brain_dir() -> Path:
    """Where the brain lives. Explicit env wins, then the `brain_dir` pref, then a default
    beside the state dir. Resolved standalone because the tool layer has no manager to ask."""
    env = os.environ.get("COWORKER_BRAIN_DIR")
    if env:
        return Path(env).expanduser()
    from ..secrets import state_dir

    try:
        import json

        prefs = json.loads((state_dir() / "prefs.json").read_text(encoding="utf-8"))
        configured = prefs.get("brain_dir")
        if configured:
            return Path(configured).expanduser()
    except Exception:
        pass
    return state_dir() / "brain"


def oneline(text: str) -> str:
    """Collapse to a single line.

    This file format is line-oriented: one entry per line, and a line starting `**Now:**` IS
    the state line. So a newline inside an entry is not content, it is structure. Written
    verbatim, everything after the first line vanished on read-back (while the reported entry
    count counted it), and any line that happened to match the grammar was PROMOTED — an
    automation summarising an untrusted page could enter a past-dated finding under a source of
    its choosing and replace the state line, which `now` is meant to be the only way to change.
    """
    return " ".join((text or "").split())


@dataclass
class Entry:
    when: str  # YYYY-MM-DD
    text: str
    source: str = ""

    def render(self) -> str:
        tail = f" (source: {oneline(self.source)})" if self.source else ""
        return f"- {self.when} — {oneline(self.text)}{tail}"


@dataclass
class Thread:
    id: str
    title: str
    now: str = ""  # the CURRENT state — one sentence, authoritative
    state: str = "active"
    updated: str = ""
    tags: list[str] = field(default_factory=list)
    history: list[Entry] = field(default_factory=list)
    path: Optional[Path] = None

    def render(self) -> str:
        meta = {
            "id": self.id,
            "title": self.title,
            "state": self.state,
            "updated": self.updated or date.today().isoformat(),
            "tags": list(self.tags),
        }
        head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
        lines = [f"---\n{head}\n---", f"**Now:** {oneline(self.now)}".rstrip(), "", "## History"]
        lines += [e.render() for e in self.history[:MAX_HISTORY]]
        return "\n".join(lines) + "\n"

    def add(self, text: str, when: str = "", source: str = "") -> None:
        """Record what happened, keeping the history newest-first and never entering the same
        thing twice — a rollup job re-reading yesterday's report must not double-enter it.

        Inserted by DATE rather than at the front: a job backfilling an older finding would
        otherwise put it at the top and make the thread read as if that were the latest news.
        `updated` is the newest date the thread holds, not the last one written.
        """
        when = when or date.today().isoformat()
        # Normalise BEFORE the duplicate check, or the guard compares the caller's multi-line
        # text against the truncated line that was parsed back and never matches: three
        # byte-identical calls produced three identical stubs.
        text = oneline(text)
        if any(e.when == when and e.text == text for e in self.history):
            return
        entry = Entry(when=when, text=text, source=oneline(source))
        at = next((i for i, e in enumerate(self.history) if e.when < when), len(self.history))
        self.history.insert(at, entry)
        del self.history[MAX_HISTORY:]
        self.updated = max((e.when for e in self.history), default=when)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "now": self.now,
            "state": self.state,
            "updated": self.updated,
            "tags": list(self.tags),
            "history": [{"when": e.when, "text": e.text, "source": e.source} for e in self.history],
        }


def parse(text: str, path: Optional[Path] = None) -> Thread:
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                meta = yaml.safe_load(text[3:end]) or {}
            except yaml.YAMLError:
                meta = {}
            body = text[end + 4 :]
    if not isinstance(meta, dict):
        meta = {}

    now = ""
    history: list[Entry] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("**Now:**"):
            now = stripped[len("**Now:**") :].strip()
        elif stripped.startswith("- "):
            m = re.match(r"- (\d{4}-\d{2}-\d{2}) — (.*?)(?: \(source: (.*)\))?$", stripped)
            if m:
                history.append(Entry(when=m.group(1), text=m.group(2), source=m.group(3) or ""))

    tid = str(meta.get("id") or (path.stem if path else "")).strip()
    state = str(meta.get("state", "active")).strip().lower()
    return Thread(
        id=tid,
        title=str(meta.get("title") or tid).strip(),
        now=now,
        state=state if state in VALID_STATES else "active",
        updated=str(meta.get("updated", "")).strip(),
        tags=[str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()],
        history=history,
        path=path,
    )


def threads_dir(base: Optional[Path] = None) -> Path:
    return (base or brain_dir()) / "threads"


def load_all(base: Optional[Path] = None) -> list[Thread]:
    d = threads_dir(base)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.md")):
        try:
            out.append(parse(f.read_text(encoding="utf-8"), f))
        except OSError:
            continue
    # Active first, then most recently updated: a recall answer leads with what is live.
    order = {"active": 0, "quiet": 1, "parked": 2, "resolved": 3}
    out.sort(key=lambda t: (order.get(t.state, 9), _neg(t.updated)))
    return out


def _neg(iso: str) -> str:
    """Sort key that puts the most recent date first without reversing the whole tuple."""
    return "".join(chr(ord("9") - int(c)) if c.isdigit() else c for c in (iso or ""))


def load(thread_id: str, base: Optional[Path] = None) -> Optional[Thread]:
    f = threads_dir(base) / f"{thread_id}.md"
    if not f.is_file():
        return None
    return parse(f.read_text(encoding="utf-8"), f)


def save(thread: Thread, base: Optional[Path] = None) -> Path:
    d = threads_dir(base)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{thread.id}.md"
    # Write-then-rename, not write_text: a reader that opens the file mid-write sees a
    # truncated one. Measured 10,545 of 18,175 concurrent reads returning an EMPTY history
    # against a stable 28-entry file — a thread that silently reads as blank is the worst
    # outcome the brain has, because the caller then records against nothing.
    tmp = d / f".{thread.id}.md.tmp"
    tmp.write_text(thread.render(), encoding="utf-8")
    os.replace(tmp, f)
    thread.path = f
    return f
