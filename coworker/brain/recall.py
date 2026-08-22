"""Recall — the brain's read path.

An archive nothing reads back is a drain with no tap: before this, sixty-odd dated reports and
a vector store accumulated for a week and no session ever consulted them. Recall answers one
question — "what do I already know about this?" — in two passes:

  1. THREADS. The durable subjects, matched on title, tags and state. A thread hit answers with
     what is true NOW plus how it got there, which is the answer you usually want.
  2. THE CORPUS. Full-text over the dated reports for anything no thread covers yet, newest
     first, returned as excerpts with their dates and paths.

Semantic recall stays with the qdrant MCP tool the personas already carry: embedding a query
natively would mean pulling an ONNX runtime into the server, and an embedder that drifts from
the one that wrote the vectors retrieves worse than lexical search.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .threads import Thread, brain_dir, load_all

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "was",
    "were", "be", "been", "it", "this", "that", "what", "how", "why", "do", "does", "did",
    "i", "my", "me", "we", "our", "you", "your", "about", "from", "at", "by", "as", "if",
}
_MAX_EXCERPT = 240


def terms(query: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9.+#-]+", (query or "").lower()) if w and w not in _STOP and len(w) > 2]


@dataclass
class ThreadHit:
    thread: Thread
    score: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.thread.id,
            "title": self.thread.title,
            "state": self.thread.state,
            "now": self.thread.now,
            "updated": self.thread.updated,
            "recent": [e.render().lstrip("- ") for e in self.thread.history[:3]],
        }


@dataclass
class CorpusHit:
    path: str
    when: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.path, "date": self.when, "text": self.text}


@dataclass
class Recall:
    query: str
    threads: list[ThreadHit] = field(default_factory=list)
    corpus: list[CorpusHit] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "threads": [t.as_dict() for t in self.threads],
            "corpus": [c.as_dict() for c in self.corpus],
            "searched": self.searched,
            # Stated explicitly: "nothing found" and "nowhere to look" are different answers,
            # and a caller that cannot tell them apart will invent the difference.
            "note": (
                "No brain directory yet — nothing has been recorded."
                if not self.searched
                else ""
            ),
        }


def score_thread(t: Thread, words: list[str]) -> int:
    """Title and tags identify a thread; the state line says what it currently means. History is
    worth least — an old entry mentioning a word should not outrank a thread that IS about it."""
    title = f"{t.title} {t.id}".lower()
    tags = " ".join(t.tags).lower()
    hist = " ".join(e.text for e in t.history[:10]).lower()
    score = 0
    for w in words:
        if w in title:
            score += 10
        if w in tags:
            score += 6
        if w in t.now.lower():
            score += 4
        if w in hist:
            score += 1
    if t.state == "active":
        score += 2  # a live thread outranks a resolved one at equal relevance
    return score


def matched_terms(t: Thread, words: list[str]) -> int:
    """How many DISTINCT query terms this thread matches anywhere."""
    hay = " ".join(
        [t.title, t.id, " ".join(t.tags), t.now, " ".join(e.text for e in t.history[:10])]
    ).lower()
    return sum(1 for w in set(words) if w in hay)


def names_the_subject(t: Thread, words: list[str]) -> bool:
    """Does a query term hit the thread's title, id or tags? Those name the subject, so one is
    enough on its own — everything else is a passing mention."""
    hay = f"{t.title} {t.id} {' '.join(t.tags)}".lower()
    return any(w in hay for w in words)


def _date_of(path: Path) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if m:
        return m.group(1)
    try:
        from datetime import datetime

        return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    except OSError:
        return ""


def search_corpus(roots: list[Path], words: list[str], limit: int) -> list[CorpusHit]:
    """Lexical search over the dated reports. ripgrep when present (it is what `grep` uses), a
    bounded Python walk otherwise so recall still works on a machine without it."""
    if not words or not roots:
        return []
    pattern = "|".join(re.escape(w) for w in words[:6])
    hits: list[CorpusHit] = []
    rg = shutil.which("rg")
    existing = [str(r) for r in roots if r.is_dir()]
    if not existing:
        return []
    if rg:
        cmd = [rg, "-i", "--no-heading", "--line-number", "--color=never", "--glob", "*.md",
               "--max-count", "3", "-e", pattern, *existing]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except Exception:
            return []
        lines = out.stdout.splitlines()
    else:
        lines = []
        for root in roots:
            for f in sorted(root.rglob("*.md"))[:400]:
                try:
                    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                        if re.search(pattern, line, re.I):
                            lines.append(f"{f}:{i}:{line}")
                except OSError:
                    continue

    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        path, _, text = parts
        text = text.strip()
        if not text or text.startswith("#"):
            continue
        p = Path(path)
        if p.name == "FOCUS.md":
            continue  # the focus file is always-loaded context, not a recall result
        hits.append(CorpusHit(path=path, when=_date_of(p), text=text[:_MAX_EXCERPT]))

    # Newest first: in a running record the recent statement supersedes the old one.
    hits.sort(key=lambda h: h.when, reverse=True)
    seen: set[str] = set()
    out: list[CorpusHit] = []
    for h in hits:
        key = h.text[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def recall(
    query: str,
    *,
    base: Optional[Path] = None,
    corpus_roots: Optional[list[Path]] = None,
    limit: int = 6,
) -> Recall:
    base = base or brain_dir()
    words = terms(query)
    out = Recall(query=query)

    # Keep a thread when the query NAMES it (title, id or tag) — that is the subject, and one
    # such term is enough. Otherwise the match is incidental and needs corroboration from a
    # second term: "openEvolve phase 2" pulled up the funding thread purely on "DARPA Phase I",
    # while "what happened to dcode-stack" must still find dcode-stack off its name alone.
    # A one-word query has no second term to corroborate with, so a body match must still count.
    need = min(2, len(set(words)))
    scored = [
        (score_thread(t, words), t)
        for t in load_all(base)
        if names_the_subject(t, words) or matched_terms(t, words) >= need
    ]
    out.threads = [ThreadHit(t, s) for s, t in sorted(scored, key=lambda x: -x[0]) if s > 0][:limit]

    roots = corpus_roots if corpus_roots is not None else default_corpus_roots(base)
    out.searched = [str(r) for r in roots if r.is_dir()]
    if base.is_dir() and str(base) not in out.searched:
        out.searched.append(str(base))
    out.corpus = search_corpus(roots, words, limit)
    return out


def default_corpus_roots(base: Optional[Path] = None) -> list[Path]:
    """Where the dated reports live: the brain's own reports plus every task workspace. Task
    workspaces sit under more than one scratch base on a machine whose `scratch_base` pref has
    changed, so they are discovered rather than assumed."""
    base = base or brain_dir()
    roots = [base]
    home = Path.home()
    for parent in {base.parent, home / "OpenWorker", home / "openworker-tasks"}:
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.is_dir() and child.name.startswith("__task__"):
                roots.append(child)
    return roots
