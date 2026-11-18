"""`git_log` — recent commit history for context (read-only), and workspace
turn checkpoints via lightweight git shadow refs for safe rollback.

Checkpoints capture the exact working tree state (including untracked and modified
files) before write tools mutate the repository, enabling safe `revert_turn`
rollbacks without modifying git history or HEAD.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aisuite as ai

from ..sandbox.runner import checkpoints as _checkpoints, tools_git as _impl

CHECKPOINT_REF_PREFIX = _checkpoints.CHECKPOINT_REF_PREFIX

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


def _checkpoint_call(name: str, workspace: str | Path, args: dict[str, Any], sandbox: Any = None, roots: list | None = None) -> Any:
    if sandbox is not None and getattr(sandbox, "client", None) is not None:
        sync = getattr(sandbox, "sync_roots", None)
        if sync is not None:
            sync()
        live = None if roots is None else [
            {"path": str(r["path"] if isinstance(r, dict) else getattr(r, "path", r)),
             "writable": bool(r.get("writable", False) if isinstance(r, dict) else getattr(r, "writable", isinstance(r, (str, Path))))}
            for r in roots
        ]
        result = sandbox.client.call(
            "tool.call", {"name": name, "workspace": str(workspace), "args": args, "roots": live}, timeout=180,
        )
        return result.get("value")
    live = None if roots is None else [
        {"path": str(r["path"] if isinstance(r, dict) else getattr(r, "path", r)),
         "writable": bool(r.get("writable", False) if isinstance(r, dict) else getattr(r, "writable", isinstance(r, (str, Path))))}
        for r in roots
    ]
    return getattr(_checkpoints, name)(workspace, roots=live, **args)


def create_checkpoint(workspace: str | Path, session_id: str, turn_index: int, *, sandbox: Any = None, roots: list | None = None) -> str | None:
    return _checkpoint_call("create_checkpoint", workspace, {"session_id": session_id, "turn_index": turn_index}, sandbox, roots)


def list_checkpoints(workspace: str | Path, session_id: str | None = None, *, sandbox: Any = None, roots: list | None = None) -> list[dict[str, Any]]:
    return _checkpoint_call("list_checkpoints", workspace, {"session_id": session_id}, sandbox, roots)


def restore_checkpoint(workspace: str | Path, session_id: str, turn_index: int, *, sandbox: Any = None, roots: list | None = None) -> dict[str, Any]:
    return _checkpoint_call("restore_checkpoint", workspace, {"session_id": session_id, "turn_index": turn_index}, sandbox, roots)


def git_tools(workspace: str, session_id: str = "", *, sandbox: Any = None, roots: list | None = None) -> list:
    root = str(Path(workspace).resolve())

    def git_log(path: str | None = None, max_count: int = 20) -> dict[str, Any]:
        return _impl.git_log(workspace, path, max_count)

    def revert_turn(turn: int = 0) -> dict[str, Any]:
        """Revert workspace to the git shadow checkpoint captured before a turn."""
        target_turn = turn
        if target_turn <= 0:
            ckpts = list_checkpoints(root, session_id=session_id, sandbox=sandbox, roots=roots)
            if not ckpts:
                return {
                    "ok": False,
                    "error": "No checkpoints available to revert.",
                }
            target_turn = ckpts[-1]["turn"]
        res = restore_checkpoint(root, session_id or "default", target_turn, sandbox=sandbox, roots=roots)
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
