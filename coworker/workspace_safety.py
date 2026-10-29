"""What code can establish about a workspace write without asking a model (OPE-233).

Auto-Approve lets a write skip the reviewer when undoing it is easy and nothing about it
can execute later: a plain document or raster image that no build, installer, test runner
or other AI tool reads as instructions, a name and content that don't look secret, and a
target that is new, git-tracked with no uncommitted changes, or written by the agent
earlier in this session. JSON and YAML are never eligible: tools read them to decide what
to run, and no list of their names can be complete, so the reviewer sees that content.

The line between a document and an excluded file: a file leaves the tier when its purpose
is to tell a tool what to do (requirements.txt, CMakeLists.txt, CLAUDE.md), when a tool
runs it with no opt-in (test*.txt under default pytest), or when merely opening it can act
(a spreadsheet cell that starts a formula). A file whose purpose is prose stays, even if a
project can opt in to running it (a README under `cargo test`, a MyST notebook under
`jupyter-book build`): the command that would run it still reaches the reviewer.

Every check fails toward "ask the reviewer": an unknown file type, an unreadable git state
or a doubtful name keeps today's behaviour.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

# Plain documents and raster images: nothing here is loaded and run by a build, test
# runner, package manager, shell or browser. Formats that can carry script are NOT here,
# however document-like: .html/.htm and .svg run JavaScript when opened, .xml can pull in
# a stylesheet that does, and .pdf supports JavaScript in many readers. A live run showed
# why (2026-10-26): refused an upload, the agent wrote an .html page whose script did the
# upload, then tried to open it. JSON and YAML are not here either (Devika, 28 Oct 2026):
# package managers, task runners, CI and deploy tools read them to decide what to run.
_DOCUMENT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".rst", ".csv", ".tsv", ".log",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
})
# Text files that decide what gets built, installed or tested: CMake runs CMakeLists.txt,
# pip installs what requirements and constraints files list (`requirements-dev.txt`,
# `dev-requirements.txt`, `requirements/base.txt`), platforms install what runtime.txt
# (Heroku), packages.txt (Streamlit Cloud) and apt.txt (Binder) name, and pytest runs
# `test*.txt` as doctests by default.
_BUILD_INPUT_NAME = re.compile(
    r"^(cmakelists|runtime|packages|apt)\.txt$|^test.*\.txt$"
    r"|(requirements|constraints).*\.txt$",
    re.IGNORECASE,
)
# Only `.txt` files under these folders: pip reads `requirements/base.txt`, not a Markdown
# file kept there.
_BUILD_INPUT_FOLDERS = frozenset({"requirements"})
# Instructions other AI coding tools read into their prompt on their next run, so a write
# here steers a later agent. AGENTS.md is not listed: the protected list (permissions.py)
# already keeps it human-only, before this tier is consulted.
_AGENT_INSTRUCTION_NAME = re.compile(
    r"^(claude|claude\.local|gemini|qwen|crush|warp|agent|agents\.override|conventions"
    r"|copilot-instructions|skill)\.md$"
    r"|\.(instructions|prompt|chatmode|agent)\.md$",  # GitHub Copilot's per-topic files
    re.IGNORECASE,
)
# Every file in these folders, at any depth: Claude Code, Cursor, Windsurf, Cline, Roo,
# Kilo Code, Kiro, Amazon Q, Continue, Junie, JetBrains AI Assistant, Augment, Gemini,
# Antigravity, OpenHands, Trae, Codex, OpenCode and Zencoder keep rules, commands,
# prompts and skills there.
_AGENT_FOLDERS = frozenset({
    ".claude", ".cursor", ".windsurf", ".clinerules", ".roo", ".kilocode", ".kiro",
    ".amazonq", ".continue", ".junie", ".aiassistant", ".augment", ".gemini", ".agent",
    ".agents", ".openhands", ".trae", ".codex", ".opencode", ".zencoder",
})
# Under .github/, only Copilot's folders: CONTRIBUTING.md and issue templates stay eligible.
_GITHUB_AGENT_FOLDERS = frozenset({"instructions", "prompts", "chatmodes", "agents", "skills"})

# Spreadsheet apps evaluate a cell that starts with = @ + or - when a .csv/.tsv is opened,
# and opening a file is not a command anyone reviews. A signed cell is a number only when
# all of it is one (-5, +1.5, -.5, -2e3): `-2+3+cmd|...` is a formula.
_SPREADSHEET_DELIMITERS = {".csv": ",", ".tsv": "\t"}
_NUMBER_CELL = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?%?")

_SENSITIVE_NAME = re.compile(
    r"^\.env(\..*)?$"
    r"|\.(pem|key|p12|pfx|kdbx|keystore|jks|ppk|gpg|asc)$"
    r"|^id_(rsa|dsa|ecdsa|ed25519)"
    r"|(^|[._-])(secret|secrets|credential|credentials|password|passwd|token|tokens"
    r"|apikey|api[_-]key|private[_-]key)([._-]|$)"
    r"|^\.(netrc|npmrc|pypirc|pgpass)$",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\b(AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\b(api[_-]?key|secret|token|password|passwd|access[_-]?key)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+=]{16,}",
    re.IGNORECASE,
)

# Git states this module reports for an existing file.
TRACKED_CLEAN = "tracked_clean"
TRACKED_MODIFIED = "tracked_modified"
UNTRACKED = "untracked"
IGNORED = "ignored"
UNKNOWN = "unknown"


def is_inert_name(name: str) -> bool:
    if os.path.splitext(name.lower())[1] not in _DOCUMENT_SUFFIXES:
        return False
    return not (_BUILD_INPUT_NAME.search(name) or _AGENT_INSTRUCTION_NAME.search(name))


def is_inert_path(path: Path, root: Optional[Path] = None) -> bool:
    """`is_inert_name`, and not inside a folder that build tools or AI tools read from.
    Folder names match in any letter case, at any depth under `root` (a writable root the
    path is in); the root's own name and everything above it are the user's choice and
    are not matched, so a workspace called `requirements` keeps the tier for its notes.
    Without a root every folder counts, and a doubtful match only sends the write to the
    reviewer."""
    if not is_inert_name(path.name):
        return False
    inside = path
    if root is not None:
        try:
            inside = path.relative_to(root)
        except ValueError:
            pass
    folders = [part.casefold() for part in inside.parts[:-1]]
    if any(f in _AGENT_FOLDERS for f in folders):
        return False
    if path.suffix.lower() == ".txt" and any(f in _BUILD_INPUT_FOLDERS for f in folders):
        return False
    return not any(
        parent == ".github" and child in _GITHUB_AGENT_FOLDERS
        for parent, child in zip(folders, folders[1:])
    )


def _root_of(path: Path, roots: Optional[list[Path]]) -> Optional[Path]:
    for root in roots or []:
        try:
            path.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def is_sensitive_name(name: str) -> bool:
    return bool(_SENSITIVE_NAME.search(name))


def _strings(value: Any, depth: int = 0) -> list[str]:
    """Every string inside the arguments, raw (not JSON-escaped, so quotes and newlines
    look the way they will on disk)."""
    if depth > 6:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v, depth + 1)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v, depth + 1)]
    return []


def has_secret_value(arguments: dict[str, Any]) -> bool:
    return any(_SECRET_VALUE.search(text) for text in _strings(arguments))


def _written_lines(arguments: dict[str, Any]) -> list[str]:
    """The lines a write puts on disk: whole string arguments, or for a patch or diff only
    its added lines, without the leading `+`."""
    out: list[str] = []
    for key, value in (arguments or {}).items():
        if key in ("patch", "diff") and isinstance(value, str):
            out.extend(
                ln[1:] for ln in value.splitlines() if ln.startswith("+") and not ln.startswith("+++")
            )
        else:
            out.extend(ln for text in _strings(value) for ln in text.splitlines())
    return out


def _starts_formula(field: str) -> bool:
    cell = field.strip().strip("\"'").strip()  # apps drop the quotes before evaluating
    if not cell:
        return False
    if cell[0] in "=@":
        return True
    if cell[0] in "+-":
        return cell != "-" and not _NUMBER_CELL.fullmatch(cell)  # a lone dash marks "none"
    return False


def has_formula_cell(arguments: dict[str, Any], delimiter: str = ",") -> bool:
    """True when any cell in the written text starts a spreadsheet formula. Lines are split
    on `delimiter` with quoting as a spreadsheet app reads it, so a comma inside a quoted
    cell doesn't start a new one. Call it only for .csv/.tsv targets; a line the csv
    module can't parse counts as a formula (reviewer)."""
    for line in _written_lines(arguments):
        try:
            fields = next(csv.reader([line], delimiter=delimiter), [])
        except csv.Error:
            return True
        if any(_starts_formula(f) for f in fields):
            return True
    return False


