"""The steps of a plan the user approved in auto-approve, and exact matching (OPE-237).

In auto-approve the agent can propose a plan as a list of steps. Once the user approves
it, every later action in the same request is checked here against the steps, by code.
Only an exact match counts:

* ``write``  - a file tool writing exactly that file (every file, for a patch);
* ``delete`` - a plain delete of exactly that file (``rm``/``del``/``Remove-Item`` with no
  pattern, no recursion, nothing chained), or a patch's ``Delete File`` of it;
* ``run``    - exactly that command, ignoring only unquoted, unescaped spacing;
* ``send``   - that file going to exactly that host (a network command whose every URL is
  that host, with no user-info in the URL, that reads exactly that file and nothing
  else, with nothing piped or chained), or an exact message body to that recipient
  through a supported message tool. A filename mentioned in a web URL is not a send.

Paths in a shell command are judged from the shell's current folder when the engine
knows it (``shell_base``), and nothing matches when it cannot. A step naming a protected
in-project file (git hooks, CI configs, the workspace policy) never matches: those files
always reach a person.

When the action is exactly a step, the reviewer gets one fixed line
(``review_facts.PLAN_STEP``). Any other action gets no plan line at all, so a plan can
only add evidence for its own steps and never makes anything else harder. The reviewer
never sees the plan's text: the steps are written by the agent, and a step can only ever
vouch for itself.
"""

from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .review_facts import shell_reaches_network

KINDS = ("write", "delete", "run", "send")
MAX_STEPS = 30
MAX_TEXT = 1000
MAX_NOTE = 200

_FILE_TOOLS = {"write_file", "replace_in_file", "apply_patch", "apply_unified_diff"}
_PATCH_DELETE = re.compile(r"^\*\*\* Delete File: (.+)$", re.MULTILINE)
# Anything that makes a command more than "delete these named files": chaining, pipes,
# redirection, substitution, globbing, variables.
_SHELL_META = re.compile(r"[;&|`$<>(){}*?\[\]\n\r]")
_DELETE_COMMANDS = {"rm", "del", "erase", "remove-item", "ri"}
_DELETE_FLAGS = {"-f", "--force", "-force", "-path", "-literalpath", "/q", "/f"}
# A send step's target is a file when it has an extension (a message is not a file).
_FILE_TARGET = re.compile(r"^[^\s<>|&;`$\"']+\.[A-Za-z0-9]{1,8}$")
# Shown as risky on the card: something besides the named program runs or decides.
_RISKY_META = re.compile(r"[;&|<>`$\n\r]")
_RISKY_PROGRAMS = {
    "sudo", "doas", "runas", "su", "crontab", "schtasks", "at", "launchctl", "mkfs",
    "dd", "format", "shutdown", "reboot", "diskpart", "chown", "chmod", "icacls", "takeown",
}
_RISKY_GIT = {"clean", "push", "restore", "filter-branch", "filter-repo"}
_RISKY_GIT_WITH = {
    ("reset", "--hard"), ("checkout", "--"), ("checkout", "."), ("branch", "-d"),
    ("branch", "--delete"), ("stash", "drop"), ("stash", "clear"),
}
# A program and a verb that publishes, deploys or uploads: the work leaves the machine.
_RISKY_VERBS = {
    ("npm", "publish"), ("yarn", "publish"), ("pnpm", "publish"), ("npm", "deprecate"),
    ("twine", "upload"), ("cargo", "publish"), ("gem", "push"), ("poetry", "publish"),
    ("docker", "push"), ("podman", "push"), ("gh", "release"), ("gh", "pr"), ("glab", "mr"),
    ("aws", "s3"), ("gsutil", "cp"), ("gsutil", "rsync"), ("az", "storage"),
    ("kubectl", "apply"), ("kubectl", "delete"), ("terraform", "apply"),
    ("terraform", "destroy"), ("pulumi", "up"), ("vercel", "deploy"), ("netlify", "deploy"),
    ("fly", "deploy"), ("heroku", "push"),
}
# Shell tokens that name the local file a network client sends. curl's short flags are
# case-sensitive (-F is a form, -f is --fail; -T uploads, -t is a telnet option); the long
# names, and PowerShell's -InFile, are not.
_UPLOAD_LONG = {"--upload-file", "-infile", "--post-file", "--body-file"}
_DATA_LONG = {"--data", "--data-binary", "--data-ascii", "--data-urlencode", "--json"}
_FORM_LONG = {"--form"}
_SHORT = {"T": "upload", "d": "data", "F": "form"}
_STDIN = {"-", "@-"}
# More than one upload in a command: chaining, pipes, redirection, substitution. Globs in
# the URL (a query string) are fine; globs in a file name are refused in `_sent_files`.
_SEND_META = re.compile(r"[;&|`$<>(){}\n\r]")
_GLOB = re.compile(r"[*?\[]")
# "httpbin.org/post", "httpbin.org:443": a host with a path or a port, no scheme.
_HOST_WITH_TAIL = re.compile(r"^([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)(?::\d+)?(?:[/?#].*)?$")


