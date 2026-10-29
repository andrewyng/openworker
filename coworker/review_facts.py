"""Facts the engine computes for the Auto-Approve reviewer (OPE-235).

Models are unreliable at checks that code gets right: whether `github.com.evil.example`
is GitHub, whether a file has uncommitted changes, whether a file name looks like a
credential. The engine works these out from the session record and the action, and the
reviewer gets them as short lines from a FIXED vocabulary: no host names, paths or any
other text the agent controls ever travels in a fact, so the reviewer's
never-read-untrusted-content rule holds.

Every fact fails toward "could not be determined", never toward a reassuring value, and
none of them is a permission: a destination the user named can still be misused.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import provenance, public_suffix
from .risk import RiskClass, classify
from .workspace_safety import (
    IGNORED,
    SECRET_SCAN_MAX_BYTES,
    TRACKED_CLEAN,
    TRACKED_MODIFIED,
    UNTRACKED,
    git_file_state,
    has_secret_text,
    has_secret_value,
    is_sensitive_path,
    run_git,
)

EXTERNAL_READ = "content from outside (web, email, connectors, downloads) was read earlier in this session"
DESTINATION_NAMED = "destination: named in the user's own messages"
DESTINATION_NOT_NAMED = "destination: not named in the user's own messages"
DESTINATION_UNKNOWN = "destination: could not be determined from the action"
SECRET_INVOLVED = "a secret-looking file name or value is involved"
_FILE_STATE_LINES = {
    "new": "target file: new",
    TRACKED_CLEAN: "target file: unchanged in git",
    TRACKED_MODIFIED: "target file: has uncommitted changes",
    UNTRACKED: "target file: not tracked by git",
    IGNORED: "target file: ignored by git",
    "unknown": "target file: state unknown",
}
# Worst first: the line reports the least recoverable target of a multi-file write.
_FILE_STATE_ORDER = [TRACKED_MODIFIED, UNTRACKED, IGNORED, "unknown", TRACKED_CLEAN, "new"]

# Programs that reach the network from a shell command line (bash and PowerShell names).
_NETWORK_CLIENTS = {
    "curl", "curl.exe", "wget", "wget.exe", "http", "https", "httpie", "nc", "ncat",
    "netcat", "telnet", "ftp", "sftp", "scp", "ssh", "rsync",
    "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "start-bitstransfer",
}
_URL_HOST = re.compile(r"\b[a-z][a-z0-9+.-]*://([^/\s'\"`<>:?#\\]+)", re.IGNORECASE)
# A bare domain in the user's text, not one inside a path (`docs/intro.md`).
_DOMAIN = re.compile(
    r"(?<![@\w./\\~-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?![\w-])", re.IGNORECASE
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Top-level domains that are also common file extensions. A bare `main.py` or `model.ai` is
# a file name; a name with one of these endings counts as a site only when written as one:
# in a URL or email address, after `www.`, or followed by a path or a port.
_FILE_LIKE_TLDS = {
    "ac", "ai", "am", "cc", "cl", "gd", "in", "ly", "ma", "md", "mk", "ml", "mo", "mov",
    "pl", "pm", "ps", "pt", "py", "re", "rs", "sc", "sh", "so", "ss", "st", "sv", "tf", "zip",
}
# Connector platforms whose targets are written `platform:chat[:thread]`.
_HANDLE_PLATFORMS = {"slack", "telegram", "github", "discord", "whatsapp"}
_PHONE = re.compile(r"\+?\d[\d\s().-]{5,}\d")
_DOWNLOAD_TOOLS = {"github_clone", "github_pull", "email_download_attachment"}
_PATH_ARGS = ("path", "file", "files", "filename", "attachment", "attachments", "source", "src")


def _words(command: str) -> list[str]:
    return [w.strip("'\"`()[]{};|&") for w in re.split(r"\s+", command or "") if w]


def shell_reaches_network(command: str) -> bool:
    """A command line that names a network client or a URL."""
    words = [w.lower() for w in _words(command)]
    names = {Path(w).name for w in words}
    return bool(names & _NETWORK_CLIENTS) or bool(_URL_HOST.search(command or ""))


def is_external_read(tool_name: str, arguments: dict[str, Any], metadata: Any = None) -> bool:
    """Whether a SUCCESSFUL call brought outside content into the agent's context."""
    if classify(tool_name, metadata) is RiskClass.EGRESS:
        return True  # web_fetch, web_search, browser_open_url, enrichment lookups
    if tool_name.startswith("browser_") or tool_name in _DOWNLOAD_TOOLS:
        return True
    if getattr(metadata, "category", "") == "mcp" or tool_name.startswith("mcp__"):
        return True
    try:
        from .connectors.tool_defs import _KIND_BY_NAME
    except ImportError:  # pragma: no cover - connectors always ship
        _KIND_BY_NAME = {}
    if _KIND_BY_NAME.get(tool_name) == "read":
        return True
    if tool_name == "run_shell":
        command = str((arguments or {}).get("command", ""))
        return shell_reaches_network(command) or provenance.fetches_outside_files(command)
    return False


