"""The project's own git settings, judged before a git command runs without a card (OPE-232).

Git obeys settings stored inside the project: `.git/config`, the files it includes and the
per-worktree config. Some of them name programs that ordinary reading commands then run
(diff and textconv drivers, filters, the file-system monitor, the hooks folder); others
point git at files or history elsewhere. A project's settings are only as trusted as the
project, so the read-only grant covers a git command only when every project-level
setting is on the short list below of ones known to be inert, the repository lies inside
the folders, and every initialised submodule passes the same check. The person's own
settings (global and system scope) are trusted. Whatever the check cannot establish asks.

Listing settings never runs them: the probes here are `rev-parse`, `config --list` and
`worktree list`, run without a pager and without a terminal.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

_TRUSTED_SCOPES = frozenset({"system", "global"})
_TIMEOUT = 10.0
_MAX_SUBMODULE_DEPTH = 3
_MAX_SUBMODULES = 64

# Project-level settings a clone or an everyday checkout carries. Everything else asks,
# including any setting that names a program, a file or another folder.
_INERT_KEYS = frozenset({
    "core.repositoryformatversion", "core.filemode", "core.bare", "core.logallrefupdates",
    "core.ignorecase", "core.precomposeunicode", "core.symlinks", "core.autocrlf", "core.eol",
    "core.safecrlf", "core.longpaths", "core.protectntfs", "core.protecthfs",
    "core.hidedotfiles", "core.trustctime", "core.checkstat", "core.quotepath",
    "core.sparsecheckout", "core.sparsecheckoutcone", "core.untrackedcache",
    "core.splitindex", "core.preloadindex", "core.fsyncobjectfiles", "core.compression",
    "core.bigfilethreshold", "core.sharedrepository", "core.commentchar", "core.whitespace",
    "user.name", "user.email", "user.signingkey", "user.useconfigonly",
    "pull.rebase", "pull.ff", "push.default", "push.autosetupremote", "push.followtags",
    "fetch.prune", "fetch.prunetags", "fetch.writecommitgraph", "fetch.showforcedupdates",
    "init.defaultbranch", "rebase.autostash", "rebase.autosquash", "rebase.updaterefs",
    "merge.ff", "merge.conflictstyle", "status.showuntrackedfiles",
    "status.submodulesummary", "diff.renames", "diff.algorithm", "diff.noprefix",
    "diff.mnemonicprefix", "diff.colormoved", "log.date", "log.decorate",
    "log.abbrevcommit", "log.follow", "commit.verbose", "commit.cleanup", "rerere.enabled",
    "rerere.autoupdate", "gc.auto", "gc.autodetach", "maintenance.auto",
    "maintenance.strategy", "index.version", "index.skiphash", "index.threads",
    "feature.manyfiles", "feature.experimental", "extensions.worktreeconfig",
    "extensions.objectformat", "extensions.refstorage", "extensions.compatobjectformat",
    "extensions.preciousobjects", "lfs.repositoryformatversion", "submodule.active",
    "submodule.recurse", "worktree.guessremote", "help.autocorrect",
})
_INERT_PATTERNS = tuple(
    re.compile(p)
    for p in (
        # not `promisor` or `partialclonefilter`: reading a missing object then fetches it
        r"^remote\..+\.(url|pushurl|fetch|push|mirror|prune|prunetags|tagopt"
        r"|skipdefaultupdate|skipfetchall|gh-resolved)$",
        r"^branch\..+\.(remote|merge|rebase|pushremote|description)$",
        r"^submodule\..+\.(url|active|branch|ignore|fetchrecursesubmodules)$",
        r"^lfs\.https?://.+\.(access|locksverify)$",
        r"^http\.(.+\.)?extraheader$",
        r"^(color|column|advice)\.",
        r"^pack\.(usesparse|usebitmaps|writereverseindex|window|depth|threads)$",
    )
)


# Settings in the person's own scopes that name a program are the person's choice, but a
# project switches them on through its attributes (`diff=NAME`, `filter=NAME`), so a
# project that does asks. Two drivers that ship with git itself are the exception:
# installing Git LFS is the choice to run it on every LFS project, and Git for Windows
# defines its text converter for documents on every machine.
_DRIVER_KEY = re.compile(r"^diff\.(.+)\.(?:command|textconv)$|^filter\.(.+)\.(?:clean|smudge|process)$")
_STANDARD_DRIVERS = {
    "filter.lfs.clean": "git-lfs clean -- %f",
    "filter.lfs.smudge": "git-lfs smudge -- %f",
    "filter.lfs.process": "git-lfs filter-process",
    "diff.astextplain.textconv": "astextplain",
}
_MAX_TREE_ENTRIES = 200_000
_MAX_GIT_DIR_ENTRIES = 500_000
_MAX_ALTERNATE_DEPTH = 5
# Environment variables that make git run a program, read attributes from elsewhere, or
# read objects or the index from elsewhere. The granted command inherits the engine's
# environment, so the engine's is what is judged.
_STEERING_ENV = ("GIT_EXTERNAL_DIFF", "GIT_ATTR_SOURCE")
_OBJECTS_ENV = ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_OBJECT_DIRECTORY")
_FILES_ENV = ("GIT_INDEX_FILE",)


def _inert(key: str) -> bool:
    lowered = key.lower()
    return lowered in _INERT_KEYS or any(p.match(lowered) for p in _INERT_PATTERNS)


def _driver(key: str, value: str) -> Optional[tuple[str, str]]:
    """("diff" | "filter", name) when a setting defines a driver program, else None."""
    lowered = key.lower()
    if _STANDARD_DRIVERS.get(lowered) == value.strip():
        return None
    found = _DRIVER_KEY.match(lowered)
    if found is None:
        return None
    return ("diff", found.group(1)) if found.group(1) else ("filter", found.group(2))


def _environment_ok(inside: Callable[[Path], bool]) -> bool:
    if any(os.environ.get(name) for name in _STEERING_ENV):
        return False
    for name in _FILES_ENV:
        item = os.environ.get(name)
        if item and not inside(Path(item).resolve()):
            return False
    for name in _OBJECTS_ENV:
        for item in filter(None, os.environ.get(name, "").split(os.pathsep)):
            if not _objects_inside(Path(item).resolve(), inside, set()):
                return False
    return True


def _objects_inside(objects: Path, inside: Callable[[Path], bool], seen: set[str]) -> bool:
    """An object folder, and every folder its alternates file borrows from in turn, must lie
    inside the folders. Git follows alternates a few levels deep; so does this."""
    key = os.path.normcase(str(objects))
    if key in seen:
        return True
    seen.add(key)
    if len(seen) > _MAX_ALTERNATE_DEPTH or not inside(objects):
        return False
    alternates = objects / "info" / "alternates"
    if not alternates.is_file():
        return True
    for line in alternates.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith('"'):
            return False  # a quoted path; not worth unquoting
        if not _objects_inside((objects / line).resolve(), inside, seen):
            return False
    return True


def _no_links_under(folders: list[Path]) -> bool:
    """Git reads objects, refs, logs and the rest through whatever these git folders hold,
    at any depth; a link or junction anywhere in them could lead anywhere."""
    seen = 0
    pending = list(folders)
    while pending:
        folder = pending.pop()
        if not folder.is_dir():
            continue
        with os.scandir(folder) as entries:
            for entry in entries:
                seen += 1
                if seen > _MAX_GIT_DIR_ENTRIES:
                    return False
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    return False
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return True


def _attributes_present(top: Path, common_dir: Path) -> Optional[bool]:
    """Whether the project carries any attributes at all: an attributes file anywhere in
    it (on disk, in another case, or kept only in the index), the repository's own
    attributes file, or a nested repository whose files the listing cannot see. Patterns
    in them apply to paths in the history too, so this asks about sources, not paths.
    None when the project is too large to list."""
    if (common_dir / "info" / "attributes").exists():
        return True
    listed = _git(["ls-files", "-z", "--cached", "--others"], top)
    if listed is None or listed.returncode != 0:
        return None
    entries = listed.stdout.split(b"\0")
    if len(entries) > _MAX_TREE_ENTRIES:
        return None
    for entry in entries:
        name = _text(entry)
        if name.endswith("/") or name.rsplit("/", 1)[-1].lower() == ".gitattributes":
            return True
    return False


def _git(args: list[str], cwd: Path, data: Optional[bytes] = None) -> Optional[subprocess.CompletedProcess]:
    exe = shutil.which("git")
    if exe is None:
        return None
    env = {**os.environ, "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    feed = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
    try:
        return subprocess.run(
            [exe, "--no-pager", *args],
            cwd=str(cwd),
            capture_output=True,
            env=env,
            timeout=_TIMEOUT,
            **feed,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _lists_worktree(common_dir: Path, top: Path, cwd: Path) -> bool:
    """Whether the repository at `common_dir` knows `top` as one of its worktrees."""
    listed = _git(["--git-dir", str(common_dir), "worktree", "list", "--porcelain"], cwd)
    if listed is None or listed.returncode != 0:
        return False
    for line in _text(listed.stdout).splitlines():
        if line.startswith("worktree "):
            try:
                if _same(Path(line[len("worktree ") :]).resolve(), top):
                    return True
            except OSError:
                continue
    return False


def _submodules_inert(top: Path, inside: Callable[[Path], bool], depth: int) -> bool:
    modules = top / ".gitmodules"
    if not modules.is_file():
        return True
    found = _git(["config", "--file", str(modules), "-z", "--get-regexp", r"^submodule\..*\.path$"], top)
    if found is None:
        return False
    if found.returncode == 1 and not found.stdout:
        return True  # no entries
    if found.returncode != 0:
        return False
    paths: list[str] = []
    for record in found.stdout.split(b"\0"):
        if not record:
            continue
        parts = _text(record).split("\n", 1)
        if len(parts) != 2:
            return False
        paths.append(parts[1])
    if len(paths) > _MAX_SUBMODULES:
        return False
    for rel in paths:
        sub = top / rel
        if (sub / ".git").exists():  # initialised: a folder or a pointer file
            if not project_git_settings_inert(sub, inside, _depth=depth + 1):
                return False
    return True


def project_git_settings_inert(folder: Path, inside: Callable[[Path], bool], _depth: int = 0) -> bool:
    """True when a git command run in `folder` obeys no project-level setting off the inert
    list, the repository it finds lies inside the folders (or is a worktree of one that
    does), and the same holds for every initialised submodule. False whenever this cannot
    be established. `inside` says whether a resolved path is within the session's folders."""
    if _depth > _MAX_SUBMODULE_DEPTH:
        return False
    try:
        if _depth == 0 and not _environment_ok(inside):
            return False
        folder = Path(folder)
        if not folder.is_dir():
            return True  # git cannot start there, so the command only prints an error
        probe = _git(["rev-parse", "--git-dir", "--git-common-dir", "--is-bare-repository"], folder)
        if probe is None:
            return False
        if probe.returncode != 0:
            # Outside any repository git only prints an error. Any other failure asks.
            return b"not a git repository" in probe.stderr
        lines = _text(probe.stdout).splitlines()
        if len(lines) != 3:
            return False
        git_dir = (folder / lines[0]).resolve()
        common_dir = (folder / lines[1]).resolve()
        top: Optional[Path] = None
        if lines[2] == "true":
            if not inside(common_dir):
                return False
        else:
            shown = _git(["rev-parse", "--show-toplevel"], folder)
            if shown is None or shown.returncode != 0:
                return False
            top = Path(_text(shown.stdout).strip()).resolve()
            if not inside(top):
                return False
            if not inside(common_dir) and not _lists_worktree(common_dir, top, folder):
                return False
        if not _no_links_under([common_dir] if _same(git_dir, common_dir) else [common_dir, git_dir]):
            return False

        def inside_or_own(path: Path) -> bool:  # the accepted repository's own folders count
            return inside(path) or _same(path, common_dir) or _same(path.parent, common_dir)

        if not _objects_inside((common_dir / "objects").resolve(), inside_or_own, set()):
            return False
        if top is not None and (top / ".lfsconfig").exists():
            return False  # a project's own LFS settings can name transfer programs
        listing = _git(["config", "--show-scope", "--show-origin", "--list", "-z"], folder)
        if listing is None or listing.returncode != 0:
            return False
        drivers: set[tuple[str, str]] = set()
        attributes_file_relative = False
        # Each entry is three NUL-ended fields: scope, origin, then "key\nvalue".
        fields = listing.stdout.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        if len(fields) % 3:
            return False
        for i in range(0, len(fields), 3):
            scope = _text(fields[i])
            key, _, value = _text(fields[i + 2]).partition("\n")
            if scope in _TRUSTED_SCOPES:
                found = _driver(key, value)
                if found is not None:
                    drivers.add((found[0], found[1].lower()))
                if key.lower() == "core.attributesfile" and not os.path.isabs(os.path.expanduser(value.strip())):
                    attributes_file_relative = True  # the project would supply that file
                continue
            if key.lower() == "core.worktree":
                # A submodule's settings name its own work tree this way, relative to the
                # git dir. Pointing anywhere else would read another folder's files.
                if top is None or not _same((common_dir / value).resolve(), top):
                    return False
            elif not _inert(key):
                return False
        if drivers:
            if top is None or attributes_file_relative:
                return False  # a bare repository's attributes are not on disk to read
            if _attributes_present(top, common_dir) is not False:
                return False
        return top is None or _submodules_inert(top, inside, _depth)
    except Exception:  # noqa: BLE001 - a check that cannot finish never allows
        return False
