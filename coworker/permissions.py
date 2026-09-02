"""Permission engine — decides allow / deny / ask-user for each proposed tool call.

Modes: Plan (read-only) · Interactive (auto reads, ask on writes/commands) · Auto
(allow, still path-scoped). Refined by argument patterns (path-under-root, command
prefixes) and a session allowlist. The engine only *decides*; the turn engine routes
`needs_user` decisions to a surface for approval and records the outcome.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# Shell metacharacters that turn one "allowlisted" command into several. Any of these in a
# command disqualifies it from allowlist auto-run — approval is required instead. Covers
# chaining (`;` `&` `&&` `||`), pipes (`|`), redirection (`>` `<`), command substitution
# (`` ` `` `$(`), process substitution / grouping (`(`), and newlines.
_SHELL_OPERATORS = (";", "&", "|", ">", "<", "`", "$(", "(", "\n", "\r")


def _has_shell_operators(command: str) -> bool:
    return any(op in command for op in _SHELL_OPERATORS)


def _command_segments(command: str) -> Optional[list[list[str]]]:
    """Split a shell command into its argv segments, respecting quotes.

    `cd repo && git push` is two segments. Returns None when the text cannot be parsed
    (unbalanced quotes) — a caller proving something is SAFE must treat that as failure,
    and a caller proving something is GATED must fall back to a raw scan.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and all(ch in ";&|()<>\n\r" for ch in token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


# Commands whose string argument is itself a command to run: `bash -lc "git push"` and
# `ssh host "git push"` must trip the same gate the bare command does. Kept narrow on
# purpose — scanning every quoted argument gated `echo 'git push'`, which is just text.
_SHELL_RUNNERS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "ssh"})

# Commands that run another command with the same effect: the gate has to see through
# them to the real one, or `sudo git push` walks straight past `git push`.
_WRAPPERS = frozenset(
    {"sudo", "doas", "env", "nohup", "nice", "ionice", "time", "stdbuf", "timeout"}
)


def _unwrap(argv: list[str]) -> list[str]:
    """Strip leading wrapper commands, their flags, and any `VAR=value` assignments."""
    out = list(argv)
    while out and Path(out[0]).name in _WRAPPERS:
        out = out[1:]
        while out and (out[0].startswith("-") or "=" in out[0].split(" ")[0]):
            out = out[1:]
    return out


def _argv_matches(argv: list[str], want: list[str]) -> bool:
    """Whether one argv triggers a gate pattern.

    The pattern's first token must be the command itself (by basename, so `/usr/bin/git`
    counts), and its remaining tokens must appear in order among the arguments. That is
    what makes `git push` catch `git -C /repo push --force` — matching only a literal
    prefix would let an interposed flag walk straight through the gate.
    """
    if not argv or not want:
        return False
    if Path(argv[0]).name != want[0]:
        return False
    rest = argv[1:]
    for token in want[1:]:
        if token not in rest:
            return False
        rest = rest[rest.index(token) + 1 :]
    return True

from .risk import (  # re-exported for back-compat (manager.py imports WRITE_TOOLS)
    SHELL_TOOL,
    WRITE_TOOLS,
    RiskClass,
    RiskOverrides,
    classify,
    is_consequential,
)


class Mode(str, Enum):
    DISCUSS = "discuss"  # read-only conversation: no edits, no planning workflow
    PLAN = (
        "plan"  # read-only + the planning contract (explore → propose_plan → execute)
    )
    INTERACTIVE = "interactive"  # ask for approval (default)
    AUTO = "auto"  # full access
    CUSTOM = "custom"  # interactive + auto-allow the config's `auto_allow` tools