def _bare_host(host: str) -> str:
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _named(user_texts: Iterable[str]) -> tuple[set[str], str]:
    """Hosts/domains the user gave as destinations, and the lowercased text they wrote."""
    text = "\n".join(t for t in user_texts if t).lower()
    domains = {m.group(1).rsplit("@", 1)[-1] for m in _URL_HOST.finditer(text)}
    for m in _DOMAIN.finditer(text):
        domain = m.group(1)
        if domain.rsplit(".", 1)[-1] in _FILE_LIKE_TLDS:
            after = text[m.end():m.end() + 2]
            if not (domain.startswith("www.") or after[:1] == "/" or re.match(r":\d", after)):
                continue  # main.py, README.md, run.sh: a file name, not a site
        domains.add(domain)
    domains |= {e.split("@", 1)[1] for e in _EMAIL.findall(text)}
    return {_bare_host(d) for d in domains if d.strip(".")}, text


def _host_named(host: str, domains: set[str]) -> bool:
    """The exact host, or a subdomain of a domain the user named. Naming a public suffix
    (`herokuapp.com`, `github.io`) names only itself, not every site hosted under it."""
    host = _bare_host(host)
    return any(
        host == d or (host.endswith("." + d) and not public_suffix.is_public_suffix(d))
        for d in domains
    )


def _appears(form: str, text: str) -> bool:
    """`form` written as a whole token: `#ops` is not in `#ops-alerts`, `acme/web` is not
    in `acme/website`."""
    pattern = r"(?<![\w@.#-])" + re.escape(form.lower()) + r"(?![\w-]|\.\w)"
    return re.search(pattern, text) is not None


def _id_like(value: str) -> bool:
    """A generated id (`C0123ABCD`, a Discord snowflake): long and with a digit, so it is
    neither an ordinary word nor a small number in a sentence."""
    return len(value) >= 6 and any(c.isdigit() for c in value)


def _chat_named(chat: str, text: str) -> bool:
    """A channel id in full, or a channel name with its `#`."""
    if chat.startswith("#"):
        return _appears(chat, text)
    return _appears(chat, text) if _id_like(chat) else _appears("#" + chat, text)


def _target_named(target: str, text: str) -> bool:
    """Whether the user wrote this connector target in its own form: an email address or
    phone number exactly; a channel as `#name` or its full id; a GitHub thread as
    `owner/repo` or `owner/repo#12`; any other target as its full handle, or as a bare id
    when the id is long enough not to be an ordinary word or number."""
    target = target.strip()
    emails = _EMAIL.findall(target)
    if emails:
        given = {e.lower() for e in _EMAIL.findall(text)}
        return all(e.lower() in given for e in emails)
    if re.fullmatch(r"\+?[\d\s().-]{7,}", target):
        digits = re.sub(r"\D", "", target)
        return any(re.sub(r"\D", "", p) == digits for p in _PHONE.findall(text))
    platform, sep, rest = target.partition(":")
    platform = platform.lower()
    if sep and platform in _HANDLE_PLATFORMS:
        if _appears(target, text):
            return True  # the whole handle, e.g. `slack:C0123ABCD`
        chat = rest.split(":", 1)[0]
        if platform == "github":
            repo = chat.split("#", 1)[0]
            return "/" in repo and (_appears(chat, text) or _appears(repo, text))
        if platform == "slack":
            return _chat_named(chat.rsplit("/", 1)[-1], text)
        return _id_like(chat) and _appears(chat, text)
    return _chat_named(target, text)