def _destination(raw: str) -> str:
    """A send step's destination as it is checked: a URL or host with a path or port
    becomes the bare host (agents write "https://httpbin.org/post"); a connector
    recipient ("slack:T1/C9", an email address) keeps its shape. Lower case."""
    text = raw.strip().lower()
    if "://" in text:
        return (urlsplit(text).hostname or "").strip(".")
    match = _HOST_WITH_TAIL.match(text)
    return match.group(1) if match else text


@dataclass(frozen=True)
class PlanStep:
    kind: str
    target: str
    destination: str = ""  # a send's destination; for a run that reaches the network, its host(s)
    note: str = ""  # the agent's few words for the user; never part of the match

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "target": self.target,
            **({"destination": self.destination} if self.destination else {}),
            **({"note": self.note} if self.note else {}),
        }


def normalize_steps(raw: Any) -> tuple[list[PlanStep], list[str]]:
    """The well-formed steps, and one line per entry dropped, saying why (for the agent).

    A write or delete target is one file path; a run target is the exact command, and when
    it reaches the network its URL hosts are shown on the card and a URL with a login before
    "@" is refused (the text before "@" is not the host). Live 28 Oct 2026: the agent wrote
    "cd src && rm old-notes.txt" as a delete step and a deceptive URL as a run step."""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["`steps` must be a list of {kind, target[, destination][, note]}"]
    steps: list[PlanStep] = []
    dropped: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            dropped.append(f"{str(entry)[:60]!r}: a step is an object with kind and target")
            continue
        kind = str(entry.get("kind") or "").strip().lower()
        target = str(entry.get("target") or "").strip()
        note = str(entry.get("note") or "").strip()[:MAX_NOTE]
        destination = _destination(str(entry.get("destination") or "")) if kind == "send" else ""
        label = f"{kind} {target[:60]!r}"
        reason = ""
        if kind not in KINDS:
            reason = "kind must be write, delete, run or send"
        elif not target:
            reason = "a step needs a target"
        elif len(target) > MAX_TEXT or len(destination) > MAX_TEXT:
            reason = "too long"
        elif kind in ("write", "delete") and _looks_like_command(target):
            reason = f"a {kind} step's target is one file path, not a command (use a run step for a command)"
        elif kind == "send" and not destination:
            reason = "a send step needs a destination (host or recipient)"
        elif kind == "run" and shell_reaches_network(target):
            hosts = _url_hosts(_words(target))
            if hosts is None:
                reason = "the URL has a login before '@' or no host; write the destination host plainly"
            else:
                destination = ", ".join(sorted(hosts))
        if not reason and len(steps) >= MAX_STEPS:
            reason = f"more than {MAX_STEPS} steps"
        if reason:
            dropped.append(f"{label}: {reason}")
            continue
        steps.append(PlanStep(kind, target, destination, note))
    return steps, dropped


