"""`git_log` — recent commit history for context (read-only), and workspace
turn checkpoints via lightweight git shadow refs for safe rollback.

Checkpoints capture the exact working tree state (including untracked and modified
files) before write tools mutate the repository, enabling safe `revert_turn`
rollbacks without modifying git history or HEAD.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import aisuite as ai

_SEP = "\x1f"
CHECKPOINT_REF_PREFIX = "refs/openworker/checkpoints"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_log",
        "description": (
            "Recent git commit history (hash, author, date, subject). Optionally "
            "scope to a path. Use it to understand how code evolved before editing. "
            "Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional file/dir to scope history to.",
                },
                "max_count": {
                    "type": "integer",
                    "description": "How many commits (default 20, max 200).",
                },
            },
        },
    },
}

_REVERT_TURN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "revert_turn",
        "description": (
            "Revert workspace changes to the git shadow checkpoint captured "
            "before a specific turn began. If turn is omitted or 0, reverts to "
            "the checkpoint taken before the latest turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "turn": {
                    "type": "integer",
                    "description": (
                        "The turn number to revert to (default 0 for latest turn "
                        "checkpoint)."
                    ),
                },
            },
        },
    },
}


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


def create_checkpoint(
    workspace: str | Path, session_id: str, turn_index: int
) -> str | None:
    """Snapshot working files and the index separately without changing HEAD."""
    root = Path(workspace).expanduser().resolve()
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
    workspace: str | Path, session_id: str | None = None
) -> list[dict[str, Any]]:
    """List available turn checkpoints for the workspace."""
    if not is_git_repo(workspace):
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
    workspace: str | Path, session_id: str, turn_index: int
) -> dict[str, Any]:
    """Restore exact filenames and the captured staging state; keep HEAD unchanged."""
    root = Path(workspace).expanduser().resolve()
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


def git_tools(workspace: str, session_id: str = "") -> list:
    root = str(Path(workspace).resolve())

    def git_log(path: str | None = None, max_count: int = 20) -> dict[str, Any]:
        n = max_count if isinstance(max_count, int) and max_count > 0 else 20
        n = min(n, 200)
        cmd = [
            "git",
            "-C",
            root,
            "log",
            f"-n{n}",
            f"--pretty=format:%h{_SEP}%an{_SEP}%ad{_SEP}%s",
            "--date=short",
        ]
        if path:
            cmd += ["--", path]
        try:
            out = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                env=_git_env(),
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"error": f"git log failed: {exc}"}
        if out.returncode != 0:
            return {"error": (out.stderr or "git log failed").strip()[:300]}
        commits = []
        for line in out.stdout.splitlines():
            parts = line.split(_SEP)
            if len(parts) == 4:
                commits.append(
                    {
                        "hash": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "subject": parts[3],
                    }
                )
        return {"count": len(commits), "commits": commits}

    def revert_turn(turn: int = 0) -> dict[str, Any]:
        """Revert workspace to the git shadow checkpoint captured before a turn."""
        target_turn = turn
        if target_turn <= 0:
            ckpts = list_checkpoints(root, session_id=session_id)
            if not ckpts:
                return {
                    "ok": False,
                    "error": "No checkpoints available to revert.",
                }
            target_turn = ckpts[-1]["turn"]
        res = restore_checkpoint(root, session_id or "default", target_turn)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "Revert failed")}
        return res

    git_log.__name__ = "git_log"
    git_log.__doc__ = _SCHEMA["function"]["description"]
    git_log.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="git_log",
        category="git",
        risk_level="low",
        capabilities=["git"],
        requires_approval=False,
    )
    git_log.__coworker_schema__ = _SCHEMA

    revert_turn.__name__ = "revert_turn"
    revert_turn.__doc__ = _REVERT_TURN_SCHEMA["function"]["description"]
    revert_turn.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="revert_turn",
        category="git",
        risk_level="high",
        capabilities=["git"],
        requires_approval=True,
    )
    revert_turn.__coworker_schema__ = _REVERT_TURN_SCHEMA

    return [git_log, revert_turn]