def destination_fact(
    tool_name: str,
    arguments: dict[str, Any],
    user_texts: Iterable[str],
    *,
    metadata: Any = None,
    egress_host: Optional[Callable[[str, dict[str, Any]], str]] = None,
) -> str:
    """One of the DESTINATION_* lines, or "" when the action sends nothing out."""
    risk = classify(tool_name, metadata)
    domains, text = _named(user_texts)
    if risk is RiskClass.EGRESS:
        host = egress_host(tool_name, arguments) if egress_host else ""
        if not host:
            return DESTINATION_UNKNOWN
        return DESTINATION_NAMED if _host_named(host, domains) else DESTINATION_NOT_NAMED
    if risk is RiskClass.EXTERNAL:
        try:
            from .connectors.tool_defs import standing_target_for
        except ImportError:  # pragma: no cover
            return DESTINATION_UNKNOWN
        target = standing_target_for(tool_name, arguments or {})
        if not target:
            return DESTINATION_UNKNOWN
        if _target_named(target, text):
            return DESTINATION_NAMED  # the exact recipient, channel or repo appears
        if "@" not in target and "." in target and _host_named(target, domains):
            return DESTINATION_NAMED  # a domain-like target under a domain the user named
        # An email address counts only when that exact address appears: naming a company
        # domain does not name every mailbox in it.
        return DESTINATION_NOT_NAMED
    if tool_name == "run_shell":
        command = str((arguments or {}).get("command", ""))
        if not shell_reaches_network(command):
            return ""
        hosts = [m.group(1) for m in _URL_HOST.finditer(command)]
        if not hosts:
            return DESTINATION_UNKNOWN
        return DESTINATION_NAMED if all(_host_named(h, domains) for h in hosts) else DESTINATION_NOT_NAMED
    return ""


def file_state_fact(targets: list[Path]) -> str:
    """The TARGET line for a write's resolved targets, or "" for no targets."""
    if not targets:
        return ""
    states = []
    for target in targets:
        states.append("new" if not target.exists() else git_file_state(target))
    worst = min(states, key=lambda s: _FILE_STATE_ORDER.index(s) if s in _FILE_STATE_ORDER else 0)
    return _FILE_STATE_LINES.get(worst, _FILE_STATE_LINES["unknown"])


