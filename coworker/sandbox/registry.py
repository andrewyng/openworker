"""The sandbox registry: which sandboxes this server started, for whom, and where.

It answers three questions: how many sandboxes run on this machine, which sandbox belongs
to this session and agent, and (later) on which machine a tool call has to go. Rows carry a
`machine_id` from the first version, though it is always this machine for now, so that
placing a runner on another machine later needs no new shape.

It also keeps things tidy: a cap per machine, and clean-up of sandboxes whose server is
gone (a crash, a kill) so they do not pile up.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..secrets import state_dir

LOCAL_MACHINE = "local"
MAX_ENV = "OPENWORKER_SANDBOX_MAX"
DEFAULT_MAX = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sandboxes (
    name TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    roots TEXT NOT NULL DEFAULT '[]',
    profile TEXT NOT NULL DEFAULT '',
    enforcement TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    server_pid INTEGER NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL NOT NULL
);
"""


class SandboxLimitReached(RuntimeError):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SandboxRegistry:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else state_dir() / "sandbox" / "registry.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # -- writes -----------------------------------------------------------------------
    def check_room(self, machine_id: str = LOCAL_MACHINE) -> None:
        limit = int(os.environ.get(MAX_ENV) or DEFAULT_MAX)
        running = self.count(machine_id)
        if running >= limit:
            raise SandboxLimitReached(
                f"{running} sandboxes are already running on this machine (the limit is {limit}; set {MAX_ENV} to change it)"
            )

    def record(
        self,
        name: str,
        *,
        provider: str,
        session_id: str = "",
        agent: str = "",
        roots: Optional[list] = None,
        profile: str = "",
        enforcement: str = "",
        machine_id: str = LOCAL_MACHINE,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO sandboxes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, provider, machine_id, session_id, agent, json.dumps(roots or []), profile, enforcement, "ready", os.getpid(), now, now),
            )

    def touch(self, name: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE sandboxes SET last_used_at = ? WHERE name = ?", (time.time(), name))

    def close(self, name: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM sandboxes WHERE name = ?", (name,))

    # -- reads ------------------------------------------------------------------------
    def list(self, machine_id: Optional[str] = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM sandboxes", ()
        if machine_id is not None:
            query, args = query + " WHERE machine_id = ?", (machine_id,)
        with self._connect() as db:
            rows = [dict(r) for r in db.execute(query + " ORDER BY created_at", args)]
        for row in rows:
            row["roots"] = json.loads(row["roots"])
        return rows

    def count(self, machine_id: str = LOCAL_MACHINE) -> int:
        return len(self.list(machine_id))

    def find(self, session_id: str, agent: str = "") -> Optional[dict[str, Any]]:
        for row in self.list():
            if row["session_id"] == session_id and (not agent or row["agent"] == agent):
                return row
        return None

    # -- clean-up ---------------------------------------------------------------------
    def reap(self) -> list[str]:
        """Remove sandboxes whose server process is gone, and OpenShell sandboxes that carry
        our label but that no living server owns. Returns the names removed."""
        removed: list[str] = []
        rows = self.list(LOCAL_MACHINE)
        dead = [r for r in rows if not _alive(int(r["server_pid"]))]
        owned = {r["name"] for r in rows if r not in dead}
        openshell_names: set[str] = set()
        if any(r["provider"] == "openshell" for r in dead) or _openshell_present():
            from .providers import openshell

            try:
                listed = openshell.list_our_sandboxes()
            except Exception:
                listed = []
            openshell_names = {str(s.get("name") or s.get("metadata", {}).get("name") or "") for s in listed} - {""}
            for name in sorted(openshell_names - owned):
                try:
                    openshell._cli("sandbox", "delete", name, timeout=90, check=False)
                    removed.append(name)
                except Exception:
                    pass
        for row in dead:
            self.close(row["name"])
            if row["name"] not in removed and row["provider"] != "openshell":
                removed.append(row["name"])
        return removed


def _openshell_present() -> bool:
    import shutil

    return shutil.which("openshell") is not None
