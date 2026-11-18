"""Git turn checkpoints shared by direct execution and the sandbox runner."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

CHECKPOINT_REF_PREFIX = "refs/openworker/checkpoints"


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "OpenWorker",
        "GIT_AUTHOR_EMAIL": "checkpoint@openworker.invalid",
        "GIT_COMMITTER_NAME": "OpenWorker",
        "GIT_COMMITTER_EMAIL": "checkpoint@openworker.invalid",
    }
    if extra:
        env.update(extra)
    return env


def _sanitize_session_id(session_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id or "")
    return cleaned or "default"


def is_git_repo(workspace: str | Path) -> bool:
    """Return True if workspace is inside a git work tree."""
    root = Path(workspace).expanduser().resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=5,
        )
        return out.returncode == 0 and out.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _git_dir(workspace: str | Path) -> Path | None:
    root = Path(workspace).expanduser().resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            raw = Path(out.stdout.strip())
            return raw if raw.is_absolute() else (root / raw).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _run_git(root: Path, *args: str, env=None, input=None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], env=env or _git_env(),
        input=input, capture_output=True, check=True, timeout=20,
    ).stdout


def _checkpoint_root(root: Path) -> bool:
    # A subfolder checkout must not snapshot or restore its parent repository.
    top = os.fsdecode(_run_git(root, "rev-parse", "--show-toplevel")).strip()
    return Path(top).resolve() == root


def _workspace_allowed(workspace: Path, roots: list[dict] | None, *, write: bool) -> bool:
    if roots is None:
        return True
    matches = []
    for entry in roots:
        path = Path(entry["path"]).expanduser().resolve()
        if workspace == path or path in workspace.parents:
            matches.append((len(path.parts), bool(entry.get("writable"))))
    if not matches:
        return False
    depth = max(d for d, _ in matches)
    return not write or all(w for d, w in matches if d == depth)


def create_checkpoint(
    workspace: str | Path, session_id: str, turn_index: int, *, roots: list[dict] | None = None
) -> str | None:
    """Snapshot working files and the index separately without changing HEAD."""
    root = Path(workspace).expanduser().resolve()
    if not _workspace_allowed(root, roots, write=True):
        return None
    tmp_idx = None
    try:
        if not _checkpoint_root(root):
            return None
        git_dir = _git_dir(root)
        if git_dir is None:
            return None
        sid = _sanitize_session_id(session_id)
        ref = f"{CHECKPOINT_REF_PREFIX}/{sid}/{turn_index}"
        index_ref = f"refs/openworker/checkpoint-index/{sid}/{turn_index}"
        # write-tree fails closed for an unmerged index.
        index_tree = _run_git(root, "write-tree").decode().strip()
        tmp_idx = git_dir / f"ow_ckpt_{uuid.uuid4().hex}"
        env = _git_env({"GIT_INDEX_FILE": str(tmp_idx)})
        _run_git(root, "read-tree", index_tree, env=env)
        _run_git(root, "add", "-A", env=env)
        tree = _run_git(root, "write-tree", env=env).decode().strip()
        commit = _run_git(root, "commit-tree", tree, "-m",
                          f"openworker checkpoint {sid} turn {turn_index}").decode().strip()
        # Publish both snapshots atomically; never overwrite an earlier turn.
        commands = f"start\ncreate {ref} {commit}\ncreate {index_ref} {index_tree}\nprepare\ncommit\n"
        _run_git(root, "update-ref", "--stdin", input=commands.encode())
        return ref
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        if tmp_idx is not None:
            tmp_idx.unlink(missing_ok=True)


def list_checkpoints(
    workspace: str | Path, session_id: str | None = None, *, roots: list[dict] | None = None
) -> list[dict[str, Any]]:
    """List available turn checkpoints for the workspace."""
    if not _workspace_allowed(Path(workspace).expanduser().resolve(), roots, write=False) or not is_git_repo(workspace):
        return []
    root = Path(workspace).expanduser().resolve()
    prefix = CHECKPOINT_REF_PREFIX
    if session_id:
        sid = _sanitize_session_id(session_id)
        prefix = f"{CHECKPOINT_REF_PREFIX}/{sid}"

    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "for-each-ref",
                "--format=%(refname) %(objectname) %(creatordate:iso8601)",
                f"{prefix}/",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=10,
        )
        if out.returncode != 0:
            return []
        results = []
        for line in out.stdout.splitlines():
            parts = line.strip().split(maxsplit=2)
            if len(parts) >= 2:
                refname = parts[0]
                commit = parts[1]
                date_str = parts[2] if len(parts) > 2 else ""
                ref_parts = refname.split("/")
                if len(ref_parts) >= 5:
                    ckpt_sid = ref_parts[3]
                    try:
                        turn = int(ref_parts[4])
                    except ValueError:
                        turn = 0
                    results.append(
                        {
                            "ref": refname,
                            "session_id": ckpt_sid,
                            "turn": turn,
                            "commit": commit,
                            "date": date_str,
                        }
                    )
        results.sort(key=lambda c: c["turn"])
        return results
    except (OSError, subprocess.SubprocessError):
        return []


def restore_checkpoint(
    workspace: str | Path, session_id: str, turn_index: int, *, roots: list[dict] | None = None
) -> dict[str, Any]:
    """Restore exact filenames and the captured staging state; keep HEAD unchanged."""
    root = Path(workspace).expanduser().resolve()
    if not _workspace_allowed(root, roots, write=True):
        return {"ok": False, "error": "checkpoint workspace is not writable"}
    if not is_git_repo(root):
        return {"ok": False, "error": "workspace is not a git repository"}
    sid = _sanitize_session_id(session_id)
    ref = f"{CHECKPOINT_REF_PREFIX}/{sid}/{turn_index}"
    index_ref = f"refs/openworker/checkpoint-index/{sid}/{turn_index}"
    try:
        if not _checkpoint_root(root):
            return {"ok": False, "error": "checkpoint restore requires the repository root"}
        # Resolve and enumerate everything before changing a file. Old checkpoints
        # without an index snapshot cannot promise a safe staging-state restore.
        tree = _run_git(root, "rev-parse", "--verify", f"{ref}^{{tree}}").decode().strip()
        index_tree = _run_git(root, "rev-parse", "--verify", f"{index_ref}^{{tree}}").decode().strip()
        cp_files = {os.fsdecode(f) for f in _run_git(root, "ls-tree", "-rz", "--name-only", tree).split(b"\0") if f}
        current = {os.fsdecode(f) for f in _run_git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0") if f}
        removed = sorted(current - cp_files)
        # Cached paths can survive an ignored directory being replaced by a
        # symlink. Refuse before any mutation rather than following its target.
        for name in cp_files | set(removed):
            parent = root
            for component in Path(name).parts[:-1]:
                parent /= component
                if parent.is_symlink():
                    return {"ok": False, "error": "checkpoint restore refuses a symlinked parent directory"}
        # Worktree-only restore never stages a formerly untracked/unstaged file.
        if cp_files:
            _run_git(root, "restore", f"--source={tree}", "--worktree", "--", ".")
        for name in removed:
            path = root / name
            if path.is_file() or path.is_symlink():
                path.unlink()
        # Only prune empty parents of files removed by this restore.
        for name in removed:
            parent = (root / name).parent
            while parent != root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        _run_git(root, "read-tree", index_tree)
        return {"ok": True, "ref": ref, "turn": turn_index, "removed_files": removed,
                "message": f"Restored workspace and staging state before turn {turn_index}."}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"checkpoint not found or restore failed: {exc}"}