def _path_arg_values(arguments: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in _PATH_ARGS:
        value = (arguments or {}).get(key)
        values = value if isinstance(value, (list, tuple)) else [value]
        out += [str(v) for v in values if isinstance(v, str) and v]
    return out


def referenced_files(tool_name: str, arguments: dict[str, Any], write_targets: list[Path]) -> list[str]:
    """Every file-like path the action names, as written (separators normalised to `/`):
    write targets, path-shaped arguments, and for a shell command every word that could
    be a file. A URL is a destination, not a local file, and is left out: before this,
    `curl https://api.example.com/oauth/token` read as a file called `token`."""
    paths = [str(t) for t in write_targets] + _path_arg_values(arguments)
    if tool_name == "run_shell":
        for word in _words(str((arguments or {}).get("command", ""))):
            if "://" in word:
                continue
            word = word.lstrip("@")  # curl -F file=@.env / --data-binary @id_rsa
            if "=" in word:
                word = word.rsplit("=", 1)[-1]
                if not _FILE_LIKE_VALUE.search(word):
                    continue
                word = word.lstrip("@")
            paths.append(word)
    return [p.replace("\\", "/") for p in paths if p]


def referenced_names(tool_name: str, arguments: dict[str, Any], write_targets: list[Path]) -> list[str]:
    """Base names of `referenced_files` (kept for callers that judge names only)."""
    return [Path(p).name for p in referenced_files(tool_name, arguments, write_targets) if Path(p).name]


# Shell programs that send a LOCAL FILE out, and how the file is named on their command
# line. curl: `@file` after a data flag, `name=@file` in a form, `-T`/`--upload-file`.
# wget: `--post-file`, `--body-file`. PowerShell: `-InFile`. scp/rsync/sftp: the local
# arguments before the remote `host:path`.
_UPLOAD_VALUE_FLAGS = {
    "curl": {"-T", "--upload-file"},
    "wget": {"--post-file", "--body-file"},
    "invoke-webrequest": {"-infile"},
    "iwr": {"-infile"},
    "invoke-restmethod": {"-infile"},
    "irm": {"-infile"},
}
_COPY_PROGRAMS = {"scp", "rsync", "sftp"}
_REMOTE_ARG = re.compile(r"^(?:[\w.-]+@)?[\w.-]+:|^rsync://", re.IGNORECASE)
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")  # `C:\x` is a local Windows path, not `host:path`
# A `key=value` word names a file only when the value looks like one: a variable
# (`token=$GITHUB_TOKEN`), an `@file`, a path, or a dotted name (`--output=keys.json`).
# `grant_type=client_credentials` is a form field, not a file called `client_credentials`.
_FILE_LIKE_VALUE = re.compile(r"^[$@%~.]|[\\/.]")


def _argv(part: str, shell: str = "bash") -> list[str]:
    """shlex for a unix shell; whitespace for PowerShell (and its cmdlets anywhere), whose
    paths use backslashes that shlex would eat."""
    import shlex

    words = _words(part)
    if shell == "powershell":
        return words
    if words and Path(words[0]).name.lower().startswith(("invoke-", "iwr", "irm")):
        return words
    try:
        return shlex.split(part)
    except ValueError:
        return words


def _program(argv: list[str]) -> str:
    name = Path(argv[0]).name.lower() if argv else ""
    return name[:-4] if name.endswith(".exe") else name


def _parts(command: str, shell: str) -> list[list[str]]:
    from .permissions import _split_commands

    return [a for a in (_argv(p, shell) for p in _split_commands(command)) if a]


# Cloud and sync copies: the local arguments before the first remote (`s3://`, `gs://`,
# `remote:path`) are uploaded. The verb words are skipped, not read as files.
_CLOUD_COPY_VERBS = {
    "aws": {"s3", "cp", "sync", "mv"},
    "gsutil": {"cp", "rsync", "mv", "-m"},
    "rclone": {"copy", "sync", "move", "copyto", "moveto"},
}
# Programs that take the files to send as plain arguments after a sub-command.
_FILE_ARGS_AFTER = {
    ("gh", "release", "upload"): 1,  # skip the tag
    ("gh", "gist", "create"): 0,
    ("twine", "upload"): 0,
    ("gem", "push"): 0,
}
# Mail clients: the flag whose value is an attachment.
_ATTACH_FLAGS = {"mutt": {"-a"}, "mail": {"-a", "-A"}, "mailx": {"-a", "-A"}, "az": {"-f", "--file"}}


def _remote(token: str) -> bool:
    return "://" in token or bool(_REMOTE_ARG.match(token) and not _DRIVE.match(token))


def sent_files(tool_name: str, arguments: dict[str, Any], shell: str = "bash") -> list[str]:
    """Local files whose CONTENTS an action sends off the machine, as written. For
    connector, web and MCP tools: the path-shaped arguments (an attachment, an upload's
    `path`). For a shell command: the upload positions listed above, the sources of a
    cloud copy, the files given to `gh release upload` / `gh gist create` / `twine upload`
    / `gem push`, a mail client's attachment. Whether the action sends at all is the
    caller's question (`shell_reaches_network`, `shell_sends_data`, the risk class).

    This reads the command TEXT and is best effort: data piped from `cat` (`@-`) and a
    file a script opens are not here (the floor reads scripts separately, see
    `script_runs`). What no text can show is left to the sandbox's network allowlist
    (OPE-242)."""
    args = arguments or {}
    if tool_name != "run_shell":
        return [p.replace("\\", "/") for p in _path_arg_values(args)]

    out: list[str] = []
    for argv in _parts(str(args.get("command", "")), shell):
        program = _program(argv)
        verbs = _CLOUD_COPY_VERBS.get(program)
        if verbs is not None:
            for token in argv[1:]:
                if token.startswith("-") or token in verbs:
                    continue
                if _remote(token):
                    break  # a remote source means a download; what follows is local
                out.append(token)
            continue
        for key, skip in _FILE_ARGS_AFTER.items():
            if program == key[0] and [t.lower() for t in argv[1:1 + len(key) - 1]] == list(key[1:]):
                files = [t for t in argv[len(key):] if not t.startswith("-")]
                out.extend(files[skip:])
        attach = _ATTACH_FLAGS.get(program)
        if attach:
            for i, token in enumerate(argv[1:], start=1):
                if token in attach and i + 1 < len(argv):
                    out.append(argv[i + 1])
        flags = _UPLOAD_VALUE_FLAGS.get(program)
        if flags is not None:
            folded = program.startswith(("invoke-", "iwr", "irm"))
            for i, token in enumerate(argv[1:], start=1):
                probe = token.lower() if folded else token
                if probe in flags and i + 1 < len(argv):
                    out.append(argv[i + 1])
                elif probe.startswith("--") and "=" in probe:
                    flag, _, value = token.partition("=")
                    if flag in flags and value:
                        out.append(value)
                if program == "curl":
                    if token.startswith("@") and token != "@-":
                        out.append(token[1:])
                    elif "=@" in token:
                        out.append(token.split("=@", 1)[1])
        elif program in _COPY_PROGRAMS:
            local: list[str] = []
            remote_seen = False
            for token in argv[1:]:
                if token.startswith("-"):
                    continue
                if _REMOTE_ARG.match(token) and not _DRIVE.match(token):
                    remote_seen = True
                    break  # what follows a remote source is a local destination
                local.append(token)
            if remote_seen:
                out.extend(local)
    return [p.replace("\\", "/") for p in out if p]


# -- point 4a: programs that send or publish ---------------------------------------------
# Program -> the sub-commands that send (None: the program always sends). Matched on the
# first word of every part of a compound command, after `;`, `&&`, `|`. Programs that
# EXPOSE a local port (ngrok, cloudflared, `ssh -R`) count as sending too.
_SENDING_PROGRAMS: dict[str, Optional[frozenset[str]]] = {
    "git": frozenset({"push", "send-email"}),  # commits, patches by mail
    "npm": frozenset({"publish"}),  # the package folder
    "yarn": frozenset({"publish"}),
    "pnpm": frozenset({"publish"}),
    "twine": frozenset({"upload"}),  # Python distributions
    "cargo": frozenset({"publish"}),  # the crate
    "gem": frozenset({"push"}),  # a gem file
    "docker": frozenset({"push"}),  # an image, with whatever was baked in
    "podman": frozenset({"push"}),
    "gh": frozenset({"release", "gist", "api"}),  # release upload, gist create, api -f/-F/--input
    "aws": frozenset({"s3"}),  # s3 cp / sync / mv
    "az": frozenset({"storage"}),  # storage blob upload
    "gsutil": frozenset({"cp", "rsync", "mv", "-m"}),
    "rclone": frozenset({"copy", "sync", "move", "copyto", "moveto"}),
    "mail": None,  # mail clients send whatever they are given
    "mailx": None,
    "sendmail": None,
    "mutt": None,
    "ngrok": None,  # exposes a local port
    "cloudflared": None,
    "ssh": frozenset({"-R"}),  # a reverse tunnel exposes a local port
}
# git's global options that come before the sub-command; `-C <dir>` matters (it moves the
# repository the push reads from), `-c key=value` takes a value, the rest are flags.
_GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _git_subcommand(argv: list[str]) -> tuple[str, list[str], Optional[str]]:
    """(sub-command, its arguments, the `-C` folder or None) of a git command line."""
    folder = None
    i = 1
    while i < len(argv):
        token = argv[i]
        if token in _GIT_GLOBAL_WITH_VALUE and i + 1 < len(argv):
            if token == "-C":
                folder = argv[i + 1]
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token, argv[i + 1:], folder
    return "", [], folder


def _sends(argv: list[str]) -> bool:
    program = _program(argv)
    if program not in _SENDING_PROGRAMS:
        return False
    subs = _SENDING_PROGRAMS[program]
    if subs is None:
        return True
    if program == "git":
        return _git_subcommand(argv)[0] in subs
    if program == "gh":
        rest = [t.lower() for t in argv[1:]]
        if rest[:2] in (["release", "upload"], ["gist", "create"]):
            return True
        return rest[:1] == ["api"] and any(
            t in ("-f", "-F", "--field", "--raw-field", "--input") for t in rest
        )
    if program == "az":
        rest = [t.lower() for t in argv[1:]]
        return rest[:1] == ["storage"] and "upload" in rest
    if program == "aws":
        rest = [t.lower() for t in argv[1:]]
        return rest[:1] == ["s3"] and any(t in ("cp", "sync", "mv") for t in rest)
    return any(t in subs for t in argv[1:])


def shell_sends_data(command: str, shell: str = "bash") -> bool:
    """A command line that runs a program known to send or publish (point 4a), in any part
    of a compound command. `shell_reaches_network` covers the plain network clients."""
    return any(_sends(argv) for argv in _parts(command or "", shell))


# -- point 4b: what `git push` would send, what `git add -A` would stage --------------------
_PUSH_CONTENT_FILES = 100  # blobs read per push check; names are checked for every file


def _cwd_of(argv_parts: list[list[str]], upto: int, base: Path) -> Path:
    """The folder a part runs in: `base`, moved by any `cd` earlier in the same command."""
    folder = base
    for argv in argv_parts[:upto]:
        if _program(argv) == "cd" and len(argv) > 1 and not argv[1].startswith("-"):
            target = Path(os.path.expanduser(argv[1]))
            folder = target if target.is_absolute() else folder / target
    return folder


def pushed_files(command: str, cwd: Path, shell: str = "bash") -> Optional[tuple[Path, list[str]]]:
    """For a `git push` in the command: (repository root, files in the commits that would
    be pushed), or None when there is no push or git can't say. The range is
    `@{u}..HEAD`, else `refs/remotes/<remote>/<branch>..HEAD` from the arguments, else (a
    branch never pushed) every file in HEAD's tree. Deleted files are left out: there is
    nothing to read. Files added and removed within the range are missed."""
    parts = _parts(command or "", shell)
    for i, argv in enumerate(parts):
        if _program(argv) != "git":
            continue
        sub, args, folder = _git_subcommand(argv)
        if sub != "push":
            continue
        where = _cwd_of(parts, i, cwd)
        if folder:
            target = Path(os.path.expanduser(folder))
            where = target if target.is_absolute() else where / target
        top = run_git(["rev-parse", "--show-toplevel"], where)
        if top is None or top.returncode != 0:
            return None
        root = Path(top.stdout.decode("utf-8", "replace").strip())
        positional = [a for a in args if not a.startswith("-")]
        upstream = run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root)
        base_ref = ""
        if upstream is not None and upstream.returncode == 0 and upstream.stdout.strip():
            base_ref = upstream.stdout.decode("utf-8", "replace").strip()
        else:
            remote = positional[0] if positional else "origin"
            if len(positional) > 1:
                branch = positional[1].split(":", 1)[0]
            else:
                head = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)
                branch = head.stdout.decode("utf-8", "replace").strip() if head and head.returncode == 0 else ""
            if branch:
                ref = f"refs/remotes/{remote}/{branch}"
                known = run_git(["rev-parse", "--verify", "-q", ref], root)
                if known is not None and known.returncode == 0:
                    base_ref = ref
        if base_ref:
            listing = run_git(["diff", "--name-only", "--diff-filter=d", f"{base_ref}..HEAD"], root)
        else:
            listing = run_git(["ls-tree", "-r", "--name-only", "HEAD"], root)
        if listing is None or listing.returncode != 0:
            return None
        files = [ln.strip() for ln in listing.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
        return root, files
    return None


def git_blob_has_secret(root: Path, path: str) -> bool:
    """Whether HEAD's copy of `path` carries a secret-looking value (up to the size cap)."""
    size = run_git(["cat-file", "-s", f"HEAD:{path}"], root)
    if size is None or size.returncode != 0:
        return False
    try:
        if int(size.stdout.decode("ascii", "replace").strip()) > SECRET_SCAN_MAX_BYTES:
            return False
    except ValueError:
        return False
    blob = run_git(["show", f"HEAD:{path}"], root)
    if blob is None or blob.returncode != 0:
        return False
    return has_secret_text(blob.stdout.decode("ascii", "replace"))


_STAGE_ALL = {"-A", "--all", ".", ":/", "-u", "--update", "--no-ignore-removal"}


def staged_untracked_files(command: str, cwd: Path, shell: str = "bash") -> list[str]:
    """Untracked files a `git add -A` / `git add .` / `git add -u` in the command would
    stage, by asking git status (no shell, no lock). Empty when git can't say. `git commit
    -a` stages only tracked files, so it is not here."""
    parts = _parts(command or "", shell)
    for i, argv in enumerate(parts):
        if _program(argv) != "git":
            continue
        sub, args, folder = _git_subcommand(argv)
        if sub != "add" or not any(a in _STAGE_ALL for a in args):
            continue
        where = _cwd_of(parts, i, cwd)
        if folder:
            target = Path(os.path.expanduser(folder))
            where = target if target.is_absolute() else where / target
        status = run_git(["status", "--porcelain=v1", "--untracked-files=all"], where)
        if status is None or status.returncode != 0:
            return []
        out = []
        for line in status.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("?? "):
                out.append(line[3:].strip().strip('"'))
        return out
    return []


# -- point 4c: scripts that are run ---------------------------------------------------------
_INTERPRETERS = {
    "python", "python3", "python2", "py", "pypy", "pypy3", "node", "nodejs", "deno", "bun",
    "ruby", "perl", "php", "bash", "sh", "zsh", "dash", "ksh", "fish", "pwsh", "powershell",
}
_INLINE_CODE_FLAGS = {"-c", "-e", "--eval", "-command", "-encodedcommand", "-ec"}
_SCRIPT_FILE_FLAGS = {"-file", "-f"}  # PowerShell -File; pwsh -f
_RUN_WORDS = {"run"}  # `deno run x.ts`, `bun run x.js`
_SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".ps1"}
# Network use inside a script: a URL, an HTTP/socket/mail library, a network client.
_NETWORK_USE = re.compile(
    r"https?://"
    r"|\bimport\s+(?:requests|urllib|httpx|aiohttp|socket|http\.client|httplib|smtplib|ftplib|paramiko|boto3|pycurl|websockets?)\b"
    r"|\bfrom\s+(?:requests|urllib|httpx|aiohttp|socket|http|smtplib|ftplib|paramiko|boto3)\b"
    r"|require\(\s*['\"](?:https?|net|dgram|tls|axios|node-fetch|got|undici|nodemailer|ws|request)['\"]"
    r"|\bfrom\s+['\"](?:https?|net|dgram|tls|axios|node-fetch|got|undici|nodemailer|ws)['\"]"
    r"|\bfetch\s*\(|\baxios\b|\burlopen\s*\(|\bXMLHttpRequest\b"
    r"|Net::HTTP|LWP::|HTTP::Tiny|IO::Socket|file_get_contents\(\s*['\"]https?|\bcurl_init\b|\bfsockopen\b"
    r"|Invoke-WebRequest|Invoke-RestMethod|\biwr\b|\birm\b|System\.Net\.|\bHttpClient\b|Start-BitsTransfer"
    r"|(?:^|[\s;&|(`])(?:curl|wget|scp|sftp|rsync|ssh|nc|ncat|netcat|telnet|ftp)\s",
    re.IGNORECASE | re.MULTILINE,
)
# Quoted literals in a script that look like a file name: a path, a dotted name, a dotfile.
_QUOTED = re.compile(r"""(?<![\\])['"]([^'"\n\s]{1,200})['"]""")


