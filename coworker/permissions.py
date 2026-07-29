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
from pathlib import Path, PurePath
from typing import Any, Optional

# Shell metacharacters that turn one "allowlisted" command into several. Any of these in a
# command disqualifies it from allowlist auto-run — approval is required instead. Covers
# chaining (`;` `&` `&&` `||`), pipes (`|`), redirection (`>` `<`), command substitution
# (`` ` `` `$(`), process substitution / grouping (`(`), and newlines.
_SHELL_OPERATORS = (";", "&", "|", ">", "<", "`", "$(", "(", "\n", "\r")


def _has_shell_operators(command: str) -> bool:
    return any(op in command for op in _SHELL_OPERATORS)


from .risk import (  # re-exported for back-compat (manager.py imports WRITE_TOOLS)
    SHELL_TOOL,
    WRITE_TOOLS,
    RiskClass,
    RiskOverrides,
    classify,
    is_consequential,
)



# An allowlist entry auto-runs a command with NO prompt, matched as an argv prefix. That
# is only sound while extra arguments cannot add authority. For the programs below they
# can: the program's own arguments choose what code executes, so an entry that stops
# before those arguments hands over unbounded authority.
#
# Two shapes, both keyed by program so the check never touches unrelated tools:
#
# `_RUNNER_ANY_ARG` - interpreters, package managers and exec wrappers where essentially
# ANY argument names code to run that is not already in the workspace: `python3 -c '...'`
# is a program the user never saw, `npm install pkg` fetches and runs a package's install
# scripts, `env CMD` / `xargs CMD` / `ssh host CMD` take the command as an argument.
# A bare-program entry auto-runs only the zero-argument invocation; to auto-run a specific
# operation, name it in the entry (`npm test`, `python3 -m pytest`) and arguments may
# still be appended to that.
#
# Deliberately NOT here: project-local task runners (`pytest`, `tox`, `nox`, `make`,
# `just`) whose default target is the workspace's own code, which the agent can already
# edit. Their escape is a path pointing outside the workspace (`pytest /tmp/evil_test.py`,
# `make -f /tmp/evil.mk`) - the same axis as `cat /etc/passwd`, and one the command
# allowlist cannot express. That belongs to path scoping, not to program matching.
#
# Names are matched lowercased and without a Windows executable suffix (see
# `_program_name`), so `python.exe` and `npm.cmd` are the same programs as `python`
# and `npm`.
_RUNNER_ANY_ARG = frozenset(
    {
        # language interpreters. `awk`/`sed` are here rather than in the flag table below
        # because their program text is a POSITIONAL argument (`sed 'e ls'`, `awk
        # 'BEGIN{system(...)}'`), so no flag is what carries the escape.
        "python", "python3", "node", "deno", "bun", "ruby", "perl", "php", "rscript",
        "awk", "gawk", "sed", "osascript",
        # package managers: install/exec fetch remote code and run its build hooks
        "npm", "npx", "pnpm", "yarn", "pip", "pip3", "uv", "uvx", "pipx",
        "cargo", "go", "gem", "bundle", "gradle", "mvn",
        # shells and exec wrappers (their argument IS the command to run)
        "sh", "bash", "zsh", "dash", "fish", "env", "sudo", "doas",
        "nohup", "timeout", "watch", "nice", "xargs",
        # remote / container execution
        "ssh", "scp", "docker", "podman", "kubectl",
    }
)

# `_RUNNER_ESCAPES` - otherwise-inert tools with a SPECIFIC flag that delegates execution.
# Only those flags are refused, so ordinary use still auto-runs (`find . -name '*.py'`
# stays allowed under a bare `find` entry; only the `-exec` family is held back).
# An entry that names the flag itself opts back in (e.g. `find . -exec`).
_RUNNER_ESCAPES: dict[str, frozenset[str]] = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir"}),
    "git": frozenset({"-c", "--exec-path", "--upload-pack", "--receive-pack"}),
    "tar": frozenset({"--checkpoint-action", "--to-command", "--use-compress-program"}),
    "rsync": frozenset({"-e", "--rsh", "--rsync-path"}),
    "vim": frozenset({"-c", "--cmd"}),
    "vi": frozenset({"-c", "--cmd"}),
    "nvim": frozenset({"-c", "--cmd"}),
}

# Windows spells the same programs `python.exe`, `npm.cmd`, `node.exe`.
_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1")


def _program_name(argv0: str) -> str:
    """The program a command invokes, normalised for lookup: directories dropped, case
    folded, Windows executable suffix removed. Backslashes are treated as separators
    regardless of host OS so a Windows-style path is not read as one long filename."""
    name = PurePath(argv0.replace("\\", "/")).name.lower()
    for suffix in _EXE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _flag_name(token: str) -> str:
    """`--opt=value` and `-e=x` carry the flag in the part before `=`."""
    return token.split("=", 1)[0]


def _delegates_execution(argv: list[str], prefix: list[str]) -> bool:
    """True when `argv` extends an allowlist `prefix` in a way that lets the program pick
    what code runs. Deliberately conservative: an unclear case returns True, which only
    means "ask the user" - the safe direction for an approval gate.
    """
    program = _program_name(argv[0])
    extra = argv[len(prefix) :]
    if not extra:
        return False  # exactly the allowlisted invocation, nothing added

    # Any argument can name code, and the entry pinned nothing but the program itself.
    if program in _RUNNER_ANY_ARG and len(prefix) == 1:
        return True

    escapes = _RUNNER_ESCAPES.get(program)
    if escapes:
        named = {_flag_name(t) for t in prefix}
        for token in extra:
            flag = _flag_name(token)
            if flag in escapes and flag not in named:
                return True
    return False


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
                # A prefix match is not enough on its own: for interpreters, package
                # managers and exec-delegating tools, the arguments AFTER the prefix are
                # what choose the code to run (see _delegates_execution).
                if _delegates_execution(argv, prefix):
                    continue
                return True
        return False
