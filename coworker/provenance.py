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

import shlex
from dataclasses import dataclass
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

    def record(
        self, tool_name: str, arguments: dict[str, Any], result: Any, *, step: int
    ) -> None:
        """Note what a SUCCESSFUL call created. Callers must not record failed calls: a
        write that raised left nothing on disk to run."""
        paths, origin = created_paths(tool_name, arguments, result)
        for path in paths:
            # A later write or download over the same path wins — the newer bytes are the
            # ones that would execute.
            self._files[resolve(path, self.root)] = Origin(step=step, kind=origin)

    def match(
        self, tool_name: str, arguments: dict[str, Any], *, step: int
    ) -> Optional[Match]:
        """The most recently created path this call names, or None. Newest wins: it is the
        one whose contents the agent most recently controlled."""
        best: Optional[Match] = None
        for path in referenced_paths(tool_name, arguments):
            origin = self._files.get(resolve(path, self.root))
            if origin is None:
                continue
            candidate = Match(
                path=path, origin=origin, steps_ago=max(step - origin.step, 0)
            )
            if best is None or candidate.origin.step > best.origin.step:
                best = candidate
        return best
