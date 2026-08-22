"""Permission engine — decides allow / deny / ask-user for each proposed tool call.

Modes: Plan (read-only) · Interactive (auto reads, ask on writes/commands) · Auto
(allow, still path-scoped). Refined by argument patterns (path-under-root, command
prefixes) and a session allowlist. The engine only *decides*; the turn engine routes
`needs_user` decisions to a surface for approval and records the outcome.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional




_UNRESOLVED_VAR_PATTERN = re.compile(
    r"(?:\$[a-zA-Z_][a-zA-Z0-9_]*|\$\{[^}]+\}|\%[a-zA-Z_][a-zA-Z0-9_]*\%|\$env:[a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)



def expand_and_check_shell_vars(cmd_str: str) -> tuple[str, bool]:
    """Single-pass, single-quote aware environment variable expansion.
    Returns (expanded_command, has_unresolved_vars).
    """
    if not cmd_str:
        return "", False

    has_unresolved = False

    def replacer(match):
        nonlocal has_unresolved
        raw = match.group(0)

        # Single-quoted strings: bash single quotes prevent variable expansion
        if raw.startswith("'") and raw.endswith("'"):
            return raw

        var_token = raw.strip('"')

        if var_token.lower().startswith("$env:"):
            var_name = var_token[5:]
        elif var_token.startswith("${") and var_token.endswith("}"):
            var_name = var_token[2:-1]
        elif var_token.startswith("%") and var_token.endswith("%"):
            var_name = var_token[1:-1]
        elif var_token.startswith("$"):
            var_name = var_token[1:]
        else:
            return raw

        if var_name in os.environ:
            val = os.environ[var_name]
            return f'"{val}"' if raw.startswith('"') else val
        else:
            has_unresolved = True
            return raw

    # Single-pass regex matching single quotes or double-quoted/unquoted variable tokens
    pattern = re.compile(
        r"\'[^\']*\'|\"?\$env:[a-zA-Z0-9_]+\"?|\"?\$\{[a-zA-Z0-9_]+\}\"?|\"?\$[a-zA-Z0-9_]+\"?|\"?%[a-zA-Z0-9_]+%\"?"
    )

    expanded_cmd = pattern.sub(replacer, cmd_str)

    if _UNRESOLVED_VAR_PATTERN.search(expanded_cmd):
        has_unresolved = True

    return expanded_cmd, has_unresolved


def expand_shell_vars(cmd_str: str) -> str:
    """Expand shell environment variables (single-pass, quote-aware)."""
    expanded, _ = expand_and_check_shell_vars(cmd_str)
    return expanded


def has_unresolved_vars(path_str: str) -> bool:
    """Return True if path contains unresolvable variable syntax ($VAR, ${VAR}, %VAR%, $env:VAR)."""
    _, unresolved = expand_and_check_shell_vars(str(path_str))
    if unresolved:
        return True
    return bool(_UNRESOLVED_VAR_PATTERN.search(str(path_str)))


# Shell metacharacters that turn one "allowlisted" command into several. Any of these in a
# command disqualifies it from allowlist auto-run — approval is required instead. Covers

# chaining (`;` `&` `&&` `||`), pipes (`|`), redirection (`>` `<`), command substitution
# (`` ` `` `$(`), process substitution / grouping (`(`), and newlines.
_SHELL_OPERATORS = (";", "&", "|", ">", "<", "`", "$(", "(", "\n", "\r")


_WRITE_COMMANDS = {
    "copy",
    "cp",
    "move",
    "mv",
    "xcopy",
    "robocopy",
    "rsync",
    "install",
    "copy-item",
    "move-item",
    "out-file",
    "set-content",
    "add-content",
    "new-item",
    "tee",
}

_DEST_FLAGS = {
    "-destination",
    "-outfile",
    "-filepath",
    "-target",
    "--output",
    "-o",
    "-c",
    "-d",
}


_INLINE_WRITE_PATTERNS = (
    "'w'",
    '"w"',
    "'w+'",
    '"w+"',
    "'wb'",
    '"wb"',
    "'a'",
    '"a"',
    "'a+'",
    '"a+"',
    "'ab'",
    '"ab"',
    "'r+'",
    '"r+"',
    "open(",
    "write_text",
    "write_bytes",
    "write(",
    "writeFileSync",
    "Set-Content",
    "Out-File",
    "Add-Content",
    "New-Item",
)

_PATH_REGEX = re.compile(
    r'(?:[a-zA-Z]:[/\\]|/|~|\.\.[/\\])[^\s"\'\`\;\|\&\<\>\)\,\{\}\[\]]+'
)


def extract_base64_paths(command: str) -> list[str]:
    """Decode base64 payload strings in command lines to extract hidden path targets."""
    if not command:
        return []
    paths: list[str] = []
    b64_candidates = re.findall(r"[A-Za-z0-9+/]{8,}={0,2}", command)
    for token in b64_candidates:
        try:
            decoded = base64.b64decode(token).decode("utf-8", errors="ignore")
            for match in _PATH_REGEX.findall(decoded):
                clean = match.strip("\"'")
                if clean and clean not in paths:
                    paths.append(clean)
        except Exception:
            continue
    return paths


def extract_shell_write_targets(command: str) -> list[str]:
    """Extract destination file or directory path targets from shell commands (e.g. copy, move, redirection, inline scripts)."""
    if not command or not command.strip():
        return []

    command = expand_shell_vars(command)
    targets: list[str] = []

    # 1. Redirections > and >>
    redir_matches = re.findall(
        r'(?:>>|>)\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s;&|]+))', command
    )
    for m in redir_matches:
        path = m[0] or m[1] or m[2]
        if path:
            targets.append(path)

    # 2. Tokenize command preserving Windows backslashes
    def _split_cmd(cmd_str: str) -> list[str]:
        if "\\" in cmd_str or ":" in cmd_str:
            try:
                return shlex.split(cmd_str, posix=False)
            except ValueError:
                pass
        try:
            return shlex.split(cmd_str, posix=(sys.platform != "win32"))
        except ValueError:
            return cmd_str.split()

    tokens = _split_cmd(command)
    if tokens:
        # 3. Destination flags (-Destination, -OutFile, of=, etc.)
        for i, tok in enumerate(tokens[:-1]):
            tok_lower = tok.lower()
            if tok_lower in _DEST_FLAGS:
                targets.append(tokens[i + 1])
            elif tok_lower.startswith("of=") and len(tok) > 3:
                targets.append(tok[3:])

        # 4. Copy/move utilities
        subcommands = re.split(r";|&&|\|\||\|", command)
        for subcmd in subcommands:
            sub_tokens = _split_cmd(subcmd.strip())
            if not sub_tokens:
                continue

            cmd_name = Path(sub_tokens[0]).name.lower()
            if "." in cmd_name:
                cmd_name = cmd_name.split(".")[0]

            if cmd_name in _WRITE_COMMANDS:
                pos_args = []
                for t in sub_tokens[1:]:
                    if t.startswith("-") or (
                        t.startswith("/") and len(t) <= 3 and not t.startswith("//")
                    ):
                        continue
                    pos_args.append(t)
                if pos_args:
                    targets.append(pos_args[-1])

    # 5. Regex search across full command for paths inside inline scripts or parameters
    has_inline_write = any(pattern in command for pattern in _INLINE_WRITE_PATTERNS)
    if has_inline_write:
        for match in _PATH_REGEX.findall(command):
            targets.append(match)

    # 6. Check base64 payload strings
    for b64_path in extract_base64_paths(command):
        targets.append(b64_path)

    cleaned: list[str] = []
    for t in targets:
        t_clean = t.strip("\"'")
        if t_clean and t_clean not in cleaned:
            cleaned.append(t_clean)
    return cleaned


def extract_shell_all_paths(command: str) -> list[str]:
    """Extract all explicit path candidates (absolute paths or upward relative paths) from a shell command."""
    if not command or not command.strip():
        return []

    command = expand_shell_vars(command)
    paths: list[str] = []


    # 1. Regex search across entire command string
    for match in _PATH_REGEX.findall(command):
        clean = match.strip("\"'")
        clean = re.sub(r"^(?:>>|>|<)\s*", "", clean)
        if not clean:
            continue
        if (
            clean.startswith("/")
            and sys.platform == "win32"
            and len(clean) <= 3
            and "/" not in clean[1:]
        ):
            continue
        if clean not in paths:
            paths.append(clean)

    # 2. Tokenize command for token-level paths
    def _split_cmd(cmd_str: str) -> list[str]:
        if "\\" in cmd_str or ":" in cmd_str:
            try:
                return shlex.split(cmd_str, posix=False)
            except ValueError:
                pass
        try:
            return shlex.split(cmd_str, posix=(sys.platform != "win32"))
        except ValueError:
            return cmd_str.split()

    tokens = _split_cmd(command)

    for tok in tokens:
        clean = tok.strip("\"'")
        clean = re.sub(r"^(?:>>|>|<)\s*", "", clean)
        if not clean:
            continue

        is_abs_win = bool(re.match(r"^[a-zA-Z]:[/\\]", clean))
        is_abs_posix = clean.startswith("/") or clean.startswith("~")
        is_traversal = ".." in clean.split("/") or ".." in clean.split("\\")

        if is_abs_win or is_abs_posix or is_traversal:
            if (
                clean.startswith("/")
                and sys.platform == "win32"
                and len(clean) <= 3
                and "/" not in clean[1:]
            ):
                continue
            if clean not in paths:
                paths.append(clean)

    # 3. Check base64 payload strings
    for b64_path in extract_base64_paths(command):
        if b64_path not in paths:
            paths.append(b64_path)

    return paths






_SCRIPT_EXTENSIONS = (
    ".py",
    ".js",
    ".ts",
    ".sh",
    ".bash",
    ".ps1",
    ".pl",
    ".rb",
    ".cmd",
    ".bat",
)


def extract_script_file_targets(
    command: str, workspace_root: Path
) -> tuple[list[str], list[str]]:
    """Inspect local script files referenced in a command line and extract embedded write and read targets."""
    write_targets: list[str] = []
    all_paths: list[str] = []

    if not command or not command.strip():
        return write_targets, all_paths

    def _split_cmd(cmd_str: str) -> list[str]:
        if "\\" in cmd_str or ":" in cmd_str:
            try:
                return shlex.split(cmd_str, posix=False)
            except ValueError:
                pass
        try:
            return shlex.split(cmd_str, posix=(sys.platform != "win32"))
        except ValueError:
            return cmd_str.split()

    tokens = _split_cmd(command)

    for tok in tokens:
        clean = tok.strip("\"'")
        if not clean or clean.startswith("-"):
            continue

        p = Path(clean)
        if (
            p.suffix.lower() in _SCRIPT_EXTENSIONS
            or clean.startswith("./")
            or clean.startswith(".\\")
        ):
            candidate = (
                p.resolve() if p.is_absolute() else (workspace_root / p).resolve()
            )
            if candidate.is_file():
                try:
                    content = candidate.read_text(encoding="utf-8", errors="ignore")
                    for w in extract_shell_write_targets(content):
                        if w not in write_targets:
                            write_targets.append(w)
                    for r in extract_shell_all_paths(content):
                        if r not in all_paths:
                            all_paths.append(r)
                except Exception:
                    pass

    return write_targets, all_paths


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
    # Session-wide read-only grant (owner ask 2026-08-11): auto-allow shell commands the
    # conservative classifier (coworker/readonly.py) accepts. User-elected per session.
    session_readonly: bool = False
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

        # Path scoping for shell commands performing file read/write/copy/move operations (all modes).
        if is_shell:
            command = str(arguments.get("command", ""))
            script_writes, script_reads = extract_script_file_targets(
                command, self.workspace_root
            )
            write_targets = list(
                dict.fromkeys([*extract_shell_write_targets(command), *script_writes])
            )
            all_paths = list(
                dict.fromkeys([*extract_shell_all_paths(command), *script_reads])
            )

            for target in write_targets:
                if has_unresolved_vars(target):
                    return Decision(
                        False,
                        f"shell command target path contains unresolved variable expansion: {target}",
                        needs_user=(self.mode not in (Mode.AUTO, *READ_ONLY_MODES)),
                    )
                if not self._under_writable_root(target):
                    return Decision(
                        False,
                        f"shell command target path is not in a writable directory: {target}",
                    )
            for path in all_paths:
                if has_unresolved_vars(path):
                    if self.mode is Mode.AUTO or self.mode in READ_ONLY_MODES:
                        return Decision(
                            False,
                            f"shell command path contains unresolved variable expansion: {path}",
                            needs_user=False,
                        )
                    return Decision(
                        False,
                        f"shell command path contains unresolved variable expansion: {path}",
                        needs_user=True,
                    )
                if not self._under_root(path):
                    if self.mode is Mode.AUTO or self.mode in READ_ONLY_MODES:
                        return Decision(
                            False,
                            f"shell command path is not in an allowed directory: {path}",
                            needs_user=False,
                        )
                    return Decision(
                        False,
                        f"shell command path requires approval: {path}",
                        needs_user=True,
                    )








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
            if self.session_readonly and command:
                from .readonly import is_readonly_command

                if is_readonly_command(command):
                    return Decision(True, "read-only command (session grant)")
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

    def allow_readonly_for_session(self) -> None:
        self.session_readonly = True

    # -- helpers ----------------------------------------------------------------
    def _candidate(self, path: str) -> Path:
        # Relative paths resolve against primary (workspace_root); absolute/`~` taken as-is.
        # Expand environment variables ($HOME, ${HOME}, %USERPROFILE%, $env:USERPROFILE) before path resolution.
        # Normalize backslashes for cross-platform resolution on POSIX systems.
        expanded_path = expand_shell_vars(str(path))
        path_str = expanded_path.replace("\\", "/")
        try:
            p = Path(path_str).expanduser()
        except RuntimeError:
            p = Path(path_str)

        if re.match(r"^[a-zA-Z]:", path_str):
            return p
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
                return True
        return False
