"""Session facts — what was already familiar when the session began, and what arrived from
outside since.

Both are deterministic: no model is involved in producing either. **In v1 neither changes a
decision.** The known world is rendered into the reviewer's prefix as orientation (step 2);
ingestion goes to the audit log and nothing reads it. That is deliberate — recording it now
means the v2 question ("would this fact have changed a verdict?") is answerable by replaying
a shadow run instead of re-arguing it.

Design of record: `ocw-context/docs/reviewed-auto-mode.md` Part 0 and §2.4.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

# Tool categories whose results carry content from outside this machine. Keyed on the
# category rather than a list of tool names so new connectors are covered the day they ship:
#   web        web_fetch, web_search
#   connector  gmail, slack, notion, … — anything reading a third-party service
#   mcp        third-party MCP tools, provenance unknown by construction
# Deliberately absent: `search` (that's local `grep`), `filesystem`, `git`, `shell`.
# `messaging` is absent too — `send_message` / `send_file` push data out, they don't pull it
# in. Local reads are excluded on purpose: count them and every turn becomes an ingestion
# turn, which kills the signal. The cost of that exclusion is recorded in the spec — a
# poisoned README in a cloned repo injects with no fact at all.
INGESTING_CATEGORIES = frozenset({"web", "connector", "mcp"})


def is_ingesting(metadata: Any) -> bool:
    """True when this tool's result can carry content authored outside the machine."""
    return getattr(metadata, "category", "") in INGESTING_CATEGORIES


def ingestion_source(arguments: dict[str, Any] | None) -> str:
    """A short, non-identifying label for where content came from — a hostname when the
    call names one, `-` otherwise. Never the content itself, and never a full URL: a query
    string is exactly the kind of thing that carries a payload."""
    raw = str((arguments or {}).get("url", "")).strip()
    if not raw:
        return "-"
    return (urlsplit(raw).hostname or "-").lower()


def _git_remotes(cwd: Path) -> tuple[tuple[str, str], ...]:
    """`(name, url)` per remote, deduplicated (git prints fetch and push separately).

    Best-effort by design: no git, not a repo, or a hang all yield an empty tuple. An empty
    known world is a reviewer with less orientation, never a blocked session.
    """
    try:
        proc = subprocess.run(
            ["git", "remote", "-v"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    seen: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            seen.setdefault(parts[0], parts[1])
    return tuple(seen.items())


@dataclass(frozen=True)
class KnownWorld:
    """Where the user was already working when the session started. Frozen on purpose.

    Freezing is what makes it useful: compared against the *live* state, an agent that runs
    `git remote add backup https://attacker.net/r.git` would make its own destination look
    familiar. Compared against a snapshot taken before it acted, it cannot.

    "Known" means *familiar*, never *safe* — nobody decided anything, the user has simply
    worked here before. The wording matters because the reviewer reads it: told something is
    "trusted", a model weighs it as reassurance. (`workspace_trust.json` keeps the word
    "trusted" because that one IS a decision.)
    """

    roots: tuple[tuple[str, bool], ...] = ()  # (path, writable)
    remotes: tuple[tuple[str, str], ...] = ()  # (name, url)
    hosts: tuple[str, ...] = ()  # NOT rendered — see `render`
    captured_at: float = 0.0

    def render(self) -> str:
        """The block that sits in the reviewer prompt's cached prefix.

        **Folders and remotes only.** Hostnames are held in `hosts` but deliberately not
        shown: a host list is only useful to a reviewer that can answer "is this destination
        in the list?", and that is a suffix match (`host == dom or host.endswith("." + dom)`)
        which models get wrong and Python does not. Printing `github.com` beside an action
        reaching `github.com.evil.site` invites the wrong answer rather than preventing it.
        Folders and remotes carry no such trap — judging them is "is this the thing I was
        told about?", not string arithmetic.

        `hosts` is kept for `DST-1` in v2, which will surface it as one *computed* line and
        never as a list for the model to search.
        """
        lines = ["KNOWN WORLD (frozen when this session started)"]
        for path, writable in self.roots:
            lines.append(
                f"  folder   {path}  [{'read-write' if writable else 'read-only'}]"
            )
        for name, url in self.remotes:
            lines.append(f"  remote   {name} -> {url}")
        return "\n".join(lines) if len(lines) > 1 else ""


def capture(
    *,
    roots: Iterable[Any] | None = None,
    allowed_domains: Iterable[str] | None = None,
    workspace: Optional[Path] = None,
) -> KnownWorld:
    """Take the snapshot. Called once, at session start, before the agent has acted."""
    root_list = list(roots or [])
    rendered_roots = tuple(
        (str(getattr(r, "path", r)), bool(getattr(r, "writable", False)))
        for r in root_list
    )

    cwd = workspace
    if cwd is None and root_list:
        cwd = Path(str(getattr(root_list[0], "path", root_list[0])))
    remotes = _git_remotes(cwd) if cwd else ()

    hosts = {d.strip().lower() for d in (allowed_domains or []) if d and d.strip()}
    for _name, url in remotes:
        host = urlsplit(url if "://" in url else "//" + url.replace(":", "/", 1)).hostname
        if host:
            hosts.add(host.lower())

    return KnownWorld(
        roots=rendered_roots,
        remotes=remotes,
        hosts=tuple(sorted(hosts)),
        captured_at=time.time(),
    )


@dataclass
class Ingestion:
    """One arrival of outside content. The fact and its source — never the content.

    Two properties worth keeping in mind before anything consumes this:

    * **It never accuses.** The record is identical for an agent following a documentation
      link found in an issue and for one running an injected `curl`, because in both cases
      the agent really did read that issue. It raises the burden of proof; judging scope is
      what separates the two.
    * **Its absence is not proof of a clean session.** Local reads are excluded, so a
      poisoned file already in the workspace produces no record at all.
    """

    turn: int
    tool: str
    source: str

    def to_audit(self) -> dict[str, Any]:
        return {
            "stage": "ingested",
            "status": "external",
            "reason": f"turn {self.turn} · {self.source}",
        }


@dataclass
class SessionFacts:
    """The known world plus the per-turn ingestion record.

    `turn` is bumped by the engine at the start of each user turn so ingestion can be
    attributed. Nothing in v1 reads `ingestions` — it exists so the audit log has a
    baseline; see the module docstring.
    """

    world: KnownWorld = field(default_factory=KnownWorld)
    turn: int = 0
    ingestions: list[Ingestion] = field(default_factory=list)

    def begin_turn(self) -> None:
        self.turn += 1

    def note(self, tool: str, arguments: dict[str, Any] | None) -> Ingestion:
        record = Ingestion(self.turn, tool, ingestion_source(arguments))
        self.ingestions.append(record)
        return record

    def this_turn(self) -> list[Ingestion]:
        return [i for i in self.ingestions if i.turn == self.turn]
