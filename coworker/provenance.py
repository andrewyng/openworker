"""What the agent itself created this session — and the one fact that follows (OPE-114 §1).

The reviewer is never shown file contents, so `python scripts/setup.py` cannot be judged
from its text: the effect lives inside a file neither the reviewer nor the human at the
card is shown. But the engine knows something neither of them does — whether it wrote or
downloaded that file moments ago. This module keeps that record and renders it as one line
of fixed-vocabulary fact.

Deliberately NOT here: reading file contents, analysing what a script does, or tracing
values out of untrusted text (the general taint tracking of OPE-114 is a separate, larger
design). A miss leaves behaviour exactly as it is today, so partial coverage only ever
moves toward caution — unlike a detector, whose false negatives would breed false
confidence.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

WRITTEN = "written"
DOWNLOADED = "downloaded"

# Download-shaped tools that resolve their own target and report it in the RESULT. Reading
# the result beats guessing an argument name: it records where the bytes actually landed
# rather than what was asked for.
# NOTE web_fetch is deliberately absent: it returns page text and never writes a file, so
# it creates nothing to later execute. Listing it here would claim coverage we do not have.
_DOWNLOAD_RESULT_TOOLS = {
    "github_clone",
    "github_pull",
    "email_download_attachment",
}

# Shell fetchers: program -> flags whose VALUE names an output path. `curl -O` (no value,
# saves under the URL's basename) is handled separately. Case matters for the unix tools —
# curl's `-o FILE` and `-O` are different flags — so only the PowerShell names below are
# folded, and their table entries are pre-lowercased.
_FETCHER_OUTPUT_FLAGS = {
    "curl": {"-o", "--output"},
    "wget": {"-O", "--output-document"},
    "invoke-webrequest": {"-outfile"},
    "iwr": {"-outfile"},
}
_CASE_FOLDED_FETCHERS = {"invoke-webrequest", "iwr"}

# Programs whose real input is a file they never name on the command line. Without this,
# `make deploy` would look like it touches nothing at all.
_IMPLICIT_TARGETS: dict[str, tuple[str, ...]] = {
    "make": ("Makefile", "makefile", "GNUmakefile"),
    "npm": ("package.json",),
    "pnpm": ("package.json",),
    "yarn": ("package.json",),
    "bun": ("package.json",),
    "pytest": ("conftest.py",),
    "tox": ("tox.ini",),
    "nox": ("noxfile.py",),
    "docker-compose": (
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yaml",
        "compose.yml",
    ),
}

# Extensions that make a bare token (no path separator) worth resolving as a file.
_SCRIPT_SUFFIXES = {
    ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl",
    ".php", ".ps1", ".bat", ".cmd", ".jar", ".exe", ".json", ".yml", ".yaml",
    ".ini", ".toml", ".cfg", ".mk",
}


@dataclass(frozen=True)
class Origin:
    """How a path came into being this session, and at which step."""

    step: int
    kind: str  # WRITTEN | DOWNLOADED


@dataclass(frozen=True)
class Match:
    """A proposed call naming a path this session created."""

    path: str  # as written in the call, for the human-facing line
    origin: Origin
    steps_ago: int

    @property
    def downloaded(self) -> bool:
        return self.origin.kind == DOWNLOADED

    def render(self) -> str:
        """One line, fixed vocabulary — never file content, never outside-authored text."""
        verb = "downloaded" if self.downloaded else "created"
        if self.steps_ago <= 0:
            when = "just now"
        elif self.steps_ago == 1:
            when = "1 step ago"
        else:
            when = f"{self.steps_ago} steps ago"
        return f"{self.path} was {verb} by the agent {when}"


def resolve(path: str, root: Path) -> str:
    """One canonical key per file, so `./a.py`, `a.py` and the absolute form collapse.
    Mirrors the permission engine's scoping resolution: relative paths hang off the
    workspace root, absolute and `~` forms are taken as-is."""
    p = Path(str(path)).expanduser()
    try:
        return str(p.resolve() if p.is_absolute() else (root / p).resolve())
    except (OSError, ValueError):  # pragma: no cover - unresolvable exotic path
        return str(p)


def _looks_like_path(token: str) -> bool:
    if not token or token.startswith("-") or "://" in token:
        return False
    if "/" in token or "\\" in token:
        return True
    return Path(token).suffix.lower() in _SCRIPT_SUFFIXES


def _program(argv: list[str]) -> str:
    name = Path(argv[0]).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _sub_commands(command: str) -> list[list[str]]:
    """Every sub-command of a compound command, tokenized. Splitting is textual and
    deliberately over-eager (see `permissions._split_commands`): more parts to scan can
    only ever surface more paths, never hide one."""
    from .permissions import _split_commands

    out: list[list[str]] = []
    for part in _split_commands(command):
        try:
            argv = shlex.split(part)
        except ValueError:
            argv = part.split()  # unbalanced quotes: still worth scanning for paths
        if argv:
            out.append(argv)
    return out


def command_paths(command: str) -> list[str]:
    """Every path a shell command names, plus the implicit files it would read.

    No attempt is made to work out WHICH token is "the script" — every path-like token is
    returned and checked. Semantics-free and conservative: understanding the command is
    exactly the thing that cannot be done reliably from its text.

    Known misses, by design rather than oversight: a file that only becomes involved
    through an import or include (agent writes `helper.py`, runs `main.py`) is invisible
    here, and no cheap analysis would find it."""
    found: list[str] = []
    for argv in _sub_commands(command):
        found.extend(t for t in argv[1:] if _looks_like_path(t))
        program = _program(argv)
        if program == "docker" and len(argv) > 1 and argv[1].lower() == "compose":
            program = "docker-compose"
        found.extend(_IMPLICIT_TARGETS.get(program, ()))
        if _looks_like_path(argv[0]):
            found.append(argv[0])  # ./run.sh
    return found


def _shell_download_paths(command: str) -> list[str]:
    """Output paths of fetch commands. `curl URL | sh` writes no file and needs no entry:
    a pipe already costs a command its prefix eligibility, so it gates today."""
    out: list[str] = []
    for argv in _sub_commands(command):
        program = _program(argv)
        flags = _FETCHER_OUTPUT_FLAGS.get(program)
        if not flags:
            continue
        folded = program in _CASE_FOLDED_FETCHERS
        for i, token in enumerate(argv[1:], start=1):
            probe = token.lower() if folded else token
            if probe in flags and i + 1 < len(argv):
                out.append(argv[i + 1])
            elif probe.startswith("--") and "=" in probe:
                # Long flags accept an attached value (`--output=FILE`); token comparison
                # alone missed those spellings, dropping the download from the record.
                flag, _, value = probe.partition("=")
                if flag in flags and value:
                    out.append(value)
        if program == "curl" and "-O" in argv[1:]:
            # curl -O saves under the URL's own basename.
            for candidate in argv[1:]:
                if "://" in candidate:
                    name = candidate.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
                    if name:
                        out.append(name)
                    break
    return out


def created_paths(
    tool_name: str, arguments: dict[str, Any], result: Any
) -> tuple[list[str], str]:
    """(paths, origin) for a call that just SUCCEEDED, or ([], "") when it created nothing."""
    from .permissions import write_paths
    from .risk import WRITE_TOOLS

    if tool_name in WRITE_TOOLS:
        paths, located = write_paths(tool_name, arguments or {})
        return (paths, WRITTEN) if located and paths else ([], "")
    if tool_name in _DOWNLOAD_RESULT_TOOLS:
        path = result.get("path") if isinstance(result, dict) else None
        return ([str(path)], DOWNLOADED) if path else ([], "")
    if tool_name == "run_shell":
        fetched = _shell_download_paths(str((arguments or {}).get("command", "")))
        return (fetched, DOWNLOADED) if fetched else ([], "")
    return ([], "")


# -- outside files (OPE-235) ------------------------------------------------------------
# The reviewer is told when content from outside is in the agent's context. A file that
# came from outside this session (cloned, downloaded, unpacked from a downloaded archive)
# is outside content whenever it is read later, by any tool. These helpers work out, from
# the command text alone, which files and folders a shell command brings in. A folder that
# already existed is never marked wholesale (it may hold the user's own files): only the
# names that appear in it during the call are.

_CD_PROGRAMS = {"cd", "chdir", "pushd", "set-location", "sl", "push-location"}
_GIT_GLOBAL_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_GIT_CLONE_VALUE_FLAGS = {
    "-b", "--branch", "-o", "--origin", "-c", "--config", "--depth", "--reference",
    "--reference-if-able", "--separate-git-dir", "-j", "--jobs", "--filter", "--template",
    "-u", "--upload-pack", "--shallow-since", "--shallow-exclude", "--server-option",
    "--bundle-uri", "--revision",
}
_GIT_FETCHING = {"clone", "fetch", "pull", "submodule"}
_VCS_FETCHING = {"svn": {"checkout", "co", "export", "update", "up"}, "hg": {"clone", "pull"}}
# gh subcommands that never return content from GitHub.
_GH_LOCAL = {"auth", "config", "alias", "extension", "help", "completion", "--version", "--help"}
_UNPACKERS_STRIP = {".gz": "", ".tgz": ".tar", ".bz2": "", ".xz": "", ".zst": ""}


def _key(path: Any, base: Path) -> str:
    """Like `resolve`, but relative to `base` and case-folded where the file system is."""
    return os.path.normcase(resolve(str(path), base))


def _positionals(tokens: list[str], value_flags: set[str]) -> list[str]:
    out: list[str] = []
    skip = False
    rest = False
    for token in tokens:
        if skip:
            skip = False
        elif rest or not token.startswith("-") or token == "-":
            out.append(token)
        elif token == "--":
            rest = True
        elif token in value_flags:
            skip = True
    return out


def _flag_value(tokens: list[str], names: set[str], *, folded: bool = False) -> Optional[str]:
    """The value of the first of `names` (`-d DIR`, `--dest=DIR`), or None."""
    for i, token in enumerate(tokens):
        probe = token.lower() if folded else token
        if probe in names and i + 1 < len(tokens):
            return tokens[i + 1]
        flag, eq, value = probe.partition("=")
        if eq and flag in names and value:
            return token.split("=", 1)[1]
    return None


def _repo_dir(source: str, *, bare: bool = False) -> str:
    name = re.split(r"[/:\\]", source.rstrip("/\\"))[-1]
    name = name[:-4] if name.endswith(".git") else name
    return (name + ".git" if bare else name) if name else ""


def _url_name(url: str) -> str:
    path = url.split("://", 1)[-1].split("?", 1)[0].split("#", 1)[0]
    if "/" not in path or path.endswith("/"):
        return "index.html"
    return path.rsplit("/", 1)[-1] or "index.html"


def _url_host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0].split("@")[-1].split(":", 1)[0]


def _pip_download_index(argv: list[str]) -> int:
    """Index of `download` in `pip download` / `python -m pip download`, or -1."""
    lowered = [a.lower() for a in argv]
    for i, token in enumerate(lowered):
        if token == "download" and i > 0:
            head = [Path(t).name for t in lowered[:i]]
            head = [t[:-4] if t.endswith(".exe") else t for t in head]
            if any(re.fullmatch(r"pip(\d+(\.\d+)?)?", t) for t in head):
                return i
    return -1


def _git_parts(argv: list[str], cwd: Path) -> tuple[str, list[str], Path]:
    """(git sub-command, its arguments, the folder it runs in)."""
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] in _GIT_GLOBAL_VALUE_FLAGS and i + 1 < len(argv):
            if argv[i] == "-C":
                cwd = cwd / argv[i + 1]
            i += 2
        else:
            i += 1
    if i >= len(argv):
        return "", [], cwd
    return argv[i].lower(), argv[i + 1:], cwd


def fetches_outside_files(command: str) -> bool:
    """A shell command that brings files or text from a remote repository or package index
    without naming a URL: `git clone git@host:a/b`, `git pull`, `gh pr view`, `pip download`."""
    for argv in _sub_commands(command):
        program = _program(argv)
        if program == "git" and _git_parts(argv, Path("."))[0] in _GIT_FETCHING:
            return True
        if program in _VCS_FETCHING and len(argv) > 1 and argv[1].lower() in _VCS_FETCHING[program]:
            return True
        if program == "gh" and len(argv) > 1 and argv[1].lower() not in _GH_LOCAL:
            return True
        if _pip_download_index(argv) > 0:
            return True
    return False


@dataclass
class IntakePlan:
    """What one shell command brings in from outside, worked out before it runs."""

    cwd: Path
    paths: list[str] = field(default_factory=list)  # resolved files/folders it creates
    watch: dict[str, set[str]] = field(default_factory=dict)  # folder -> names before the call
    brings_outside_in: bool = False  # unpacking an outside archive also lists its names


class _Planner:
    def __init__(self, files: "SessionFiles", cwd: Path) -> None:
        self.files = files
        self.plan = IntakePlan(cwd=cwd)

    def add(self, path: Path) -> None:
        self.plan.paths.append(_key(path, self.files.root))

    def into(self, folder: Path) -> None:
        """Content lands in `folder`: all of it when the call creates the folder, else only
        the names that appear in it."""
        key = _key(folder, self.files.root)
        try:
            if Path(key).is_dir():
                self.plan.watch.setdefault(key, set(os.listdir(key)))
                return
        except OSError:
            return
        self.plan.paths.append(key)

    def outside(self, path: Path) -> bool:
        key = _key(path, self.files.root)
        return self.files.from_outside(key) or any(
            key == p or key.startswith(p + os.sep) for p in self.plan.paths
        )

    def step(self, argv: list[str], cwd: Path) -> None:
        program = _program(argv)
        args = argv[1:]
        if program == "git":
            sub, rest, gcwd = _git_parts(argv, cwd)
            if sub == "clone":
                pos = _positionals(rest, _GIT_CLONE_VALUE_FLAGS)
                bare = any(a in ("--bare", "--mirror") for a in rest)
                if len(pos) > 1:
                    self.into(gcwd / pos[1])
                elif pos and _repo_dir(pos[0], bare=bare):
                    self.into(gcwd / _repo_dir(pos[0], bare=bare))
            elif sub == "submodule" and rest[:1] == ["add"]:
                pos = _positionals(rest[1:], {"-b", "--branch", "--name", "--reference", "--depth"})
                if len(pos) > 1:
                    self.into(gcwd / pos[1])
                elif pos and _repo_dir(pos[0]):
                    self.into(gcwd / _repo_dir(pos[0]))
        elif program == "gh" and len(args) > 1 and args[1].lower() == "clone":
            pos = _positionals(args[2:], set())
            if len(pos) > 1:
                self.into(cwd / pos[1])
            elif pos and _repo_dir(pos[0]):
                self.into(cwd / _repo_dir(pos[0]))
        elif program == "gh" and args[:2] == ["release", "download"]:
            folder = _flag_value(args, {"-D", "--dir"})
            self.into(cwd / folder if folder else cwd)
        elif program == "wget":
            self._wget(args, cwd)
        elif program == "curl":
            self._curl(args, cwd)
        elif program == "start-bitstransfer":
            dest = _flag_value(args, {"-destination"}, folded=True)
            source = _flag_value(args, {"-source"}, folded=True) or ""
            if dest:
                target = cwd / dest
                self.add(target / _url_name(source) if target.is_dir() else target)
        elif _pip_download_index(argv) > 0:
            folder = _flag_value(args, {"-d", "--dest", "--destination-directory"})
            self.into(cwd / folder if folder else cwd)
        else:
            self._unpack(program, argv, cwd)

    def _wget(self, args: list[str], cwd: Path) -> None:
        if _flag_value(args, {"-O", "--output-document"}):
            return  # recorded as a download by `_shell_download_paths`
        prefix = _flag_value(args, {"-P", "--directory-prefix"})
        base = cwd / prefix if prefix else cwd
        urls = [a for a in args if "://" in a]
        recursive = any(a in ("-r", "--recursive", "-m", "--mirror") for a in args)
        flat = any(a in ("-nH", "--no-host-directories", "-nd", "--no-directories") for a in args)
        if any(a in ("-i", "--input-file", "--content-disposition") for a in args) or (recursive and flat):
            self.into(base)
            return
        for url in urls:
            # A recursive fetch lands in a folder named after the host.
            self.add(base / (_url_host(url) if recursive else _url_name(url)))

    def _curl(self, args: list[str], cwd: Path) -> None:
        folder = _flag_value(args, {"--output-dir"})
        base = cwd / folder if folder else cwd
        urls = [a for a in args if "://" in a]
        remote = False
        for i, token in enumerate(args):
            letters = token[1:] if token.startswith("-") and not token.startswith("--") else ""
            if token in ("-o", "--output") or (letters.endswith("o") and len(letters) > 1):
                if i + 1 < len(args):
                    self.add(base / args[i + 1])
            elif token.startswith("--output="):
                self.add(base / token.split("=", 1)[1])
            if token in ("--remote-name", "--remote-name-all") or "O" in letters:
                remote = True
        if remote and any(a in ("-J", "--remote-header-name") for a in args):
            self.into(base)
        elif remote:
            for url in urls if "--remote-name-all" in args else urls[:1]:
                self.add(base / _url_name(url))

    def _unpack(self, program: str, argv: list[str], cwd: Path) -> None:
        args = argv[1:]
        archive: Optional[str] = None
        dest: Optional[str] = None
        outputs: list[Path] = []
        if program in ("tar", "bsdtar"):
            old_style = args[0] if args and not args[0].startswith("-") else ""  # `tar xzf a.tgz`
            short = [a[1:] for a in args if a.startswith("-") and not a.startswith("--")]
            if not ("--extract" in args or "--get" in args or "x" in old_style or any("x" in s for s in short)):
                return
            archive = _flag_value(args, {"-f", "--file"})
            if archive is None:
                for i, token in enumerate(args):
                    body = token.lstrip("-")
                    if not token.startswith("--") and body.endswith("f") and i + 1 < len(args):
                        archive = args[i + 1]
                        break
            dest = _flag_value(args, {"-C", "--directory"})
        elif program == "unzip":
            pos = _positionals(args, {"-d", "-P", "-x"})
            archive = pos[0] if pos else None
            dest = _flag_value(args, {"-d"})
        elif program in ("7z", "7za", "7zz", "7zr") and args[:1] and args[0].lower() in ("x", "e"):
            pos = _positionals(args[1:], set())
            archive = pos[0] if pos else None
            dest = next((a[2:] for a in args if a.startswith("-o") and len(a) > 2), None)
        elif program == "expand-archive":
            archive = _flag_value(args, {"-path", "-literalpath"}, folded=True)
            dest = _flag_value(args, {"-destinationpath"}, folded=True)
            pos = [a for a in args if not a.startswith("-") and a not in (archive, dest)]
            archive = archive or (pos[0] if pos else None)
            dest = dest or (pos[1] if len(pos) > 1 else None)
        elif program in ("jar",) and args[:1] and "x" in args[0]:
            pos = _positionals(args[1:], set())
            archive = pos[0] if pos else None
        elif program in ("python", "python3", "py") and "-m" in args:
            module = args[args.index("-m") + 1:]
            if module[:2] in (["zipfile", "-e"], ["tarfile", "-e"]) and len(module) > 2:
                archive = module[2]
                dest = module[3] if len(module) > 3 else None
        elif program in ("gunzip", "bunzip2", "unxz", "unzstd") or (
            program in ("gzip", "bzip2", "xz", "zstd") and any(a in ("-d", "--decompress") for a in args)
        ):
            for name in _positionals(args, set()):
                path = cwd / name
                suffix = path.suffix.lower()
                if suffix in _UNPACKERS_STRIP and self.outside(path):
                    self.plan.brings_outside_in = True
                    outputs.append(path.with_suffix(_UNPACKERS_STRIP[suffix]))
            for out in outputs:
                self.add(out)
            return
        if not archive or not self.outside(cwd / archive):
            return
        self.plan.brings_outside_in = True
        self.into(cwd / dest if dest else cwd)


def _walk(command: str, cwd: Path) -> list[tuple[list[str], Path]]:
    """Each sub-command with the folder it runs in, following `cd` within the command."""
    out: list[tuple[list[str], Path]] = []
    for argv in _sub_commands(command):
        out.append((argv, cwd))
        if _program(argv) in _CD_PROGRAMS:
            pos = _positionals(argv[1:], set())
            target = _flag_value(argv[1:], {"-path", "-literalpath"}, folded=True) or (pos[0] if pos else None)
            if target and target not in ("-", "~"):
                cwd = cwd / target
    return out


def referenced_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    """Paths a PROPOSED call would run or act on. Shell only in phase 1: it is where the
    write-then-execute chain lands, and where the command text hides the effect."""
    if tool_name == "run_shell":
        return command_paths(str((arguments or {}).get("command", "")))
    return []


class SessionFiles:
    """Per-session record of what the agent created. Runtime-only, like the engine's other
    reviewer state: a restart starts clean rather than inheriting stale provenance."""

    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root)
        self._files: dict[str, Origin] = {}
        # OPE-235: files and folders whose content came from outside this session, beyond
        # the DOWNLOADED entries above (clones, unpacked archives, spilled tool output).
        # Kept apart from `_files` so it informs the reviewer's facts without changing which
        # commands count as running a downloaded file.
        self._outside: set[str] = set()

    def before(
        self, tool_name: str, arguments: dict[str, Any], *, cwd: Optional[Path | str] = None
    ) -> Optional[IntakePlan]:
        """What a shell command is about to bring in from outside, worked out before it
        runs so folders it adds to can be compared after. None for every other tool."""
        if tool_name != "run_shell":
            return None
        planner = _Planner(self, Path(cwd) if cwd else self.root)
        for argv, at in _walk(str((arguments or {}).get("command", "")), planner.plan.cwd):
            planner.step(argv, at if at.is_absolute() else self.root / at)
        return planner.plan

    def record(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        *,
        step: int,
        cwd: Optional[Path | str] = None,
        plan: Optional[IntakePlan] = None,
    ) -> None:
        """Note what a SUCCESSFUL call created. Callers must not record failed calls: a
        write that raised left nothing on disk to run."""
        if tool_name == "run_shell":
            plan = plan or self.before(tool_name, arguments, cwd=cwd)
            self._outside.update(plan.paths if plan else ())
            for folder, names in (plan.watch if plan else {}).items():
                try:
                    appeared = set(os.listdir(folder)) - names
                except OSError:
                    continue
                self._outside.update(os.path.normcase(os.path.join(folder, n)) for n in appeared)
        elif tool_name in _DOWNLOAD_RESULT_TOOLS and isinstance(result, dict) and result.get("path"):
            self._outside.add(_key(result["path"], self.root))
        paths, origin = created_paths(tool_name, arguments, result)
        for path in paths:
            key = resolve(path, self.root)
            if self._is_device(key):
                continue  # A null/device sink does not retain downloaded script bytes.
            # A later write or download over the same path wins — the newer bytes are the
            # ones that would execute.
            self._files[key] = Origin(step=step, kind=origin)

    def wrote(self, path: Path | str) -> bool:
        """True when the agent's own write (not a download) produced this file's current
        bytes this session. Overwriting it loses nothing of the user's (OPE-233)."""
        origin = self._files.get(resolve(str(path), self.root))
        return origin is not None and origin.kind == WRITTEN

    def downloaded(self, path: Path | str) -> bool:
        """True when this file's current bytes came from a download this session (OPE-235:
        reading it brings outside content into the agent's context)."""
        origin = self._files.get(resolve(str(path), self.root))
        return origin is not None and origin.kind == DOWNLOADED

    def mark_outside(self, path: Path | str) -> None:
        """Note a file the engine itself wrote from outside content (a spilled tool result,
        a compacted transcript)."""
        self._outside.add(_key(path, self.root))

    def _outside_keys(self) -> set[str]:
        downloaded = {os.path.normcase(k) for k, o in self._files.items() if o.kind == DOWNLOADED}
        return self._outside | downloaded

    def from_outside(self, path: Path | str, *, cwd: Optional[Path | str] = None) -> bool:
        """True when this file came from outside this session, or sits in a folder that did.
        The user's own files never do: only what this session brought in is recorded."""
        keys = self._outside_keys()
        if not keys:
            return False
        key = Path(_key(path, Path(cwd) if cwd else self.root))
        return any(str(p) in keys for p in (key, *key.parents))

    def _holds_outside(self, key: str, keys: set[str]) -> bool:
        return any(k.startswith(key.rstrip(os.sep) + os.sep) for k in keys)

    def reads_outside(
        self, tool_name: str, arguments: dict[str, Any], *, cwd: Optional[Path | str] = None
    ) -> bool:
        """Whether this call reads content that came from outside: it names such a file, a
        folder holding one, or (shell) runs inside such a folder. Writes read nothing."""
        from .risk import WRITE_TOOLS

        keys = self._outside_keys()
        if not keys or tool_name in WRITE_TOOLS:
            return False
        args = arguments or {}

        def touches(token: str, base: Path) -> bool:
            key = _key(token, base)
            return self.from_outside(key) or self._holds_outside(key, keys)

        if tool_name != "run_shell":
            values = []
            for name in ("path", "file", "files", "filename", "directory", "dir", "source", "src"):
                value = args.get(name)
                values += value if isinstance(value, (list, tuple)) else [value]
            return any(touches(str(v), self.root) for v in values if isinstance(v, str) and v)
        start = Path(cwd) if cwd else self.root
        for argv, at in _walk(str(args.get("command", "")), start):
            at = at if at.is_absolute() else self.root / at
            if self.from_outside(at):
                return True  # running inside a cloned or unpacked folder
            for token in argv:
                token = token.split("=", 1)[-1].lstrip("@")  # --file=x, curl -d @x
                if token and not token.startswith("-") and "://" not in token and touches(token, at):
                    return True
        return False

    @staticmethod
    def _is_device(path: str) -> bool:
        try:
            mode = Path(path).stat().st_mode
        except (OSError, ValueError):
            return False  # Unknown paths retain conservative provenance handling.
        return stat.S_ISCHR(mode) or stat.S_ISBLK(mode)

    def match(
        self, tool_name: str, arguments: dict[str, Any], *, step: int
    ) -> Optional[Match]:
        """The most recently created path this call names, or None. Newest wins: it is the
        one whose contents the agent most recently controlled."""
        best: Optional[Match] = None
        for path in referenced_paths(tool_name, arguments):
            key = resolve(path, self.root)
            if self._is_device(key):
                continue  # Also ignore device entries recorded by an older runtime.
            origin = self._files.get(key)
            if origin is None:
                continue
            candidate = Match(
                path=path, origin=origin, steps_ago=max(step - origin.step, 0)
            )
            if best is None or candidate.origin.step > best.origin.step:
                best = candidate
        return best
