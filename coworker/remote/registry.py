"""Machines registry — the controller's durable record of enrolled boxes.

Lives in coworker.db next to the other stores. Presence is deliberately NOT a
column: liveness belongs to the acceptor's in-memory socket map, the registry
only records identity (pinned pubkey), the user-facing name, and timestamps.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

MAX_NAME_CHARS = 60


def _clean_name(name: str) -> str:
    cleaned = " ".join((name or "").split())[:MAX_NAME_CHARS].strip()
    return cleaned or "machine"


class MachinesRegistry:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # Name uniqueness is PER ORG, enforced in code (enroll's suffix loop
            # and rename's clash check) — not by a table constraint, so the
            # desktop's org-"" world and a multi-tenant deployment share one
            # schema. (Pre-tenancy desktop DBs carry a UNIQUE(name) constraint;
            # harmless there — one org.)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS machines (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    pubkey TEXT UNIQUE NOT NULL,
                    seal_pubkey TEXT DEFAULT '',
                    app_version TEXT DEFAULT '',
                    org_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    last_seen REAL
                )
                """
            )
            # Tier-2 snapshot (remote-home-design.md data path): the last-known
            # session-LIST METADATA per machine — what keeps an offline machine's
            # sessions greyed-but-visible instead of vanished.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS machine_sessions (
                    machine_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            # Desktop-tier transcript cache (owner amendment 2026-08-25):
            # cache-on-view — every transcript the user fetched while the box was
            # connected stays readable (READ-ONLY) while it's offline. This is the
            # DESKTOP controller only: same user, same device, same trust domain.
            # The cloud tier never stores conversation content.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS machine_transcripts (
                    machine_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (machine_id, session_id)
                )
                """
            )
            # Databases from before the sealing key / tenancy (idempotent-by-
            # error ALTERs). Pre-tenancy rows land in org "" — the desktop org.
            for alter in (
                "ALTER TABLE machines ADD COLUMN seal_pubkey TEXT DEFAULT ''",
                "ALTER TABLE machines ADD COLUMN org_id TEXT NOT NULL DEFAULT ''",
                # Fleet under the org (2026-09-02): the member who armed/approved
                # the enrollment owns the machine in the admin's view.
                "ALTER TABLE machines ADD COLUMN enrolled_by TEXT NOT NULL DEFAULT ''",
                # Managed sandboxes (2026-09-02): where the machine came from
                # ("" = brought by the user, "fly" = our sandbox) and the
                # provisioner's own id for it, so lifecycle code can find the
                # box a sandbox became.
                "ALTER TABLE machines ADD COLUMN provenance TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE machines ADD COLUMN provenance_ref TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    self._conn.execute(alter)
                except sqlite3.OperationalError:
                    pass  # already migrated
            # Wallet deployments ledger: WHICH profiles went WHERE, with a hash
            # of the deployed value for staleness — values themselves never
            # flow back (remote-home-design.md §Keys wallet).
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS machine_secret_deploys (
                    machine_id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    value_hash TEXT NOT NULL,
                    deployed_at REAL NOT NULL,
                    PRIMARY KEY (machine_id, profile)
                )
                """
            )
            self._conn.commit()

    def enroll(
        self,
        name: str,
        pubkey: str,
        app_version: str = "",
        seal_pubkey: str = "",
        org_id: str = "",
        enrolled_by: str = "",
        provenance: str = "",
        provenance_ref: str = "",
    ) -> dict[str, Any]:
        """Register a new pubkey in an org; collisions on the requested name
        auto-suffix (-2, -3…) within that org. `enrolled_by` = the member who
        armed/approved the join (the machine's owner in the org's fleet view).
        `provenance`/`provenance_ref` = which provisioner made the machine and
        its id there ("" for machines the user brought)."""
        base = _clean_name(name)
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM machines WHERE pubkey = ?", (pubkey,)
            ).fetchone()
            if row is not None:
                raise ValueError("pubkey already enrolled")
            candidate, attempt = base, 1
            while self._conn.execute(
                "SELECT 1 FROM machines WHERE name = ? AND org_id = ?",
                (candidate, org_id),
            ).fetchone():
                attempt += 1
                candidate = f"{base}-{attempt}"
            machine_id = uuid.uuid4().hex[:12]
            now = time.time()
            self._conn.execute(
                "INSERT INTO machines (id, name, pubkey, seal_pubkey, app_version, org_id, enrolled_by,"
                " provenance, provenance_ref, created_at, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (machine_id, candidate, pubkey, seal_pubkey, app_version, org_id, enrolled_by,
                 provenance, provenance_ref, now, now),
            )
            self._conn.commit()
            return {
                "id": machine_id,
                "name": candidate,
                "pubkey": pubkey,
                "org_id": org_id,
                "enrolled_by": enrolled_by,
                "provenance": provenance,
                "provenance_ref": provenance_ref,
            }

    def by_pubkey(self, pubkey: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM machines WHERE pubkey = ?", (pubkey,)
            ).fetchone()
            return dict(row) if row else None

    def by_id(self, machine_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM machines WHERE id = ?", (machine_id,)
            ).fetchone()
            return dict(row) if row else None

    def list(self, org_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            if org_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM machines ORDER BY created_at"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM machines WHERE org_id = ? ORDER BY created_at",
                    (org_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def touch(self, machine_id: str, app_version: str | None = None) -> None:
        with self._lock:
            if app_version is None:
                self._conn.execute(
                    "UPDATE machines SET last_seen = ? WHERE id = ?",
                    (time.time(), machine_id),
                )
            else:
                self._conn.execute(
                    "UPDATE machines SET last_seen = ?, app_version = ? WHERE id = ?",
                    (time.time(), app_version, machine_id),
                )
            self._conn.commit()

    def rename(self, machine_id: str, name: str) -> Optional[dict[str, Any]]:
        cleaned = _clean_name(name)
        with self._lock:
            row = self.by_id(machine_id)
            if row is None:
                return None
            clash = self._conn.execute(
                "SELECT 1 FROM machines WHERE name = ? AND org_id = ? AND id != ?",
                (cleaned, row["org_id"], machine_id),
            ).fetchone()
            if clash:
                raise ValueError(f"a machine named '{cleaned}' already exists")
            self._conn.execute(
                "UPDATE machines SET name = ? WHERE id = ?", (cleaned, machine_id)
            )
            self._conn.commit()
            return self.by_id(machine_id)

    def remove(self, machine_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM machines WHERE id = ?", (machine_id,)
            )
            self._conn.execute(
                "DELETE FROM machine_sessions WHERE machine_id = ?", (machine_id,)
            )
            self._conn.execute(
                "DELETE FROM machine_transcripts WHERE machine_id = ?", (machine_id,)
            )
            self._conn.execute(
                "DELETE FROM machine_secret_deploys WHERE machine_id = ?",
                (machine_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def set_seal_pubkey(self, machine_id: str, seal_pubkey: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE machines SET seal_pubkey = ? WHERE id = ?",
                (seal_pubkey, machine_id),
            )
            self._conn.commit()

    def record_deploy(self, machine_id: str, profile: str, value_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO machine_secret_deploys (machine_id, profile, value_hash, deployed_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(machine_id, profile) DO UPDATE SET"
                " value_hash = excluded.value_hash, deployed_at = excluded.deployed_at",
                (machine_id, profile, value_hash, time.time()),
            )
            self._conn.commit()

    def remove_deploy(self, machine_id: str, profile: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM machine_secret_deploys WHERE machine_id = ? AND profile = ?",
                (machine_id, profile),
            )
            self._conn.commit()

    def deploys(self, machine_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT profile, value_hash, deployed_at FROM machine_secret_deploys"
                " WHERE machine_id = ? ORDER BY profile",
                (machine_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_sessions_snapshot(self, machine_id: str, payload: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO machine_sessions (machine_id, payload, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(machine_id) DO UPDATE SET payload = excluded.payload,"
                " updated_at = excluded.updated_at",
                (machine_id, payload, time.time()),
            )
            self._conn.commit()

    def sessions_snapshot(self, machine_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM machine_sessions WHERE machine_id = ?",
                (machine_id,),
            ).fetchone()
            return row["payload"] if row else None

    def save_transcript(self, machine_id: str, session_id: str, payload: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO machine_transcripts (machine_id, session_id, payload, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(machine_id, session_id) DO UPDATE SET"
                " payload = excluded.payload, updated_at = excluded.updated_at",
                (machine_id, session_id, payload, time.time()),
            )
            self._conn.commit()

    def transcript(self, machine_id: str, session_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM machine_transcripts"
                " WHERE machine_id = ? AND session_id = ?",
                (machine_id, session_id),
            ).fetchone()
            return row["payload"] if row else None