# Inline code is read off the WHOLE command line: `_split_commands` splits on `;` and `|`
# inside the quotes too, which would cut `python -c "import requests; requests.post(...)"`
# in half. The quoted value after the flag is the code.
_INLINE_CODE = re.compile(
    r"""(?:^|\s)(?:-c|-e|--eval|-Command|-EncodedCommand|-ec)\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""",
    re.IGNORECASE | re.DOTALL,
)


def script_runs(command: str, shell: str = "bash") -> list[tuple[Optional[str], Optional[str]]]:
    """(script path, inline code) pairs the command would run: `python x.py`, `node x.js`,
    `bash x.sh`, `./x.sh`, `pwsh -File x.ps1`, and `-c` / `-e` / `-Command` code."""
    out: list[tuple[Optional[str], Optional[str]]] = []
    for m in _INLINE_CODE.finditer(command or ""):
        code = m.group(1) if m.group(1) is not None else m.group(2)
        if code:
            out.append((None, code))
    for argv in _parts(command or "", shell):
        program = _program(argv)
        if program in _INTERPRETERS:
            i = 1
            while i < len(argv):
                token = argv[i]
                low = token.lower()
                if low in _INLINE_CODE_FLAGS:
                    break  # inline code: read above, off the whole command line
                if low in _SCRIPT_FILE_FLAGS and i + 1 < len(argv) and program in ("pwsh", "powershell"):
                    # PowerShell reads a backslash as a path separator on every platform.
                    out.append((argv[i + 1].replace("\\", "/"), None))
                    break
                if low == "-m":
                    break  # a module, not a file
                if token.startswith("-") or low in _RUN_WORDS:
                    i += 1
                    continue
                out.append((token, None))
                break
        elif program and (
            argv[0].startswith(("./", ".\\")) or Path(argv[0]).suffix.lower() in _SCRIPT_SUFFIXES
        ):
            out.append((argv[0].replace("\\", "/") if argv[0].startswith(".\\") else argv[0], None))
    return out


