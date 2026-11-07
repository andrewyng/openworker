"""Permission engine — decides allow / deny / ask-user for each proposed tool call.

Modes: Plan (read-only) · Interactive (auto reads, ask on writes/commands) · Auto
(allow, still path-scoped). Refined by argument patterns (path-under-root, command
prefixes) and a session allowlist. The engine only *decides*; the turn engine routes
`needs_user` decisions to a surface for approval and records the outcome.
"""

from __future__ import annotations

import glob as _glob
import os
import re
import shlex
import sys
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
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
# Flags that turn a search/list tool into an execution, deletion, or file-writing tool.
# The fprint family writes find's output to an arbitrary path; omitting it let a bare
# `find` allowlist entry auto-run `find . -fprint /tmp/exfil.txt`. readonly.py's
# `_FIND_BAD` is the complete copy of this set, so keep the two in step.
_DANGEROUS_FLAGS = {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprintf", "-fprint", "-fprint0", "-fls"}


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
}


# The agent's way to ask for a site its commands cannot reach (OPE-219). It changes what the
# sandbox lets out, so only a person answers it, and full access cannot grant it.
NETWORK_ACCESS_TOOL = "request_network_access"
NETWORK_ACCESS_MAX_HOSTS = 5


def network_request_hosts(arguments: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The hosts a `request_network_access` call names, as ("host:port" entries, rejected
    inputs). Exact host names only: no wildcards, no addresses, nothing that is not a name,
    since the entry is shown to a person and opened on the sandbox as written."""
    from .sandbox.network_profiles import clean_host

    raw = (arguments or {}).get("hosts")
    items = [raw] if isinstance(raw, str) else list(raw or []) if isinstance(raw, (list, tuple)) else []
    good: list[str] = []
    bad: list[str] = []
    for item in items:
        text = str(item or "").strip()
        try:
            entry = clean_host(text)
        except ValueError:
            bad.append(text[:80])
            continue
        name = entry.rsplit(":", 1)[0]
        if name.startswith("*.") or name.replace(".", "").isdigit():
            bad.append(text[:80])
        elif entry not in good:
            good.append(entry)
    return good, bad


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
    ".github/actions/",  # actions the workflows call; they run with the workflow
    ".gitlab-ci.yml",
    ".vscode/tasks.json",
    ".coworker/",  # workspace policy + skills the agent would otherwise self-grant
    # Run by a build backend, env tool, git or a hook manager without being named on the
    # command line (OPE-231). package.json / pyproject.toml / setup.cfg are deliberately
    # NOT here: users edit them constantly (versions, dependencies) and the reviewer sees
    # the content, so it can tell a version bump from a new install script.
    "setup.py",
    ".envrc",
    ".gitattributes",
    ".pre-commit-config.yaml",
    "tox.ini",
    ".devcontainer/",
    ".husky/",
    ".circleci/",
    "AGENTS.md",  # read into every later session's system prompt (project.py)
    # git's own settings: they can name programs git runs (fsmonitor, pager, aliases,
    # filters) or move the hooks folder. `info/attributes` does what `.gitattributes`
    # does; a submodule keeps its own git folder, hooks and settings under `modules/`;
    # a `.git` FILE names the folder git takes all of those from. `.gitmodules` stays
    # with the reviewer: git refuses to run commands named there.
    ".git/config",
    ".git/info/attributes",
    ".git/modules/",
    ".git/worktrees/",  # each linked worktree's own settings, same role as `modules/`
    ".git",
    "conftest.py",  # pytest imports it on every test run, from any folder it collects
    "pytest.ini",  # its options can load a plugin on every test run, like `tox.ini`
    # Python imports these on every start of the interpreter that can see them.
    "sitecustomize.py",
    "usercustomize.py",
    # mise loads these on entering the folder, as direnv does `.envrc`.
    ".mise.toml",
    "mise.toml",
    ".mise.local.toml",
    "mise.local.toml",
    # CI services run these on push.
    "azure-pipelines.yml",
    ".travis.yml",
    "Jenkinsfile",
    "bitbucket-pipelines.yml",
    ".buildkite/",
    ".vscode/launch.json",  # what the editor runs when the user starts or debugs the program
    # Editor settings can name programs the editor starts on its own: the terminal
    # shell, the interpreter, formatters and linters.
    ".vscode/settings.json",
    # Package managers read these on every run, and a setting can name a program to run
    # in place of (or alongside) the package manager itself.
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnpmfile.cjs",  # code pnpm runs on every install
    # cargo's settings can name the program that runs, links or wraps every build.
    ".cargo/config.toml",
    ".cargo/config",
)


def _case_insensitive_fs() -> bool:
    """Windows always, and macOS by default, treat `.GIT/hooks` and `.git/hooks` as one
    folder. `Path.resolve()` only restores the on-disk case for parts that already exist,
    so a not-yet-created target keeps the case the agent typed and matching has to fold
    case itself. Folding on a case-sensitive macOS volume only adds a card, never removes
    one."""
    return sys.platform in ("win32", "darwin")


def _fold(text: str) -> str:
    return text.casefold() if _case_insensitive_fs() else text


def _names_protected(path_text: str, markers: Optional[Sequence[str]] = None) -> bool:
    """Whether a path, as text, names a protected in-project file or folder. Folder
    markers match at any depth and also the folder itself (`.git/hooks`); file markers
    match the last part only."""
    text = "/" + _fold(path_text.replace("\\", "/")).strip("/") + "/"
    return any(
        f"/{marker}" in text if marker.endswith("/") else text.endswith(f"/{marker}/")
        for marker in map(_fold, _PROTECTED_IN_PROJECT if markers is None else markers)
    )


def _shell_markers() -> tuple[str, ...]:
    """The markers matched in shell commands: all but the bare `.git`, which in a command
    almost always names the folder being listed or left out, not a file being written."""
    return tuple(m for m in _PROTECTED_IN_PROJECT if m != ".git")


def _real_location(candidate: Path) -> Optional[str]:
    """Where a write to `candidate` really lands, or None when that cannot be worked out.

    The folders that exist are resolved by the operating system, so every spelling that
    reaches them (links, junctions, Windows short names, letter case) gives the same
    answer. Windows also reads the parts that don't exist yet in its own way: it drops
    trailing dots and spaces from a name, and `name:stream` is a stream of the file or
    folder `name`. Those parts are read the same way here."""
    try:
        real = os.path.realpath(candidate)
    except (OSError, ValueError):
        return None
    if sys.platform != "win32":
        return real
    if real.startswith("\\\\?\\UNC\\"):
        real = "\\\\" + real[8:]
    elif real.startswith("\\\\?\\"):
        real = real[4:]
    drive, rest = os.path.splitdrive(real)
    parts = []
    for part in re.split(r"[\\/]+", rest):
        if not part:
            continue
        name = part.split(":", 1)[0].rstrip(" .")
        if not name:
            return None
        parts.append(name)
    return drive + "\\" + "\\".join(parts)


def _is_protected_in_project(
    candidate: Path, *, fail_closed: bool = True, markers: Optional[Sequence[str]] = None
) -> bool:
    """Whether a write to `candidate` changes a protected in-project file, judged from the
    path as given and from where it really lands on disk. With `fail_closed`, a location
    that cannot be worked out counts as protected."""
    if _names_protected(candidate.as_posix(), markers):
        return True
    real = _real_location(candidate)
    if real is None:
        return fail_closed
    return _names_protected(real, markers)


# `git config` forms that only read. Anything else (a key with a value, --unset, --edit,
# the `set` / `unset` subcommands) changes git's settings.
_GIT_CONFIG_READS = {
    "--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color",
    "--get-colorbool", "--list", "-l", "get", "list",
}
_GIT_CONFIG_WRITES = {
    "--add", "--unset", "--unset-all", "--replace-all", "--rename-section",
    "--remove-section", "--edit", "-e", "set", "unset", "rename-section",
    "remove-section", "edit",
}
_GIT_CONFIG_VALUE_FLAGS = {"-f", "--file", "--blob", "-t", "--type", "--default", "--comment", "--value"}
# Settings that only label commits or pick a default: none names a program to run, a
# file to load, or where git fetches from. Setting one keeps its normal handling.
_GIT_CONFIG_PLAIN_KEYS = {
    "user.name", "user.email", "init.defaultbranch", "core.autocrlf", "pull.rebase", "color.ui",
    "core.longpaths", "push.default", "push.autosetupremote", "fetch.prune", "rebase.autostash",
    "merge.conflictstyle",
}
_GIT_CONFIG_WHOLE_FILE = {"--edit", "-e", "edit", "--rename-section", "rename-section",
                          "--remove-section", "remove-section"}


def _sets_only_plain_key(args: list[str]) -> bool:
    """Whether a `git config` change sets or unsets only one of the plain settings."""
    if any(a in _GIT_CONFIG_WHOLE_FILE for a in args):
        return False
    operands, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in _GIT_CONFIG_VALUE_FLAGS:
            skip = True
        elif not a.startswith("-"):
            operands.append(a)
    if operands and operands[0] in ("set", "unset"):
        operands = operands[1:]
    return bool(operands) and operands[0].lower() in _GIT_CONFIG_PLAIN_KEYS


def _changes_git_config(argv: list[str]) -> bool:
    """Whether one parsed command runs `git config` to change a setting that is not one
    of the plain ones (name, email, default branch and the like)."""
    for i, word in enumerate(argv):
        program = Path(word).name.lower()
        if program not in ("git", "git.exe"):
            continue
        rest = argv[i + 1 :]
        j = 0
        while j < len(rest) and rest[j].startswith("-"):
            j += 2 if rest[j] in ("-C", "-c") else 1
        if j >= len(rest) or rest[j] != "config":
            continue
        args = rest[j + 1 :]
        if _sets_only_plain_key(args):
            continue
        if any(a in _GIT_CONFIG_WRITES for a in args):
            return True
        if any(a in _GIT_CONFIG_READS for a in args):
            continue
        operands, skip = 0, False
        for a in args:
            if skip:
                skip = False
            elif a in _GIT_CONFIG_VALUE_FLAGS:
                skip = True
            elif not a.startswith("-"):
                operands += 1
        if operands != 1:  # `git config key value`, or a form not read here
            return True
    return False


def _split_sequence(command: str) -> Optional[list[str]]:
    """Split on top-level `;`, `&&` and `||`, outside quotes. None on unbalanced quotes."""
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == quote:
                quote = ""
            current.append(ch)
        elif ch in "'\"":
            quote = ch
            current.append(ch)
        elif ch == ";" or command.startswith(("&&", "||"), i):
            parts.append("".join(current))
            current = []
            i += 1 if ch == ";" else 2
            continue
        else:
            current.append(ch)
        i += 1
    if quote:
        return None
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _is_cd(part: str) -> bool:
    """Whether one part of a sequence only changes folder: `cd`, or `cd` and one plain
    folder name with nothing the shell would expand."""
    words = part.split()
    return bool(words) and words[0] == "cd" and len(words) <= 2 and not any(
        c in part for c in "$`(){}<>|&;*?["
    )


# Redirects that only throw output away. A read with its errors discarded is still a read.
_DISCARDED_OUTPUT = re.compile(r"(?<!\S)(?:(?:[12]|&)?>{1,2}\s*/dev/null|2>&1)(?!\S)")

# Characters that end a word when a shell command is read for the paths it names. Quotes
# and PowerShell's backtick are removed first, so `".git"/hooks` reads as `.git/hooks`.
_WORD_BREAK = re.compile(r"[\s;&|<>(){},=:]+")


def _script_run(argv: list[str]) -> Optional[str]:
    """The file one parsed command runs, when it runs a file: `python setup.py`,
    `bash .husky/pre-commit`, `./setup.py`. Running a protected file is not changing it,
    so that one word keeps its normal handling. None when no file is run this way
    (inline code, a module, or a form not read here)."""
    if not argv:
        return None
    program = Path(argv[0]).name.lower()
    program = program[:-4] if program.endswith(".exe") else program
    if program not in _INTERPRETERS and program not in ("py", "python2"):
        return argv[0] if "/" in argv[0] else None
    for arg in argv[1:]:
        if arg in _INLINE_CODE_FLAGS or arg in ("-m", "-W", "-X", "-Q"):
            return None
        if not arg.startswith("-"):
            return arg
    return None


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
from . import workspace_safety as _ws
from .workspace_safety import (
    file_has_secret,
    has_secret_text,
    has_secret_value,
    inert_write_check,
    is_sensitive_path,
    read_capped,
)

# The reason prefix of an OPE-233 inert-write allow; the engine keys the transcript chip
# and the audit row on `Decision.tier`, this text is what the record says.
INERT_WRITE_REASON = "inert, reversible write inside your folders"
# OPE-234: the reason prefix of a read-only command the tier let through.
READONLY_COMMAND_REASON = "read-only command inside your folders"
SECRET_EGRESS_REASON = "this sends a secret-looking file or value off the machine — approval required"


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
    "dangerously-bypass-approvals": "Dangerously bypass approvals",
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
    # Everything BYPASS_APPROVALS keeps is granted too: the three floors that otherwise
    # reach a person (running a file the agent downloaded, writing outside the session's
    # folders, files that run later such as git hooks / CI configs, and authority that
    # outlives the session) are cleared by the mode and each clearance is recorded. The
    # name follows the convention other harnesses use for the same switch, because the
    # name is the warning: only for a disposable machine or container. Never offered in
    # the desktop picker; the server accepts it only when started with the flag. The
    # self-protection floor (OpenWorker's own settings files) is a refusal, not an
    # approval, and stays.
    DANGEROUSLY_BYPASS_APPROVALS = "dangerously-bypass-approvals"
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
# Modes that grant every ordinary approval without a card. Only the second also clears
# the floors below.
BYPASS_MODES = frozenset({Mode.BYPASS_APPROVALS, Mode.DANGEROUSLY_BYPASS_APPROVALS})

# Reason prefix on every decision the dangerous mode granted in place of a person. The
# engine audits these individually ("recorded, never invisible") and the tool card says so.
CLEARED_BY_MODE = "cleared by mode"


def _cleared(floor: str) -> "Decision":
    return Decision(True, f"{CLEARED_BY_MODE}: {floor}")


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
    # The one exception is DANGEROUSLY_BYPASS_APPROVALS, which never produces these asks:
    # it grants them up front (see `_cleared`) and the engine records each grant.
    human_only: bool = False
    # Set when a task-scoped standing rule allowed the call ("tool → target") so the
    # engine can audit the exact rule and the tool card can say so (§25).
    rule: str = ""
    # The host the sandbox's allowed-sites wall stopped (OPE-219). The card then says why
    # it is asking and offers to add the site to the list.
    site: str = ""
    # The "host:port" entries a `request_network_access` call still needs a person for.
    network_hosts: tuple[str, ...] = ()
    # Set when an Auto-Approve gate tier matched (OPE-233), e.g. "inert_write". With
    # `allowed` the call runs without the reviewer; with `tier_audit_only` the decision is
    # unchanged and the engine only records that the tier WOULD have allowed it.
    tier: str = ""
    tier_audit_only: bool = False
    tier_note: str = ""


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
    from .connectors.tool_defs import standing_target_for

    if classify(tool_name, metadata, overrides) is not RiskClass.EXTERNAL:
        return None
    return standing_target_for(tool_name, arguments or {})


def standing_tool_candidate(
    tool_name: str,
    metadata: Any = None,
    overrides: Optional[RiskOverrides] = None,
) -> bool:
    """Whether this tool may earn a task-scoped standing rule with NO target binding.

    Egress only. The reasoning is the strictness table itself: egress (1) ranks below
    external (2), and an external call already earns a standing rule when it names a
    target. Refusing the lower class outright meant a routine could be trusted forever
    to message a chat, but never to read a web page — so a daily sweep asked the same
    question every morning and "Always" had nothing to bind to.

    Write-local and exec stay out, as they do for target-bound rules: shell asks
    forever. External stays target-bound — the target IS the safety there, and a
    blanket "send anything anywhere" is a different proposition entirely.
    """
    return classify(tool_name, metadata, overrides) is RiskClass.EGRESS


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
    # The sandbox's allowed sites, as a wall for the web tools too (OPE-219). None: no wall
    # (the session is not sandboxed, or its network setting is "Allow everything"). A list,
    # possibly empty: the session runs with "Only the sites you allow", so `web_fetch`,
    # `web_search` and `browser_open_url` may reach only these hosts ("host[:port]",
    # "*.example.com"), the same list its commands are held to. These tools run in the
    # OpenWorker process, outside the sandbox; before this the list did not apply to them
    # and Bypass reached any site (reported on 0.3.0, 2026-10-02).
    sandbox_sites: Optional[list[str]] = None
    # Where `web_search` goes: the configured provider's host (it has no url argument).
    search_host: Optional[Callable[[], str]] = None
    # Persists a site the user chose to always allow (adds it to the machine's list) and
    # returns that list, so this session's copy carries the same entries.
    grant_site: Optional[Callable[[str], Optional[list[str]]]] = None
    # Opens a site on the session's running sandbox, so commands reach it too (OPE-219).
    open_site: Optional[Callable[[str], None]] = None
    # Takes a site back from the session's running sandbox.
    close_site: Optional[Callable[[str], None]] = None
    # "host:port" entries a person allowed for this session only (either card, or the
    # session's own list in the app). The machine's list is `sandbox_sites`.
    session_sites: list[str] = field(default_factory=list)
    # A site the person allowed that the running sandbox could not take: entry -> why.
    site_open_errors: dict[str, str] = field(default_factory=dict)
    # Session-wide read-only grant (owner ask 2026-08-11): auto-allow shell commands the
    # conservative classifier (coworker/readonly.py) accepts. User-elected per session.
    session_readonly: bool = False
    # OPE-232: which shell runs `run_shell` — "bash" (macOS / Linux) or "powershell"
    # (Windows); None picks by platform. The read-only check reads the command as that
    # shell would.
    shell_kind: Optional[str] = None
    # The persistent shell's current folder, as the engine knows it right now. None (no
    # callable) means no shell is wired and the workspace folder is used, as before; a
    # callable that returns None means the folder is unknown for this call (another shell
    # command was cleared earlier in the same batch and may move it), so the grant asks.
    shell_cwd: Optional[Callable[[], Optional[str]]] = None
    # Task-scoped standing rules (§25): {tool: {allowed targets}}, seeded from the owning
    # ScheduledTask's target-shaped entries. Kept by reference and re-read every check, so a
    # rule minted mid-run ("Allow every time") applies to the run's next call too.
    task_rules: dict[str, set[str]] = field(default_factory=dict)
    # Thread grants (spec §11.4): the origin threads this session may answer without
    # asking. Each entry expands into task_rules for the platform's reply tools (the
    # manager's `_grant_thread_rules`); persisted with the session's grants and
    # re-applied on rebuild, so a subscribed session tagged in a thread keeps its
    # grant across restarts (mention-spawned sessions also re-derive from the thread map).
    thread_grants: set[str] = field(default_factory=set)
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
    # OPE-233 gate tier, Auto-Approve only: "off" (default), "audit" (record what it would
    # have allowed, change nothing) or "on". User-global config, never a workspace's.
    inert_writes: str = "off"
    # "Did the agent's own write produce this file this session?" — the engine's
    # provenance record. A file the agent wrote can be rewritten without losing the user's.
    agent_wrote: Optional[Callable[[Path], bool]] = None
    # "Did the agent write a secret-looking value into this file this session?" (OPE-236).
    # A file the agent filled with a key and then sends is caught even when the file can't
    # be read back (moved, over the size cap, outside the folders).
    agent_file_has_secret: Optional[Callable[[Path], bool]] = None
    # OPE-234 gate tier, Auto-Approve only: "off" (default), "audit" or "on", like
    # `inert_writes`. User-global config, never a workspace's.
    readonly_commands: str = "off"
    # (The tier reads the shell's folder from `shell_cwd`, above. Unlike the session grant,
    # it never falls back to the workspace folder: no folder, no tier.)

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.auto_allow_tools = set(self.auto_allow_tools)
        if self.roots is None:
            self.roots = [{"path": self.workspace_root, "writable": True}]
        if self.shell_kind is None:
            self.shell_kind = "powershell" if sys.platform == "win32" else "bash"
        if self.inert_writes not in ("off", "audit", "on"):
            self.inert_writes = "off"
        if self.readonly_commands not in ("off", "audit", "on"):
            self.readonly_commands = "off"

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
            is_consequential(risk)
            or tool_name in PERSISTENT_AUTHORITY_TOOLS
            or tool_name == NETWORK_ACCESS_TOOL
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

        # The dangerous mode clears every floor below that would otherwise reach a person.
        # Each clearance keeps its own reason so the record says which floor was crossed.
        # (The self-protection refusal above and the read-only modes are not approvals and
        # are untouched.)
        dangerous = self.mode is Mode.DANGEROUSLY_BYPASS_APPROVALS

        # Path scoping for writes (all modes): every path the write touches must land in a
        # writable root. A write whose path can't be located is not scoped-able, so it fails
        # closed to approval rather than slipping through auto/custom unscoped.
        needs_human_for_protected = False
        if is_write:
            paths, located = write_paths(tool_name, arguments)
            if not located:
                if dangerous:
                    return _cleared("write path could not be scoped")
                return Decision(
                    False,
                    "cannot determine the write path to scope",
                    needs_user=True,
                    human_only=True,  # an unscopable write must reach a person, not the reviewer
                )
            for path in paths:
                if not self._under_writable_root(path):
                    if dangerous:
                        # The permission floor is cleared; the file tools still resolve
                        # paths against the session's roots by construction, so this
                        # mostly matters for tools that write wherever they are pointed.
                        return _cleared(f"write outside the session's directories: {path}")
                    return Decision(
                        False, f"path is not in a writable directory: {path}"
                    )
                # In-project files that run on a later action (git hooks, CI configs) may be
                # edited, but never by an auto-approve path — a human must see it.
                if _is_protected_in_project(self._candidate(path)):
                    needs_human_for_protected = True
        # The same files changed by a command: one that names a protected path, unless the
        # read-only check accepts it (reading a hook or a CI file keeps its normal handling).
        if is_shell and self._shell_changes_protected(str(arguments.get("command", ""))):
            needs_human_for_protected = True

        # Asking for a site (OPE-219): a change to the allowed-sites wall. Only a person
        # answers it; full access cannot, like every other change to a wall.
        if tool_name == NETWORK_ACCESS_TOOL:
            return self._evaluate_network_request(arguments)

        # Authority outliving the session reaches a person, over the reviewer and over
        # every allowlist below (OPE-117). Placed ahead of the non-consequential return on
        # purpose: these tools are consequential today, but a metadata slip must not be
        # able to switch the floor off. Read-only modes still hard-deny above this.
        if tool_name in PERSISTENT_AUTHORITY_TOOLS:
            if dangerous:
                return _cleared("authority that outlives the session")
            return Decision(
                False,
                "this outlives the session — approval required",
                needs_user=True,
                human_only=True,
            )

        # OPE-236: secret-looking data leaving the machine reaches a person, over the
        # reviewer, every allowlist and the allowed-sites wall. Whether an agent declines
        # to upload a .env file is model behaviour, not policy. A backstop on top of the
        # sandbox's network allowlist: shell egress can't be recognised completely.
        if self._sends_secret(tool_name, arguments, risk):
            if dangerous:
                return _cleared("secret-looking data leaving the machine")
            return Decision(False, SECRET_EGRESS_REASON, needs_user=True, human_only=True)

        # Non-consequential tools always run.
        if not consequential:
            return Decision(True, "low risk")

        # A protected in-project target (git hooks, CI config) skips every auto-approve path
        # below — including auto mode and the session/config allowlists — and asks.
        if needs_human_for_protected:
            if dangerous:
                return _cleared("file that runs automatically later")
            return Decision(
                False,
                "this file runs automatically later — approval required",
                needs_user=True,
                human_only=True,  # deferred-execution files: a human sees every one (§ floor)
            )

        # THE ALLOWED-SITES WALL (OPE-219). Like the folder wall, no mode removes it: in
        # Bypass a site that is not on the list is refused outright, with no card (owner
        # ruling 2026-10-02); in the asking modes it reaches a person, never the reviewer,
        # who may not widen the user's list. A site on the list runs without a card.
        walled_site = ""
        on_site_wall = False
        if is_egress and self.sandbox_sites is not None:
            host = self.egress_host(tool_name, arguments)
            if host and self._site_allowed(host):
                on_site_wall = True
            else:
                walled_site = host or "this site"
                if self.mode is Mode.BYPASS_APPROVALS:
                    return Decision(
                        False,
                        f"{walled_site} is not on this machine's allowed sites, so the sandbox "
                        "setting blocks it. The user can add it in Settings > Sandbox > Choose sites.",
                        needs_user=False,
                        site=walled_site,
                    )

        # Full access.
        if self.mode in BYPASS_MODES:
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
                # The classifier vets what a command DOES; the roots vet what it READS
                # (OPE-130). Without the second half, a grant the user reads as "stop
                # asking about my project files" also covers ~/.aws/credentials, another
                # repo's history, and OpenWorker's own secrets file — none of which the
                # self-protection floor catches, since that guards writes, not reads.
                if self._readonly_grant_covers(command):
                    return Decision(True, "read-only command (session grant)")
        if on_site_wall:
            return Decision(True, "site on the allowed sites")
        if walled_site:
            return Decision(
                False,
                f"{walled_site} is not on your allowed sites",
                needs_user=True,
                human_only=True,
                site=walled_site,
            )
        if is_egress:
            url = str(arguments.get("url", ""))
            if self._domain_allowed(url, include_session=honor_session_grants):
                return Decision(True, "domain on allowlist")
        if (
            honor_session_grants
            and tool_name in self.session_allow_tools
            and not is_connector
            and not is_shell
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

        # OPE-234: in Auto-Approve, a read-only command (or a sequence of them) inside the
        # folders needs no reviewer call. Last checks on purpose, like the inert-write tier
        # below: every floor above has already had its say.
        if (
            is_shell
            and self.mode is Mode.AUTO_APPROVE
            and self.readonly_commands != "off"
            and self._readonly_tier_supported()
        ):
            eligible, detail = self._readonly_tier(str(arguments.get("command", "")))
            if eligible and self.readonly_commands == "on":
                return Decision(True, f"{READONLY_COMMAND_REASON}: {detail}", tier="readonly_command")
            if eligible:
                return Decision(
                    False, "requires approval", needs_user=True,
                    tier="readonly_command", tier_audit_only=True, tier_note=detail,
                )

        # OPE-233: in Auto-Approve, an inert write that can be undone needs no reviewer
        # call. It is the last check on purpose: every floor above (folders, protected
        # files, self-protection, read-only modes) has already had its say.
        if is_write and self.mode is Mode.AUTO_APPROVE and self.inert_writes != "off":
            paths, _located = write_paths(tool_name, arguments)  # located: checked above
            eligible, detail = inert_write_check(
                arguments, [self._candidate(p) for p in paths], agent_wrote=self.agent_wrote,
                roots=[rp for rp, writable in self._resolved_roots() if writable],
            )
            if eligible and self.inert_writes == "on":
                return Decision(True, f"{INERT_WRITE_REASON}: {detail}", tier="inert_write")
            if eligible:
                return Decision(
                    False, "requires approval", needs_user=True,
                    tier="inert_write", tier_audit_only=True, tier_note=detail,
                )

        # Otherwise: ask the user.
        return Decision(False, "requires approval", needs_user=True)

    def _readonly_tier_supported(self) -> bool:
        """OPE-234: the read-only command tier follows the shell the session runs, not the
        operating system: bash only. PowerShell's checker is not ready for a reviewer-less
        clearance (OPE-232 / OPE-230), so there the reviewer decides."""
        return self.shell_kind == "bash"

    def _readonly_tier(self, command: str) -> tuple[bool, str]:
        """OPE-234: whether `command` qualifies for the read-only tier, judged from the
        shell's real current folder. Each part of the sequence goes through the same check
        as the session grant (`_readonly_reads_inside`), with the secret rule on. Any
        failure means no tier: the reviewer decides."""
        if self.shell_cwd is None:
            return False, ""
        try:
            cwd = self.shell_cwd()
            if not cwd:
                return False, ""
            from .readonly import readonly_tier_check

            return readonly_tier_check(
                command,
                cwd=Path(cwd).expanduser().resolve(),
                inside=lambda p: self._under_root(str(p)),
                covers=lambda text, here: self._readonly_reads_inside(text, here, secrets=True),
            )
        except Exception:  # noqa: BLE001 - a check that cannot finish never allows
            return False, ""

    def _secret_path(self, path: Path) -> bool:
        """A secret-shaped file name, or one kept under a secret-shaped folder inside the
        session's folders (`secrets/prod.json`). The folder rule looks only below the root,
        so a project called `my-secrets` is not refused wholesale."""
        if is_sensitive_path(str(path)):  # OPE-236: by name, and by the folders it lives in
            return True
        for rp, _ in self._resolved_roots():
            try:
                rel = path.relative_to(rp)
            except ValueError:
                continue
            return any(is_sensitive_path(part) for part in rel.parts[:-1])
        return False

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

    def _readonly_grant_covers(self, command: str) -> bool:
        """The read-only session grant's check (OPE-130, OPE-232): the command only reads,
        as this platform's shell runs it, and every path it reads is inside the folders.
        Paths are judged from the shell's real current folder when the engine knows it,
        and wildcards are expanded with every match checked. A git command is covered only
        when the project's own git settings are inert (coworker/git_settings.py). Fails closed. The grant is the
        user's own click, so it reads secret-shaped files inside the folders; the Auto-Approve
        tier (OPE-234) runs the same check with `secrets=True`."""
        try:
            if self.shell_cwd is None:
                base = self.workspace_root
            else:
                cwd = self.shell_cwd()
                if not cwd:
                    return False
                base = Path(cwd).expanduser().resolve()
                if not self._under_root(str(base)):
                    return False
            return self._readonly_reads_inside(command, base) is not None
        except Exception:  # noqa: BLE001 - a check that cannot finish never allows
            return False

    def _readonly_reads_inside(
        self, command: str, base: Path, *, secrets: bool = False
    ) -> Optional[list[Path]]:
        """The one read-only check behind the session grant and the OPE-234 tier: `command`
        only reads, as this platform's shell runs it, and every path it reads, judged from
        `base` with wildcards expanded, is inside the folders. Returns the resolved paths
        it reads, or None when the command is not covered. With `secrets`, a secret-shaped
        file (by name, by folder, or named as a git `revision:path`) and a command that
        prints the environment or git's settings are not covered either. Raises on anything
        unexpected; callers fail closed."""
        from .readonly import git_revision_paths, is_readonly_command, prints_settings, read_targets, readonly_globs_safe

        if not is_readonly_command(command, shell=self.shell_kind):
            return None
        if not readonly_globs_safe(command, base, shell=self.shell_kind):
            return None
        if secrets and prints_settings(command, shell=self.shell_kind):
            return None
        reads: list[Path] = []
        for target in read_targets(command, shell=self.shell_kind):
            path = Path(os.path.expanduser(target))
            full = path if path.is_absolute() else base / path
            if any(c in target for c in "*?["):
                for match in _glob.glob(str(full)):
                    reads.append(Path(match).resolve())
            reads.append(full.resolve())
        for p in reads:
            if not self._under_root(str(p)):
                return None
            if secrets and self._secret_path(p):
                return None
        if secrets:
            for target in git_revision_paths(command, shell=self.shell_kind):
                if self._secret_path((base / target.lstrip("/")).resolve()):
                    return None
        # A git command is covered only when the project's own git settings are inert
        # (coworker/git_settings.py): git obeys settings stored inside the project, and
        # some of them name programs that reading commands then run (OPE-232).
        from .git_settings import project_git_settings_inert
        from .readonly import git_repo_dirs

        for repo in git_repo_dirs(command, shell=self.shell_kind):
            # The config probe must examine the directory git will actually enter.
            # A literal `repo*` is not a directory, even when bash expands it to one.
            if any(c in repo for c in "*?["):
                return None
            path = Path(os.path.expanduser(repo))
            full = path if path.is_absolute() else base / path
            if not project_git_settings_inert(full, lambda p: self._under_root(str(p))):
                return None
        return reads

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

    def allow_site_always(self, url_or_domain: str) -> None:
        """"Always allow <site>" on the allowed-sites card: the site joins the machine's
        list (Settings > Sandbox), for commands and web tools alike. Without a store wired
        (ephemeral engines in tests) it degrades to this session."""
        host = _host_of(url_or_domain)
        if not host:
            return
        if self.sandbox_sites is not None and not self._on_site_list(host):
            self.sandbox_sites.append(host)
        if self.grant_site is not None:
            stored = self.grant_site(host)
            if stored is not None and self.sandbox_sites is not None:
                self.sandbox_sites[:] = [str(x) for x in stored]
        else:
            self.session_allow_domains.add(host)
        self._open(host)

    def allow_site_for_session(self, url_or_domain: str) -> None:
        """"Allow for this session" on the allowed-sites card: the web tools and, through the
        running sandbox, the commands of this session reach the site; nothing is stored."""
        host = _host_of(url_or_domain)
        if not host:
            return
        self.allow_domain_for_session(host)
        self._note_session_site(host)
        self._open(host)

    def allow_network_hosts(self, entries: Sequence[str], *, always: bool) -> None:
        """A person allowed these "host:port" entries (the network-access card, or the
        session's list in the app): for this session, or for good when `always` and a store
        is wired. Commands reach them through the running sandbox; the web tools too."""
        for entry in entries:
            host = str(entry).rsplit(":", 1)[0]
            if always and self.grant_site is not None:
                stored = self.grant_site(str(entry))
                if stored is not None and self.sandbox_sites is not None:
                    self.sandbox_sites[:] = [str(x) for x in stored]
            else:
                self.session_allow_domains.add(host)
                self._note_session_site(str(entry))
            self._open(str(entry))

    def remove_session_site(self, entry: str) -> bool:
        """The person took back a site they had allowed for this session. Sites on the
        machine's list are not touched here; those are changed in Settings. Raises when the
        running sandbox could not drop it, and then nothing is changed."""
        if entry not in self.session_sites:
            return False
        if self.close_site is not None:
            self.close_site(entry)
        self.session_sites.remove(entry)
        self.site_open_errors.pop(entry, None)
        host = entry.rsplit(":", 1)[0]
        if not any(other.rsplit(":", 1)[0] == host for other in self.session_sites):
            self.session_allow_domains.discard(host)
            self.session_allow_domains.discard(host[4:] if host.startswith("www.") else host)
        return True

    def _note_session_site(self, host_or_entry: str) -> None:
        from .sandbox.network_profiles import clean_host

        try:
            entry = clean_host(host_or_entry)
        except ValueError:
            return
        if entry not in self.session_sites and not self._entry_allowed(entry):
            self.session_sites.append(entry)

    def _open(self, host_or_entry: str) -> None:
        """Tell the running sandbox. A failure does not undo the person's choice for the
        web tools; it is kept so the agent and the app can say the commands lack the site."""
        if self.open_site is None:
            return
        try:
            self.open_site(host_or_entry)
            self.site_open_errors.pop(host_or_entry, None)
        except Exception as exc:  # noqa: BLE001
            self.site_open_errors[host_or_entry] = str(exc) or type(exc).__name__

    def _entry_allowed(self, entry: str) -> bool:
        """Whether a "host:port" entry is already open to this session's commands: on the
        machine's list (same port; `*.example.com` covers subdomains) or allowed for the
        session."""
        from .sandbox.network_profiles import clean_host

        if entry in self.session_sites:
            return True
        host, _, port = entry.rpartition(":")
        for item in self.sandbox_sites or []:
            try:
                name, _, item_port = clean_host(str(item)).rpartition(":")
            except ValueError:
                continue
            if item_port != port:
                continue
            if name.startswith("*."):
                if host.endswith(name[1:]) and host != name[2:]:
                    return True
            elif host == name:
                return True
        return False

    def _evaluate_network_request(self, arguments: dict[str, Any]) -> Decision:
        if self.sandbox_sites is None:
            return Decision(False, "this session has no allowed-sites list to change", needs_user=False)
        wanted, bad = network_request_hosts(arguments)
        if bad or not wanted or len(wanted) > NETWORK_ACCESS_MAX_HOSTS:
            return Decision(
                False,
                f"give `hosts` as one to {NETWORK_ACCESS_MAX_HOSTS} exact host names, such as registry.npmjs.org "
                "or db.example.com:5432 (no wildcards, no IP addresses)" + (f"; not accepted: {', '.join(bad)}" if bad else ""),
                needs_user=False,
            )
        pending = [entry for entry in wanted if not self._entry_allowed(entry)]
        if not pending:
            return Decision(True, "already on the allowed sites")
        if self.mode is Mode.BYPASS_APPROVALS:
            return Decision(
                False,
                "The allowed sites cannot be changed from this session. Tell the user; they can add the site in Settings > Sandbox > Choose sites.",
                needs_user=False,
            )
        return Decision(
            False,
            "asks to let this session's commands reach " + ", ".join(e[:-4] if e.endswith(":443") else e for e in pending),
            needs_user=True,
            human_only=True,
            network_hosts=tuple(pending),
        )

    def egress_host(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Where an egress tool is going: its url's host, or for `web_search` (a fixed
        destination, no url) the configured search provider's host."""
        url = str((arguments or {}).get("url", "") or "")
        if url:
            return _host_of(url)
        if self.search_host is not None:
            try:
                return _host_of(self.search_host() or "")
            except Exception:  # noqa: BLE001 - an unknown provider reads as "not allowed"
                return ""
        return ""

    def _on_site_list(self, host: str) -> bool:
        """Whether `host` is on the sandbox's list. The web tools speak HTTP(S) only, so
        the entry's port is not compared; `*.example.com` covers subdomains, as in the
        sandbox's own proxy (netproxy.allows)."""
        for entry in self.sandbox_sites or []:
            name = str(entry).strip().lower()
            if not name:
                continue
            if "://" in name:
                name = _host_of(name)
            name = name.rsplit(":", 1)[0] if ":" in name and not name.endswith("]") else name
            if name.startswith("*."):
                if host.endswith(name[1:]) and host != name[2:]:
                    return True
            elif host == name:
                return True
        return False

    def _site_allowed(self, host: str) -> bool:
        """On the machine's list, or granted by the user for this session. A session grant
        counts in every mode here: a person gave it, on a card only a person can answer."""
        if self._on_site_list(host):
            return True
        return any(host == d or host.endswith("." + d) for d in self.session_allow_domains)

    def _shell_base(self) -> Path:
        """The folder a shell command runs in, as far as the engine knows: the persistent
        shell's folder when one is wired, else the workspace. Unknown (another shell call
        in the same batch may move it) falls back to the workspace: best effort."""
        if self.shell_cwd is not None:
            try:
                cwd = self.shell_cwd()
            except Exception:  # noqa: BLE001 - a broken callback must not block the floor
                cwd = None
            if cwd:
                return Path(cwd).expanduser().resolve()
        return self.workspace_root

    def _resolve_from(self, path: str, base: Path) -> Path:
        p = Path(os.path.expanduser(path))
        return (p if p.is_absolute() else base / p).resolve()

    def _read_inside(self, path: str, base: Path, cap: int) -> Optional[str]:
        """A file's text, only when it lies inside the session's folders and under `cap`."""
        try:
            full = self._resolve_from(path, base)
            if not self._under_root(str(full)):
                return None
            return read_capped(full, cap)
        except (OSError, ValueError):
            return None

    def _sent_file_has_secret(self, path: str, base: Path) -> bool:
        try:
            full = self._resolve_from(path, base)
        except (OSError, ValueError):
            return False  # an unresolvable path is judged by its name
        if self.agent_file_has_secret is not None and self.agent_file_has_secret(full):
            return True
        if not self._under_root(str(full)):
            return False
        return bool(file_has_secret(full))

    def _sends_secret(self, tool_name: str, arguments: dict[str, Any], risk: RiskClass) -> bool:
        """OPE-236: the call sends data off the machine AND a secret is involved.

        SENDING is: egress or external risk (web tools, connector writes, MCP tools); a
        shell command that runs a network client (`curl`, `scp`, ...), a program that
        sends or publishes (`git push`, `npm publish`, `twine upload`, `docker push`,
        `gh release upload`, `aws s3 cp`, `rclone`, `mail`, ... see
        `review_facts._SENDING_PROGRAMS`), or a script whose text uses the network
        (`python upload.py` where upload.py imports requests; `-c` code too).

        A SECRET is involved when:

        * an argument carries a secret-looking value (`has_secret_value`); or
        * a file the action names has a secret-looking name or path (`is_sensitive_path`:
          `.env`, `id_rsa`, `~/.kube/config`, `*.tfstate`, ...); files a network-using
          script names count as named by the action; or
        * a file the action SENDS (an upload, an attachment, `scp`, a cloud copy, a file
          a network-using script names) holds a secret-looking value: the engine recorded
          the agent writing one into it this session, or the file is inside the session's
          folders, no bigger than `SECRET_SCAN_MAX_BYTES`, and its contents match. A file
          that can't be read (missing, a folder, too big, outside the folders) is judged
          by its name alone, so an unreadable plain file is not a card; or
        * a network-using script's own text carries a secret-looking value; or
        * `npm`/`yarn`/`pnpm publish` runs in a folder holding a secret-named file (npm
          sends the folder; `.npmrc`, `.git`, `node_modules` and the ignore files, which
          npm never packs, don't count); or
        * `git push` would push a commit holding a secret-named file, or a file whose
          blob carries a secret-looking value (`@{u}..HEAD`, else the remote-tracking
          branch, else the whole branch for a first push; names for every file, blobs for
          the first hundred).

        Scripts are read only inside the session's folders and up to
        `SCRIPT_SCAN_MAX_BYTES`. Everything here reads text the agent chose: a module the
        script imports, a compiled program, a URL assembled from data at run time, or a
        `git push` outside a repository git can describe, is not seen, and the floor does
        not fire. Those are left to the sandbox's network allowlist; OPE-242 is the
        per-command outbound host report that would close the gap.
        """
        from . import review_facts as rf

        arguments = arguments or {}
        shell = self.shell_kind or "bash"
        command = str(arguments.get("command", "")) if tool_name == SHELL_TOOL else ""
        sends = risk in (RiskClass.EGRESS, RiskClass.EXTERNAL) or bool(
            command and (rf.shell_reaches_network(command) or rf.shell_sends_data(command, shell))
        )
        base = self._shell_base() if command else self.workspace_root
        network_scripts: list[str] = []
        if command:
            for path, code in rf.script_runs(command, shell):
                text = code if code is not None else self._read_inside(path or "", base, _ws.SCRIPT_SCAN_MAX_BYTES)
                if text and rf.script_uses_network(text):
                    sends = True
                    network_scripts.append(text)
        if not sends:
            return False
        if has_secret_value(arguments):
            return True
        names = rf.referenced_files(tool_name, arguments, [])
        sent = rf.sent_files(tool_name, arguments, shell)
        for text in network_scripts:
            if has_secret_text(text):
                return True
            found = rf.script_named_files(text)
            names += found
            sent += found
        if any(is_sensitive_path(p) for p in names):
            return True
        if any(self._sent_file_has_secret(p, base) for p in sent):
            return True
        if command:
            for folder in rf.publish_dirs(command, shell):
                if self._folder_holds_secret_name(folder, base):
                    return True
            push = rf.pushed_files(command, base, shell)
            if push is not None:
                root, files = push
                if any(is_sensitive_path(f) for f in files):
                    return True
                if any(rf.git_blob_has_secret(root, f) for f in files[: rf._PUSH_CONTENT_FILES]):
                    return True
        return False

    _NEVER_PACKED = {".npmrc", ".git", "node_modules", ".gitignore", ".npmignore", ".ds_store"}

    def _folder_holds_secret_name(self, folder: str, base: Path) -> bool:
        try:
            full = self._resolve_from(folder, base)
            if not full.is_dir():
                return False
            for n, entry in enumerate(full.iterdir()):
                if n > 500:
                    break
                if entry.name.lower() in self._NEVER_PACKED:
                    continue
                if is_sensitive_path(entry.name):
                    return True
        except (OSError, ValueError):
            return False
        return False

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
        resolved = {_fold(str(self._candidate(p))) for p in paths}
        for target in targets:
            if _fold(str(Path(target).resolve())) in resolved:
                return target
        return None

    def _only_reads(self, command: str) -> bool:
        """The read-only check, applied to the whole command or to each part of a
        `;` / `&&` / `||` sequence (where `cd <folder>` counts too: it changes no file),
        after dropping redirects that only throw output away (`2>/dev/null`, `2>&1`).
        Fails closed."""
        from .readonly import is_readonly_command

        try:
            if self.shell_kind == "bash":
                command = _DISCARDED_OUTPUT.sub(" ", command)
            if is_readonly_command(command, shell=self.shell_kind):
                return True
            parts = _split_sequence(command)
            return bool(parts) and len(parts) > 1 and all(
                is_readonly_command(p, shell=self.shell_kind) or _is_cd(p) for p in parts
            )
        except Exception:  # noqa: BLE001 - a check that cannot finish is not a read
            return False

    def _shell_changes_protected(self, command: str) -> bool:
        """Whether a shell command may change a protected in-project file: it is not a read
        the read-only check accepts, and it names one (or runs `git config` to change a
        setting). Like the settings floor this reads the command text, so it catches the
        paths a command spells out, not ones it computes. Each word is matched as written,
        case-folded where the file system ignores case, and also where it really lands
        from the shell's current folder, with wildcards expanded."""
        if not command.strip() or self._only_reads(command):
            return False
        text = command.replace("\\", "/")
        run: Counter[str] = Counter()
        for part in _split_commands(text):
            try:
                argv = shlex.split(part)
            except ValueError:
                argv = part.split()
            if _changes_git_config(argv):
                return True
            script = _script_run(argv)
            if script and not _WORD_BREAK.search(script):
                run[script] += 1
        base = self.workspace_root
        if self.shell_cwd is not None:
            try:
                cwd = self.shell_cwd()
            except Exception:  # noqa: BLE001 - fall back to the workspace folder
                cwd = None
            if cwd:
                base = Path(cwd).expanduser()
        words = Counter(
            w for w in _WORD_BREAK.split(re.sub(r"[\"'`]", "", text)) if w and not w.startswith("-")
        )
        markers = _shell_markers()
        # A file the command only runs is not a change to it; any other mention of the
        # same path in the command still counts.
        for word in [w for w, n in words.items() if n > run[w]]:
            if _names_protected(word, markers):
                return True
            if word.startswith("//"):
                continue  # a network path: matched as text only
            full = Path(os.path.expanduser(word))
            full = full if full.is_absolute() else base / full
            matches = _glob.glob(str(full)) if any(c in word for c in "*?[") else []
            for candidate in [full, *map(Path, matches)]:
                if _is_protected_in_project(candidate, fail_closed=False, markers=markers):
                    return True
        return False

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
