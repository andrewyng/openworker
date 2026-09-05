"""Persona manifest — parse + validate a persona definition.

Format: YAML frontmatter (identity + capability declaration) followed by a markdown body that
is the system prompt. `persona ⊇ skill` — the same frontmatter-markdown shape as SKILL.md, with
more structured fields. Parsing is strict: an invalid manifest raises ``ManifestError`` rather
than silently producing a broken persona (a third-party persona must fail loudly).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

# Persona ids become directory names under the managed install area (and registry keys), so
# they are restricted to a filesystem-safe slug on every OS: no path separators or `..`
# (traversal), no `:*?"<>|` (invalid on Windows), bounded length.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

VALID_FAMILIES = {"code", "knowledge"}  # legacy key, shimmed in parse()
VALID_TEAM = {"lead", "worker"}
# "auto" kept as the legacy spelling of "bypass-approvals" (Mode._missing_).
VALID_MODES = {"discuss", "plan", "interactive", "custom", "auto", "bypass-approvals", "auto-approve"}
VALID_REC_KINDS = {"connector", "mcp"}
VALID_REC_TIERS = {"core", "optional"}
VALID_ACCENTS = {
    "cobalt",
    "indigo",
    "violet",
    "magenta",
    "rose",
    "amber",
    "lime",
    "green",
    "teal",
    "cyan",
    "slate",
}
# Start-screen rows are the persona's own suggestions; more than this and the empty state stops
# reading as "pick one" and starts reading as a menu.
MAX_STARTERS = 4
# Checkpoints are the shape of ONE job for this persona. More than six and the rail's progress
# strip stops being a glance; fewer than two and there is no "next" to point at.
MAX_CHECKPOINTS = 6
# Budgets are a glance, not a dashboard: past four counters the rail stops being readable and
# the numbers stop being acted on.
MAX_BUDGETS = 4
# Persona groupings (origin/main), cosmetic — grouping never gates behavior:
VALID_GROUPS = {"general", "security"}


class ManifestError(ValueError):
    """A persona manifest is malformed or references unknown capabilities/values."""


@dataclass
class Recommendation:
    """A connection a persona recommends, surfaced in the per-session connections drawer. ``ref`` is a
    connector id or an MCP server name; ``reason`` is the value it unlocks; ``tier`` ranks it. Not
    validated against shipped connectors — a persona may recommend one we don't ship yet.
    """

    kind: str  # "connector" | "mcp"
    ref: str
    reason: str = ""
    tier: str = "optional"  # "core" | "optional"


@dataclass
class Starter:
    """One start-screen template task. ``title`` is the row, ``sub`` states the OUTCOME (never
    connection state), ``prompt`` is what clicking prefills into the composer, and ``requires``
    lists what must be live first — ``folder`` for a shared directory, otherwise a connector id.
    A row whose requirements are unmet renders gated, offering setup instead of the prompt.
    """

    title: str
    prompt: str
    sub: str = ""
    requires: list[str] = field(default_factory=list)
    # Stable handle for the row (test ids, React keys). Derived from the title when unset.
    key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "sub": self.sub,
            "prompt": self.prompt,
            "requires": list(self.requires),
        }


@dataclass
class Checkpoint:
    """One step in the shape of this persona's job — what "done" looks like, in order.

    The Progress panel could always show a todo list, but a todo list is what the MODEL decided
    to do this run; it cannot say whether the run is halfway through the work this persona is
    supposed to do. Checkpoints are that second axis: the persona's own definition of a
    complete job, with each step evidenced by tool calls the session can actually observe.

    `evidence` names the tools whose use means this step has happened. A step nothing can
    evidence would sit "pending" forever and make a finished run look stuck, so an empty list
    is rejected at parse time.

    `after` names an EARLIER step that must already have happened for this one's evidence to
    count. Some evidence only means something in sequence: `run_shell` is the most-called tool
    in any code run — `ls`, `wc`, `sed` — so on its own it is not evidence of verification,
    while the same call after an edit is. Ungated, Verify was satisfied within the first few
    calls of every run, which dragged the furthest-reached marker to the end of the strip and
    made every unfinished step before it read as deliberately skipped.
    """

    id: str
    label: str
    evidence: list[str] = field(default_factory=list)
    after: str = ""

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "evidence": list(self.evidence),
        }
        # Omitted rather than sent empty: the panel treats "no gate" and "a gate naming nothing"
        # differently, and an empty string is the second one.
        if self.after:
            d["after"] = self.after
        return d


@dataclass
class Budget:
    """A ceiling on one kind of tool call for a single run.

    These exist in prose today — "AT MOST 4 arxiv searches", "at most 15 tool calls" — where
    nothing can enforce or even display them, so an overrun only shows up afterwards as a long
    transcript and a run marked incomplete. Structured, the rail can show `searches 6/8` while
    the run is still happening.

    The limit is ADVISORY: nothing here blocks a call. Blocking would turn a budget into a
    failure mode of its own — a run halted at 8/8 with the deliverable unwritten is worse than
    one that went to 9. The value is in seeing it.
    """

    id: str
    label: str
    limit: int
    # Tool names to count, or the single entry "*" meaning EVERY tool call. A total-call ceiling
    # is the most common budget the automations declare ("AT MOST 15 tool calls", seven of them),
    # and enumerating a persona's whole toolset by hand to express it would rot the moment the
    # catalog changes.
    tools: list[str] = field(default_factory=list)

    @property
    def counts_everything(self) -> bool:
        return self.tools == ["*"]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "limit": self.limit, "tools": list(self.tools)}


@dataclass
class PersonaIntro:
    """How a persona introduces itself: the empty-state greeting + lede, the composer's
    placeholder, and its own template tasks. Every field is optional — whatever a manifest
    leaves out, the GUI fills from the persona's family, so a minimal manifest still gets a
    coherent start screen instead of another persona's.
    """

    greeting: str = ""
    lede: str = ""
    placeholder: str = ""
    starters: list[Starter] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "greeting": self.greeting,
            "lede": self.lede,
            "placeholder": self.placeholder,
            "starters": [s.as_dict() for s in self.starters],
        }

    def __bool__(self) -> bool:
        return bool(self.greeting or self.lede or self.placeholder or self.starters)


@dataclass
class PersonaManifest:
    id: str
    name: str
    system_prompt: str
    icon: str = ""
    tagline: str = ""
    description: str = ""
    tools: list[str] = field(default_factory=list)
    # Workspace/toolset traits (workspace-scratch-design.md — replaces the old
    # family/workspace pair). requires_folder: the composer/engine gate on a
    # user-picked primary folder. subagents: explorer fan-out. scheduling:
    # scheduled tasks + self-wake (defaults to the opposite of requires_folder
    # when the manifest is silent — folder personas fan out instead).
    requires_folder: bool = False
    subagents: bool = False
    scheduling: bool = True
    messaging: bool = False
    # Connector grant (OPE-93): False = none, a tuple = allowlist of connector ids
    # (session exposes declared ∩ connected), True = every connected connector — the
    # `all` sentinel, reserved for built-in general personas. Coarser grants leaked
    # undeclared tools (browser, email) into security sessions; undeclared = absent.
    connectors: bool | tuple[str, ...] = False
    # Team identity (agent-teams design, third/fourth pass): "lead" = coordinates a
    # team (gets the board coordination verbs + gates; consent copy says "can create
    # and direct worker coworkers"); "worker" = purpose-built to work under a lead
    # (board worker verbs, no ask_user-shaped prompt); None = solo-only. Solo
    # personas are NOT team-eligible — team-awareness changes who the prompt talks
    # to, so staffing fails closed on personas without the trait.
    team: Optional[str] = None
    default_permission_mode: str = "interactive"
    recommended_models: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    mcp: list[str] = field(default_factory=list)
    # Sharing v1 (OPE-7): the author's version string ("1", "1.2", "2026-08"…). Purely
    # informational provenance — with folder/git distribution there is no authoritative
    # update channel, so this drives the "replaces vN" note on re-install, nothing more.
    version: str = ""
    recommends: list[Recommendation] = field(default_factory=list)
    # Whether this persona's sessions belong to a PROJECT: the user picks a folder before
    # starting, and the sidebar groups its sessions under that folder. `None` means "derive" —
    # a persona that needs no workspace can never have projects. Declared separately from
    # `family` on purpose: family still governs the multi-root engine behaviour that scheduled
    # runs depend on, and conflating the two would change how automations see their workspace.
    projects: Optional[bool] = None
    accent: str = ""  # one of VALID_ACCENTS; empty → the GUI derives one from the id
    intro: PersonaIntro = field(default_factory=PersonaIntro)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    budgets: list[Budget] = field(default_factory=list)
    # Distribution decision, not a maturity claim (owner, 2026-08-21): ships:false
    # coworkers exist in the codebase but are absent from release builds — internal
    # builds opt them in via OPENWORKER_UNSHIPPED=1.
    ships: bool = True
    # Settings-page grouping ("general" | "security"). Cosmetic — grouping never
    # gates behavior, so a third-party persona claiming "security" is harmless.
    group: str = "general"
    builtin: bool = False
    source: Optional[str] = (
        None  # where it was loaded from (path / url), for provenance
    )

    @property
    def has_projects(self) -> bool:
        """Declared value, else: a persona groups its sessions by project — they land in
        whichever folder the user picks. A chat-shaped persona opts out with `projects: false`."""
        if self.projects is not None:
            return self.projects
        return True

    def to_agent(self):
        """Materialize the runtime Agent (prompt + catalog-expanded tools + traits)."""
        from ..agents.base import Agent
        from ..catalog import expand

        tool_ids = list(self.tools)
        factory = (lambda ctx: expand(tool_ids, ctx)) if tool_ids else None
        return Agent(
            name=self.id,
            title=self.name,
            system_prompt=self.system_prompt,
            tool_factory=factory,
            requires_folder=self.requires_folder,
            subagents=self.subagents,
            scheduling=self.scheduling,
            messaging=self.messaging,
            connectors=self.connectors,
            team=self.team,
        )


def _connectors(
    persona_id: str,
    raw: Any,
    recommends: list[Recommendation],
    builtin: bool,
) -> bool | tuple[str, ...]:
    """Parse the connector grant (OPE-93). Fail closed at every ambiguity.

    - list → explicit allowlist (the normal case).
    - "all" → every connected connector; reserved for BUILT-IN general personas — a
      shared bundle claiming it is exactly the trust violation the allowlist exists
      to prevent, so third-party loads reject it.
    - legacy `true` (pre-allowlist manifests) → the connector refs the manifest already
      recommends (author intent); no recommends → no grant.
    - recommends must stay within the grant: a recommendation the coworker can't use is
      author drift, surfaced at load rather than at the user's consent screen.
    """
    if raw is None or raw is False:
        declared: bool | tuple[str, ...] = False
    elif raw is True:
        refs = {r.ref for r in recommends if r.kind == "connector"}
        declared = tuple(sorted(refs)) if refs else False
    elif isinstance(raw, str):
        if raw.strip().lower() != "all":
            raise ManifestError(
                f"{persona_id}: `connectors` must be a list of connector ids or 'all'"
            )
        if not builtin:
            raise ManifestError(
                f"{persona_id}: `connectors: all` is reserved for built-in coworkers — "
                "declare the specific connectors this coworker uses"
            )
        declared = True
    elif isinstance(raw, list):
        declared = tuple(
            dict.fromkeys(s for s in (str(x).strip() for x in raw) if s)
        )
    else:
        raise ManifestError(
            f"{persona_id}: `connectors` must be a list of connector ids or 'all'"
        )

    if declared is not True:
        granted = set(declared or ())
        for r in recommends:
            if r.kind == "connector" and r.ref not in granted:
                raise ManifestError(
                    f"{persona_id}: recommends connector '{r.ref}' but does not declare "
                    "it in `connectors` — a recommendation must stay within the grant"
                )
    return declared


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise ManifestError("manifest must start with a YAML frontmatter block (---)")
    end = text.find("\n---", 3)
    if end == -1:
        raise ManifestError("unterminated frontmatter block (missing closing ---)")
    raw = text[3:end]
    body = text[end + 4 :].lstrip("\n")
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:  # pragma: no cover - exercised via parse error path
        raise ManifestError(f"invalid YAML frontmatter: {e}") from e
    if not isinstance(meta, dict):
        raise ManifestError("frontmatter must be a mapping of key: value")
    return meta, body


def _slugify(stem: str) -> str:
    """Normalize a filename stem into the persona-id charset (used only for ids derived
    from filenames; explicit `id:` values must already be valid)."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", stem.strip().lower()).strip("-_")[:64]
    return slug if _ID_RE.match(slug) else ""