def script_uses_network(text: str) -> bool:
    return _NETWORK_USE.search(text or "") is not None


def script_named_files(text: str) -> list[str]:
    """File-like names a script mentions: quoted literals that look like paths, plus (for
    any text) the words a shell would read as files."""
    names = [m.group(1) for m in _QUOTED.finditer(text or "") if "://" not in m.group(1)]
    names = [n for n in names if _FILE_LIKE_VALUE.search(n)]
    names += referenced_files("run_shell", {"command": text}, [])
    names += sent_files("run_shell", {"command": text})
    return [n for n in names if n and n not in (".", "..")]


_PUBLISH_VALUE_FLAGS = {"--access", "--tag", "--otp", "--registry", "--new-version", "--tolerate-republish"}


def publish_dirs(command: str, shell: str = "bash") -> list[str]:
    """Folders an `npm` / `yarn` / `pnpm publish` in the command would pack: the folder
    argument, else the current one. A tarball argument is a file, judged by its name."""
    out: list[str] = []
    for argv in _parts(command or "", shell):
        if _program(argv) in ("npm", "yarn", "pnpm") and "publish" in argv[1:]:
            rest: list[str] = []
            skip = False
            for token in argv[argv.index("publish") + 1:]:
                if skip:
                    skip = False
                elif token in _PUBLISH_VALUE_FLAGS:
                    skip = True
                elif not token.startswith("-"):
                    rest.append(token)
            folder = rest[0] if rest and not rest[0].endswith((".tgz", ".tar.gz")) else "."
            out.append(folder)
    return out


