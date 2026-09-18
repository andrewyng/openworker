"""Permission engine — decides allow / deny / ask-user for each proposed tool call.

Modes: Plan (read-only) · Interactive (auto reads, ask on writes/commands) · Auto
(allow, still path-scoped). Refined by argument patterns (path-under-root, command
prefixes) and a session allowlist. The engine only *decides*; the turn engine routes
`needs_user` decisions to a surface for approval and records the outcome.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

# Constructs whose *contents* we cannot evaluate, so a command carrying one is never
# eligible for prefix auto-run: command/process substitution, redirection (writes anywhere
# the allowlist never vetted), and variable expansion (the value was set out of view).
_OPAQUE_CONSTRUCTS = ("`", "$(", "$", ">", "<", "(")

# Separators that chain several commands into one string. Each part is checked independently
# against the allowlist — the old behaviour rejected the whole command outright, which both
# refused harmless `git status && git diff` and (because `-exec` needs no separator) still
# auto-allowed `find . -exec rm {} +` under a `find` prefix.
_SEPARATORS = ("&&", "||", ";", "|&", "|", "&", "\n", "\r")

# Programs that run *another* program named in their arguments. A prefix rule on the outer
# program can never vouch for the inner one, so these always fall through to approval.
_ARG_EXECUTORS = {
    "xargs", "env", "nohup", "nice", "stdbuf", "timeout", "watch", "sudo", "doas",
    "ssh", "docker", "podman", "kubectl", "npx", "pnpx", "bunx", "uvx",
}
# Interpreters carrying inline code, e.g. `python -c "..."`, `node -e "..."`.
_INLINE_CODE_FLAGS = {"-c", "-e", "--eval", "--command", "-Command", "-EncodedCommand"}
_INTERPRETERS = {
    "sh", "bash", "zsh", "dash", "ksh", "fish", "powershell", "pwsh", "cmd",
    "python", "python3", "node", "deno", "bun", "ruby", "perl", "php",
}
# Flags that turn a search/list tool into an execution or deletion tool.
_DANGEROUS_FLAGS = {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprintf"}


def _split_commands(command: str) -> list[str]:
    """Split a compound command on its separators. Longest separators first so `&&` isn't
    read as two `&`. Purely textual — quoted separators are not respected, which is
    deliberate: over-splitting only ever produces MORE parts to justify, never fewer."""
    parts = [command]
    for sep in _SEPARATORS:
        parts = [chunk for part in parts for chunk in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def _is_prefix_eligible(argv: list[str]) -> bool:
    """False when a parsed command can never be vouched for by a prefix rule, because it
    runs code the rule never saw: another program named in its arguments, inline source, or
    an execution/deletion flag."""
    if not argv:
        return False
    program = Path(argv[0]).name.lower()
    program = program[:-4] if program.endswith(".exe") else program
    if program in _ARG_EXECUTORS:
        return False
    if program in _INTERPRETERS and any(a in _INLINE_CODE_FLAGS for a in argv[1:]):
        return False
    if any(a.lower() in _DANGEROUS_FLAGS for a in argv[1:]):
        return False
    return True


# Tools granting authority that OUTLIVES this session: instructions the agent will follow
# in later conversations, or a task that runs on its own afterwards (OPE-117). The reviewer
# never clears these — the same floor as deferred-execution files, for the same reason: the
# effect lands after the conversation that authorised it has ended, so the person who bears
# it is not in the room. `create_scheduled_task` states the contract in its own comment
# ("the human granted them by approving this gated call"); this makes that true again.
#
# `update_` is included because it can rewrite the instructions and schedule of a task the
# user already approved while keeping its existing grants; `delete_` because tampering with
# standing configuration the user personally set up is the same class of harm, in reverse.
# Narrowing an update is floored along with broadening it: telling the two apart means
# judging intent, which is exactly what a floor exists to avoid.
PERSISTENT_AUTHORITY_TOOLS = {
    "save_skill",
    "create_scheduled_task",
    "update_scheduled_task",
    "delete_scheduled_task",
    # These mutations persist beyond the current session.
    "remember",
    "memory_update",
    "memory_forget",
}


def protected_paths() -> list[Path]:
    """Files that govern the permission system itself. Nothing the agent does may write
    these — in any mode, through any tool. The escalation this blocks is: approve one
    ordinary-looking command, it quietly appends to the rule file, every future session is
    more permissive. That happens in the DEFAULT interactive mode, so this cannot be a
    property of a sandbox or of any one mode; it is a floor."""
    from .secrets import state_dir

    base = state_dir()
    return [
        base / "config.toml",
        base / "risk_overrides.json",
        base / "workspace_trust.json",
        base / "unattended.json",
        base / "coworker.db",  # session records carry the saved "always allow" grants
        base / "secrets.json",
        base / "inbox_routing.json",
    ]


# Files INSIDE a workspace that execute on a later, innocuous-looking action. An edit here
# is a deferred command: writing `.git/hooks/pre-commit` and then running `git commit` runs
# it. They stay writable, but never WITHOUT a human — no auto-approve path may clear them.
_PROTECTED_IN_PROJECT = (
    ".git/hooks/",
    ".github/workflows/",
    ".gitlab-ci.yml",
    ".vscode/tasks.json",
    ".coworker/",  # workspace policy + skills the agent would otherwise self-grant
)


def _is_protected_in_project(candidate: Path) -> bool:
    posix = candidate.as_posix()
    return any(
        (f"/{marker}" in posix or posix.startswith(marker))
        if marker.endswith("/")
        else posix.endswith("/" + marker)
        for marker in _PROTECTED_IN_PROJECT
    )


def _host_of(url_or_domain: str) -> str:
    """The lowercased host of a URL, or a bare domain as-is. `''` when there's nothing
    usable. Accepts both `https://docs.python.org/x` and `docs.python.org`."""
    s = (url_or_domain or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        return urlsplit(s).hostname or ""
    return urlsplit("//" + s).hostname or s


# The argument that names a write tool's target path, when it's a single top-level field.
# Patch/diff tools carry their paths inside the blob instead — extracted in `write_paths`.
_PATH_ARG: dict[str, str] = {"write_file": "path", "replace_in_file": "path"}
# apply_patch (Codex format) file headers, and unified-diff `+++ b/<path>` headers.
_APPLY_PATCH_FILE = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE
)
_APPLY_PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)
_UNIFIED_DIFF_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*$", re.MULTILINE)


def write_paths(tool_name: str, arguments: dict[str, Any]) -> tuple[list[str], bool]:
    """Every filesystem path a write tool would touch, for root scoping.

    Returns ``(paths, located)``. ``located`` is False when the path can't be determined
    (an unknown write tool, or a patch/diff blob with no parseable file header) — the caller
    must then fail closed rather than skip scoping, so an unscoped write can't slip through
    auto/custom mode.
    """
    arg = _PATH_ARG.get(tool_name)
    if arg is not None:
        value = arguments.get(arg)
        return ([str(value)], True) if value else ([], False)
    if tool_name == "apply_patch":
        blob = str(arguments.get("patch", ""))
        paths = _APPLY_PATCH_FILE.findall(blob) + _APPLY_PATCH_MOVE.findall(blob)
        return ([p.strip() for p in paths], bool(paths))
    if tool_name == "apply_unified_diff":
        blob = str(arguments.get("diff", ""))
        paths = [p for p in _UNIFIED_DIFF_FILE.findall(blob) if p and p != "/dev/null"]
        return (paths, bool(paths))
    # Unknown write tool (e.g. one promoted to write via a user override): we cannot locate
    # its path, so it cannot be auto-scoped.
    return ([], False)

from .risk import (  # re-exported for back-compat (manager.py imports WRITE_TOOLS)
    SHELL_TOOL,
    WRITE_TOOLS,
    RiskClass,
    RiskOverrides,
    classify,
    is_consequential,
)


# The transcript's full Auto-Approve explainer (owner copy 2026-08-24). Persisted as a
# `mode_notice` message the FIRST time a session enters Auto-Approve — server-authored so
# it appears exactly once, in place, and survives reloads (the old client-side banner
# re-announced on every restart).
AUTO_APPROVE_NOTICE = (
    "Auto-approve uses a model to let routine actions through without asking; anything "
    "it isn't sure about still comes to you. It cuts interruptions but still carries "
    "some risk i.e. a command it allows still reaches anything you can. These are model "
    "judgments, and not guarantees."
)

# Human labels for the one-line persisted switch markers ("Ask for approval is on.").
MODE_LABELS = {
    "discuss": "Discuss",
    "plan": "Plan",
    "interactive": "Ask for approval",
    "auto": "Bypass approvals",
    "bypass-approvals": "Bypass approvals",
    "auto-approve": "Auto-approve",
}


class Mode(str, Enum):
    DISCUSS = "discuss"  # read-only conversation: no edits, no planning workflow
    PLAN = (
        "plan"  # read-only + the planning contract (explore → propose_plan → execute)
    )
    INTERACTIVE = "interactive"  # ask for approval (default)
    # Renamed from "auto" (spec §1.5, 2026-08-12): "bypass" names the action — switching a
    # safety system off — and can't be confused with AUTO_APPROVE in a picker. Deliberately
    # NOT "bypass-ALL-approvals": Phase 1's floors (settings files, out-of-root writes,
    # `.git/hooks`) still hold in this mode, so "all" would be a false promise.
    BYPASS_APPROVALS = "bypass-approvals"  # full access (minus the hard floors)
    # Interactive, but an LLM reviewer judges each would-be approval card first: clear
    # allows run without a prompt, everything else still reaches the human. The reviewer
    # can only turn "ask" into "allow", never "blocked" into "allow" (spec §1.2). With no
    # reviewer plugged into the engine this mode behaves exactly like INTERACTIVE.
    AUTO_APPROVE = "auto-approve"
    CUSTOM = "custom"  # interactive + auto-allow the config's `auto_allow` tools

    @classmethod
    def _missing_(cls, value: object) -> "Mode | None":
        # Legacy spelling from configs, saved sessions, and older UIs.
        if value == "auto":
            return cls.BYPASS_APPROVALS
        return None


# Modes whose enforcement is read-only. DISCUSS and PLAN share the same gate; they differ
# only in intent — PLAN additionally drives the agent toward a propose_plan approval.
READ_ONLY_MODES = frozenset({Mode.DISCUSS, Mode.PLAN})


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    needs_user: bool = False  # True → surface should prompt the user for approval
    # True → this ask is reserved for a HUMAN: the Auto-Approve reviewer must not be
    # consulted and cannot clear it. Set on decisions whose entire point is that a person
    # sees them — protected in-project files that execute later (git hooks, CI configs:
    # "never WITHOUT a human — no auto-approve path may clear them") and writes whose path
    # could not be located for scoping (an allow would bypass root scoping unverified).
    human_only: bool = False
    # Set when a task-scoped standing rule allowed the call ("tool → target") so the
    # engine can audit the exact rule and the tool card can say so (§25).
    rule: str = ""


def standing_rule_candidate(
    tool_name: str,
    arguments: dict[str, Any],
    metadata: Any = None,
    overrides: Optional[RiskOverrides] = None,
) -> Optional[str]:
    """The target value iff this call is eligible for a task-scoped standing rule
    (UX-DECISIONS §25): external-risk only (never exec/write-local — shell asks forever),
    the tool must declare a target argument, and the call must actually name a target.
    Returns None otherwise — ineligible calls keep parking approvals as today."""
    from .connectors.tool_defs import target_arg_for

    if classify(tool_name, metadata, overrides) is not RiskClass.EXTERNAL:
        return None
    arg = target_arg_for(tool_name)
    if arg is None:
        return None
    value = str((arguments or {}).get(arg) or "").strip()
    return value or None


@dataclass
class PermissionEngine:
    workspace_root: Path
    mode: Mode = Mode.INTERACTIVE
    allowed_commands: list[str] = field(default_factory=list)
    auto_allow_tools: set[str] = field(default_factory=set)
    session_allow_tools: set[str] = field(default_factory=set)
    session_allow_commands: set[str] = field(default_factory=set)
    # OPE-136 run grants ("Allow for this request"): tool names covered for the
    # REMAINDER OF THE CURRENT RUN only. In-memory by design — the engine clears the
    # set when the run finishes or is interrupted, and a process restart ending the
    # run makes the empty set correct, not a loss. Minted only for EXTERNAL-risk
    # tools (server-validated in manager._grant_offered); unlike the session grant
    # this one exists FOR connectors and MCP — the loop/retry/pagination shapes.
    run_allow_tools: set[str] = field(default_factory=set)
    # Egress domains that auto-run without a prompt: `allowed_domains` from user config, plus
    # `session_allow_domains` minted by "Always allow this domain". Matched by exact host or
    # subdomain suffix (see `_domain_allowed`).
    allowed_domains: list[str] = field(default_factory=list)
    session_allow_domains: set[str] = field(default_factory=set)
    # Session-wide read-only grant (owner ask 2026-08-11): auto-allow shell commands the
    # conservative classifier (coworker/readonly.py) accepts. User-elected per session.
    session_readonly: bool = False
    # Task-scoped standing rules (§25): {tool: {allowed targets}}, seeded from the owning
    # ScheduledTask's target-shaped entries. Kept by reference and re-read every check, so a
    # rule minted mid-run ("Allow every time") applies to the run's next call too.
    task_rules: dict[str, set[str]] = field(default_factory=dict)
    # User-local risk override resolver (Phase 2). None → use the base classification.
    risk_overrides: Optional[RiskOverrides] = None
    # OPE-136 durable trust: tool name → has the user minted a standing "don't ask" rule?
    # (RiskOverrideStore.trusted). Waives only the card, only outside AUTO_APPROVE —
    # never the class, the mode gates, or the audit trail. None → no trust rules.
    trust_overrides: Optional[Callable[[str], bool]] = None
    # The write half (RiskOverrideStore.set_trust) — how ApprovalOutcome.ALWAYS_TRUST
    # lands on disk. Kept as an injected callable so this module never imports the store.
    grant_trust: Optional[Callable[[str], None]] = None
    # Shared, possibly-mutable list of roots (RootDir-like / dicts). When omitted, the single
    # `workspace_root` is the sole writable root (back-compat). Kept by reference and re-read on
    # every check, so runtime add/remove of folders takes effect without rebuilding the engine.
    roots: Optional[list] = None

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.auto_allow_tools = set(self.auto_allow_tools)
        if self.roots is None:
            self.roots = [{"path": self.workspace_root, "writable": True}]

    def _resolved_roots(self) -> list[tuple[Path, bool]]:
        out: list[tuple[Path, bool]] = []
        for r in self.roots or []:
            if isinstance(r, dict):
                p, w = r["path"], bool(r.get("writable", False))
            elif isinstance(r, (str, Path)):
                p, w = r, True
            else:  # duck-typed RootDir-like
                p, w = getattr(r, "path"), bool(getattr(r, "writable", False))
            out.append((Path(p).expanduser().resolve(), w))
        return out

    def evaluate(
        self, tool_name: str, arguments: dict[str, Any], metadata: Any = None
    ) -> Decision:
        arguments = arguments or {}
        is_connector = getattr(metadata, "category", "") == "connector"
        risk = classify(tool_name, metadata, self.risk_overrides)
        is_write = risk is RiskClass.WRITE_LOCAL
        is_shell = risk is RiskClass.EXEC
        is_egress = risk is RiskClass.EGRESS
        # Persistent-authority tools are consequential BY NAME: their risk class can
        # read as READ (no base-table/catalog entry), but granting standing authority is
        # a side effect — read-only modes must DENY them, not offer a grant card. The
        # OPE-117 comment below always promised "read-only modes still hard-deny above
        # this"; the OPE-136 gate-order pin caught that the class-based check alone
        # didn't deliver it (save_skill in Discuss reached the human-only card).
        consequential = (
            is_consequential(risk) or tool_name in PERSISTENT_AUTHORITY_TOOLS
        )

        # SELF-PROTECTION FLOOR — runs before mode, allowlists and every auto-approve path,
        # because the escalation it blocks happens in the DEFAULT mode. No verdict below can
        # reach these files, and no human click in the flow can grant it either: loosening
        # requires editing the files out-of-band.
        if is_write or is_shell:
            hit = self._touches_protected(tool_name, arguments, is_shell)
            if hit is not None:
                return Decision(
                    False,
                    f"refusing to modify OpenWorker's own settings: {hit}",
                    needs_user=False,
                )

        # Discuss / plan modes: read-only.
        if self.mode in READ_ONLY_MODES and consequential:
            return Decision(
                False, f"{self.mode.value} mode is read-only", needs_user=False
            )

        # Path scoping for writes (all modes): every path the write touches must land in a
        # writable root. A write whose path can't be located is not scoped-able, so it fails
        # closed to approval rather than slipping through auto/custom unscoped.
        needs_human_for_protected = False
        if is_write:
            paths, located = write_paths(tool_name, arguments)
            if not located:
                return Decision(
                    False,
                    "cannot determine the write path to scope",
                    needs_user=True,
                    human_only=True,  # an unscopable write must reach a person, not the reviewer
                )
            for path in paths:
                if not self._under_writable_root(path):
                    return Decision(
                        False, f"path is not in a writable directory: {path}"
                    )
                # In-project files that run on a later action (git hooks, CI configs) may be
                # edited, but never by an auto-approve path — a human must see it.
                if _is_protected_in_project(self._candidate(path)):
                    needs_human_for_protected = True

        # Authority outliving the session reaches a person, over the reviewer and over
        # every allowlist below (OPE-117). Placed ahead of the non-consequential return on
        # purpose: these tools are consequential today, but a metadata slip must not be
        # able to switch the floor off. Read-only modes still hard-deny above this.
        if tool_name in PERSISTENT_AUTHORITY_TOOLS:
            return Decision(
                False,
                "this outlives the session — approval required",
                needs_user=True,
                human_only=True,
            )

        # Non-consequential tools always run.
        if not consequential:
            return Decision(True, "low risk")

        # A protected in-project target (git hooks, CI config) skips every auto-approve path
        # below — including auto mode and the session/config allowlists — and asks.
        if needs_human_for_protected:
            return Decision(
                False,
                "this file runs automatically later — approval required",
                needs_user=True,
                human_only=True,  # deferred-execution files: a human sees every one (§ floor)
            )

        # Full access.
        if self.mode is Mode.BYPASS_APPROVALS:
            return Decision(True, "full access")

        # interactive / custom / auto-approve: allowlists.
        #
        # In AUTO_APPROVE, session grants ("always allow this …" clicks) deliberately do
        # NOT auto-allow (spec §1.5): out-of-band standing policy — the user-settings
        # allowlists checked via `_command_allowed` / config `allowed_domains` — may skip
        # the judge, but an in-flow click may not. A domain grant matches on host only and
        # is blind to the path and query string (where exfiltration rides), and command
        # grants replay as exact text; both are precisely what the reviewer should see.
        # The skipped checks return `needs_user` instead, which routes to the reviewer.
        honor_session_grants = self.mode is not Mode.AUTO_APPROVE
        if is_shell:
            command = str(arguments.get("command", ""))
            if self._command_allowed(command):
                return Decision(True, "command on allowlist")
            if (
                honor_session_grants
                and command
                and command in self.session_allow_commands
            ):
                return Decision(True, "command allowed for session")
            # Also a session grant, so §1.5 applies: in Auto-Approve the reviewer judges
            # these rather than the classifier waving them through.
            if honor_session_grants and self.session_readonly and command:
                from .readonly import is_readonly_command, read_targets

                # The classifier vets what a command DOES; the roots vet what it READS
                # (OPE-130). Without the second half, a grant the user reads as "stop
                # asking about my project files" also covers ~/.aws/credentials, another
                # repo's history, and OpenWorker's own secrets file — none of which the
                # self-protection floor catches, since that guards writes, not reads.
                if is_readonly_command(command) and all(
                    self._under_root(t) for t in read_targets(command)
                ):
                    return Decision(True, "read-only command (session grant)")
        if is_egress:
            url = str(arguments.get("url", ""))
            if self._domain_allowed(url, include_session=honor_session_grants):
                return Decision(True, "domain on allowlist")
        if (
            honor_session_grants
            and tool_name in self.session_allow_tools
            and not is_connector
        ):
            return Decision(True, "tool allowed for session")
        # Run grant (OPE-136 "Allow for this request"): same checkpoint, shorter life —
        # and no connector exclusion, because EXTERNAL is exactly who it exists for.
        # §1.5 still applies: an in-flow click never skips the Auto-Approve judge.
        if honor_session_grants and tool_name in self.run_allow_tools:
            return Decision(True, "tool allowed for this request")

        # OPE-136: MCP trust waives only the card, in the one mode where the card is the
        # deciding voice. Two sources, one branch: a per-tool trust RULE the user minted
        # from the card ("Always allow this tool" → risk_overrides.json), or the legacy
        # server-level `requires_approval: false` (which no longer reclassifies — the MCP
        # floor in risk.classify keeps these tools EXTERNAL). Everything above still
        # applied: read-only modes denied before this line, the persistent-authority and
        # protected-file floors returned before it, and Bypass already returned.
        # Deliberately NOT honored in AUTO_APPROVE: v1 keeps §1.5 conservative — the
        # reviewer judges trusted MCP calls (falling through to needs_user routes
        # there); only hand-authored config allowlists skip the judge.
        if (
            getattr(metadata, "category", "") == "mcp"
            and self.mode is not Mode.AUTO_APPROVE
        ):
            if self.trust_overrides is not None and self.trust_overrides(tool_name):
                return Decision(True, "trusted MCP tool (user trust rule)")
            if not bool(getattr(metadata, "requires_approval", True)):
                return Decision(True, "trusted MCP tool (server marked don't-ask)")

        # Task-scoped standing rules (§25): tool + exact target, owned by the automation.
        # Deliberately NOT subject to the connector exclusion above — the exact-target
        # binding is what makes auto-allowing a connector tool safe. Never for exec risk
        # (candidate extraction is external-risk-only), and additive on top of the mode:
        # read-only modes already returned before this point.
        if tool_name in self.task_rules:
            target = standing_rule_candidate(
                tool_name, arguments, metadata, self.risk_overrides
            )
            if target and target in self.task_rules[tool_name]:
                rule = f"{tool_name} → {target}"
                return Decision(True, f"allowed by standing rule: {rule}", rule=rule)

        # Custom mode auto-approves the configured tools.
        if self.mode is Mode.CUSTOM and tool_name in self.auto_allow_tools:
            return Decision(True, "auto-allowed by config")

        # Otherwise: ask the user.
        return Decision(False, "requires approval", needs_user=True)

    # -- session memory ---------------------------------------------------------
    def allow_tool_for_session(self, tool_name: str) -> None:
        self.session_allow_tools.add(tool_name)

    def allow_tool_for_run(self, tool_name: str) -> None:
        self.run_allow_tools.add(tool_name)

    def clear_run_allowances(self) -> None:
        """The run boundary IS the grant's expiry: the engine calls this when a run
        finishes or is interrupted, so "Allow for this request" never outlives the
        answer the user was watching."""
        self.run_allow_tools.clear()

    def grant_trust_for_tool(self, tool_name: str) -> None:
        """OPE-136 durable trust: persist a per-tool "don't ask" rule (survives sessions).
        Falls back to the session grant when no store is wired (ephemeral engines in
        tests) — the card's promise degrades to session scope rather than to nothing."""
        if self.grant_trust is not None:
            self.grant_trust(tool_name)
        else:
            self.session_allow_tools.add(tool_name)

    def allow_command_for_session(self, command: str) -> None:
        if command:
            self.session_allow_commands.add(command)

    def allow_readonly_for_session(self) -> None:
        self.session_readonly = True

    def allow_domain_for_session(self, url_or_domain: str) -> None:
        """Remember an egress destination for this session ("Always allow this domain").

        A leading `www.` is stripped at minting (§1.9): `bbc.com` and `www.bbc.com` are one
        site in every user's mental model, and the suffix match in `_domain_allowed` already
        treats `www.bbc.com` as a subdomain of `bbc.com`. Pure spelling only — never eTLD+1
        or any broader normalisation, which would silently widen the grant."""
        host = _host_of(url_or_domain)
        if host.startswith("www."):
            host = host[4:]
        if host:
            self.session_allow_domains.add(host)

    # -- helpers ----------------------------------------------------------------
    def _candidate(self, path: str) -> Path:
        # Relative paths resolve against the primary (workspace_root); absolute/`~` taken as-is.
        p = Path(path).expanduser()
        return p.resolve() if p.is_absolute() else (self.workspace_root / p).resolve()

    def _under_root(self, path: str) -> bool:
        candidate = self._candidate(path)
        for rp, _ in self._resolved_roots():
            try:
                candidate.relative_to(rp)
                return True
            except ValueError:
                continue
        return False

    def _under_writable_root(self, path: str) -> bool:
        candidate = self._candidate(path)
        for rp, writable in self._resolved_roots():
            if not writable:
                continue
            try:
                candidate.relative_to(rp)
                return True
            except ValueError:
                continue
        return False

    def _touches_protected(
        self, tool_name: str, arguments: dict[str, Any], is_shell: bool
    ) -> Optional[str]:
        """The protected settings path this call would modify, or None.

        For writes we resolve the real target. For shell we can only inspect the command
        text — parser depth, so it stops accidents and casual attempts, not a determined
        adversary (that needs the OS sandbox). Cheap and worth having regardless.

        Shell matching is on the FULL path only, never a bare filename: matching
        `secrets.json` anywhere in a command would refuse unrelated work that merely
        mentions the name. A command naming the real settings path is refused whether it
        reads or writes — we cannot tell which from text, and the conservative direction is
        the right one for these files.
        """
        targets = [str(p) for p in protected_paths()]
        if is_shell:
            command = str(arguments.get("command", ""))
            if not command:
                return None
            lowered = command.replace("\\", "/").lower()
            for target in targets:
                if target.replace("\\", "/").lower() in lowered:
                    return target
            return None
        paths, located = write_paths(tool_name, arguments)
        if not located:
            return None  # unlocatable writes are already failed closed by the caller
        resolved = {str(self._candidate(p)) for p in paths}
        for target in targets:
            if str(Path(target).resolve()) in resolved:
                return target
        return None

    def _domain_allowed(self, url: str, *, include_session: bool = True) -> bool:
        """True when the URL's host is an allowed egress destination — an exact match or a
        subdomain of an allowed domain (so `docs.python.org` matches `python.org`, but
        `evil-python.org` never matches `python.org`).

        `include_session=False` (AUTO_APPROVE mode) checks the user-settings list only:
        mid-session "always allow this domain" clicks don't bypass the reviewer there."""
        host = _host_of(url)
        if not host:
            return False
        allowed = {d for d in (_host_of(x) for x in self.allowed_domains) if d}
        if include_session:
            allowed |= self.session_allow_domains
        for dom in allowed:
            if host == dom or host.endswith("." + dom):
                return True
        return False

    def _command_allowed(self, command: str) -> bool:
        """True only when EVERY part of a (possibly compound) command is independently
        covered by an allowlist entry.

        An allowlist entry auto-runs without approval, and a prefix rule can only vouch for
        the words it matched — everything after is unexamined. So this does two jobs:
        guarantee the unexamined tail can only be arguments, then match the beginning.

        - Constructs whose contents we can't evaluate (substitution, redirection, variable
          expansion) disqualify the whole command.
        - Compound commands are split and each part checked on its own, so
          `git status && git diff` runs when both are allowed, while
          `git status && rm -rf ~` does not.
        - Parts that run code named in their arguments (`xargs`, `sh -c`, `find -exec`,
          `-delete`) are never prefix-eligible: a `find` rule must not auto-run
          `find . -exec rm {} +`.
        - Matching is on parsed words, not text, so `git status` covers `git status -s` but
          never `git statusfoo` or a bare `git`.
        """
        if not command.strip():
            return False
        if any(tok in command for tok in _OPAQUE_CONSTRUCTS):
            return False
        parts = _split_commands(command)
        if not parts:
            return False
        prefixes: list[list[str]] = []
        for allowed in self.allowed_commands:
            try:
                prefix = shlex.split(allowed)
            except ValueError:
                continue
            if prefix:
                prefixes.append(prefix)
        if not prefixes:
            return False
        for part in parts:
            try:
                argv = shlex.split(part)
            except ValueError:
                return False  # unbalanced quotes etc. — treat as not-allowlisted
            if not argv or not _is_prefix_eligible(argv):
                return False
            if not any(argv[: len(p)] == p for p in prefixes):
                return False
        return True
