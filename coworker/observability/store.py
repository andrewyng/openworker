"""`recordings.db`: where the recorder's turns and steps are saved (technical design §11).

A SQLite file next to `coworker.db`, on the machine that ran the work. It is separate from
`coworker.db` on purpose: recordings are pruned after 30 days while sessions are kept, the
layout can change without migrating the sessions database, and deleting this one file resets
all recordings and nothing else.

These tables are the Python layout only. The contract between engines is the recording JSON
of recording-format.md; `load_recording()` gives it back.

Writing never makes a turn wait. `write_turn` and `write_step` only put the record in a
pending map keyed by its id, so a step written three times before the writer runs is written
once, with its latest state; one background thread, shared by every store in the process,
writes the map in a single transaction
every `FLUSH_MS`. Nothing is dropped, and no queue can grow past the number of records that
changed since the last write.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import weakref
import zlib
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

FILENAME = "recordings.db"
RETENTION_DAYS = 30  # feature spec Q12
FLUSH_MS = 50
SWEEP_EVERY_S = 24 * 60 * 60
SWEEP_BATCH = 2000  # rows per transaction, so the file is never locked for long (P-8)
SWEEP_PAUSE_S = 0.05

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
  turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, seq INTEGER, kind TEXT, trigger TEXT,
  started_at INTEGER, duration_ms INTEGER, status TEXT, incomplete INTEGER DEFAULT 0,
  approval_mode TEXT, attended INTEGER, reviewer TEXT, stamp TEXT,
  first_message_id TEXT, last_message_id TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id, seq);
CREATE INDEX IF NOT EXISTS turns_started ON turns(started_at);
CREATE TABLE IF NOT EXISTS steps (
  step_id TEXT PRIMARY KEY, turn_id TEXT, session_id TEXT, parent_step_id TEXT,
  seq INTEGER, kind TEXT, name TEXT, started_at INTEGER, duration_ms INTEGER, status TEXT,
  call_id TEXT, call_index INTEGER, message_id TEXT,
  shadow INTEGER DEFAULT 0, after_turn INTEGER DEFAULT 0,
  purpose TEXT, model TEXT, usage_reported INTEGER,
  input_fresh INTEGER, cache_read INTEGER, cache_write INTEGER, output INTEGER, reasoning INTEGER,
  error_type TEXT, served_model TEXT, provider_request_id TEXT, context_window INTEGER,
  attrs TEXT, content TEXT, provider_meta BLOB);
CREATE INDEX IF NOT EXISTS steps_turn ON steps(turn_id, seq);
CREATE INDEX IF NOT EXISTS steps_call ON steps(session_id, call_id);
CREATE INDEX IF NOT EXISTS steps_usage ON steps(session_id, kind, purpose, model);
CREATE INDEX IF NOT EXISTS steps_started ON steps(started_at);
CREATE TABLE IF NOT EXISTS exchange_pieces (
  sha256 TEXT PRIMARY KEY, data BLOB NOT NULL, size INTEGER NOT NULL, first_seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS step_pieces (
  step_id TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY (step_id, sha256));
CREATE INDEX IF NOT EXISTS step_pieces_sha ON step_pieces(sha256);
CREATE TABLE IF NOT EXISTS export_marks (
  destination TEXT NOT NULL, item_id TEXT NOT NULL, turn_id TEXT, state TEXT NOT NULL, at INTEGER NOT NULL,
  PRIMARY KEY (destination, item_id));
CREATE INDEX IF NOT EXISTS export_marks_turn ON export_marks(turn_id);
CREATE TABLE IF NOT EXISTS export_counters (
  destination TEXT PRIMARY KEY, failed INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0);
"""

