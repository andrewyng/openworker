"""Feature worktrees for the Code coworker (owner ruling 2026-11-07).

A task gets its own branch and worktree, cut from the freshly fetched base branch, so
the user's checkout is never edited in place. The policy lives here, not in the model's
hands: where the worktree goes, how the branch is named, whether to fetch first, the
`.git/info/exclude` entry (the user's `.gitignore` is never touched), the optional
per-repo setup command, and when a worktree may be removed (never while dirty).

Locations:
- in_repo (default): `<repo>/.openworker/worktrees/<slug>` — near the editor, travels with
  the repo, cleans up with it.
- sibling: `<repo parent>/<repo name>-worktrees/<slug>`.
- custom: `<custom dir>/<repo name>/<slug>`.

All git calls go through `_run_git` (never raises); failures come back as WorktreeError
with a sentence the agent can relay.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

LOCATIONS = ("in_repo", "sibling", "custom")
IN_REPO_DIR = Path(".openworker") / "worktrees"
EXCLUDE_LINE = ".openworker/"
_SETUP_TIMEOUT_S = 900
_FETCH_TIMEOUT_S = 120


class WorktreeError(RuntimeError):
    pass


@dataclass
class WorktreeSettings:
    location: str = "in_repo"
    custom_dir: str = ""
    branch_prefix: str = ""  # "" = derive from the git user name's initials
    fetch_first: bool = True
    cleanup_on_archive: bool = True
    prune_days: int = 14
    setup_command: str = ""

    def payload(self) -> dict[str, Any]:
        return {f"worktree_{k}": v for k, v in asdict(self).items()}


_PREF_KEYS = tuple(WorktreeSettings.__dataclass_fields__)


def settings_from_prefs(prefs: dict[str, Any], project_key: Optional[str] = None) -> WorktreeSettings:
    """Global `worktree_*` prefs, overlaid with the repo's override (keyed by project
    key) when there is one. Unknown or malformed values fall back to the defaults."""
    s = WorktreeSettings()
    layers: list[dict[str, Any]] = [{k[len("worktree_"):]: v for k, v in prefs.items()
                                     if k.startswith("worktree_") and k != "worktree_overrides"}]
    overrides = prefs.get("worktree_overrides") or {}
    if project_key and isinstance(overrides, dict) and isinstance(overrides.get(project_key), dict):
        layers.append(overrides[project_key])
    for layer in layers:
        for k in _PREF_KEYS:
            if k not in layer or layer[k] is None:
                continue
            v = layer[k]
            if k == "location":
                if v in LOCATIONS:
                    s.location = v
            elif k in ("fetch_first", "cleanup_on_archive"):
                setattr(s, k, bool(v))
            elif k == "prune_days":
                try:
                    s.prune_days = max(1, int(v))
                except (TypeError, ValueError):
                    pass
            else:
                setattr(s, k, str(v))
    return s


def validate_settings(body: dict[str, Any]) -> dict[str, Any]:
    """The subset of `body` that is a valid settings change, normalized. Raises
    ValueError with a user-facing sentence on a bad value."""
    out: dict[str, Any] = {}
    if "location" in body:
        if body["location"] not in LOCATIONS:
            raise ValueError("where worktrees go must be in_repo, sibling or custom")
        out["location"] = body["location"]
    if "custom_dir" in body:
        out["custom_dir"] = str(body["custom_dir"] or "").strip()
    if out.get("location") == "custom" and not out.get("custom_dir", body.get("custom_dir")):
        raise ValueError("a custom location needs a folder")
    if "branch_prefix" in body:
        prefix = str(body["branch_prefix"] or "").strip()
        if prefix and not re.fullmatch(r"[A-Za-z0-9._/-]{1,40}", prefix):
            raise ValueError("the branch prefix may only use letters, digits, . _ - and /")
        out["branch_prefix"] = prefix
    for k in ("fetch_first", "cleanup_on_archive"):
        if k in body:
            out[k] = bool(body[k])
    if "prune_days" in body:
        try:
            days = int(body["prune_days"])
        except (TypeError, ValueError):
            raise ValueError("prune days must be a whole number") from None
        if days < 1:
            raise ValueError("prune days must be at least 1")
        out["prune_days"] = days
    if "setup_command" in body:
        out["setup_command"] = str(body["setup_command"] or "").strip()
    return out


# -- git ---------------------------------------------------------------------------------

def _git(args: list[str], cwd: Any = None, timeout: int = 60) -> tuple[str, str]:
    from .connectors.integration_tools import _run_git

    return _run_git(args, cwd=cwd, timeout=timeout)


def repo_root(path: str | Path) -> Optional[Path]:
    """The repository's top level for `path`, or None when it is not inside a git repo."""
    out, err = _git(["-C", str(path), "rev-parse", "--show-toplevel"])
    if err or not out.strip():
        return None
    return Path(out.strip())