def _strlist(meta: dict, key: str) -> list[str]:
    val = meta.get(key, [])
    if val is None:
        return []
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    raise ManifestError(f"`{key}` must be a list or comma-separated string")


def _recommends(persona_id: str, meta: dict) -> list[Recommendation]:
    raw = meta.get("recommends")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ManifestError(f"persona {persona_id!r}: `recommends` must be a list")
    out: list[Recommendation] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ManifestError(
                f"persona {persona_id!r}: each `recommends` item must be a mapping"
            )
        if "connector" in item:
            kind, ref = "connector", str(item.get("connector") or "").strip()
        elif "mcp" in item:
            kind, ref = "mcp", str(item.get("mcp") or "").strip()
        else:
            raise ManifestError(
                f"persona {persona_id!r}: each `recommends` item needs a `connector:` or `mcp:` key"
            )
        if not ref:
            raise ManifestError(
                f"persona {persona_id!r}: a `recommends` item has an empty {kind}"
            )
        tier = str(item.get("tier", "optional")).strip().lower()
        if tier not in VALID_REC_TIERS:
            raise ManifestError(
                f"persona {persona_id!r}: recommend tier must be one of {sorted(VALID_REC_TIERS)}"
            )
        out.append(
            Recommendation(
                kind=kind,
                ref=ref,
                reason=str(item.get("reason", "")).strip(),
                tier=tier,
            )
        )
    return out