_TURN_COLUMNS = (
    "turn_id", "session_id", "seq", "kind", "trigger", "started_at", "duration_ms", "status",
    "incomplete", "approval_mode", "attended", "reviewer", "stamp", "first_message_id",
    "last_message_id", "schema_version", "max_iterations", "woken_by",
)
# Columns added after the first release of the store: an existing recordings.db gets them
# here (CREATE TABLE IF NOT EXISTS does not add columns). `issues` and `issues_seen` hold the
# Issues checks' result for the turn (phase-2 design §5): derived, not part of the format.
_ADDED_COLUMNS = (
    ("turns", "max_iterations", "INTEGER"),
    ("turns", "woken_by", "TEXT"),
    ("turns", "issues", "TEXT"),
    ("turns", "issues_seen", "INTEGER DEFAULT 0"),
)
_STEP_COLUMNS = (
    "step_id", "turn_id", "session_id", "parent_step_id", "seq", "kind", "name", "started_at",
    "duration_ms", "status", "call_id", "call_index", "message_id", "shadow", "after_turn",
    "purpose", "model", "usage_reported", "input_fresh", "cache_read", "cache_write", "output",
    "reasoning", "error_type", "served_model", "provider_request_id", "context_window", "attrs",
    "content", "provider_meta",
)
_USAGE_PARTS = ("input_fresh", "cache_read", "cache_write", "output", "reasoning")


def _upsert(table: str, columns: tuple[str, ...], key: str, sticky: tuple[str, ...] = ()) -> str:
    sets = ", ".join(
        f"{c} = MAX({table}.{c}, excluded.{c})" if c in sticky else f"{c} = excluded.{c}"
        for c in columns if c != key
    )
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))}) "
        f"ON CONFLICT({key}) DO UPDATE SET {sets}"
    )


# `incomplete` only ever goes from 0 to 1.
_UPSERT_TURN = _upsert("turns", _TURN_COLUMNS, "turn_id", sticky=("incomplete",))
_UPSERT_STEP = _upsert("steps", _STEP_COLUMNS, "step_id")