# Modes whose enforcement is read-only. DISCUSS and PLAN share the same gate; they differ
# only in intent — PLAN additionally drives the agent toward a propose_plan approval.
READ_ONLY_MODES = frozenset({Mode.DISCUSS, Mode.PLAN})


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    needs_user: bool = False  # True → surface should prompt the user for approval
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
    # Commands that ALWAYS require the user, in every mode and past every allowlist.
    # Matched per segment against the whole command; see `_gated`.
    gated_commands: list[str] = field(default_factory=list)
    # Task-scoped standing rules (§25): {tool: {allowed targets}}, seeded from the owning
    # ScheduledTask's target-shaped entries. Kept by reference and re-read every check, so a
    # rule minted mid-run ("Allow every time") applies to the run's next call too.
    task_rules: dict[str, set[str]] = field(default_factory=dict)
    # User-local risk override resolver (Phase 2). None → use the base classification.
    risk_overrides: Optional[RiskOverrides] = None
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
        consequential = is_consequential(risk)

        # Discuss / plan modes: read-only.
        if self.mode in READ_ONLY_MODES and consequential:
            return Decision(
                False, f"{self.mode.value} mode is read-only", needs_user=False
            )

        # Path scoping for writes that name a path (all modes): must land in a writable root.
        if is_write:
            path = arguments.get("path")
            if path is not None and not self._under_writable_root(path):
                return Decision(False, f"path is not in a writable directory: {path}")

        # Non-consequential tools always run.
        if not consequential:
            return Decision(True, "low risk")

        # Gated commands always ask. Deliberately ahead of AUTO mode, the command
        # allowlist, the session allowlist and custom-mode auto-allow: these are the
        # irreversible acts (publishing, deploying, force-resetting), everything else a
        # run does is recoverable from git, and no permission accumulated during a run
        # should be able to widen quietly into them. Default is empty — a machine only
        # gets this by asking for it in config.
        if is_shell:
            gate = self._gated(str(arguments.get("command", "")))
            if gate:
                return Decision(
                    False, f"{gate!r} always needs approval", needs_user=True
                )

        # Full access.
        if self.mode is Mode.AUTO:
            return Decision(True, "full access")

        # interactive / custom: allowlists.
        if is_shell:
            command = str(arguments.get("command", ""))
            if self._command_allowed(command):
                return Decision(True, "command on allowlist")
            if command and command in self.session_allow_commands:
                return Decision(True, "command allowed for session")
        if tool_name in self.session_allow_tools and not is_connector:
            return Decision(True, "tool allowed for session")

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

    def allow_command_for_session(self, command: str) -> None:
        if command:
            self.session_allow_commands.add(command)

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

    def _gated(self, command: str) -> str:
        """The gate pattern this command trips, or "" if none.

        Every segment is checked, so chaining (`cd x && git push`) cannot slip past, and
        so is every quoted argument that parses as a command of its own, which is what
        catches `bash -lc "git push"`. Unparseable text falls back to a raw substring
        scan: a command we cannot read is the last thing to auto-approve.
        """
        if not self.gated_commands or not command.strip():
            return ""
        patterns: list[list[str]] = []
        for pattern in self.gated_commands:
            try:
                tokens = shlex.split(pattern)
            except ValueError:
                continue
            if tokens:
                patterns.append(tokens)
        if not patterns:
            return ""

        segments = _command_segments(command)
        if segments is None:
            for tokens in patterns:
                if all(token in command for token in tokens):
                    return " ".join(tokens)
            return ""

        for tokens in patterns:
            for raw in segments:
                argv = _unwrap(raw)
                if not argv:
                    continue
                if _argv_matches(argv, tokens):
                    return " ".join(tokens)
                # `bash -lc "git push"` carries the real command as one string token.
                if Path(argv[0]).name not in _SHELL_RUNNERS:
                    continue
                for arg in argv[1:]:
                    if " " not in arg:
                        continue
                    nested = _command_segments(arg)
                    if nested and any(
                        _argv_matches(_unwrap(a), tokens) for a in nested
                    ):
                        return " ".join(tokens)
        return ""

    def _command_allowed(self, command: str) -> bool:
        # An allowlist entry auto-runs a command WITHOUT approval, so prefix matching is
        # unsafe: `git status` would auto-approve `git status && rm -rf ~`. Reject anything
        # carrying shell operators (chaining/redirection/substitution) up front, then match
        # the parsed argv against each entry — the entry's own tokens must be an exact
        # prefix of the command's tokens (so `git status` matches `git status -s` but never
        # `git statusfoo` or a bare `git`).
        if _has_shell_operators(command):
            return False
        try:
            argv = shlex.split(command)
        except ValueError:
            return False  # unbalanced quotes etc. — treat as not-allowlisted
        if not argv:
            return False
        for allowed in self.allowed_commands:
            try:
                prefix = shlex.split(allowed)
            except ValueError:
                continue
            if prefix and argv[: len(prefix)] == prefix:
                return True
        return False