def _starter_key(title: str, index: int) -> str:
    """A stable handle for a starter row (React key, test id). Whole words only — a hard
    character cut leaves a truncated word in the DOM, which reads as a bug in a test id."""
    words = [w for w in re.split(r"[^a-z0-9]+", title.strip().lower()) if w]
    slug = ""
    for w in words:
        candidate = f"{slug}-{w}" if slug else w
        if len(candidate) > 32:
            break
        slug = candidate
    return slug or f"task-{index + 1}"


def _intro(persona_id: str, meta: dict) -> PersonaIntro:
    raw = meta.get("intro")
    if raw is None:
        return PersonaIntro()
    if not isinstance(raw, dict):
        raise ManifestError(f"persona {persona_id!r}: `intro` must be a mapping")
    starters_raw = raw.get("starters") or []
    if not isinstance(starters_raw, list):
        raise ManifestError(f"persona {persona_id!r}: `intro.starters` must be a list")
    if len(starters_raw) > MAX_STARTERS:
        raise ManifestError(
            f"persona {persona_id!r}: at most {MAX_STARTERS} `intro.starters` (got {len(starters_raw)})"
        )
    starters: list[Starter] = []
    for i, item in enumerate(starters_raw):
        if not isinstance(item, dict):
            raise ManifestError(
                f"persona {persona_id!r}: each `intro.starters` item must be a mapping"
            )
        title = str(item.get("title", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not title or not prompt:
            raise ManifestError(
                f"persona {persona_id!r}: each `intro.starters` item needs a `title` and a `prompt`"
            )
        requires = _strlist(item, "requires")
        key = str(item.get("key", "")).strip() or _starter_key(title, i)
        starters.append(
            Starter(
                title=title,
                prompt=prompt,
                sub=str(item.get("sub", "")).strip(),
                requires=requires,
                key=key,
            )
        )
    return PersonaIntro(
        greeting=str(raw.get("greeting", "")).strip(),
        lede=str(raw.get("lede", "")).strip(),
        placeholder=str(raw.get("placeholder", "")).strip(),
        starters=starters,
    )


def _checkpoints(persona_id: str, meta: dict) -> list[Checkpoint]:
    raw = meta.get("checkpoints")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ManifestError(f"persona {persona_id!r}: `checkpoints` must be a list")
    if len(raw) > MAX_CHECKPOINTS:
        raise ManifestError(
            f"persona {persona_id!r}: at most {MAX_CHECKPOINTS} checkpoints (got {len(raw)})"
        )
    out: list[Checkpoint] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(
                f"persona {persona_id!r}: each `checkpoints` item must be a mapping"
            )
        label = str(item.get("label", "")).strip()
        if not label:
            raise ManifestError(f"persona {persona_id!r}: a checkpoint needs a `label`")
        evidence = _strlist(item, "evidence")
        if not evidence:
            raise ManifestError(
                f"persona {persona_id!r}: checkpoint {label!r} needs `evidence` — a step no tool "
                "call can satisfy stays pending forever and makes a finished run look stuck"
            )
        cid = str(item.get("id", "")).strip() or _starter_key(label, i)
        after = str(item.get("after", "")).strip()
        # Must name a step ALREADY parsed: a gate on a later step (or on itself) can never open,
        # which would pin this step pending for the whole run — the exact failure the empty
        # `evidence` check above exists to prevent, arriving by a different door.
        if after and after not in {c.id for c in out}:
            known = ", ".join(c.id for c in out) or "none yet"
            raise ManifestError(
                f"persona {persona_id!r}: checkpoint {label!r} has `after: {after}`, which is "
                f"not an earlier checkpoint (earlier ids: {known})"
            )
        out.append(Checkpoint(id=cid, label=label, evidence=evidence, after=after))
    return out


def _budgets(persona_id: str, meta: dict) -> list[Budget]:
    raw = meta.get("budgets")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ManifestError(f"persona {persona_id!r}: `budgets` must be a list")
    if len(raw) > MAX_BUDGETS:
        raise ManifestError(
            f"persona {persona_id!r}: at most {MAX_BUDGETS} budgets (got {len(raw)})"
        )
    out: list[Budget] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(f"persona {persona_id!r}: each `budgets` item must be a mapping")
        label = str(item.get("label", "")).strip()
        if not label:
            raise ManifestError(f"persona {persona_id!r}: a budget needs a `label`")
        try:
            limit = int(item.get("limit"))
        except (TypeError, ValueError):
            raise ManifestError(
                f"persona {persona_id!r}: budget {label!r} needs a whole-number `limit`"
            ) from None
        if limit <= 0:
            raise ManifestError(
                f"persona {persona_id!r}: budget {label!r} has limit {limit} — a ceiling of zero "
                "or less can only ever read as already exceeded"
            )
        tools = _strlist(item, "tools")
        if not tools:
            raise ManifestError(
                f"persona {persona_id!r}: budget {label!r} needs `tools` — a budget with nothing "
                "to count would sit at 0 forever and quietly reassure"
            )
        if "*" in tools and tools != ["*"]:
            raise ManifestError(
                f"persona {persona_id!r}: budget {label!r} mixes `*` with named tools; `*` already "
                "counts every call, so the named ones would be counted twice"
            )
        bid = str(item.get("id", "")).strip() or _starter_key(label, i)
        out.append(Budget(id=bid, label=label, limit=limit, tools=tools))
    return out


def parse_manifest(
    text: str,
    *,
    fallback_id: Optional[str] = None,
    builtin: bool = False,
    source: Optional[str] = None,
) -> PersonaManifest:
    meta, body = _split_frontmatter(text)

    explicit_id = str(meta.get("id") or "").strip()
    if explicit_id:
        persona_id = explicit_id
        if not _ID_RE.match(persona_id):
            raise ManifestError(
                f"persona id {persona_id!r} is invalid: lowercase letters, digits, '-' or '_' "
                "only, starting with a letter/digit, max 64 chars (ids become directory names)"
            )
    else:
        # Derived from the filename: normalize it into the id charset instead of erroring,
        # so `My Persona.md` without an explicit id still installs (as `my-persona`).
        persona_id = _slugify(str(fallback_id or ""))
        if not persona_id:
            raise ManifestError(
                "manifest needs an `id` (or a filename to derive one from)"
            )
    if not body.strip():
        raise ManifestError(f"persona {persona_id!r} has no body (the system prompt)")

    # Workspace/toolset traits (workspace-scratch-design.md). Legacy shim: pre-trait
    # bundles declared `family: code|knowledge` (and a dead `workspace:` enum, ignored
    # here) — when the new keys are absent, `family: code` maps to the folder-gated
    # profile so an old bundle keeps its gate. New keys always win.
    legacy_family = str(meta.get("family", "")).strip().lower()
    if legacy_family and legacy_family not in VALID_FAMILIES:
        raise ManifestError(
            f"persona {persona_id!r}: family (legacy) must be one of {sorted(VALID_FAMILIES)}"
        )
    legacy_code = legacy_family == "code"
    requires_folder = bool(meta.get("requires_folder", legacy_code))
    subagents = bool(meta.get("subagents", legacy_code))
    # Folder personas fan out to explorers instead of scheduling — the silent default
    # mirrors that split; either can be declared explicitly.
    scheduling = bool(meta.get("scheduling", not requires_folder))

    mode = str(meta.get("default_permission_mode", "interactive")).strip().lower()
    if mode not in VALID_MODES:
        raise ManifestError(
            f"persona {persona_id!r}: default_permission_mode must be one of {sorted(VALID_MODES)}"
        )

    group = str(meta.get("group", "general") or "general").strip().lower()
    if group not in VALID_GROUPS:
        raise ManifestError(
            f"persona {persona_id!r}: group must be one of {sorted(VALID_GROUPS)}"
        )

    team_raw = str(meta.get("team", "") or "").strip().lower()
    if team_raw and team_raw not in VALID_TEAM:
        raise ManifestError(
            f"persona {persona_id!r}: team must be one of {sorted(VALID_TEAM)}"
            " (omit for a solo coworker)"
        )

    tools = _strlist(meta, "tools")
    _validate_tools(persona_id, tools)
    recommends = _recommends(persona_id, meta)
    connectors = _connectors(persona_id, meta.get("connectors"), recommends, builtin)

    projects = meta.get("projects")
    if projects is not None and not isinstance(projects, bool):
        raise ManifestError(
            f"persona {persona_id!r}: `projects` must be true or false, got {projects!r}"
        )

    accent = str(meta.get("accent", "")).strip().lower()
    if accent and accent not in VALID_ACCENTS:
        raise ManifestError(
            f"persona {persona_id!r}: accent must be one of {sorted(VALID_ACCENTS)}"
        )

    return PersonaManifest(
        id=persona_id,
        name=str(meta.get("name") or persona_id).strip(),
        system_prompt=body.strip(),
        icon=str(meta.get("icon", "")).strip(),
        tagline=str(meta.get("tagline", "")).strip(),
        description=str(meta.get("description", "")).strip(),
        tools=tools,
        requires_folder=requires_folder,
        subagents=subagents,
        scheduling=scheduling,
        messaging=bool(meta.get("messaging", False)),
        connectors=connectors,
        team=team_raw or None,
        default_permission_mode=mode,
        recommended_models=_strlist(meta, "recommended_models"),
        skills=_strlist(meta, "skills"),
        mcp=_strlist(meta, "mcp"),
        projects=projects,
        accent=accent,
        intro=_intro(persona_id, meta),
        checkpoints=_checkpoints(persona_id, meta),
        budgets=_budgets(persona_id, meta),
        version=str(meta.get("version", "") or "").strip(),
        recommends=recommends,
        ships=bool(meta.get("ships", True)),
        group=group,
        builtin=builtin,
        source=source,
    )


def _validate_tools(persona_id: str, tools: list[str]) -> None:
    # Imported here to avoid a module-load cycle (catalog imports agents.base).
    from ..catalog import CATALOG

    unknown = [t for t in tools if t not in CATALOG]
    if unknown:
        raise ManifestError(
            f"persona {persona_id!r} references unknown tool capabilities: {unknown}. "
            f"Known: {sorted(CATALOG)}"
        )


def load_manifest_file(path: str | Path, *, builtin: bool = False) -> PersonaManifest:
    p = Path(path)
    return parse_manifest(
        p.read_text(encoding="utf-8"),
        fallback_id=p.stem,
        builtin=builtin,
        source=str(p),
    )