def _js(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _bool(value: Any) -> Optional[int]:
    return None if value is None else int(bool(value))


def _turn_row(t: dict[str, Any]) -> tuple:
    return (
        t["turn_id"], t["session_id"], t.get("seq"), t.get("kind"), t.get("trigger"),
        t.get("started_at"), t.get("duration_ms"), t.get("status"), int(bool(t.get("incomplete"))),
        t.get("approval_mode"), _bool(t.get("attended")), _js(t.get("reviewer")),
        _js(t.get("stamp") or {}), t.get("first_message_id"), t.get("last_message_id"),
        t.get("schema_version", 1), t.get("max_iterations"), _js(t.get("woken_by")),
    )


def _piece_refs(value: Any) -> Iterator[str]:
    """Every piece an exchange points at: manifest `$blob`s, a whole-body `blob`, `body_blob`."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("$blob", "blob", "body_blob") and isinstance(v, str):
                yield v
            else:
                yield from _piece_refs(v)
    elif isinstance(value, list):
        for v in value:
            yield from _piece_refs(v)


def _step_row(s: dict[str, Any]) -> tuple:
    attrs = dict(s.get("attrs") or {})
    # provider_meta has its own compressed column, so the light step list and the usage sums
    # never read it (§5.2.1, §11).
    meta = attrs.pop("provider_meta", None)
    usage = attrs.get("usage") if isinstance(attrs.get("usage"), dict) else None
    if s.get("kind") == "model":
        # A call the provider gave no counts for is `usage: {"reported": false}` (§5.2).
        reported: Optional[int] = int(bool(usage) and usage.get("reported", True) is not False)
    else:
        reported = _bool(attrs.get("usage_reported"))
    facts = attrs.get("provider_facts") or {}
    error = attrs.get("error") if isinstance(attrs.get("error"), dict) else {}
    return (
        s["step_id"], s.get("turn_id"), s.get("session_id"), s.get("parent_step_id"), s.get("seq"),
        s.get("kind"), s.get("name"), s.get("started_at"), s.get("duration_ms"), s.get("status"),
        s.get("call_id"), s.get("call_index"), s.get("message_id"), int(bool(s.get("shadow"))),
        int(bool(s.get("after_turn"))), attrs.get("purpose"), attrs.get("model"), reported,
        *((usage or {}).get(p) for p in _USAGE_PARTS),
        error.get("type"), facts.get("served_model"), facts.get("provider_request_id"),
        attrs.get("context_window"), _js(attrs), _js(s.get("content") or {}),
        None if meta is None else zlib.compress(_js(meta).encode("utf-8")),
    )


class RecordingStore:
    """`recordings.db`. One connection, guarded by a lock; one writer thread."""

    def __init__(
        self,
        path: str | Path,
        *,
        retention_days: Optional[int] = RETENTION_DAYS,
        background: bool = True,
    ) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        for table, column, kind in _ADDED_COLUMNS:
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            except sqlite3.OperationalError:
                pass  # already there
        self._conn.commit()
        # Records waiting to be written, by id; the latest state of each replaces the last.
        self._pending_turns: dict[str, dict[str, Any]] = {}
        self._pending_issues: dict[str, tuple[str, int]] = {}  # turn_id -> (json, seen)
        self._pending_steps: dict[str, dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._closed = False
        self._last_sweep = 0.0
        self._background = background
        # OB-6: the destination being exported to and the time it was chosen, so a turn the
        # retention sweep deletes before it was sent is counted as dropped (design §10.5).
        self.export_destination: Optional[tuple[str, int]] = None
        if background:
            _writer.add(self)

    @classmethod
    def beside(cls, coworker_db_dir: str | Path, **kw: Any) -> "RecordingStore":
        """The store for a data folder: `recordings.db` next to its `coworker.db`."""
        return cls(Path(coworker_db_dir) / FILENAME, **kw)

    # -- writing (called by the recorder; never blocks on the disk) --------------------------
    def write_turn(self, record: dict[str, Any]) -> None:
        with self._pending_lock:
            self._pending_turns[record["turn_id"]] = record
        if self._background:
            _writer.wake()

    def write_step(self, record: dict[str, Any]) -> None:
        with self._pending_lock:
            self._pending_steps[record["step_id"]] = record
        if self._background:
            _writer.wake()

    def flush(self) -> None:
        """Write everything pending now, on the calling thread."""
        # The connection lock is taken before the pending map is swapped, so a session
        # deleted meanwhile cannot be written back by a flush that started before it.
        with self._lock:
            with self._pending_lock:
                turns, self._pending_turns = self._pending_turns, {}
                steps, self._pending_steps = self._pending_steps, {}
                issues, self._pending_issues = self._pending_issues, {}
            if not turns and not steps and not issues:
                return
            try:
                with self._conn:
                    self._conn.executemany(_UPSERT_TURN, [_turn_row(t) for t in turns.values()])
                    self._conn.executemany(_UPSERT_STEP, [_step_row(s) for s in steps.values()])
                    self._conn.executemany(
                        "UPDATE turns SET issues = ?, issues_seen = ? WHERE turn_id = ?",
                        [(js, seen, tid) for tid, (js, seen) in issues.items()],
                    )
                    for s in steps.values():
                        refs = set(_piece_refs((s.get("content") or {}).get("exchange")))
                        if refs:
                            self._conn.executemany(
                                "INSERT OR IGNORE INTO step_pieces (step_id, sha256) VALUES (?, ?)",
                                [(s["step_id"], r) for r in refs],
                            )
            except Exception:
                log.exception("recordings: write failed (%d turns, %d steps)", len(turns), len(steps))

    def _tick(self) -> None:
        """One turn of the shared writer: write what is pending, and sweep once a day."""
        if self._closed:
            return
        self.flush()
        if self.retention_days is not None and time.time() - self._last_sweep >= SWEEP_EVERY_S:
            self._last_sweep = time.time()
            try:
                self.sweep()
            except Exception:
                log.exception("recordings: retention sweep failed")

    def close(self) -> None:
        _writer.remove(self)
        with self._lock:
            if self._closed:
                return
            self.flush()
            self._closed = True
            self._conn.close()

    # -- exchange pieces (TD-17) --------------------------------------------------------------
    def put_piece(self, data: bytes) -> str:
        """Store one exchange piece once, compressed, keyed by its SHA-256. Returns the key."""
        key = sha256(data).hexdigest()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO exchange_pieces (sha256, data, size, first_seen) "
                "VALUES (?, ?, ?, ?)",
                (key, zlib.compress(data), len(data), int(time.time() * 1000)),
            )
        return key

    def get_piece(self, key: str) -> Optional[bytes]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM exchange_pieces WHERE sha256 = ?", (key,)
            ).fetchone()
        return None if row is None else zlib.decompress(row["data"])

    # -- found open at start (TD-12) ------------------------------------------------------------
    def close_open_turns(self) -> int:
        """Close the turns and steps a previous run of the app left open (it crashed, was
        killed or restarted mid-turn): status `unknown` with no duration, the turn's steps
        numbered in canonical order, and a `recording_gap` notice at the turn's last known
        moment. Only the process that owns the data folder may call this, at start: a
        running turn of another process would look the same. Returns the turns closed."""
        from .ids import new_ulid
        from .recorder import KIND_ORDER, fill_unended

        with self._lock:
            self.flush()
            open_turns = self._conn.execute(
                "SELECT turn_id, session_id, started_at FROM turns WHERE status = 'running' OR status IS NULL"
            ).fetchall()
            if not open_turns:
                return 0
            with self._conn:
                for t in open_turns:
                    tid = t["turn_id"]
                    self._conn.execute(
                        "UPDATE turns SET status = 'unknown', duration_ms = NULL WHERE turn_id = ?", (tid,))
                    opened = self._conn.execute(
                        "SELECT step_id, kind, attrs FROM steps "
                        "WHERE turn_id = ? AND (status = 'running' OR status IS NULL)", (tid,)).fetchall()
                    self._conn.executemany(
                        "UPDATE steps SET status = 'unknown', duration_ms = NULL, attrs = ? WHERE step_id = ?",
                        [(json.dumps(fill_unended(o["kind"], json.loads(o["attrs"] or "{}"))), o["step_id"])
                         for o in opened])
                    steps = self._conn.execute(
                        "SELECT step_id, kind, name, started_at, duration_ms, call_index, call_id, shadow "
                        "FROM steps WHERE turn_id = ?", (tid,)).fetchall()
                    # The last moment known: a step's end, or the start of one still open.
                    last = max([t["started_at"] or 0] + [s["started_at"] + (s["duration_ms"] or 0) for s in steps
                                                          if s["started_at"] is not None])
                    gap = {"step_id": new_ulid(), "kind": "notice", "name": "recording_gap", "started_at": last,
                           "duration_ms": 0, "call_index": None, "call_id": None, "shadow": 0}
                    self._conn.execute(
                        "INSERT INTO steps (step_id, turn_id, session_id, kind, name, started_at, duration_ms, "
                        "status, shadow, after_turn, attrs, content) VALUES (?, ?, ?, 'notice', 'recording_gap', ?, 0, "
                        "'ok', 0, 0, '{}', '{}')", (gap["step_id"], tid, t["session_id"], last))
                    ordered = sorted([dict(s) for s in steps] + [gap], key=lambda s: (
                        s["started_at"] or 0, 0 if s["duration_ms"] == 0 else 1, KIND_ORDER.get(s["kind"], 9),
                        s["call_index"] or 0, s["name"] or "", s["call_id"] or "", bool(s["shadow"])))
                    self._conn.executemany("UPDATE steps SET seq = ? WHERE step_id = ?",
                                           [(i, s["step_id"]) for i, s in enumerate(ordered, 1)])
            log.info("recordings: closed %d turn(s) left open by an earlier run", len(open_turns))
            return len(open_turns)

    # -- deleting -----------------------------------------------------------------------------
    def delete_session(self, session_id: str) -> None:
        """The session-delete cascade (§11): its turns, steps and the pieces only they used."""
        with self._lock, self._conn:
            with self._pending_lock:
                self._pending_turns = {
                    k: v for k, v in self._pending_turns.items()
                    if v.get("session_id") != session_id
                }
                self._pending_steps = {
                    k: v for k, v in self._pending_steps.items()
                    if v.get("session_id") != session_id
                }
            self._conn.execute(
                "DELETE FROM step_pieces WHERE step_id IN "
                "(SELECT step_id FROM steps WHERE session_id = ?)",
                (session_id,),
            )
            self._conn.execute(
                "DELETE FROM export_marks WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?)",
                (session_id,),
            )
            self._conn.execute("DELETE FROM steps WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        self._delete_unused_pieces()

    def sweep(self, *, now_ms: Optional[int] = None) -> int:
        """Delete turns older than the retention period, their steps, then unused pieces.

        Runs in batches of `SWEEP_BATCH` rows with a short pause between them. Steps with no
        known turn (proxy calls) age out by their own start time. Returns the turns deleted.
        """
        if self.retention_days is None:
            return 0
        now = int(time.time() * 1000) if now_ms is None else now_ms
        cutoff = now - self.retention_days * 24 * 60 * 60 * 1000
        deleted = 0
        while True:
            with self._lock, self._conn:
                ids = [
                    r["turn_id"]
                    for r in self._conn.execute(
                        "SELECT turn_id FROM turns WHERE started_at < ? LIMIT ?",
                        (cutoff, SWEEP_BATCH),
                    )
                ]
                if ids:
                    marks = ", ".join("?" * len(ids))
                    self._count_unsent_dropped(ids, marks)
                    self._conn.execute(f"DELETE FROM export_marks WHERE turn_id IN ({marks})", ids)
                    self._conn.execute(
                        "DELETE FROM step_pieces WHERE step_id IN "
                        f"(SELECT step_id FROM steps WHERE turn_id IN ({marks}))",
                        ids,
                    )
                    self._conn.execute(f"DELETE FROM steps WHERE turn_id IN ({marks})", ids)
                    self._conn.execute(f"DELETE FROM turns WHERE turn_id IN ({marks})", ids)
                    deleted += len(ids)
            if len(ids) < SWEEP_BATCH:
                break
            time.sleep(SWEEP_PAUSE_S)
        while True:
            with self._lock, self._conn:
                ids = [
                    r["step_id"]
                    for r in self._conn.execute(
                        "SELECT step_id FROM steps WHERE started_at < ? AND "
                        "(turn_id IS NULL OR turn_id NOT IN (SELECT turn_id FROM turns)) LIMIT ?",
                        (cutoff, SWEEP_BATCH),
                    )
                ]
                if ids:
                    marks = ", ".join("?" * len(ids))
                    self._conn.execute(f"DELETE FROM step_pieces WHERE step_id IN ({marks})", ids)
                    self._conn.execute(f"DELETE FROM steps WHERE step_id IN ({marks})", ids)
            if len(ids) < SWEEP_BATCH:
                break
            time.sleep(SWEEP_PAUSE_S)
        self._delete_unused_pieces()
        return deleted

    def _delete_unused_pieces(self) -> None:
        while True:
            with self._lock, self._conn:
                cur = self._conn.execute(
                    "DELETE FROM exchange_pieces WHERE sha256 IN (SELECT sha256 FROM "
                    "exchange_pieces WHERE sha256 NOT IN (SELECT sha256 FROM step_pieces) "
                    "LIMIT ?)",
                    (SWEEP_BATCH,),
                )
            if cur.rowcount < SWEEP_BATCH:
                break
            time.sleep(SWEEP_PAUSE_S)

    # -- export marks (OB-6, phase-2 design §10.2, §10.5) ----------------------------------------
    # The queue is the store itself: a finished turn's root and steps with no mark for the
    # destination are still to send. A mark is `sent`, `dropped` (refused on its own) or
    # `skipped` (a notice that came after its turn's span had gone). Marks are per destination,
    # so turning export off and on again never re-sends a step.

    def _count_unsent_dropped(self, ids: list[str], marks: str) -> None:
        """Inside the sweep's transaction: turns about to be deleted that ended after the
        destination was chosen and were never sent."""
        dest = self.export_destination
        if dest is None:
            return
        key, since = dest
        n = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM turns t WHERE t.turn_id IN ({marks}) "
            "AND t.status IS NOT NULL AND t.status != 'running' "
            "AND t.started_at + COALESCE(t.duration_ms, 0) >= ? AND NOT EXISTS "
            "(SELECT 1 FROM export_marks m WHERE m.destination = ? AND m.item_id = t.turn_id)",
            (*ids, since, key),
        ).fetchone()["n"]
        if n:
            self._bump(key, dropped=n)

    def _bump(self, destination: str, *, failed: int = 0, dropped: int = 0) -> None:
        self._conn.execute(
            "INSERT INTO export_counters (destination, failed, dropped) VALUES (?, ?, ?) "
            "ON CONFLICT(destination) DO UPDATE SET failed = failed + excluded.failed, "
            "dropped = dropped + excluded.dropped",
            (destination, failed, dropped),
        )

    def count_export(self, destination: str, *, failed: int = 0, dropped: int = 0) -> None:
        with self._lock, self._conn:
            self._bump(destination, failed=failed, dropped=dropped)

    def export_queue(self, destination: str, since_ms: int, limit: int = 50) -> list[str]:
        """Finished turns, ended at or after `since_ms`, with something not yet marked for
        this destination: the turn itself, or a step that has ended (an `after_turn` step
        is sent alone when it ends). Oldest first."""
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT t.turn_id FROM turns t "
                "WHERE t.status IS NOT NULL AND t.status != 'running' "
                "AND t.started_at + COALESCE(t.duration_ms, 0) >= ? AND ("
                " NOT EXISTS (SELECT 1 FROM export_marks m WHERE m.destination = ? AND m.item_id = t.turn_id)"
                " OR EXISTS (SELECT 1 FROM steps s WHERE s.turn_id = t.turn_id"
                "  AND s.status IS NOT NULL AND s.status != 'running' AND NOT EXISTS"
                "  (SELECT 1 FROM export_marks m WHERE m.destination = ? AND m.item_id = s.step_id))) "
                "ORDER BY t.started_at, t.turn_id LIMIT ?",
                (since_ms, destination, destination, limit),
            ).fetchall()
        return [r["turn_id"] for r in rows]

    def export_marked(self, destination: str, turn_id: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT item_id FROM export_marks WHERE destination = ? AND turn_id = ?",
                (destination, turn_id),
            ).fetchall()
        return {r["item_id"] for r in rows}

    def mark_export(self, destination: str, items: list[tuple[str, str]], state: str) -> None:
        """`items`: (item id, turn id) pairs. A mark, once written, is kept."""
        now = int(time.time() * 1000)
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO export_marks (destination, item_id, turn_id, state, at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(destination, item, turn, state, now) for item, turn in items],
            )

    def export_counts(self, destination: str, *, since_ms: int = 0) -> dict[str, int]:
        """Steps sent (since `since_ms`), steps dropped, and batches failed for good."""
        with self._lock:
            sent = self._conn.execute(
                "SELECT COUNT(*) AS n FROM export_marks WHERE destination = ? AND state = 'sent' AND at >= ?",
                (destination, since_ms),
            ).fetchone()["n"]
            dropped = self._conn.execute(
                "SELECT COUNT(*) AS n FROM export_marks WHERE destination = ? AND state = 'dropped'",
                (destination,),
            ).fetchone()["n"]
            row = self._conn.execute(
                "SELECT failed, dropped FROM export_counters WHERE destination = ?", (destination,)
            ).fetchone()
            last = self._conn.execute(
                "SELECT MAX(at) AS at FROM export_marks WHERE destination = ? AND state = 'sent'", (destination,)
            ).fetchone()["at"]
        return {"sent": int(sent), "dropped": int(dropped) + (int(row["dropped"]) if row else 0),
                "failed": int(row["failed"]) if row else 0, "last_at": last}

    def load_turn(self, turn_id: str) -> Optional[tuple[dict[str, Any], list[dict[str, Any]]]]:
        """One turn and its steps (by `seq`), content included."""
        self.flush()
        with self._lock:
            t = self._conn.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if t is None:
                return None
            steps = self._conn.execute(
                "SELECT * FROM steps WHERE turn_id = ? ORDER BY seq IS NULL, seq, started_at", (turn_id,)
            ).fetchall()
        return _turn_record(t), [_step_record(s) for s in steps]

    # -- the Issues result (phase-2 design §5): derived, stored beside the turn ---------------
    def write_issues(self, turn_id: str, result: dict[str, Any], *, seen: bool = False) -> None:
        """Store a turn's Issues result. `seen` is false only for a result worked out when the
        turn ended: one worked out later, for an old turn, never lights the session dot."""
        with self._pending_lock:
            self._pending_issues[turn_id] = (json.dumps(result, ensure_ascii=False), int(seen))
        if self._background:
            _writer.wake()

    def read_issues(self, session_id: str) -> dict[str, dict[str, Any]]:
        """The stored results of a session's turns, by turn id, each with `seen`."""
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT turn_id, issues, issues_seen FROM turns WHERE session_id = ? AND issues IS NOT NULL",
                (session_id,),
            ).fetchall()
        out = {}
        for r in rows:
            try:
                out[r["turn_id"]] = {**json.loads(r["issues"]), "seen": bool(r["issues_seen"])}
            except ValueError:
                continue
        return out

    def mark_issues_seen(self, session_id: str) -> None:
        self.flush()
        with self._lock, self._conn:
            self._conn.execute("UPDATE turns SET issues_seen = 1 WHERE session_id = ?", (session_id,))

    def unseen_warnings(self) -> dict[str, int]:
        """Warnings not seen yet, by session: the sidebar dot (TD2-4)."""
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, issues FROM turns WHERE issues_seen = 0 AND issues IS NOT NULL"
            ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            try:
                warn = int(json.loads(r["issues"]).get("warn") or 0)
            except (ValueError, TypeError):
                continue
            if warn:
                out[r["session_id"]] = out.get(r["session_id"], 0) + warn
        return out

    # -- reading ------------------------------------------------------------------------------
    def turn_count(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM turns WHERE session_id = ?", (session_id,)
            ).fetchone()
        return int(row["n"])

    def worker_turns(self, lead_session_id: str, since_ms: int = 0) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        """Team workers' turns woken by this lead (`woken_by.lead_session_id`, 1.2), started at
        or after `since_ms`, each with its steps: what the lead's timeline shows as worker rows
        (OB-7, derived on read, never copied into the lead's recording)."""
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM turns WHERE woken_by IS NOT NULL AND started_at >= ? "
                "AND json_extract(woken_by, '$.lead_session_id') = ? ORDER BY started_at, turn_id",
                (since_ms, lead_session_id),
            ).fetchall()
            ids = [r["turn_id"] for r in rows]
            steps = self._conn.execute(
                f"SELECT * FROM steps WHERE turn_id IN ({', '.join('?' * len(ids))}) ORDER BY seq",
                ids,
            ).fetchall() if ids else []
        by_turn: dict[str, list[dict[str, Any]]] = {}
        for s in steps:
            by_turn.setdefault(s["turn_id"], []).append(_step_record(s))
        return [(_turn_record(r), by_turn.get(r["turn_id"], [])) for r in rows]

    def load_recording(self, session_id: str) -> dict[str, Any]:
        """A session's recording as format 1.0 JSON: {"turns": [...], "steps": [...]}.
        Turns by (started_at, turn_id); each turn's steps by `seq`."""
        self.flush()
        with self._lock:
            turns = self._conn.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY started_at, turn_id",
                (session_id,),
            ).fetchall()
            steps = self._conn.execute(
                "SELECT * FROM steps WHERE session_id = ?", (session_id,)
            ).fetchall()
        order = {t["turn_id"]: i for i, t in enumerate(turns)}
        steps = sorted(
            steps,
            key=lambda s: (order.get(s["turn_id"], len(order)), s["seq"] is None, s["seq"] or 0,
                           s["started_at"] or 0),
        )
        return {"turns": [_turn_record(t) for t in turns], "steps": [_step_record(s) for s in steps]}