def _looks_like_command(target: str) -> bool:
    """A write/delete target that is really a command line."""
    if _SEND_META.search(target):
        return True
    words = target.split()
    return len(words) > 1 and words[0].lower() in _DELETE_COMMANDS | {"cd", "rmdir", "rd", "mv", "move", "cp", "copy"}


def _words(command: str) -> list[str]:
    try:
        return [w.strip("'\"") for w in shlex.split(command, posix=False)]
    except ValueError:
        return command.split()


def step_is_risky(step: PlanStep, resolve: Optional[Callable[[str], Path]] = None) -> bool:
    """Shown as risky on the card: sending, deleting, pushing or publishing, a command
    that deletes, reaches the network, runs inline code, chains, pipes or redirects, or a
    write or delete of a protected in-project file (with `resolve`). Writes outside the
    workspace are marked by the engine, which knows the workspace."""
    if step.kind in ("delete", "send"):
        return True
    if step.kind == "write":
        return resolve is not None and _protected(resolve(step.target))
    if step.kind == "run":
        from .permissions import _DANGEROUS_FLAGS, _INLINE_CODE_FLAGS, _INTERPRETERS

        words = [w.lower() for w in _words(step.target)]
        if not words:
            return False
        if _RISKY_META.search(step.target) or shell_reaches_network(step.target):
            return True
        programs = {_program(w) for w in words}
        if programs & (_DELETE_COMMANDS | {"rmdir", "rd"}) or _program(words[0]) in _RISKY_PROGRAMS:
            return True
        if any(w in _DANGEROUS_FLAGS for w in words):
            return True
        inline = {f.lower() for f in _INLINE_CODE_FLAGS}
        if programs & _INTERPRETERS and any(w in inline for w in words):
            return True
        for i, word in enumerate(words):
            rest = words[i + 1 :]
            program = _program(word)
            if program == "git" and (
                set(rest) & _RISKY_GIT or any(a in rest and b in rest for a, b in _RISKY_GIT_WITH)
            ):
                return True
            if any(program == prog and verb in rest for prog, verb in _RISKY_VERBS):
                return True
    return False


def _program(word: str) -> str:
    name = Path(word).name
    return name[:-4] if name.endswith(".exe") else name


def _protected(path: Path) -> bool:
    from .permissions import _is_protected_in_project

    return _is_protected_in_project(path)


def _key(path: Path) -> str:
    text = path.as_posix()
    return text.casefold() if sys.platform in ("win32", "darwin") else text


def _host(host: str) -> str:
    host = host.lower().strip().strip(".")
    return host[4:] if host.startswith("www.") else host


def _norm_command(command: Any) -> str:
    from .readonly import _raw_tokens

    text = str(command or "")
    # Newlines and PowerShell's backtick escape are significant. For simple single-line
    # commands normalize only unquoted, unescaped whitespace, retaining quoted bytes.
    if any(c in text for c in "\r\n`"):
        return text
    tokens = _raw_tokens(text)
    return " ".join(tokens) if tokens is not None else text


def _plain_delete_paths(command: str) -> Optional[list[str]]:
    """The files a command deletes when it is only a plain delete of named files."""
    if _SHELL_META.search(command):
        return None
    words = _words(command)
    if not words or words[0].lower() not in _DELETE_COMMANDS:
        return None
    paths = []
    for word in words[1:]:
        if word.lower() in _DELETE_FLAGS:
            continue
        if word.startswith("-"):
            return None  # -r, -rf, -Recurse …: not a plain file delete
        paths.append(word)
    return paths or None


def _covered(paths: Iterable[str], approved: set[str], resolve: Callable[[str], Path]) -> bool:
    keys = [_key(resolve(p)) for p in paths]
    return bool(keys) and all(k in approved for k in keys) and not any(_protected(resolve(p)) for p in paths)