def common_dir(root: Path) -> Path:
    out, _ = _git(["-C", str(root), "rev-parse", "--git-common-dir"])
    p = Path(out.strip() or ".git")
    return p if p.is_absolute() else (root / p)


def default_branch(root: Path) -> str:
    """origin's HEAD (main/master/…) when known, else the current branch, else main."""
    out, err = _git(["-C", str(root), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if not err and out.strip():
        return out.strip().split("/", 1)[-1]
    out, err = _git(["-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"])
    if not err and out.strip() and out.strip() != "HEAD":
        return out.strip()
    return "main"


def has_remote(root: Path, name: str = "origin") -> bool:
    out, err = _git(["-C", str(root), "remote", "get-url", name])
    return not err and bool(out.strip())


def derive_prefix(root: Optional[Path]) -> str:
    """`<initials>/` from git's user.name ("Rohit C Prasad" → "rp/"), else "ow/"."""
    out, err = _git(["-C", str(root), "config", "user.name"] if root else ["config", "user.name"])
    name = "" if err else out.strip()
    words = [w for w in re.split(r"\s+", name) if w]
    if len(words) >= 2:
        return f"{words[0][0]}{words[-1][0]}".lower() + "/"
    if words:
        return words[0][:2].lower() + "/"
    return "ow/"


def slugify(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:limit].strip("-")
    return s or "work"


def plan_path(root: Path, slug: str, s: WorktreeSettings) -> Path:
    if s.location == "sibling":
        return root.parent / f"{root.name}-worktrees" / slug
    if s.location == "custom" and s.custom_dir:
        return Path(s.custom_dir).expanduser() / root.name / slug
    return root / IN_REPO_DIR / slug


def _branch_exists(root: Path, branch: str) -> bool:
    out, _ = _git(["-C", str(root), "branch", "--list", branch])
    return bool(out.strip())


def _unique(root: Path, branch: str, path: Path) -> tuple[str, Path]:
    cand_branch, cand_path, n = branch, path, 1
    while _branch_exists(root, cand_branch) or cand_path.exists():
        n += 1
        cand_branch, cand_path = f"{branch}-{n}", path.with_name(f"{path.name}-{n}")
    return cand_branch, cand_path


def ensure_excluded(root: Path) -> bool:
    """Keep `.openworker/` out of git status via `.git/info/exclude` — never the user's
    `.gitignore`. Returns whether a line was added."""
    exclude = common_dir(root) / "info" / "exclude"
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        if EXCLUDE_LINE in existing.splitlines():
            return False
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(EXCLUDE_LINE + "\n")
        return True
    except OSError:
        return False


def create_worktree(root: Path, task: str, s: WorktreeSettings) -> dict[str, Any]:
    """A worktree on a new branch for `task`, by policy. Returns the record the session
    keeps: path, branch, base, start, fetched, repo_root, created, setup, warnings."""
    warnings: list[str] = []
    prefix = s.branch_prefix or derive_prefix(root)
    if not prefix.endswith("/"):
        prefix += "/"
    slug = slugify(task)
    base = default_branch(root)
    start, fetched = base, False
    if s.fetch_first and has_remote(root):
        _, err = _git(["-C", str(root), "fetch", "origin", base], timeout=_FETCH_TIMEOUT_S)
        if err:
            warnings.append(f"could not fetch origin/{base} ({err.strip()[:120]}); branched from the local {base}")
        else:
            start, fetched = f"origin/{base}", True
    branch, path = _unique(root, prefix + slug, plan_path(root, slug, s))
    path.parent.mkdir(parents=True, exist_ok=True)
    _, err = _git(["-C", str(root), "worktree", "add", "-b", branch, str(path), start], timeout=_FETCH_TIMEOUT_S)
    if err:
        raise WorktreeError(f"git could not create the worktree: {err.strip()[:200]}")
    if s.location == "in_repo":
        ensure_excluded(root)
    setup = _run_setup(path, s, warnings)
    return {
        "path": str(path),
        "branch": branch,
        "base": base,
        "start": start,
        "fetched": fetched,
        "repo_root": str(root),
        "location": s.location,
        "created": time.time(),
        "setup": setup,
        "warnings": warnings,
    }


def reattach_worktree(root: Path, previous: dict[str, Any], s: WorktreeSettings) -> dict[str, Any]:
    """A session resumed after its worktree was removed (archive cleanup) gets the SAME
    branch back in a fresh worktree — its commits are still there; only the folder went."""
    branch = str(previous.get("branch") or "")
    if not branch or not _branch_exists(root, branch):
        raise WorktreeError(f"branch {branch or '?'} no longer exists")
    warnings: list[str] = []
    slug = Path(str(previous.get("path") or branch.rsplit("/", 1)[-1])).name
    path = plan_path(root, slug, s)
    if path.exists():
        _, path = _unique(root, "", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _, err = _git(["-C", str(root), "worktree", "add", str(path), branch], timeout=_FETCH_TIMEOUT_S)
    if err:
        raise WorktreeError(f"git could not re-create the worktree: {err.strip()[:200]}")
    if s.location == "in_repo":
        ensure_excluded(root)
    setup = _run_setup(path, s, warnings)
    return {
        **{k: previous[k] for k in ("base", "start", "fetched") if k in previous},
        "path": str(path),
        "branch": branch,
        "repo_root": str(root),
        "location": s.location,
        "created": time.time(),
        "reattached": True,
        "setup": setup,
        "warnings": warnings,
    }


def _run_setup(path: Path, s: WorktreeSettings, warnings: list[str]) -> Optional[dict[str, Any]]:
    if not s.setup_command:
        return None
    try:
        proc = subprocess.run(
            s.setup_command, shell=True, cwd=str(path), capture_output=True, text=True,
            timeout=_SETUP_TIMEOUT_S, env={**os.environ},
        )
        tail = (proc.stdout + proc.stderr)[-1500:]
        if proc.returncode != 0:
            warnings.append(f"the setup command exited {proc.returncode}")
        return {"command": s.setup_command, "exit_code": proc.returncode, "output": tail}
    except subprocess.TimeoutExpired:
        warnings.append("the setup command timed out")
        return {"command": s.setup_command, "exit_code": None, "output": "timed out"}


def is_clean(path: str | Path) -> bool:
    """No uncommitted changes and no untracked files — the only state a worktree may be
    removed in without the person's say-so."""
    out, err = _git(["-C", str(path), "status", "--porcelain"])
    return not err and not out.strip()


def remove_worktree(root: str | Path, path: str | Path, *, force: bool = False) -> bool:
    """Remove a worktree (its branch stays — unmerged work is never deleted). Without
    `force`, a dirty worktree is left alone and False is returned."""
    p = Path(path)
    if not p.is_dir():
        _git(["-C", str(root), "worktree", "prune"])
        return True
    if not force and not is_clean(p):
        return False
    _, err = _git(["-C", str(root), "worktree", "remove", "--force", str(p)])
    if err and p.is_dir():
        return False
    _git(["-C", str(root), "worktree", "prune"])
    return True


def is_stale(record: dict[str, Any], prune_days: int, now: Optional[float] = None) -> bool:
    """Untouched for `prune_days`: the worktree directory's newest mtime is older."""
    p = Path(str(record.get("path") or ""))
    if not p.is_dir():
        return False
    now = now or time.time()
    try:
        newest = max((f.stat().st_mtime for f in p.rglob("*") if ".git" not in f.parts), default=p.stat().st_mtime)
    except OSError:
        return False
    return (now - newest) > prune_days * 86400
