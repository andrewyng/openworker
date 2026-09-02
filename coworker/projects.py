"""Project identity — the key under boards and workspace memory, plus user names.

Twentieth-pass ladder (agent-teams-design.md): an explicit per-session binding
beats derivation; derivation prefers the git repo (all worktrees of one repo
collapse to one project); a plain folder falls back to its resolved path — the
status-quo key. Resolution happens at discrete moments (engine build, root
grant); nothing watches the filesystem.
"""

from __future__ import annotations

import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

NAME_KINDS = ("memory", "board")
MAX_NAME_CHARS = 60


def _git_common_dir(workspace: Path) -> Optional[Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    raw = out.stdout.strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = workspace / p
    try:
        return p.resolve()
    except OSError:
        return None


def project_key(workspace: str | Path) -> str:
    """Derive the project key for a directory. Git repo → the repo directory
    (common-dir's parent — shared by every worktree); otherwise the resolved
    path. Never raises; a missing directory just resolves as a path."""
    ws = Path(workspace).expanduser()
    try:
        ws = ws.resolve()
    except OSError:
        ws = Path(str(workspace))
    if ws.is_dir():
        common = _git_common_dir(ws)
        if common is not None:
            # Normal layout: <repo>/.git → key is <repo>. Bare/odd layouts keep
            # the common dir itself — still one stable key per repo.
            return str(common.parent if common.name == ".git" else common)
    return str(ws)


def project_label(key: str, *, home: Optional[str] = None) -> dict[str, Any]:
    """Display info for a derived key. kind 'git' when the key currently looks
    like a repo (has a .git); label rules per UX-044: git = repo folder name,
    folder = ~-collapsed path (the GUI trims to the last 3 segments)."""
    p = Path(key)
    is_git = (p / ".git").exists()
    full = key
    h = home or str(Path.home())
    shown = key
    if shown == h:
        shown = "~"
    elif shown.startswith(h + "/"):
        shown = "~" + shown[len(h):]
    return {
        "kind": "git" if is_git else "folder",
        "label": p.name if is_git else shown,
        "full": full,
    }


class ProjectNames:
    """User-given names for memories and boards — aliases over project keys.
    One table in the session DB; a name never moves data, it only points."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS project_names (
                    kind TEXT NOT NULL, name TEXT NOT NULL, key TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (kind, name)
                )"""
            )
            self._conn.commit()

    def name_current(self, kind: str, name: str, key: str) -> dict[str, Any]:
        name = (name or "").strip()[:MAX_NAME_CHARS]
        if kind not in NAME_KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        if not name:
            raise ValueError("empty name")
        with self._lock:
            self._conn.execute(
                "INSERT INTO project_names (kind, name, key) VALUES (?, ?, ?) "
                "ON CONFLICT (kind, name) DO UPDATE SET key = excluded.key, "
                "last_used_at = datetime('now')",
                (kind, name, key),
            )
            self._conn.commit()
        return {"kind": kind, "name": name, "key": key}

    def resolve(self, kind: str, name: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT key FROM project_names WHERE kind = ? AND name = ?",
                (kind, name),
            ).fetchone()
        return row[0] if row else None

    def touch(self, kind: str, name: str) -> None:
        """Bump MRU when a binding is used."""
        with self._lock:
            self._conn.execute(
                "UPDATE project_names SET last_used_at = datetime('now') "
                "WHERE kind = ? AND name = ?",
                (kind, name),
            )
            self._conn.commit()

    def list(self, kind: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        q = (
            "SELECT name, key, last_used_at FROM project_names "
            "WHERE kind = ? ORDER BY last_used_at DESC, name"
        )
        args: list[Any] = [kind]
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [{"name": r[0], "key": r[1], "last_used_at": r[2]} for r in rows]

    def forget(self, kind: str, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM project_names WHERE kind = ? AND name = ?", (kind, name)
            )
            self._conn.commit()
        return cur.rowcount > 0


def resolve_memory_key(
    workspace: Optional[str],
    *,
    binding: Optional[str] = None,
    names: Optional["ProjectNames"] = None,
    memory_store: Any = None,
) -> Optional[str]:
    """The identity ladder for memory: explicit binding > git > path. Applies the
    one-time path→git re-key as a read-path side effect (idempotent: once moved,
    the path key has no rows left to move)."""
    if binding and names is not None:
        key = names.resolve("memory", binding)
        if key:
            names.touch("memory", binding)
            return key
    if not workspace:
        return None
    derived = project_key(workspace)
    path_key = str(Path(workspace).expanduser().resolve()) if Path(
        workspace
    ).expanduser().exists() else str(Path(workspace).expanduser())
    if memory_store is not None and derived != path_key:
        try:
            memory_store.rekey_workspace(path_key, derived)
        except Exception:
            pass
    return derived


def resolve_board_space(
    workspace: Optional[str],
    *,
    binding: Optional[str] = None,
    names: Optional["ProjectNames"] = None,
    team_store: Any = None,
) -> Optional[str]:
    """The identity ladder for the board space — same ladder, board collision
    rule: when both a path-keyed and a git-keyed space have events, the git key
    wins and the path space stays dormant (rekey_space refuses; nothing merges)."""
    if binding and names is not None:
        key = names.resolve("board", binding)
        if key:
            names.touch("board", binding)
            return key
    if not workspace:
        return None
    derived = project_key(workspace)
    path_key = str(Path(workspace).expanduser().resolve()) if Path(
        workspace
    ).expanduser().exists() else str(Path(workspace).expanduser())
    if team_store is not None and derived != path_key:
        try:
            team_store.rekey_space(path_key, derived)
        except Exception:
            pass
    return derived


def project_presence(
    key: str, *, memory_store: Any = None, team_store: Any = None
) -> dict[str, int]:
    """What already exists under a project key — feeds the grant-time notice.
    Counts only; the notice is a pointer, never content."""
    memories = 0
    items = 0
    if memory_store is not None:
        try:
            memories = len(memory_store.list(workspace=key))
        except Exception:
            pass
    if team_store is not None:
        try:
            # A cheap existence probe that needs no actor: any event under the key.
            items = team_store.event_count(key)
        except Exception:
            pass
    return {"memories": memories, "board_items": items}