def _url_hosts(words: list[str]) -> Optional[set[str]]:
    """The host of every URL in a command, or None when a URL carries user-info (the text
    before "@" is a login, not the host: "https://httpbin.org:443@evil.example/" goes to
    evil.example) or has no host."""
    hosts: set[str] = set()
    for word in words:
        if "://" not in word:
            continue
        try:
            parts = urlsplit(word)
        except ValueError:
            return None
        if parts.username is not None or parts.password is not None or not parts.hostname:
            return None
        hosts.add(_host(parts.hostname))
    return hosts


def _sent_files(words: list[str]) -> Optional[list[str]]:
    """The local files a network command sends (curl, wget and PowerShell forms), or None
    when it sends standard input, a glob, or something that cannot be read as a file."""
    files: list[str] = []
    is_curl = bool(words) and _program(words[0]).lower() == "curl"
    skip = False
    for i, word in enumerate(words):
        if skip:
            skip = False
            continue
        kind, attached = "", ""
        low = word.lower()
        name, _, rest = low.partition("=")
        if name in _UPLOAD_LONG | _DATA_LONG | _FORM_LONG:
            kind = "upload" if name in _UPLOAD_LONG else "data" if name in _DATA_LONG else "form"
            attached = word.partition("=")[2] if _ else ""
        elif is_curl and re.fullmatch(r"-[A-Za-z]+.*", word):
            # Short flags, possibly combined ("-sF", "-d@file"): the first letter that
            # takes a value takes the rest of the word, or the next word.
            for j, ch in enumerate(word[1:], 1):
                if ch in _SHORT:
                    kind, attached = _SHORT[ch], word[j + 1 :]
                    break
                if not ch.isalpha():
                    break
        if kind and not attached:
            if i + 1 >= len(words):
                return None
            attached, skip = words[i + 1], True
        if kind == "upload":
            if attached in _STDIN:
                return None
            files.append(attached)
        elif kind == "data":
            if "@" in attached:
                name = attached.split("@", 1)[1]
                if not name or name == "-":
                    return None
                files.append(name)
        elif kind == "form":
            if "=@" in attached or "=<" in attached:
                name = re.split(r"=[@<]", attached, 1)[1]
                if not name or name == "-":
                    return None
                files.append(name)
        elif word.startswith("@"):
            if word in _STDIN:
                return None
            files.append(word[1:])
    if any(_GLOB.search(f) for f in files):
        return None  # the shell may expand it to any number of files
    return files


