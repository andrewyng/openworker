"""SQLite persistence for runtime state and idempotent consequential tool effects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .types import RuntimeState


def canonical_args(arguments: dict[str, Any] | None) -> str:
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str)


class RuntimeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS runtime_runs (
                session_id TEXT NOT NULL, run_id TEXT NOT NULL, state_json TEXT NOT NULL,
                updated_at REAL NOT NULL, PRIMARY KEY(session_id, run_id)
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS runtime_runs_latest ON runtime_runs(session_id, updated_at DESC)"
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS runtime_delegations (
                delegation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS runtime_delegations_session ON runtime_delegations(session_id, updated_at DESC)"
        )
        self._conn.commit()

    def save(self, state: RuntimeState) -> None:
        state.updated_at = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO runtime_runs(session_id, run_id, state_json, updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(session_id, run_id) DO UPDATE SET
                state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (state.session_id, state.run_id, json.dumps(state.to_dict(), default=str), state.updated_at),
            )
            self._conn.commit()

    def get(self, session_id: str, run_id: str | None = None) -> RuntimeState | None:
        query = (
            "SELECT state_json FROM runtime_runs WHERE session_id=? AND run_id=?"
            if run_id
            else "SELECT state_json FROM runtime_runs WHERE session_id=? ORDER BY updated_at DESC LIMIT 1"
        )
        params = (session_id, run_id) if run_id else (session_id,)
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        if row is None:
            return None
        try:
            return RuntimeState.from_dict(json.loads(row["state_json"]))
        except (TypeError, ValueError):
            return None

    def save_delegation(self, delegation: dict[str, Any]) -> None:
        value = dict(delegation)
        value["updated_at"] = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO runtime_delegations(delegation_id, session_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(delegation_id) DO UPDATE SET
                payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (value["id"], value["session_id"], json.dumps(value, default=str), value["updated_at"]),
            )
            self._conn.commit()

    def get_delegation(self, delegation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM runtime_delegations WHERE delegation_id=?", (delegation_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["payload_json"])
        except ValueError:
            return None

    def delegations(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM runtime_delegations WHERE session_id=? ORDER BY updated_at DESC",
                (session_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                out.append(json.loads(row["payload_json"]))
            except ValueError:
                pass
        return out


class EffectLedger:
    """At-most-once fence for writes, commands and external actions.

    An unclosed record is intentionally reported as *uncertain* after a crash.  Replaying a
    potentially completed action is worse than asking the user to reconcile it.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS effect_ledger (
                session_id TEXT NOT NULL, run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
                args_hash TEXT NOT NULL, tool_name TEXT NOT NULL, status TEXT NOT NULL,
                result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(session_id, run_id, tool_call_id, args_hash)
            )"""
        )
        self._conn.commit()

    @staticmethod
    def args_hash(arguments: dict[str, Any] | None) -> str:
        return hashlib.sha256(canonical_args(arguments).encode("utf-8")).hexdigest()

    def begin(
        self, *, session_id: str, run_id: str, tool_call_id: str, tool_name: str,
        arguments: dict[str, Any] | None,
    ) -> tuple[str, Any | None]:
        digest = self.args_hash(arguments)
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT status, result_json FROM effect_ledger WHERE session_id=? AND run_id=? AND tool_call_id=? AND args_hash=?",
                (session_id, run_id, tool_call_id, digest),
            ).fetchone()
            if row is not None:
                if row["status"] == "completed":
                    try:
                        return "completed", json.loads(row["result_json"] or "null")
                    except ValueError:
                        return "completed", None
                return "uncertain", None
            self._conn.execute(
                "INSERT INTO effect_ledger VALUES (?, ?, ?, ?, ?, 'started', NULL, ?, ?)",
                (session_id, run_id, tool_call_id, digest, tool_name, now, now),
            )
            self._conn.commit()
        return "started", None

    def complete(
        self, *, session_id: str, run_id: str, tool_call_id: str,
        arguments: dict[str, Any] | None, result: Any,
    ) -> None:
        digest = self.args_hash(arguments)
        with self._lock:
            self._conn.execute(
                """UPDATE effect_ledger SET status='completed', result_json=?, updated_at=?
                WHERE session_id=? AND run_id=? AND tool_call_id=? AND args_hash=?""",
                (json.dumps(result, default=str), time.time(), session_id, run_id, tool_call_id, digest),
            )
            self._conn.commit()

    def uncertain(
        self, *, session_id: str, run_id: str, tool_call_id: str,
        arguments: dict[str, Any] | None,
    ) -> None:
        digest = self.args_hash(arguments)
        with self._lock:
            self._conn.execute(
                """UPDATE effect_ledger SET status='uncertain', updated_at=?
                WHERE session_id=? AND run_id=? AND tool_call_id=? AND args_hash=?""",
                (time.time(), session_id, run_id, tool_call_id, digest),
            )
            self._conn.commit()
