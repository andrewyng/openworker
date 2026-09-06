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
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": "/tmp",
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


def create_checkpoint(
    workspace: str | Path, session_id: str, turn_index: int
) -> str | None:
    """Capture workspace working tree as a git shadow ref before writes apply.

    Saves tracked, modified, and untracked files into a temporary index without
    affecting the repository's real index, HEAD, or branch. Returns the ref name
    on success, or None if the workspace is not a git repo or checkpointing fails.
    """
    if not is_git_repo(workspace):
        return None
    root = Path(workspace).expanduser().resolve()
    git_dir = _git_dir(root)
    if not git_dir or not git_dir.is_dir():
        return None

    sid = _sanitize_session_id(session_id)
    ref = f"{CHECKPOINT_REF_PREFIX}/{sid}/{turn_index}"
    tmp_name = f"ow_ckpt_{sid}_{turn_index}_{uuid.uuid4().hex[:8]}"
    tmp_idx = git_dir / tmp_name

    try:
        env = _git_env({"GIT_INDEX_FILE": str(tmp_idx)})
        add_res = subprocess.run(
            ["git", "-C", str(root), "--work-tree", str(root), "add", "-A"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=15,
        )
        if add_res.returncode != 0:
            return None

        wt_res = subprocess.run(
            ["git", "-C", str(root), "write-tree"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=15,
        )
        if wt_res.returncode != 0 or not wt_res.stdout.strip():
            return None
        tree_sha = wt_res.stdout.strip()

        parent = None
        head_res = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=5,
        )
        if head_res.returncode == 0 and head_res.stdout.strip():
            parent = head_res.stdout.strip()

        commit_cmd = [
            "git",
            "-C",
            str(root),
            "commit-tree",
            tree_sha,
            "-m",
            f"openworker checkpoint {sid} turn {turn_index}",
        ]
        if parent:
            commit_cmd.extend(["-p", parent])
        ct_res = subprocess.run(
            commit_cmd,
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=15,
        )
        if ct_res.returncode != 0 or not ct_res.stdout.strip():
            return None
        commit_sha = ct_res.stdout.strip()

        up_res = subprocess.run(
            ["git", "-C", str(root), "update-ref", ref, commit_sha],
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=5,
        )
        if up_res.returncode != 0:
            return None
        return ref
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        if tmp_idx.exists():
            try:
                tmp_idx.unlink()
            except OSError:
                pass


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
    """Restore workspace files to the checkpoint captured before the turn began."""
    if not is_git_repo(workspace):
        return {"ok": False, "error": "workspace is not a git repository"}
    root = Path(workspace).expanduser().resolve()
    sid = _sanitize_session_id(session_id)
    ref = f"{CHECKPOINT_REF_PREFIX}/{sid}/{turn_index}"

    try:
        chk = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=5,
        )
        if chk.returncode != 0:
            return {
                "ok": False,
                "error": f"checkpoint not found for turn {turn_index} ({ref})",
            }

        checkout = subprocess.run(
            ["git", "-C", str(root), "checkout", ref, "--", "."],
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=20,
        )
        if checkout.returncode != 0:
            return {
                "ok": False,
                "error": (checkout.stderr or "git checkout failed").strip()[:300],
            }

        tree_out = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "--name-only", ref],
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
            timeout=10,
        )
        cp_files = set(tree_out.stdout.splitlines())

        removed_files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            if ".git" in dirnames:
                dirnames.remove(".git")
            for filename in filenames:
                file_path = Path(dirpath) / filename
                rel = str(file_path.relative_to(root))
                if rel.startswith(".git") or ".git" in file_path.parts:
                    continue
                if rel not in cp_files:
                    ign = subprocess.run(
                        ["git", "-C", str(root), "check-ignore", rel],
                        capture_output=True,
                        text=True,
                        check=False,
                        env=_git_env(),
                        timeout=5,
                    )
                    if ign.returncode != 0:
                        try:
                            file_path.unlink()
                            removed_files.append(rel)
                        except OSError:
                            pass
        for dirpath, dirnames, _ in os.walk(root, topdown=False):
            if ".git" in Path(dirpath).parts:
                continue
            for dirname in dirnames:
                dpath = Path(dirpath) / dirname
                if dpath.name != ".git" and ".git" not in dpath.parts:
                    try:
                        dpath.rmdir()
                    except OSError:
                        pass

        return {
            "ok": True,
            "ref": ref,
            "turn": turn_index,
            "removed_files": removed_files,
            "message": (
                f"Successfully reverted workspace to turn {turn_index} checkpoint "
                f"({len(removed_files)} post-turn file(s) removed)."
            ),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"restore failed: {exc}"}


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