def match_step(
    steps: list[PlanStep],
    tool_name: str,
    arguments: dict[str, Any],
    *,
    resolve: Callable[[str], Path],
    egress_host: Optional[Callable[[str, dict[str, Any]], str]] = None,
    shell_base: Optional[Callable[[], Optional[str]]] = None,
) -> bool:
    """True only when the action is exactly one of the approved steps. `resolve` maps a
    step's or a file tool's path to its real place. `shell_base`, when given, returns the
    shell's current folder, from which a command's paths are judged, or None when the
    engine cannot know it: then no shell path matches."""
    if not steps:
        return False
    args = arguments if isinstance(arguments, dict) else {}
    try:
        writes = {_key(resolve(s.target)) for s in steps if s.kind == "write"}
        deletes = {_key(resolve(s.target)) for s in steps if s.kind == "delete"}
        sends = [s for s in steps if s.kind == "send"]

        if tool_name in _FILE_TOOLS:
            from .permissions import write_paths

            paths, located = write_paths(tool_name, args)
            if not located:
                return False
            removed = _PATCH_DELETE.findall(str(args.get("patch", ""))) if tool_name == "apply_patch" else []
            removed = [p.strip() for p in removed]
            changed = [p for p in paths if p not in removed]
            return (not changed or _covered(changed, writes, resolve)) and (
                not removed or _covered(removed, deletes, resolve)
            ) and bool(changed or removed)

        if tool_name == "run_shell":
            command = str(args.get("command", ""))
            same_run = any(s.kind == "run" and _norm_command(s.target) == _norm_command(command) for s in steps)
            deleted = _plain_delete_paths(command)
            if deleted is not None:
                # A plain delete is judged as a delete even when listed as a run step, so
                # a protected file is never deleted on a plan's word.
                shell_resolve = _shell_resolver(resolve, shell_base)
                if shell_resolve is None or any(_protected(shell_resolve(p)) for p in deleted):
                    return False
                return same_run or _covered(deleted, deletes, shell_resolve)
            if same_run:
                # A run step that reaches the network was checked at proposal time; a URL
                # with a login before "@" never matches, whatever the plan says.
                return not shell_reaches_network(command) or _url_hosts(_words(command)) is not None
            if shell_reaches_network(command):
                if not sends or _SEND_META.search(command):
                    return False  # piped, chained or redirected: more than the one upload
                words = _words(command)
                hosts = _url_hosts(words)
                files = _sent_files(words)
                shell_resolve = _shell_resolver(resolve, shell_base)
                if not hosts or len(hosts) != 1 or not files or shell_resolve is None:
                    return False
                host = next(iter(hosts))
                approved = {
                    _key(resolve(s.target))
                    for s in sends
                    if _host(s.destination) == host and _FILE_TARGET.match(s.target)
                }
                return all(_key(shell_resolve(f)) in approved for f in files)
            return False

        if not sends:
            return False
        if args.get("url"):
            # Mentioning a file in a URL does not establish what data is being sent.
            return False

        from .connectors.tool_defs import standing_target_for

        recipient = str(standing_target_for(tool_name, args) or "").strip().lower()
        if not recipient:
            return False  # e.g. a browser upload: where it goes is not in the action
        # Only message tools whose complete payload we can compare. A prose label like
        # "weekly summary" is not authority for arbitrary text to the same recipient.
        message_fields = {
            "send_message": ("text", {"target", "text"}),
            "slack_post_message": ("text", {"workspace", "channel", "thread_ts", "text"}),
            "telegram_send_message": ("text", {"chat_id", "thread_id", "text"}),
            "discord_send_message": ("content", {"channel_id", "content"}),
            "whatsapp_send_message": ("text", {"to", "text"}),
        }
        spec = message_fields.get(tool_name)
        if spec is None or set(args) - spec[1]:
            return False
        payload = args.get(spec[0])
        return any(
            s.destination.strip().lower() == recipient
            and isinstance(payload, str) and payload == s.target
            for s in sends
        )
    except Exception:  # noqa: BLE001 - a step that cannot be checked does not match
        return False


def _shell_resolver(
    resolve: Callable[[str], Path], shell_base: Optional[Callable[[], Optional[str]]]
) -> Optional[Callable[[str], Path]]:
    """How a shell command's relative paths resolve: from the shell's current folder when
    the engine tracks it, else like a step's path. None when the folder cannot be known."""
    if shell_base is None:
        return resolve
    cwd = shell_base()
    if not cwd:
        return None
    base = Path(cwd).expanduser().resolve()

    def from_shell(path: str) -> Path:
        p = Path(path).expanduser()
        return p.resolve() if p.is_absolute() else (base / p).resolve()

    return from_shell


def steps_text(shown: list[dict[str, Any]]) -> str:
    """The steps as plain lines, for the Inbox body and its Slack mirror."""
    lines = []
    for step in shown or []:
        if not isinstance(step, dict):
            continue
        line = f"- {step.get('kind', '')} {step.get('target', '')}"
        if step.get("destination"):
            line += f" -> {step['destination']}"
        if step.get("risky"):
            line += " (risky)"
        if step.get("note"):
            line += f"\n  {step['note']}"
        lines.append(line)
    return "Steps:\n" + "\n".join(lines) if lines else ""