def _git(args: list[str], cwd: Path) -> Optional[subprocess.CompletedProcess]:
    exe = shutil.which("git")
    if exe is None:
        return None
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # never take the index lock to look
    try:
        return subprocess.run(
            [exe, *args], cwd=str(cwd), capture_output=True, timeout=5, env=env, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git_file_state(path: Path) -> str:
    """State of an EXISTING file: tracked_clean, tracked_modified, untracked, ignored, or
    unknown (no git, not a repository, or git failed)."""
    folder = path.parent
    top = _git(["rev-parse", "--show-toplevel"], folder)
    if top is None or top.returncode != 0:
        return UNKNOWN
    root = Path(top.stdout.decode("utf-8", "replace").strip())
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return UNKNOWN
    tracked = _git(["ls-files", "--error-unmatch", "--", rel], root)
    if tracked is None:
        return UNKNOWN
    status = _git(["status", "--porcelain=v1", "--untracked-files=all", "--ignored", "--", rel], root)
    if status is None or status.returncode != 0:
        return UNKNOWN
    lines = [ln for ln in status.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    if tracked.returncode == 0:
        return TRACKED_CLEAN if not lines else TRACKED_MODIFIED
    if any(ln.startswith("!!") for ln in lines):
        return IGNORED
    return UNTRACKED


# A patch that deletes a file, or moves one (which removes the original name). Deletes always
# reach the reviewer, even when git could restore the file (Devika, 26 Oct 2026): a clearly
# requested delete is allowed there, one nobody asked for becomes a card.
_REMOVES_FILE = re.compile(r"^\*\*\* (?:Delete File|Move to): ", re.MULTILINE)


def removes_files(arguments: dict[str, Any]) -> bool:
    """True when a write removes or renames a file (a patch's Delete File or Move to)."""
    return bool(_REMOVES_FILE.search(str((arguments or {}).get("patch", ""))))


def inert_write_check(
    arguments: dict[str, Any],
    targets: list[Path],
    *,
    agent_wrote: Optional[Callable[[Path], bool]] = None,
    roots: Optional[list[Path]] = None,
) -> tuple[bool, str]:
    """(eligible, detail) for a write whose targets already passed the gate's floors (in a
    writable root, not protected). `roots` are the resolved writable roots, so folder rules
    look only below them. `detail` says what made it eligible, for the audit row."""
    if not targets:
        return False, ""
    if has_secret_value(arguments):
        return False, ""
    if removes_files(arguments):
        return False, ""
    for delimiter in {_SPREADSHEET_DELIMITERS.get(t.suffix.lower()) for t in targets} - {None}:
        if has_formula_cell(arguments, delimiter):
            return False, ""
    parts: list[str] = []
    for target in targets:
        name = target.name
        if not is_inert_path(target, _root_of(target, roots)) or is_sensitive_name(name):
            return False, ""
        if not target.exists():
            parts.append(f"new file {name}")
        elif agent_wrote is not None and agent_wrote(target):
            parts.append(f"{name} written by the agent this session")
        elif git_file_state(target) == TRACKED_CLEAN:
            parts.append(f"{name} unchanged in git")
        else:
            return False, ""
    return True, "; ".join(parts)