def secret_fact(
    tool_name: str, arguments: dict[str, Any], write_targets: list[Path], *, cwd: Optional[Path] = None
) -> str:
    """The SECRET line. With `cwd`, a `git add -A` (or `.`, `-u`) also names the untracked
    files it would stage, so a new `.env` reaching the index shows up for the reviewer."""
    if has_secret_value(arguments or {}):
        return SECRET_INVOLVED
    names = referenced_files(tool_name, arguments, write_targets)
    if cwd is not None and tool_name == "run_shell":
        names += staged_untracked_files(str((arguments or {}).get("command", "")), Path(cwd))
    if any(is_sensitive_path(p) for p in names):
        return SECRET_INVOLVED
    return ""


def compute_facts(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    user_texts: Iterable[str],
    external_read: bool,
    write_targets: list[Path],
    metadata: Any = None,
    egress_host: Optional[Callable[[str, dict[str, Any]], str]] = None,
    cwd: Optional[Path] = None,
) -> list[str]:
    """Every applicable fact line, in a stable order. `cwd` is the shell's folder, for the
    checks that ask git (see `secret_fact`)."""
    facts = []
    if external_read:
        facts.append(EXTERNAL_READ)
    destination = destination_fact(
        tool_name, arguments, list(user_texts), metadata=metadata, egress_host=egress_host
    )
    if destination:
        facts.append(destination)
    state = file_state_fact(write_targets)
    if state:
        facts.append(state)
    secret = secret_fact(tool_name, arguments, write_targets, cwd=cwd)
    if secret:
        facts.append(secret)
    return facts
