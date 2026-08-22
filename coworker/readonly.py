"""Conservative read-only shell-command classifier for the session-scoped grant.

"Allow read-only commands for this session" (owner ask 2026-08-11, born of approval
fatigue in security-scan sessions: ~15 hand-approvals per run) auto-allows a command only
when THIS classifier accepts it. The contract:

- **Local filesystem reads only.** Network clients (curl/wget/ssh/nc) are deliberately
  excluded even for GET — an auto-allowed network command is an exfiltration channel
  under prompt injection. Interpreters (python/ruby/sh -c) and anything that can write,
  execute, or mutate are excluded.
- **Pipelines are allowed** (`nl … | sed -n … | grep …`) — every stage must classify.
  All other shell operators (;, &&, ||, &, redirections, substitutions) are rejected
  outright.
- **Fail closed.** Unknown commands, unparseable input, path-invoked binaries, and any
  doubtful flag reject. False negatives cost one manual approval; false positives cost
  an unreviewed side effect — the asymmetry decides every edge case here.

This is a user-elected convenience on top of the approval flow, not a sandbox: the
session still runs under its permission mode, and the user granted the scope explicitly.
"""

from __future__ import annotations

import re
import shlex

# Commands that only read local state, with no writing flags to police.
_SIMPLE_SAFE = {
    "ls", "cat", "head", "tail", "wc", "nl", "sort", "uniq", "cut", "tr",
    "grep", "egrep", "fgrep", "rg", "ugrep", "file", "stat", "du", "df",
    "pwd", "echo", "printf", "which", "whoami", "id", "date", "uname",
    "basename", "dirname", "realpath", "readlink", "jq", "column", "diff",
    "comm", "strings", "md5sum", "shasum", "sha1sum", "sha256sum",
    "hexdump", "xxd", "od", "true", "false", "yamllint", "actionlint",
}

# Git subcommands that only read. Note the per-subcommand guards below — several git
# "read" commands grow write/exec behavior through specific flags.
_GIT_SAFE = {
    "status", "log", "show", "diff", "blame", "shortlog", "describe",
    "rev-parse", "rev-list", "ls-files", "ls-tree", "grep", "cat-file",
    "name-rev", "merge-base", "count-objects", "var", "check-ignore",
}

_GIT_BRANCH_FLAG_OK = {
    "--show-current", "--list", "-a", "-r", "-v", "-vv", "--contains",
    "--merged", "--no-merged", "--all",
}

_FIND_BAD = ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fls", "-fprintf")

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[^;&|<>`]*$")

# A sed script token that invokes the `w`/`W` (write-file) command: at the start, after a
# separator, or after an address. Conservative — a false hit just means one manual approval.
_SED_WRITE = re.compile(r"(^|[;{])\s*[0-9,$/ ]*[wW]\s")


def _stages(command: str) -> list[list[str]] | None:
    """Tokenize with operators surfaced; split into pipeline stages. None = reject."""
    if not command or not command.strip():
        return None
    # Substitutions can hide inside double quotes, which the tokenizer strips — check the
    # raw text. Rejects a literal '$(' in a grep pattern too; that asymmetry is the point.
    if "`" in command or "$(" in command or "<(" in command or ">(" in command:
        return None
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return None  # unbalanced quotes etc.
    stages: list[list[str]] = [[]]
    for tok in tokens:
        if tok == "|":
            stages.append([])
        elif tok in {";", "&", "&&", "||", "|&"} or (tok and set(tok) <= {">", "<", "&", "0", "1", "2"} and any(c in tok for c in "<>&")):
            return None  # every operator except a plain pipe rejects (incl. 2>, &>, <<)
        else:
            stages[-1].append(tok)
    if any(not s for s in stages):
        return None  # empty stage ("| cmd", "cmd |")
    return stages


def _git_ok(args: list[str]) -> bool:
    # Global flags: only `-C <dir>` and `--no-pager` pass; `-c`/`--config-env` can set
    # core.pager and similar exec hooks — rejected.
    i = 0
    while i < len(args):
        if args[i] == "-C" and i + 1 < len(args):
            i += 2
            continue
        if args[i] == "--no-pager":
            i += 1
            continue
        break
    if i >= len(args):
        return False
    sub, rest = args[i], args[i + 1 :]
    if any(t.startswith("--output") for t in rest):
        return False  # git log/diff --output=<file> writes
    if sub in _GIT_SAFE:
        return True
    if sub == "branch":
        return all(t in _GIT_BRANCH_FLAG_OK or t.startswith(("--format=", "--sort=")) for t in rest)
    if sub == "tag":
        return bool(rest) and all(
            t in {"-l", "--list", "-n", "--contains", "--merged"} or t.startswith("-n") for t in rest
        )
    if sub == "stash":
        return bool(rest) and rest[0] in {"list", "show"}
    if sub == "remote":
        return not rest or rest[0] in {"-v", "show", "get-url"}
    if sub == "config":
        return any(t in {"--get", "--get-all", "--get-regexp", "--list", "-l"} for t in rest)
    if sub == "reflog":
        return not rest or rest[0] == "show"
    return False


def _stage_ok(argv: list[str]) -> bool:
    # Leading VAR=value assignments (LC_ALL=C grep …) are inert — skip them.
    i = 0
    while i < len(argv) and _ENV_ASSIGN.match(argv[i]):
        i += 1
    argv = argv[i:]
    if not argv:
        return False
    head = argv[0]
    if "/" in head:
        return False  # path-invoked binaries can be anything; bare names only
    args = argv[1:]
    if head in _SIMPLE_SAFE:
        return True
    if head == "env":
        return not args  # bare `env` prints; `env CMD` executes
    if head == "command":
        return bool(args) and args[0] in {"-v", "-V"}
    if head == "git":
        return _git_ok(args)
    if head == "sed":
        if any(t.startswith(("-i", "--in-place", "-f", "--file")) for t in args):
            return False
        return not any(_SED_WRITE.search(t) for t in args if not t.startswith("-"))
    if head in {"awk", "gawk", "mawk", "nawk"}:
        return not any(">" in t or "system" in t for t in args)
    if head == "find":
        return not any(t.startswith(_FIND_BAD) for t in args)
    return False


def is_readonly_command(command: str) -> bool:
    """True iff `command` is a single command or pure pipeline of local read-only stages."""
    stages = _stages(str(command or ""))
    if stages is None:
        return False
    return all(_stage_ok(s) for s in stages)