class _SharedWriter:
    """One background thread for every open store in the process. It sleeps until a store
    has something to write (or an hour passes, to check the daily sweep), then writes each
    store's pending records in one transaction. Stores are held weakly, so a store nobody
    uses any more is not kept alive by the writer (tests make thousands of them)."""

    def __init__(self) -> None:
        self._stores: "weakref.WeakSet[RecordingStore]" = weakref.WeakSet()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def add(self, store: "RecordingStore") -> None:
        with self._lock:
            self._stores.add(store)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="recordings-writer", daemon=True)
                self._thread.start()
        self._wake.set()  # a new store's first sweep runs soon

    def remove(self, store: "RecordingStore") -> None:
        with self._lock:
            self._stores.discard(store)

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait(timeout=3600)
            self._wake.clear()
            with self._lock:
                stores = list(self._stores)
            for store in stores:
                try:
                    store._tick()
                except Exception:
                    log.exception("recordings: writer failed")
            del stores
            time.sleep(FLUSH_MS / 1000)  # gather the next burst of writes into one transaction


_writer = _SharedWriter()


def _turn_record(r: sqlite3.Row) -> dict[str, Any]:
    d = {k: r[k] for k in _TURN_COLUMNS}
    d["incomplete"] = bool(d["incomplete"])
    d["attended"] = None if d["attended"] is None else bool(d["attended"])
    d["reviewer"] = None if d["reviewer"] is None else json.loads(d["reviewer"])
    d["stamp"] = json.loads(d["stamp"] or "{}")
    # Format 1.2 fields are left out when not recorded, so a 1.1 turn reads back unchanged.
    if d.get("max_iterations") is None:
        d.pop("max_iterations", None)
    if d.get("woken_by") is None:
        d.pop("woken_by", None)
    else:
        d["woken_by"] = json.loads(d["woken_by"])
    return d


def _step_record(r: sqlite3.Row) -> dict[str, Any]:
    attrs = json.loads(r["attrs"] or "{}")
    if r["provider_meta"] is not None:
        attrs["provider_meta"] = json.loads(zlib.decompress(r["provider_meta"]))
    return {
        "step_id": r["step_id"], "turn_id": r["turn_id"], "session_id": r["session_id"],
        "parent_step_id": r["parent_step_id"], "seq": r["seq"], "kind": r["kind"],
        "name": r["name"], "started_at": r["started_at"], "duration_ms": r["duration_ms"],
        "status": r["status"], "call_id": r["call_id"], "call_index": r["call_index"],
        "message_id": r["message_id"], "shadow": bool(r["shadow"]),
        "after_turn": bool(r["after_turn"]), "attrs": attrs,
        "content": json.loads(r["content"] or "{}"),
    }
