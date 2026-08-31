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


# -- read targets (OPE-130) ------------------------------------------------------------
# The classifier above decides what a command may DO. It says nothing about what the
# command may READ, so a session grant meant for "stop asking about my project files" also
# covered ~/.aws/credentials, ~/.ssh/id_rsa and OpenWorker's own secrets file. These
# helpers name the file operands so the caller can hold them to the session's roots — the
# same shape as the fix for browser uploads in OPE-122.
#
# Extracting read targets from arbitrary shell is not possible in general; it is tractable
# here only because the classifier has already narrowed the input to the verbs above.

# Operands are not paths: arguments are strings, charsets, or command names.
_NO_PATH_OPERANDS = {
    "echo", "printf", "pwd", "whoami", "id", "date", "uname", "true", "false",
    "which", "basename", "dirname", "tr", "command", "env",
}
# The FIRST non-flag operand is a pattern/program, not a path; the rest are files.
_PATTERN_FIRST = {"grep", "egrep", "fgrep", "rg", "ugrep", "jq", "awk", "gawk", "mawk", "nawk", "sed"}
# Flags whose VALUE is a path, for the commands that accept them.
_PATH_VALUE_FLAGS = {"-f", "--file", "--exclude-from", "--include-from"}
# `head -n 5`, `cut -f 1`, `sed -n 2p`: a bare number is some flag's count, never a file
# worth scoping. Dropping them keeps the target list honest without a per-flag table.
_NUMERIC = re.compile(r"^[0-9]+([,:.-][0-9]+)*[a-zA-Z]?$")


def _stage_targets(argv: list[str]) -> list[str]:
    """File operands of one accepted pipeline stage."""
    i = 0
    while i < len(argv) and _ENV_ASSIGN.match(argv[i]):
        i += 1
    argv = argv[i:]
    if not argv:
        return []
    head, args = argv[0], argv[1:]
    if head in _NO_PATH_OPERANDS:
        return []

    if head == "git":
        # Only `-C <dir>` escapes the working directory; everything else the classifier
        # accepts reads the repo already in scope. Operands after `--` are pathspecs.
        out: list[str] = []
        for j, tok in enumerate(args):
            if tok == "-C" and j + 1 < len(args):
                out.append(args[j + 1])
            elif tok == "--":
                out.extend(t for t in args[j + 1 :] if not t.startswith("-"))
                break
        return out

    out = []
    skip_next = False
    seen_operand = False
    for tok in args:
        if skip_next:
            # `-f` is a pattern FILE for grep but a field NUMBER for cut; the numeric test
            # separates them without needing a per-command flag table.
            if not _NUMERIC.match(tok):
                out.append(tok)
            # `grep -f patterns.txt build.log`: the pattern came from the flag, so the
            # first positional is already a FILE and must not be skipped as the pattern.
            seen_operand = True
            skip_next = False
            continue
        if tok.startswith("-"):
            if tok in _PATH_VALUE_FLAGS:
                skip_next = True
            elif head == "find":
                break  # find's predicates start here; paths precede them
            continue
        if head in _PATTERN_FIRST and not seen_operand:
            seen_operand = True  # the pattern/script/filter, not a file
            continue
        seen_operand = True
        if not _NUMERIC.match(tok):
            out.append(tok)
    return out


def read_targets(command: str) -> list[str]:
    """Every file operand `command` would read, for scoping against the session's roots.

    Only meaningful for commands `is_readonly_command` accepts — it assumes that vetting.
    Errs toward naming MORE operands: an extra one costs a manual approval, a missed one
    is an unscoped read, and that asymmetry decides the edge cases here as it does above.

    Known limit: a path reached through a flag this table does not list is not returned.
    The positional operands that carry the real exposure are covered.
    """
    stages = _stages(str(command or ""))
    if stages is None:
        return []
    return [t for stage in stages for t in _stage_targets(stage)]
