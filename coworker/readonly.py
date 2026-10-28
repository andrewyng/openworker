"""Conservative read-only shell-command classifier for the session-scoped grant.

"Allow read-only commands for this session" (owner ask 2026-08-11, born of approval
fatigue in security-scan sessions: ~15 hand-approvals per run) auto-allows a command only
when THIS classifier accepts it. The contract:

- **Local filesystem reads only.** Network clients (curl/wget/ssh/nc) are deliberately
  excluded even for GET — an auto-allowed network command is an exfiltration channel
  under prompt injection. Interpreters (python/ruby/sh -c), pagers, and anything that can
  write, execute, or mutate are excluded.
- **Allowlisted flags** (OPE-232). Every accepted command lists the flags known only to
  change what is shown or which local files are read. Any other flag, including one
  nobody has reviewed, makes the command not read-only. The same holds for each git
  subcommand and each PowerShell cmdlet. sed, awk and jq programs are accepted only in a
  conservative subset.
- **Pipelines are allowed** (`nl … | sed -n … | grep …`) — every stage must classify.
  All other shell operators (;, &&, ||, &, redirections, substitutions, subshells,
  brace expansion) are rejected outright.
- **Shell-aware** (OPE-232). The caller says which shell runs the command: `bash` (macOS,
  Linux) or `powershell` (Windows). For PowerShell only a plain command with literal
  arguments is eligible, and only the cmdlets listed below.
- **One line.** A command spanning lines is several commands: rejected.
- **Fail closed.** Unknown commands, unparseable input, path-invoked binaries, and any
  flag not on a list reject. False negatives cost one manual approval; false positives
  cost an unreviewed side effect — the asymmetry decides every edge case here.

The same parse that accepts a command names the files it reads (`read_targets`), so every
path an allowed flag carries is held to the session's folders.

This is a user-elected convenience on top of the approval flow, not a sandbox: the
session still runs under its permission mode, and the user granted the scope explicitly.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Callable, Optional

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[^;&|<>`]*$")
# Environment prefixes that only change formatting (locale, time zone, colour, width).
# Any other variable may name a program the command then runs, so it rejects.
_SAFE_ENV = frozenset({
    "LANG", "LANGUAGE", "LC_ALL", "LC_COLLATE", "LC_CTYPE", "LC_MESSAGES", "LC_NUMERIC",
    "LC_TIME", "TZ", "NO_COLOR", "COLUMNS",
})

SHELLS = ("bash", "powershell")
# PowerShell evaluates or expands these even inside what looks like an argument:
# variables, sub-expressions and grouping, script blocks, array/hash literals, arrays of
# arguments, escapes, call / redirection / sequence operators. Fail closed on any of them.
_POWERSHELL_UNSAFE = frozenset("$`(){}[]@,;&<>")


# -- tokenizing -----------------------------------------------------------------------------


def _has_unquoted_expansion(command: str) -> bool:
    """A variable, subshell or brace expansion bash would apply before the command runs.

    Single-quoted text is literal; `$` inside double quotes still expands. Brace expansion
    (`{a,b}`, `{1..3}`) turns one written word into several operands, so it rejects;
    braces without a comma or `..` (`stash@{0}`) are literal and pass.
    """
    quote: str | None = None
    escaped = False
    brace = 0
    prev = ""
    for char in command:
        if escaped:
            escaped = False
            prev = ""
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if char == "'" and quote != '"':
            quote = None if quote == "'" else "'"
        elif char == '"' and quote != "'":
            quote = None if quote == '"' else '"'
        elif char == "$" and quote != "'":
            return True
        elif quote is None:
            if char in "()":
                return True
            if char == "{":
                brace += 1
            elif char == "}":
                brace = max(0, brace - 1)
            elif char in " \t":
                brace = 0
            elif brace and (char == "," or (char == "." and prev == ".")):
                return True
        prev = char if quote is None else ""
    return False


def _stages(command: str) -> list[list[str]] | None:
    """Tokenize with operators surfaced; split into pipeline stages. None = reject."""
    if not command or not command.strip():
        return None
    if any(ord(c) < 32 and c != "\t" for c in command):
        return None  # a line break starts another command; other controls are never needed
    if _has_unquoted_expansion(command) or "$(" in command or "`" in command or "<(" in command or ">(" in command:
        return None
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""  # a `#` inside a word is part of the word, as the shell reads it
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


# -- the flag model -------------------------------------------------------------------------
# A flag either takes no value, takes one (attached: `-n5`, `--lines=5`; or the next
# token), or takes an optional value that must be attached (`--color`, `--color=never`).
# Value kinds: "num" must look like a count; "path" names a file the command reads and is
# returned as a read target; "text" is anything else (a pattern, a format, a field list).
#
# Where GNU and BSD disagree on whether a short flag takes a value, it is listed as taking
# none: the token after it then counts as an operand, which is a read target, so the
# disagreement can only add targets, never hide one.

_NUM = re.compile(r"^[+-]?[0-9]+[A-Za-z]*$")

Flags = list[tuple[str, Optional[str], Optional[str]]]  # (name, value, kind)


@dataclass(frozen=True, eq=False)
class _Spec:
    short: frozenset[str]
    short_val: dict[str, str]
    short_opt: frozenset[str]
    long: frozenset[str]
    long_val: dict[str, tuple[str, ...]]
    long_opt: frozenset[str]
    numeric: bool  # `-20` is a count (head, tail, grep context)
    operands: str  # "paths", "none", or "first" (a pattern/script/program, then paths)
    max_operands: Optional[int]
    pattern_flags: frozenset[str]  # with any of these, "first" has no positional pattern
    check: Optional[Callable[[Flags, list[str]], bool]]


def _spec(
    short: str = "",
    vals: str = "",
    sopt: str = "",
    long: str = "",
    lvals: str = "",
    lopt: str = "",
    *,
    numeric: bool = False,
    operands: str = "paths",
    max_operands: Optional[int] = None,
    pattern_flags: str = "",
    check: Optional[Callable[[Flags, list[str]], bool]] = None,
    common: bool = True,
) -> _Spec:
    """Build a spec from compact strings: `vals="n:num f:path"`, `lvals="arg:text,text"`."""

    def kinds(text: str) -> dict[str, tuple[str, ...]]:
        return {k: tuple(v.split(",")) for k, v in (item.split(":") for item in text.split())}

    longs = set(long.split()) | ({"help", "version"} if common else set())
    return _Spec(
        frozenset(short),
        {k: v[0] for k, v in kinds(vals).items()},
        frozenset(sopt),
        frozenset(longs),
        kinds(lvals),
        frozenset(lopt.split()),
        numeric,
        operands,
        max_operands,
        frozenset(pattern_flags.split()),
        check,
    )


def _merge(base: _Spec, **extra: str) -> _Spec:
    """`base` plus more flags (same keyword strings as `_spec`); `drop` removes short flags
    of `base` first."""
    add = _spec(common=False, **{k: v for k, v in extra.items() if k != "drop"})
    drop = set(extra.get("drop", ""))
    return _Spec(
        (base.short - drop) | add.short,
        {**{k: v for k, v in base.short_val.items() if k not in drop}, **add.short_val},
        base.short_opt | add.short_opt,
        base.long | add.long,
        {**base.long_val, **add.long_val},
        base.long_opt | add.long_opt,
        base.numeric,
        base.operands,
        base.max_operands,
        base.pattern_flags,
        base.check,
    )


def _value_ok(kind: str, value: str) -> bool:
    return kind != "num" or bool(_NUM.match(value))


def _parse(spec: _Spec, args: list[str]) -> tuple[Flags, list[str]] | None:
    """Split `args` into flags and operands under `spec`. None = a flag is not allowed.

    Long flags must be spelled out: an abbreviation (`--out` for `--output`) is not on any
    list, so it rejects. A short-flag cluster (`-rn`, `-n5`) is read the way getopt reads
    it: letters until one that takes a value, which takes the rest of the token.
    """
    flags: Flags = []
    operands: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            operands.extend(args[i:])
            break
        if not tok.startswith("-") or tok == "-":
            operands.append(tok)
            continue
        if tok.startswith("--"):
            name, eq, value = tok[2:].partition("=")
            if name in spec.long and not eq:
                flags.append((name, None, None))
                continue
            if name in spec.long_opt:
                flags.append((name, value if eq else None, "text"))
                continue
            kinds = spec.long_val.get(name)
            if kinds is None or (eq and len(kinds) > 1):
                return None
            values = [value] if eq else []
            while len(values) < len(kinds):
                if i >= len(args):
                    break
                values.append(args[i])
                i += 1
            if len(values) < len(kinds):
                if kinds != ("commit",):  # git's `--contains` may end the command bare
                    return None
                flags.append((name, None, None))
                continue
            for kind, v in zip(kinds, values):
                if not _value_ok(kind, v):
                    return None
                flags.append((name, v, kind))
            continue
        body = tok[1:]
        if spec.numeric and body.isdigit():
            flags.append(("", body, "num"))
            continue
        j = 0
        while j < len(body):
            c = body[j]
            j += 1
            if c in spec.short:
                flags.append((c, None, None))
                continue
            if c in spec.short_opt:
                flags.append((c, body[j:] or None, "text"))
                break
            kind = spec.short_val.get(c)
            if kind is None:
                return None
            value = body[j:]
            if not value:
                if i >= len(args):
                    return None
                value = args[i]
                i += 1
            if not _value_ok(kind, value):
                return None
            flags.append((c, value, kind))
            break
    return flags, operands


def _has(flags: Flags, *names: str) -> bool:
    return any(name in names for name, _, _ in flags)


def _apply(spec: _Spec, args: list[str]) -> list[str] | None:
    """Read targets of a command under `spec`, or None when it is not read-only."""
    parsed = _parse(spec, args)
    if parsed is None:
        return None
    flags, operands = parsed
    if spec.max_operands is not None and len(operands) > spec.max_operands:
        return None
    if spec.check is not None and not spec.check(flags, operands):
        return None
    targets = [v for _, v, kind in flags if kind == "path" and v is not None]
    if spec.operands == "none":
        return targets
    if spec.operands == "first" and not _has(flags, *spec.pattern_flags):
        operands = operands[1:]
    return targets + operands


# -- sed, awk and jq programs ----------------------------------------------------------------
# sed: addresses (line, `$`, `/regex/`, ranges, `first~step`, `addr,+N`), `!`, `{ }`, and
# the commands that only print, delete, quit or edit the pattern space: p d q Q = l P D n N
# g G h H x z F, `s///` with the flags g p i I m M and a number, and `y///`. Commands
# that read or write files, run programs, branch, or take free text (a i c) are not in it.

_SED_SIMPLE = frozenset("pdqQ=lPDnNgGhHxzF")
_SED_S_FLAGS = frozenset("gpiImM0123456789")


def _sed_part(s: str, i: int, delim: str, regex: bool) -> int:
    """Index just past the closing `delim`, or -1. A bracket expression holding the
    delimiter is read differently by GNU and BSD sed, so it rejects."""
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == delim:
            return i + 1
        if regex and c == "[":
            j = i + 1
            if j < n and s[j] == "^":
                j += 1
            if j < n and s[j] == "]":
                j += 1
            while j < n and s[j] != "]":
                if s[j] == delim:
                    return -1
                if s[j] == "[" and j + 1 < n and s[j + 1] in ":.=":
                    end = s.find(s[j + 1] + "]", j + 2)
                    if end < 0:
                        return -1
                    j = end + 2
                    continue
                j += 1
            if j >= n:
                return -1
            i = j + 1
            continue
        i += 1
    return -1


def _sed_address(s: str, i: int, second: bool = False) -> int:
    """Index past one address at `i` (`i` itself when there is none), or -1."""
    n = len(s)
    if i >= n:
        return i
    c = s[i]
    if second and c in "+~":
        j = i + 1
        while j < n and s[j].isdigit():
            j += 1
        return j if j > i + 1 else -1
    if c.isdigit():
        while i < n and s[i].isdigit():
            i += 1
        if i < n and s[i] == "~":
            j = i + 1
            while j < n and s[j].isdigit():
                j += 1
            return j if j > i + 1 else -1
        return i
    if c == "$":
        return i + 1
    if c == "/" or (c == "\\" and i + 1 < n and s[i + 1] not in "\\\n"):
        delim = "/" if c == "/" else s[i + 1]
        i = _sed_part(s, i + (1 if c == "/" else 2), delim, regex=True)
        if i < 0:
            return -1
        while i < n and s[i] in "IM":
            i += 1
        return i
    return i


def _sed_script_ok(script: str) -> bool:
    s, n, i, depth = script, len(script), 0, 0
    while True:
        while i < n and s[i] in " \t;":
            i += 1
        if i >= n:
            return depth == 0
        start = i
        i = _sed_address(s, i)
        if i < 0:
            return False
        if i > start:
            while i < n and s[i] in " \t":
                i += 1
            if i < n and s[i] == ",":
                j = i + 1
                while j < n and s[j] in " \t":
                    j += 1
                i = _sed_address(s, j, second=True)
                if i <= j:
                    return False
        while i < n and s[i] in " \t":
            i += 1
        if i < n and s[i] == "!":
            i += 1
            while i < n and s[i] in " \t":
                i += 1
        if i >= n:
            return False  # an address with no command
        c = s[i]
        i += 1
        if c == "{":
            depth += 1
            continue
        if c == "}":
            depth -= 1
            if depth < 0 or i - 1 > start:
                return False
        elif c in "sy":
            if i >= n or s[i] in "\\\n":
                return False
            delim = s[i]
            i = _sed_part(s, i + 1, delim, regex=(c == "s"))
            if i < 0:
                return False
            i = _sed_part(s, i, delim, regex=False)
            if i < 0:
                return False
            if c == "s":
                while i < n and s[i] in _SED_S_FLAGS:
                    i += 1
        elif c in _SED_SIMPLE:
            if c in "qQl":
                while i < n and (s[i].isdigit() or s[i] in " \t"):
                    i += 1
        else:
            return False
        while i < n and s[i] in " \t":
            i += 1
        if i < n and s[i] not in ";}":
            return False


def _sed_check(flags: Flags, operands: list[str]) -> bool:
    scripts = [v for name, v, _ in flags if name in ("e", "expression") and v is not None]
    if not scripts:
        scripts = operands[:1]
    return bool(scripts) and all(_sed_script_ok(t) for t in scripts)


# awk: any program that prints, matches and computes. Rejected: output redirection and
# pipes (`>`, `>>`, `|`, `|&` — a `>` after a print statement starts is read as one),
# `getline`, `system`, the environment and process tables, rewriting ARGV/ARGC (which
# picks the files read), and gawk's `@` directives and indirect calls.
_AWK_BANNED = re.compile(r"\b(system|getline|ENVIRON|PROCINFO|ARGV|ARGC)\b|@")


def _awk_program_ok(program: str) -> bool:
    if _AWK_BANNED.search(program):
        return False
    if "|" in program.replace("||", ""):
        return False
    first_print = re.search(r"\bprint", program)
    return not (first_print and re.search(r">(?!=)", program[first_print.start():]))


def _awk_check(_flags: Flags, operands: list[str]) -> bool:
    return bool(operands) and _awk_program_ok(operands[0])


# jq filters cannot write or run anything; what is left out is reading modules from the
# library path and reading the environment.
_JQ_BANNED = re.compile(r"\b(import|include|env|ENV|modulemeta|get_search_list|get_prog_origin|get_jq_origin)\b")


def _jq_check(_flags: Flags, operands: list[str]) -> bool:
    return not (operands and _JQ_BANNED.search(operands[0]))


def _date_check(_flags: Flags, operands: list[str]) -> bool:
    return all(o.startswith("+") for o in operands)  # any other operand sets the clock


def _strings_check(_flags: Flags, operands: list[str]) -> bool:
    return not any(o.startswith("@") for o in operands)  # `@file` is read as more options


def _diff_check(flags: Flags, _operands: list[str]) -> bool:
    # A recursive diff follows symbolic links it meets inside the folders unless told not
    # to; the targets check only sees the operands named here.
    return not _has(flags, "r", "recursive") or _has(flags, "no-dereference")


def _command_check(flags: Flags, _operands: list[str]) -> bool:
    return _has(flags, "v", "V")  # only the lookup; `command NAME` runs NAME


# -- the commands ----------------------------------------------------------------------------

_GREP = _spec(
    "EFGPiyvwxcLloqsbHhnTZzaIrU",
    "e:text f:path m:num A:num B:num C:num d:text D:text",
    long="extended-regexp fixed-strings basic-regexp perl-regexp ignore-case no-ignore-case "
    "invert-match word-regexp line-regexp count files-with-matches files-without-match "
    "only-matching quiet silent no-messages byte-offset with-filename no-filename "
    "line-number initial-tab null null-data text recursive line-buffered",
    lvals="regexp:text file:path max-count:num after-context:num before-context:num "
    "context:num label:text binary-files:text devices:text directories:text exclude:text "
    "exclude-dir:text include:text exclude-from:path",
    lopt="color colour",
    numeric=True,
    operands="first",
    pattern_flags="e f regexp file",
)

_SHA = _spec("btz", long="binary text zero tag")

_SPECS: dict[str, _Spec] = {
    "ls": _spec(
        "aAbBcCdDfFgGhHiIklmnNopqQrRsStTuUvwxXZ1eOP@",
        long="all almost-all author escape ignore-backups directory dired file-type full-time "
        "group-directories-first no-group human-readable si dereference-command-line "
        "dereference-command-line-symlink-to-dir inode kibibytes literal numeric-uid-gid "
        "hide-control-chars show-control-chars quote-name recursive reverse size context zero",
        lvals="block-size:text format:text hide:text indicator-style:text ignore:text "
        "quoting-style:text sort:text time:text time-style:text tabsize:num width:num",
        lopt="color classify hyperlink",
    ),
    "cat": _spec(
        "AbeEnstTuv",
        long="show-all number-nonblank show-ends number squeeze-blank show-tabs show-nonprinting",
    ),
    "head": _spec(
        "qvz", "n:num c:num",
        long="quiet silent verbose zero-terminated", lvals="lines:num bytes:num", numeric=True,
    ),
    "tail": _spec(
        "qvzr", "n:num c:num",
        long="quiet silent verbose zero-terminated", lvals="lines:num bytes:num", numeric=True,
    ),
    "wc": _spec("cmlLw", long="bytes chars lines max-line-length words", lvals="total:text"),
    "nl": _spec(
        "p", "b:text d:text f:text h:text i:num l:num n:text s:text v:num w:num",
        long="no-renumber",
        lvals="body-numbering:text section-delimiter:text footer-numbering:text "
        "header-numbering:text line-increment:num join-blank-lines:num number-format:text "
        "number-separator:text starting-line-number:num number-width:num",
    ),
    "sort": _spec(
        "bdfgihMnRrVcCmsuz", "k:text t:text S:text",
        long="ignore-leading-blanks dictionary-order ignore-case general-numeric-sort "
        "ignore-nonprinting month-sort human-numeric-sort numeric-sort random-sort reverse "
        "version-sort merge stable unique zero-terminated debug",
        lvals="key:text field-separator:text buffer-size:text parallel:num sort:text batch-size:num",
        lopt="check",
    ),
    "uniq": _spec(
        "cdDiuz", "f:num s:num w:num",
        long="count repeated ignore-case unique zero-terminated",
        lvals="skip-fields:num skip-chars:num check-chars:num",
        lopt="all-repeated group",
        max_operands=1,  # a second operand is the output file
    ),
    "cut": _spec(
        "nsz", "b:text c:text d:text f:text",
        long="complement only-delimited zero-terminated",
        lvals="bytes:text characters:text delimiter:text fields:text output-delimiter:text",
    ),
    "tr": _spec(
        "cCdst", long="complement delete squeeze-repeats truncate-set1",
        operands="none", max_operands=2,
    ),
    "grep": _GREP,
    "egrep": _GREP,
    "fgrep": _GREP,
    "ugrep": _GREP,
    "rg": _spec(
        "acFHhiIlnNopqsSuUvwxbPV0",
        "A:num B:num C:num e:text f:path g:text m:num M:num r:text t:text T:text d:num E:text j:num",
        long="text count count-matches column files files-with-matches files-without-match "
        "fixed-strings hidden ignore-case invert-match json line-number no-line-number "
        "no-filename with-filename no-heading heading no-ignore no-ignore-vcs no-ignore-dot "
        "no-ignore-parent no-ignore-global no-ignore-exclude no-ignore-files no-messages "
        "only-matching pretty quiet case-sensitive smart-case stats type-list unrestricted "
        "multiline multiline-dotall word-regexp line-regexp null trim byte-offset pcre2 "
        "vimgrep no-config passthru crlf null-data one-file-system glob-case-insensitive "
        "line-buffered block-buffered no-unicode binary include-zero max-columns-preview "
        "sort-files no-require-git no-follow no-search-zip",
        lvals="after-context:num before-context:num context:num regexp:text file:path "
        "glob:text iglob:text max-count:num max-columns:num replace:text type:text "
        "type-not:text max-depth:num maxdepth:num encoding:text threads:num "
        "max-filesize:text ignore-file:path sort:text sortr:text type-add:text "
        "type-clear:text color:text colors:text context-separator:text "
        "field-match-separator:text field-context-separator:text path-separator:text "
        "engine:text",
        operands="first",
        pattern_flags="e f regexp file files type-list",
    ),
    "file": _spec(
        "bihLkrNnsE0", "F:text e:text P:text",
        long="brief mime mime-type mime-encoding dereference no-dereference keep-going raw "
        "no-pad no-buffer special-files print0 extension apple",
        lvals="separator:text exclude:text exclude-quiet:text parameter:text",
    ),
    "stat": _spec(
        "LftFnqrsxl", "c:text",
        long="dereference file-system terse", lvals="format:text printf:text cached:text",
    ),
    "du": _spec(
        "abcDHhklmPSsx0", "d:num B:text t:text X:path",
        long="all apparent-size bytes total dereference-args human-readable inodes count-links "
        "no-dereference null separate-dirs si summarize one-file-system",
        lvals="block-size:text max-depth:num threshold:text exclude:text exclude-from:path "
        "time-style:text",
        lopt="time",
    ),
    "df": _spec(
        "ahHiklPTt", "x:text B:text",
        long="all human-readable si inodes local portability print-type total no-sync",
        lvals="block-size:text type:text exclude-type:text", lopt="output",
    ),
    "pwd": _spec("LP", operands="none", max_operands=0),
    "echo": _spec("neE", operands="none", common=False),
    "printf": _spec(operands="none", common=False),
    "which": _spec("as", operands="none"),
    "whoami": _spec(max_operands=0),
    "id": _spec("ugGnrz", long="user group groups name real zero", operands="none"),
    "date": _spec(
        "uRj", "d:text", "I",
        long="utc universal rfc-email debug", lvals="date:text rfc-3339:text", lopt="iso-8601",
        operands="none", max_operands=1, check=_date_check,
    ),
    "uname": _spec(
        "asnrvmpio",
        long="all kernel-name nodename kernel-release kernel-version machine processor "
        "hardware-platform operating-system",
        max_operands=0,
    ),
    "basename": _spec("az", "s:text", long="multiple zero", lvals="suffix:text", operands="none"),
    "dirname": _spec("z", long="zero", operands="none"),
    "realpath": _spec(
        "emLPqsz",
        long="canonicalize-existing canonicalize-missing logical physical quiet strip "
        "no-symlinks zero",
        lvals="relative-to:path relative-base:path",
    ),
    "readlink": _spec(
        "femnqsvz",
        long="canonicalize canonicalize-existing canonicalize-missing no-newline quiet silent "
        "verbose zero",
    ),
    "true": _spec(max_operands=0, common=False),
    "false": _spec(max_operands=0, common=False),
    "command": _spec("vVp", operands="none", check=_command_check, common=False),
    "column": _spec("tx", "c:num s:text o:text"),
    "diff": _spec(
        "abBcdeEfhHiNnpPqrsStTuwyZ",
        "U:num C:num W:num F:text I:text x:text X:path S:text L:text D:text",
        long="normal brief report-identical-files recursive new-file unidirectional-new-file "
        "text ignore-space-change ignore-all-space ignore-blank-lines ignore-case "
        "ignore-tab-expansion ignore-trailing-space strip-trailing-cr side-by-side "
        "left-column suppress-common-lines show-c-function expand-tabs initial-tab minimal "
        "speed-large-files no-dereference suppress-blank-empty ignore-file-name-case "
        "no-ignore-file-name-case ed rcs",
        lvals="width:num show-function-line:text label:text exclude:text exclude-from:path "
        "ignore-matching-lines:text starting-file:text from-file:path to-file:path "
        "tabsize:num ifdef:text horizon-lines:num line-format:text old-line-format:text "
        "new-line-format:text unchanged-line-format:text old-group-format:text "
        "new-group-format:text changed-group-format:text unchanged-group-format:text "
        "palette:text",
        lopt="unified context color",
        check=_diff_check,
    ),
    "comm": _spec(
        "123iz", long="check-order nocheck-order zero-terminated total",
        lvals="output-delimiter:text", max_operands=2,
    ),
    "strings": _spec(
        "afdow", "n:num t:text e:text s:text T:text",
        long="all print-file-name data include-all-whitespace",
        lvals="bytes:num radix:text encoding:text output-separator:text target:text unicode:text",
        numeric=True, check=_strings_check,
    ),
    "md5sum": _SHA,
    "sha1sum": _SHA,
    "sha224sum": _SHA,
    "sha256sum": _SHA,
    "sha384sum": _SHA,
    "sha512sum": _SHA,
    "b2sum": _merge(_SHA, vals="l:num", lvals="length:num"),
    "shasum": _spec("btU0", "a:text", long="binary text UNIVERSAL 01 tag", lvals="algorithm:text"),
    "cksum": _spec("", "a:text l:num o:text", long="base64 raw tag untagged", lvals="algorithm:text length:num"),
    "sum": _spec("rs", long="sysv"),
    "hexdump": _spec("bcCdoxv", "e:text n:num s:text"),
    "od": _spec(
        "abcdfilosxvDeFHILOXh", "A:text j:text N:text S:text t:text", "w",
        long="output-duplicates traditional",
        lvals="address-radix:text skip-bytes:text read-bytes:text format:text endian:text",
        lopt="strings width",
    ),
    "jq": _spec(
        "rjacnesSCMRhV",
        long="raw-output join-output ascii-output compact-output null-input exit-status slurp "
        "sort-keys color-output monochrome-output raw-input tab seq stream stream-errors "
        "raw-output0 unbuffered args jsonargs",
        lvals="indent:num arg:text,text argjson:text,text slurpfile:text,path rawfile:text,path",
        operands="first", check=_jq_check,
    ),
    "sed": _spec(
        "nErsuz", "e:text l:num",
        long="quiet silent regexp-extended separate unbuffered null-data posix debug sandbox",
        lvals="expression:text line-length:num",
        operands="first", pattern_flags="e expression", check=_sed_check,
    ),
    "yamllint": _spec(
        "s", "c:path d:text f:text",
        long="strict no-warnings list-files",
        lvals="config-file:path config-data:text format:text",
    ),
    # Added in OPE-232: common read-only tools, each with its own flag list.
    "tree": _spec(
        "adfxpsugDFqNQCAJXvtcUrnihS", "L:num P:text I:text H:text T:text",
        long="noreport prune matchdirs gitignore ignore-case dirsfirst filesfirst du si inodes "
        "device nolinks",
        lvals="charset:text filelimit:num timefmt:text sort:text",
    ),
    "cmp": _spec(
        "blsx", "i:text n:text",
        long="print-bytes verbose quiet silent", lvals="ignore-initial:text bytes:text",
        max_operands=4,
    ),
    "tac": _spec("br", "s:text", long="before regex", lvals="separator:text"),
    "rev": _spec(),
    "fold": _spec("bs", "w:num", long="bytes spaces", lvals="width:num"),
    "paste": _spec("sz", "d:text", long="serial zero-terminated", lvals="delimiters:text"),
    "join": _spec(
        "iz", "a:text e:text j:text o:text t:text v:text 1:text 2:text",
        long="ignore-case check-order nocheck-order header zero-terminated", max_operands=2,
    ),
    "expand": _spec("i", "t:text", long="initial", lvals="tabs:text"),
    "unexpand": _spec("a", "t:text", long="all first-only", lvals="tabs:text"),
    "seq": _spec("w", "f:text s:text t:text", long="equal-width", lvals="format:text separator:text", operands="none"),
    "nproc": _spec(long="all", lvals="ignore:num", max_operands=0),
}
for _awk in ("awk", "gawk", "mawk", "nawk"):
    _SPECS[_awk] = _spec(
        "", "F:text v:text", lvals="field-separator:text assign:text",
        operands="first", check=_awk_check,
    )


# find(1): global options, starting points, then an expression of tests and actions. The
# allowed predicates select files or print to standard output; actions that delete, run
# programs, or write to a named file are not among them, nor are the options that follow
# symbolic links during the walk or read starting points from a file.
_FIND_GLOBAL_NO_VALUE = frozenset({"-H", "-L", "-P", "-E", "-X", "-d", "-s", "-x"})
_FIND_GLOBAL_OK = _FIND_GLOBAL_NO_VALUE - {"-L"}
_FIND_PRED0 = frozenset(
    "-print -print0 -ls -prune -quit -true -false -empty -readable -writable -executable "
    "-nouser -nogroup -depth -d -mount -xdev -noleaf -ignore_readdir_race "
    "-noignore_readdir_race -daystart -not -a -and -o -or -nowarn -warn ( ) ! ,".split()
)
_FIND_PRED1 = {
    **dict.fromkeys(
        "-name -iname -path -ipath -wholename -iwholename -regex -iregex -lname -ilname "
        "-regextype -type -xtype -size -perm -user -group -uid -gid -links -inum -mtime "
        "-mmin -atime -amin -ctime -cmin -used -maxdepth -mindepth -fstype -printf "
        "-context -Bmin -Btime -flags".split(),
        "text",
    ),
    **dict.fromkeys("-newer -anewer -cnewer -samefile -mnewer -Bnewer".split(), "path"),
}
_FIND_NEWER_XY = re.compile(r"^-newer[aBcm]([aBcmt])$")


def _find_analyze(args: list[str]) -> list[str] | None:
    targets: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in _FIND_GLOBAL_OK or (tok.startswith("-O") and tok[2:].isdigit() and tok != "-O"):
            i += 1
        elif tok == "-D" and i + 1 < len(args):
            i += 2  # debug options, never a path
        elif tok == "-f" and i + 1 < len(args):
            targets.append(args[i + 1])  # BSD: -f names a starting point
            i += 2
        elif tok in _FIND_GLOBAL_NO_VALUE:
            return None
        else:
            break
    while i < len(args) and not args[i].startswith("-") and args[i] not in _FIND_PRED0:
        targets.append(args[i])
        i += 1
    while i < len(args):
        tok = args[i]
        i += 1
        if tok in _FIND_PRED0:
            continue
        xy = _FIND_NEWER_XY.match(tok)
        kind = ("text" if xy.group(1) == "t" else "path") if xy else _FIND_PRED1.get(tok)
        if kind is None or i >= len(args):
            return None
        if kind == "path":
            targets.append(args[i])
        i += 1
    return targets


def _find_targets(args: list[str]) -> list[str]:
    """Starting points of `find`, with its global options skipped (for any find command,
    accepted or not).

    `find -L /etc -name shadow` reads /etc exactly like `find /etc -name shadow`, but
    treating every leading `-` token as the start of the predicates returned no targets
    for the prefixed form — so the session grant's root check passed vacuously.
    """
    out: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in _FIND_GLOBAL_NO_VALUE or (tok.startswith("-O") and tok != "-O"):
            i += 1
            continue
        if tok == "-D" and i + 1 < len(args):
            i += 2  # debugopts, never a path
            continue
        if tok == "-f" and i + 1 < len(args):
            out.append(args[i + 1])  # BSD: -f names the starting point
            i += 2
            continue
        break
    # Starting points end at the first predicate (any other `-` token). With none, find
    # defaults to `.`, which is inside every session root.
    while i < len(args) and not args[i].startswith("-"):
        out.append(args[i])
        i += 1
    return out


# xxd reads its options by their leading letters, so only these exact spellings pass. At
# most one operand: a second one is the output file.
_XXD_SWITCHES = frozenset(
    "-a -autoskip -b -bits -C -capitalize -E -EBCDIC -e -i -include -p -ps -postscript "
    "-plain -u -uppercase -d -h -help -v -version".split()
)
_XXD_VALUE = frozenset("-c -cols -g -groupsize -l -len -o -offset -s -seek -n -name".split())
_XXD_ATTACHED = re.compile(r"^-[cglo][0-9]+$|^-s[+-]?(0x)?[0-9a-fA-F]+$")


def _xxd_analyze(args: list[str]) -> list[str] | None:
    operands: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            operands.extend(args[i:])
            break
        if tok in _XXD_SWITCHES or _XXD_ATTACHED.match(tok):
            continue
        if tok in _XXD_VALUE:
            if i >= len(args):
                return None
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            return None
        operands.append(tok)
    return operands if len(operands) <= 1 else None


# -- git ---------------------------------------------------------------------------------------
# Each read-only subcommand has its own flag list. Every positional operand is returned as
# a read target: most are revisions or pathspecs inside the repository, which resolve
# inside the folders, but `git diff` also compares plain files named on the command line.
# Pretty-format placeholders that verify signatures (`%G…`) run an external program, so a
# format containing them rejects; so do the flags that run diff or textconv drivers.
# `git config` is not on the list: settings can hold credentials, so reading them asks.

_GIT_GLOBAL_FLAGS = frozenset({
    "--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs", "--glob-pathspecs",
    "--noglob-pathspecs", "--icase-pathspecs", "--no-replace-objects",
})


def _git_format_check(flags: Flags, _operands: list[str]) -> bool:
    return not any(
        name in ("format", "pretty") and v is not None and "%G" in v for name, v, _ in flags
    )


_GIT_REV = _spec(
    "pusRawzmcbtWEFPigqw", "n:num S:text G:text U:num l:num I:text", "MCBX",
    long="oneline graph no-decorate all abbrev-commit no-abbrev-commit relative-date reverse "
    "first-parent merges no-merges follow name-only name-status numstat shortstat summary "
    "patch no-patch full-diff left-right cherry-pick cherry-mark cherry boundary topo-order "
    "date-order author-date-order source parents children no-notes regexp-ignore-case "
    "extended-regexp fixed-strings perl-regexp basic-regexp all-match invert-grep "
    "ancestry-path simplify-by-decoration full-history dense sparse simplify-merges do-walk "
    "walk-reflogs expand-tabs no-expand-tabs no-color pickaxe-all pickaxe-regex no-renames "
    "ignore-all-space ignore-space-change ignore-blank-lines ignore-space-at-eol "
    "ignore-cr-at-eol function-context minimal patience histogram raw full-index binary "
    "no-prefix default-prefix cc combined-all-paths text check exit-code quiet "
    "compact-summary no-ext-diff no-textconv mailmap use-mailmap no-mailmap log-size "
    "show-linear-break clear-decorations show-pulls not cached staged merge-base no-index "
    "irreversible-delete single-worktree reflog no-relative",
    lvals="format:text date:text author:text committer:text grep:text since:text after:text "
    "until:text before:text max-count:num skip:num min-parents:num max-parents:num "
    "diff-filter:text unified:num src-prefix:text dst-prefix:text line-prefix:text "
    "stat-width:num stat-name-width:num stat-graph-width:num stat-count:num encoding:text "
    "word-diff-regex:text diff-algorithm:text decorate-refs:text decorate-refs-exclude:text "
    "exclude:text glob:text find-object:text since-as-filter:text inter-hunk-context:num "
    "anchored:text diff-merges:text ignore-matching-lines:text",
    lopt="decorate abbrev pretty stat dirstat dirstat-by-file color word-diff color-words "
    "find-renames find-copies relative no-walk branches tags remotes ignore-submodules "
    "submodule break-rewrites notes show-notes color-moved",
    numeric=True,
    check=_git_format_check,
    common=False,
)


def _git_list_check(flags: Flags, operands: list[str]) -> bool:
    return not operands or _has(flags, "l", "list")  # operands without --list create


_GIT_SPECS: dict[str, _Spec] = {
    "log": _GIT_REV,
    "show": _GIT_REV,
    "diff": _GIT_REV,
    "rev-list": _merge(
        _GIT_REV,
        long="count objects objects-edge no-object-names left-only right-only timestamp header "
        "bisect bisect-vars bisect-all",
        lvals="max-age:text min-age:text",
    ),
    "shortlog": _merge(_GIT_REV, short="nsec", sopt="w", long="numbered summary email committer", lvals="group:text", drop="n"),
    "status": _spec(
        "sbvz", sopt="u",
        long="short branch long verbose show-stash ahead-behind no-ahead-behind renames "
        "no-renames no-column null",
        lopt="porcelain untracked-files ignored ignore-submodules column find-renames",
        common=False,
    ),
    "blame": _spec(
        "bclfnsepwt", "L:text", "MC",
        long="root show-stats reverse first-parent porcelain line-porcelain incremental "
        "score-debug show-name show-number show-email color-lines color-by-age minimal",
        lvals="date:text encoding:text ignore-rev:text", lopt="abbrev",
        common=False,
    ),
    "describe": _spec(
        long="all tags contains exact-match debug long always first-parent",
        lvals="abbrev:num candidates:num match:text exclude:text", lopt="dirty broken",
        common=False,
    ),
    "rev-parse": _spec(
        "q",
        long="verify quiet git-dir show-toplevel show-prefix show-cdup is-inside-work-tree "
        "is-inside-git-dir is-bare-repository is-shallow-repository absolute-git-dir "
        "git-common-dir symbolic symbolic-full-name all revs-only no-revs flags no-flags sq "
        "not show-object-format show-superproject-working-tree shared-index-path "
        "local-env-vars show-ref-format sq-quote",
        lvals="since:text after:text until:text before:text default:text glob:text "
        "exclude:text disambiguate:text git-path:text prefix:text",
        lopt="short abbrev-ref branches tags remotes",
        common=False,
    ),
    "ls-files": _spec(
        "cdmoiskutvfz", "x:text X:path",
        long="cached deleted modified others ignored stage unmerged killed directory "
        "no-empty-directory eol deduplicate exclude-standard error-unmatch full-name "
        "recurse-submodules debug sparse resolve-undo",
        lvals="exclude:text exclude-from:path exclude-per-directory:text with-tree:text format:text",
        lopt="abbrev",
        common=False,
    ),
    "ls-tree": _spec(
        "drtlz", long="long name-only name-status object-only full-name full-tree",
        lvals="format:text", lopt="abbrev", common=False,
    ),
    "grep": _spec(
        "aiIrwvhHEGPFnlLzocpWq", "e:text f:path m:num A:num B:num C:num",
        long="text no-textconv ignore-case recursive no-recursive word-regexp invert-match "
        "full-name extended-regexp basic-regexp perl-regexp fixed-strings line-number column "
        "files-with-matches name-only files-without-match null only-matching count no-color "
        "break heading show-function function-context and or not all-match quiet cached "
        "untracked no-index exclude-standard no-exclude-standard recurse-submodules",
        lvals="max-depth:num threads:num regexp:text file:path max-count:num "
        "after-context:num before-context:num context:num",
        lopt="color",
        numeric=True,
        common=False,
    ),
    "cat-file": _spec(
        "tsep", long="batch-all-objects buffer unordered allow-unknown-type follow-symlinks",
        lopt="batch batch-check", common=False,
    ),
    "name-rev": _spec(
        long="tags all annotate-stdin name-only no-undefined always",
        lvals="refs:text exclude:text", common=False,
    ),
    "merge-base": _spec("a", long="all octopus is-ancestor independent fork-point", common=False),
    "count-objects": _spec("vH", long="verbose human-readable", common=False),
    "var": _spec("l", common=False),
    "check-ignore": _spec("qvzn", long="quiet verbose stdin non-matching no-index", common=False),
    "branch": _spec(
        "arvli",
        long="show-current list all remotes verbose no-color no-column ignore-case omit-empty no-abbrev",
        lvals="contains:commit no-contains:commit merged:commit no-merged:commit "
        "points-at:commit format:text sort:text",
        lopt="color column abbrev",
        check=_git_list_check,
        common=False,
    ),
    "tag": _spec(
        "li", sopt="n",
        long="list ignore-case omit-empty no-column",
        lvals="contains:commit no-contains:commit merged:commit no-merged:commit "
        "points-at:commit sort:text format:text",
        lopt="color column",
        check=_git_list_check,
        common=False,
    ),
}
_GIT_REMOTE_GET_URL = _spec(long="push all", max_operands=1, common=False)
_GIT_REMOTE_SHOW = _spec("n", check=lambda flags, _ops: _has(flags, "n"), common=False)  # -n: no network


def _git_leading(args: list[str]) -> tuple[list[str], int]:
    """The `-C DIR` operands among git's leading global flags, and where the subcommand
    starts. The accepted global flags are `-C` and a few that only change how pathspecs
    and output read; `-c`, `--config-env`, `--git-dir`, `--work-tree` and `--exec-path`
    are not on the list."""
    dirs: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "-C" and i + 1 < len(args):
            dirs.append(args[i + 1])
            i += 2
        elif args[i] in _GIT_GLOBAL_FLAGS:
            i += 1
        else:
            break
    return dirs, i


def _git_analyze(args: list[str]) -> list[str] | None:
    targets, i = _git_leading(args)
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1 :]
    if sub in ("--version", "version"):
        return targets if not rest else None
    found: list[str] | None
    if sub in _GIT_SPECS:
        found = _apply(_GIT_SPECS[sub], rest)
    elif sub == "stash":
        found = _apply(_GIT_REV, rest[1:]) if rest[:1] in (["list"], ["show"]) else None
    elif sub == "reflog":
        if rest[:1] in (["show"], ["list"]):
            rest = rest[1:]
        found = _apply(_GIT_REV, rest) if not rest or rest[0].startswith("-") or len(rest) == 1 else None
    elif sub == "remote":
        if not rest or rest in (["-v"], ["--verbose"]):
            found = []
        elif rest[0] == "get-url":
            found = _apply(_GIT_REMOTE_GET_URL, rest[1:])
        elif rest[0] == "show":
            found = _apply(_GIT_REMOTE_SHOW, rest[1:])
        else:
            found = None
    else:
        found = None
    return None if found is None else targets + found


# -- bash stages -----------------------------------------------------------------------------


def _strip_env(argv: list[str]) -> list[str] | None:
    """Drop leading `VAR=value` assignments; None when one is not a formatting variable."""
    i = 0
    while i < len(argv) and _ENV_ASSIGN.match(argv[i]):
        if argv[i].split("=", 1)[0] not in _SAFE_ENV:
            return None
        i += 1
    return argv[i:]


def _bash_stage(argv: list[str]) -> list[str] | None:
    """Read targets of one pipeline stage, or None when it is not read-only."""
    stripped = _strip_env(argv)
    if not stripped:
        return None
    head, args = stripped[0], stripped[1:]
    if "/" in head:
        return None  # path-invoked binaries can be anything; bare names only
    if head == "git":
        return _git_analyze(args)
    if head == "find":
        return _find_analyze(args)
    if head == "xxd":
        return _xxd_analyze(args)
    spec = _SPECS.get(head)
    return _apply(spec, args) if spec is not None else None


# -- PowerShell -------------------------------------------------------------------------------
# Only these cmdlets and aliases, with these parameters, are read-only; anything else gets
# a card (native programs other than git included). Parameter names are matched in full or
# by a listed alias, without case. Paths given to a provider other than the file system
# (`env:`, `HKLM:`, `Variable:`, `Cert:` …) are not file reads, so they reject.


@dataclass(frozen=True, eq=False)
class _PSSpec:
    params: dict[str, Optional[str]]  # name -> None (a switch) or a value kind
    aliases: dict[str, str]
    positional: tuple[str, ...]
    rest: Optional[str] = None  # takes every remaining positional argument


def _ps(params: str, positional: str = "", aliases: str = "", rest: Optional[str] = None) -> _PSSpec:
    table: dict[str, Optional[str]] = {}
    for item in params.split():
        name, _, kind = item.partition(":")
        table[name.lower()] = kind or None
    alias_map = {k.lower(): v.lower() for k, v in (a.split("=") for a in aliases.split())}
    return _PSSpec(table, alias_map, tuple(p.lower() for p in positional.split()), rest)


_PS_PATH_ALIASES = "PSPath=LiteralPath LP=LiteralPath"
_PS_SPECS: dict[str, _PSSpec] = {
    "get-childitem": _ps(
        "Path:path LiteralPath:path Filter:text Include:text Exclude:text Recurse Depth:num "
        "Force Name File Directory Hidden ReadOnly System Attributes:text",
        "Path Filter",
        _PS_PATH_ALIASES + " s=Recurse r=Recurse ad=Directory d=Directory af=File ah=Hidden "
        "h=Hidden ar=ReadOnly as=System",
    ),
    "get-content": _ps(
        "Path:path LiteralPath:path TotalCount:num Tail:num Raw Encoding:text Delimiter:text "
        "ReadCount:num Force Filter:text Include:text Exclude:text AsByteStream",
        "Path",
        _PS_PATH_ALIASES + " First=TotalCount Head=TotalCount Last=Tail",
    ),
    "select-string": _ps(
        "Pattern:text Path:path LiteralPath:path SimpleMatch CaseSensitive Context:text "
        "AllMatches NotMatch List Quiet Raw NoEmphasis Encoding:text Include:text "
        "Exclude:text Culture:text",
        "Pattern Path",
        _PS_PATH_ALIASES,
    ),
    "measure-object": _ps(
        "Property:text Line Word Character IgnoreWhiteSpace Sum Average Maximum Minimum "
        "StandardDeviation AllStats",
        "Property",
    ),
    "get-location": _ps(""),
    "test-path": _ps(
        "Path:path LiteralPath:path PathType:text IsValid Filter:text Include:text "
        "Exclude:text NewerThan:text OlderThan:text",
        "Path",
        _PS_PATH_ALIASES + " Type=PathType",
    ),
    "resolve-path": _ps("Path:path LiteralPath:path Relative RelativeBasePath:path", "Path", _PS_PATH_ALIASES),
    "get-filehash": _ps("Path:path LiteralPath:path Algorithm:text", "Path", _PS_PATH_ALIASES),
    "sort-object": _ps("Property:text Descending Unique CaseSensitive Top:num Bottom:num Stable Culture:text", "Property"),
    "select-object": _ps(
        "Property:text ExcludeProperty:text ExpandProperty:text Unique First:num Last:num "
        "Skip:num SkipLast:num Index:num",
        "Property",
    ),
    "write-output": _ps("NoEnumerate", rest="text"),
}
_PS_ALIASES = {
    "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem",
    "cat": "get-content", "type": "get-content", "gc": "get-content",
    "sls": "select-string", "measure": "measure-object",
    "pwd": "get-location", "gl": "get-location",
    "rvpa": "resolve-path", "sort": "sort-object", "select": "select-object",
    "echo": "write-output", "write": "write-output",
}
# Common parameters every cmdlet takes. The ones that store results in variables, and the
# error actions that stop for input, are not on the list.
_PS_COMMON = {"erroraction": "action", "ea": "action", "warningaction": "action", "wa": "action",
              "informationaction": "action", "infa": "action", "verbose": None, "vb": None,
              "debug": None, "db": None}
_PS_ACTIONS = frozenset({"silentlycontinue", "continue", "stop", "ignore"})
_PS_PROVIDER = re.compile(r"^[^/:]{2,}:|::")


def _ps_value_ok(kind: Optional[str], value: str) -> bool:
    if kind == "num":
        return bool(re.match(r"^-?[0-9]+$", value))
    if kind == "action":
        return value.lower() in _PS_ACTIONS
    if kind == "path":
        return not _PS_PROVIDER.search(value)
    return True


# A recursive listing may follow a directory junction out of the folders. Later stages
# that only see names and sizes are fine; a stage that reads the listed files asks.
_PS_NAME_ONLY = frozenset({"select-object", "sort-object", "measure-object", "write-output"})


def _ps_recursive_listing(argv: list[str]) -> bool:
    head = argv[0].lower()
    if _PS_ALIASES.get(head, head) != "get-childitem":
        return False
    aliases = _PS_SPECS["get-childitem"].aliases
    for tok in argv[1:]:
        if tok.startswith("-") and len(tok) > 1:
            name = tok[1:].partition(":")[0].lower()
            if aliases.get(name, name) in ("recurse", "depth"):
                return True
    return False


def _powershell_pipeline_ok(stages: list[list[str]]) -> bool:
    for i, stage in enumerate(stages):
        if _ps_recursive_listing(stage):
            later = (s[0].lower() for s in stages[i + 1 :])
            return all(_PS_ALIASES.get(h, h) in _PS_NAME_ONLY for h in later)
    return True


def _powershell_stage(argv: list[str]) -> list[str] | None:
    head, args = argv[0].lower(), argv[1:]
    if head == "git":
        return _git_analyze(args)
    spec = _PS_SPECS.get(_PS_ALIASES.get(head, head))
    if spec is None:
        return None
    targets: list[str] = []
    bound: set[str] = set()
    positionals: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            positionals.extend(args[i:])
            break
        if not tok.startswith("-") or len(tok) == 1 or re.match(r"^-[0-9]", tok):
            positionals.append(tok)
            continue
        name, colon, value = tok[1:].partition(":")
        key = spec.aliases.get(name.lower(), name.lower())
        if key in spec.params:
            kind = spec.params[key]
        elif key in _PS_COMMON:
            kind = _PS_COMMON[key]
        else:
            return None
        if key in bound:
            return None
        bound.add(key)
        if kind is None:
            if colon:
                return None
            continue
        if not colon:
            if i >= len(args):
                return None
            value = args[i]
            i += 1
        if not _ps_value_ok(kind, value):
            return None
        if kind == "path":
            targets.append(value)
    slots = [p for p in spec.positional if p not in bound]
    for value in positionals:
        if slots:
            kind = spec.params[slots.pop(0)]
        elif spec.rest is not None:
            kind = spec.rest
        else:
            return None
        if not _ps_value_ok(kind, value):
            return None
        if kind == "path":
            targets.append(value)
    return targets


def _powershell_plain(command: str) -> bool:
    """A command PowerShell runs as written: no construct that evaluates or expands. (A
    leading `.` or `&` that would run a script is rejected too: `&` here, and `.` because
    it is not a read-only command.)"""
    if any(c in _POWERSHELL_UNSAFE for c in command):
        return False
    return "--%" not in command.split()


def _powershell_paths(command: str) -> str:
    """PowerShell has no backslash escape: a backslash is a path separator. The tokenizer
    below is bash-style, so hand it forward slashes, which PowerShell paths accept too."""
    return command.replace(chr(92), "/")


# -- entry points ------------------------------------------------------------------------------


def _analyze(command: str, shell: str) -> tuple[list[list[str]], list[list[str] | None]] | None:
    """Pipeline stages and, per stage, its read targets (None: not read-only).
    None overall when the command does not even tokenize as a plain pipeline."""
    if shell not in SHELLS:
        return None
    command = str(command or "")
    if shell == "powershell":
        if not _powershell_plain(command):
            return None
        command = _powershell_paths(command)
    stages = _stages(command)
    if stages is None:
        return None
    if shell == "powershell":
        if any(_ENV_ASSIGN.match(s[0]) for s in stages):
            return None  # not an assignment in PowerShell: it would be a command name
        if not _powershell_pipeline_ok(stages):
            return stages, [None] * len(stages)
        return stages, [_powershell_stage(s) for s in stages]
    return stages, [_bash_stage(s) for s in stages]


def is_readonly_command(command: str, shell: str = "bash") -> bool:
    """True iff `command` is a single command or pure pipeline of local read-only stages,
    as the given shell (`bash` or `powershell`) would run it."""
    analyzed = _analyze(command, shell)
    return analyzed is not None and all(t is not None for t in analyzed[1])


# -- read targets (OPE-130) ------------------------------------------------------------
# The classifier above decides what a command may DO. It says nothing about what the
# command may READ, so a session grant meant for "stop asking about my project files" also
# covered ~/.aws/credentials, ~/.ssh/id_rsa and OpenWorker's own secrets file. These
# helpers name the file operands so the caller can hold them to the session's roots — the
# same shape as the fix for browser uploads in OPE-122.
#
# Extracting read targets from arbitrary shell is not possible in general; it is tractable
# here only because the classifier has already narrowed the input to the verbs above, and
# since OPE-232 the same flag parse that accepts a command names its files.

# `head -n 5`, `cut -f 1`: a bare number is a count, never a file worth scoping.
_NUMERIC = re.compile(r"^[0-9]+([,:.-][0-9]+)*[a-zA-Z]?$")


def _flag_values(args: list[str]) -> list[str]:
    """Values carried inside flag tokens (`--file=PATH`, `-Path:PATH`, `-f/etc/hosts`)."""
    out: list[str] = []
    for tok in args:
        if not tok.startswith("-") or tok in {"-", "--"}:
            continue
        if "=" in tok:
            value = tok.split("=", 1)[1]
        elif ":" in tok:
            value = tok.split(":", 1)[1]
        elif not tok.startswith("--") and len(tok) > 2 and (
            "/" in tok[2:] or "\\" in tok[2:] or tok[2:].startswith(("~", "."))
        ):
            value = tok[2:]
        else:
            continue
        if value and not _NUMERIC.match(value):
            out.append(value)
    return out


def _fallback_targets(argv: list[str]) -> list[str]:
    """For a stage the classifier rejects: every operand-looking token, erring wide."""
    stripped = _strip_env(argv) or argv
    head, args = (stripped[0], stripped[1:]) if stripped else ("", [])
    if head == "find":
        return _find_targets(args)
    return _flag_values(args) + [t for t in args if not t.startswith("-") and not _NUMERIC.match(t)]


def git_repo_dirs(command: str, shell: str = "bash") -> list[str]:
    """The folder each git stage of an accepted command runs in: its `-C` operands joined,
    or `.`. Empty when the command is not read-only. The engine checks the project's git
    settings there before the grant covers the command (coworker/git_settings.py)."""
    analyzed = _analyze(command, shell)
    if analyzed is None or any(t is None for t in analyzed[1]):
        return []
    out: list[str] = []
    for stage in analyzed[0]:
        argv = stage if shell == "powershell" else _strip_env(stage)
        if not argv:
            continue
        head = argv[0].lower() if shell == "powershell" else argv[0]
        if head == "git":
            dirs, _ = _git_leading(argv[1:])
            out.append(os.path.join(*dirs) if dirs else ".")
    return out


def read_targets(command: str, shell: str = "bash") -> list[str]:
    """Every file operand `command` would read, for scoping against the session's roots.

    Only meaningful for commands `is_readonly_command` accepts — it assumes that vetting.
    For those, every operand and every path an allowed flag carries is named. Errs toward
    naming MORE: an extra one costs a manual approval, a missed one is an unscoped read.
    """
    analyzed = _analyze(command, shell)
    if analyzed is None:
        return []
    stages, found = analyzed
    out: list[str] = []
    for stage, targets in zip(stages, found):
        out.extend(targets if targets is not None else _fallback_targets(stage))
    return out
