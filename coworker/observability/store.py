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
from typing import Any, Iterable, Iterator, Optional

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
CREATE TABLE IF NOT EXISTS turn_usage (
  turn_id TEXT NOT NULL, purpose TEXT, model TEXT, first INTEGER NOT NULL DEFAULT 0,
  session_id TEXT, session_deleted INTEGER NOT NULL DEFAULT 0, started_at INTEGER, trigger TEXT,
  wake_reason TEXT, persona_id TEXT, persona_fp TEXT, machine TEXT, team_id TEXT, team_role TEXT,
  task_id TEXT, run_id TEXT, automation_trigger TEXT, status TEXT, duration_ms INTEGER,
  waiting_ms INTEGER NOT NULL DEFAULT 0,
  input_fresh INTEGER NOT NULL DEFAULT 0, cache_read INTEGER NOT NULL DEFAULT 0,
  cache_write INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0,
  reasoning INTEGER NOT NULL DEFAULT 0, cache_read_known_input INTEGER NOT NULL DEFAULT 0,
  calls INTEGER NOT NULL DEFAULT 0, unreported INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0, over_limit INTEGER NOT NULL DEFAULT 0,
  warn INTEGER, info INTEGER, warn_checks TEXT, approvals INTEGER NOT NULL DEFAULT 0,
  auto_approved INTEGER NOT NULL DEFAULT 0, live_checks INTEGER NOT NULL DEFAULT 0,
  rating TEXT, signals_version TEXT, updated_at INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS turn_usage_key ON turn_usage(turn_id, COALESCE(purpose, ''), COALESCE(model, ''));
CREATE INDEX IF NOT EXISTS turn_usage_started ON turn_usage(started_at);
CREATE INDEX IF NOT EXISTS turn_usage_session ON turn_usage(session_id);
CREATE INDEX IF NOT EXISTS turn_usage_persona ON turn_usage(persona_id, started_at);
CREATE TABLE IF NOT EXISTS feedback (
  turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, rating TEXT NOT NULL, reasons TEXT NOT NULL,
  note TEXT, auto_approved TEXT NOT NULL, rated_at INTEGER NOT NULL, changed_at INTEGER);
CREATE INDEX IF NOT EXISTS feedback_session ON feedback(session_id);
CREATE TABLE IF NOT EXISTS persona_versions (
  persona_id TEXT NOT NULL, fp TEXT NOT NULL, name TEXT, first_seen INTEGER NOT NULL, files TEXT NOT NULL,
  PRIMARY KEY (persona_id, fp));
CREATE TABLE IF NOT EXISTS persona_files (
  persona_id TEXT NOT NULL, fp TEXT NOT NULL, path TEXT NOT NULL, data BLOB NOT NULL,
  PRIMARY KEY (persona_id, fp, path));
"""
# Phase 3 (TD3-5): the usage ledger holds counts only, so it is kept 90 days, whatever
# "Keep for" says, and through a session's delete (TD3-3).
LEDGER_DAYS = 90

_TURN_COLUMNS = (
    "turn_id", "session_id", "seq", "kind", "trigger", "started_at", "duration_ms", "status",
    "incomplete", "approval_mode", "attended", "reviewer", "stamp", "first_message_id",
    "last_message_id", "schema_version", "max_iterations", "woken_by", "wake", "automation",
)
# Columns added after the first release of the store: an existing recordings.db gets them
# here (CREATE TABLE IF NOT EXISTS does not add columns). `issues` and `issues_seen` hold the
# Issues checks' result for the turn (phase-2 design §5): derived, not part of the format.
_ADDED_COLUMNS = (
    ("turns", "max_iterations", "INTEGER"),
    ("turns", "woken_by", "TEXT"),
    ("turns", "issues", "TEXT"),
    ("turns", "issues_seen", "INTEGER DEFAULT 0"),
    ("turns", "wake", "TEXT"),  # 1.3
    ("turns", "automation", "TEXT"),  # 1.3
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
        _js(t.get("wake")), _js(t.get("automation")),
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
        # The column is the model call's own usage. An auto-approver check's `usage_reported`
        # (whether its nested call reported) stays in attrs: in the column it read as a call
        # of 0 tokens to any sum over it (Phase 3 V3-06).
        reported = None
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
        self._pending_refresh: set[str] = set()  # turns whose ledger rows to work out again
        self._pending_lock = threading.Lock()
        # How many writes touched each session since start (P3B-5): a team view keys its
        # cache on its members' numbers, so it is worked out again only when one changed.
        self._generation: dict[str, int] = {}
        self._owner = False  # this process called close_open_turns: it owns the data folder
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
                refresh, self._pending_refresh = self._pending_refresh, set()
            refresh |= set(issues)
            if not turns and not steps and not issues and not refresh:
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
            self._touch_sessions({t.get("session_id") for t in turns.values()} | {x.get("session_id") for x in steps.values()})
            # Phase 3: the ledger rows of turns that ended or changed, from what was just
            # written (the turn, its steps and its Issues result). After the recording's own
            # transaction, one turn at a time: a ledger error loses that turn's rows only,
            # never the recorded steps (P3B-6).
            self._refresh_usage(refresh)

    def _tick(self) -> None:
        """One turn of the shared writer: write what is pending, and sweep once a day."""
        if self._closed:
            return
        self.flush()
        if time.time() - self._last_sweep >= SWEEP_EVERY_S:
            self._last_sweep = time.time()
            try:
                self.sweep()
                self.sweep_ledger()
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
            if self._owner:
                # A clean stop: a turn found open at the next start was cut by the app being
                # stopped, not by a crash (recording_gap cause, OB-14).
                self._marker("running").unlink(missing_ok=True)
                try:
                    self._marker("stopped").write_text(str(int(time.time() * 1000)), encoding="utf-8")
                except OSError:
                    pass

    def _marker(self, name: str) -> Path:
        return self.path.with_name(self.path.name + f".{name}")

    def _last_stop(self) -> str:
        """How the previous run of the app ended: `stopped` (it closed this store), `restart`
        (it was running and never closed it: a crash or a kill) or `unknown` (no word)."""
        if self._marker("stopped").exists():
            return "stopped"
        if self._marker("running").exists():
            return "restart"
        return "unknown"

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
            cause, until = self._last_stop(), int(time.time() * 1000)
            self._owner = True
            self._marker("stopped").unlink(missing_ok=True)
            try:
                self._marker("running").write_text(str(until), encoding="utf-8")
            except OSError:
                pass
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
                    # 1.2: why the turn was found open, and the start that found it (OB-14).
                    self._conn.execute(
                        "INSERT INTO steps (step_id, turn_id, session_id, kind, name, started_at, duration_ms, "
                        "status, shadow, after_turn, attrs, content) VALUES (?, ?, ?, 'notice', 'recording_gap', ?, 0, "
                        "'ok', 0, 0, ?, '{}')",
                        (gap["step_id"], tid, t["session_id"], last, json.dumps({"cause": cause, "until": until})))
                    ordered = sorted([dict(s) for s in steps] + [gap], key=lambda s: (
                        s["started_at"] or 0, 0 if s["duration_ms"] == 0 else 1, KIND_ORDER.get(s["kind"], 9),
                        s["call_index"] or 0, s["name"] or "", s["call_id"] or "", bool(s["shadow"])))
                    self._conn.executemany("UPDATE steps SET seq = ? WHERE step_id = ?",
                                           [(i, s["step_id"]) for i, s in enumerate(ordered, 1)])
            log.info("recordings: closed %d turn(s) left open by an earlier run", len(open_turns))
            return len(open_turns)

    # -- deleting -----------------------------------------------------------------------------
    def _touch_sessions(self, session_ids: Iterable[Optional[str]]) -> None:
        with self._pending_lock:
            for sid in session_ids:
                if sid:
                    self._generation[sid] = self._generation.get(sid, 0) + 1

    def generation(self, session_ids: Iterable[str]) -> tuple[int, ...]:
        """How many writes have touched each of these sessions since start (P3B-5). Writes
        still pending are written first, so an unchanged answer means nothing new."""
        self.flush()
        with self._pending_lock:
            return tuple(self._generation.get(sid, 0) for sid in session_ids)

    def delete_session(self, session_id: str) -> None:
        """The session-delete cascade (§11): its turns, steps and the pieces only they used."""
        self.flush()  # the ledger rows of its turns are worked out before the steps go
        self._touch_sessions({session_id})
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
            # Phase 3 (TD3-3): the content-free ledger rows stay, marked deleted; the ratings
            # (whose note is the person's own text) go, their count stays on the ledger row.
            self._conn.execute("UPDATE turn_usage SET session_deleted = 1 WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM feedback WHERE session_id = ?", (session_id,))
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

    def outside_calls(self, *, since_ms: int = 0) -> dict[str, int]:
        """Model calls made outside any session (purpose `proxy`: through this machine's
        model address, for example by a remote machine) since `since_ms`, and their tokens.
        No session's Steps or Usage shows them (fix list F-4)."""
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT attrs FROM steps WHERE kind = 'model' AND turn_id IS NULL AND started_at >= ?",
                (since_ms,),
            ).fetchall()
        calls = tokens = 0
        for r in rows:
            a = json.loads(r["attrs"] or "{}")
            if a.get("purpose") != "proxy":
                continue
            calls += 1
            u = a.get("usage") or {}
            if u.get("reported"):
                tokens += sum(int(u.get(k) or 0) for k in ("input_fresh", "cache_read", "cache_write", "output"))
        return {"calls": calls, "tokens": tokens}

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

    def sessions_needing_check(self, version: str) -> list[str]:
        """Sessions with a finished turn whose Issues result is missing or was made by
        another `signals.yaml` version (P3-4): the ones the start-up re-check works on."""
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, issues FROM turns WHERE status IS NOT NULL AND status != 'running'"
            ).fetchall()
        out: dict[str, None] = {}
        for r in rows:
            if r["session_id"] in out:
                continue
            try:
                have = json.loads(r["issues"]).get("signals_version") if r["issues"] else None
            except (ValueError, AttributeError):
                have = None
            if have != version:
                out[r["session_id"]] = None
        return list(out)

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

    # -- the usage ledger (Phase 3, E24a; attributes.yaml Part 3) -------------------------------
    # One row per turn x purpose x model, content-free, worked out from the stored turn, its
    # steps and its Issues result whenever one of them changes. The rollups read it; the Steps
    # panel keeps reading the steps. `persona_of(session_id)` stands in for `stamp.persona_id`
    # on turns recorded before 1.3 (the session's coworker, from coworker.db).
    persona_of: Optional[Any] = None

    def refresh_usage(self, turn_id: str) -> None:
        """Work out a turn's ledger rows again at the next write (turn end, a late step)."""
        with self._pending_lock:
            self._pending_refresh.add(turn_id)
        if self._background:
            _writer.wake()

    def _refresh_usage(self, turn_ids: Iterable[str]) -> None:
        """With the connection lock held: replace each finished turn's ledger rows, each turn in
        a transaction of its own. A turn whose rows cannot be worked out keeps its old rows and
        is logged; the others go on (P3B-6)."""
        now = int(time.time() * 1000)
        for tid in turn_ids:
            try:
                with self._conn:
                    self._refresh_turn_usage(tid, now)
            except Exception:
                log.exception("recordings: usage ledger not updated for turn %s", tid)

    def _refresh_turn_usage(self, tid: str, now: int) -> None:
        from .rollups import LEDGER_COLUMNS, ledger_rows

        t = self._conn.execute("SELECT * FROM turns WHERE turn_id = ?", (tid,)).fetchone()
        if t is None or t["status"] in (None, "running"):
            return
        self._touch_sessions({t["session_id"]})  # its Issues result or ledger rows changed
        steps = [_step_record(r) for r in self._conn.execute(
            "SELECT * FROM steps WHERE turn_id = ? AND kind IN ('model', 'wait', 'auto_approver', 'tool')",
            (tid,))]
        try:
            issues = json.loads(t["issues"]) if t["issues"] else None
        except ValueError:
            issues = None
        prior = self._conn.execute(
            "SELECT rating, session_deleted, persona_id FROM turn_usage WHERE turn_id = ? AND first = 1",
            (tid,)).fetchone()
        fb = self._conn.execute("SELECT rating FROM feedback WHERE turn_id = ?", (tid,)).fetchone()
        persona = prior["persona_id"] if prior is not None else None
        if persona is None and self.persona_of is not None:
            try:
                persona = self.persona_of(t["session_id"])
            except Exception:  # noqa: BLE001 - a stand-in never breaks the write
                persona = None
        rating = fb["rating"] if fb is not None else (prior["rating"] if prior is not None else None)
        rows = ledger_rows(_turn_record(t), steps, issues, persona_id=persona, rating=rating, now_ms=now)
        if prior is not None and prior["session_deleted"]:
            for r in rows:
                r["session_deleted"] = 1
        self._conn.execute("DELETE FROM turn_usage WHERE turn_id = ?", (tid,))
        self._conn.executemany(
            f"INSERT INTO turn_usage ({', '.join(LEDGER_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(LEDGER_COLUMNS))})",
            [tuple(r.get(c) for c in LEDGER_COLUMNS) for r in rows])

    def sweep_ledger(self, *, now_ms: Optional[int] = None) -> int:
        """Ledger rows older than 90 days go (TD3-5), whatever "Keep for" says."""
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM turn_usage WHERE started_at < ?",
                                     (now - LEDGER_DAYS * 24 * 60 * 60 * 1000,))
        return cur.rowcount

    def turns_without_usage(self, limit: int = 200) -> list[str]:
        """Finished turns with no ledger rows (recorded before Phase 3): the background job
        fills them in batches (TD3-8)."""
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT t.turn_id FROM turns t WHERE t.status IS NOT NULL AND t.status != 'running' "
                "AND NOT EXISTS (SELECT 1 FROM turn_usage u WHERE u.turn_id = t.turn_id) "
                "ORDER BY t.started_at LIMIT ?", (limit,)).fetchall()
        return [r["turn_id"] for r in rows]

    @staticmethod
    def _filters(where: Optional[dict[str, Any]], allowed: set[str], alias: str = "") -> Optional[tuple[str, list]]:
        """Equal-to (or IN, for a list) filters on ledger columns. None: an empty list, so no row."""
        clauses, params = "", []
        for k, v in (where or {}).items():
            if k not in allowed:
                raise ValueError(f"cannot filter on {k!r}")
            if isinstance(v, (list, tuple, set)):
                v = list(v)
                if not v:
                    return None
                clauses += f" AND {alias}{k} IN ({', '.join('?' * len(v))})"
                params += v
            elif v is None:
                clauses += f" AND {alias}{k} IS NULL"
            else:
                clauses += f" AND {alias}{k} = ?"
                params.append(v)
        return clauses, params

    _FILTERABLE = {"persona_id", "persona_fp", "team_id", "task_id", "run_id", "session_id", "machine"}

    def usage_rollup(self, group: list[str], start: int, end: int, *, days: Optional[list] = None,
                     where: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """One GROUP BY over the ledger: the turns that started in [start, end), grouped.
        `days`: the viewer's [label, start, end) boundaries (TD3-2); `where`: filters on
        ledger columns (persona_id, team_id, task_id, run_id, session_id, machine)."""
        from .rollups import ROLLUP_FIELDS, day_params, rollup_sql

        f = self._filters(where, self._FILTERABLE, "u.")
        if f is None:
            return []
        sql, n = rollup_sql(group, days=days, where=f[0])
        self.flush()
        with self._lock:
            rows = self._conn.execute(sql, [*day_params(days), start, end, *f[1]]).fetchall()
        out = []
        for r in rows:
            vals = list(r)
            out.append({"key": dict(zip(group or ["all"], vals[:n])),
                        **{k: int(v or 0) for k, v in zip(ROLLUP_FIELDS, vals[n:])}})
        return out

    _SUMMED = ("input_fresh", "cache_read", "cache_write", "output", "reasoning", "cache_read_known_input",
               "calls", "unreported", "failed", "over_limit")

    def usage_turns(self, start: int, end: int, *, where: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Each turn of the period as one dict (its rows summed), oldest first: what the
        medians, heaviest turns, coworker runs and automation runs read."""
        f = self._filters(where, self._FILTERABLE)
        if f is None:
            return []
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM turn_usage WHERE started_at >= ? AND started_at < ?" + f[0] +
                " ORDER BY started_at, turn_id, first DESC", [start, end, *f[1]]).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            d = out.get(r["turn_id"])
            if d is None:
                d = out[r["turn_id"]] = {k: r[k] for k in r.keys() if k not in ("purpose", "model", "first")}
                d["warn_checks"] = json.loads(d["warn_checks"] or "[]")
                d["total"], d["models"], d["by_purpose"] = 0, [], {}
                for k in self._SUMMED:
                    d[k] = 0
            for k in self._SUMMED:
                d[k] += r[k] or 0
            tot = (r["input_fresh"] or 0) + (r["cache_read"] or 0) + (r["cache_write"] or 0) + (r["output"] or 0)
            d["total"] += tot
            if r["purpose"] is not None:
                d["by_purpose"][r["purpose"]] = d["by_purpose"].get(r["purpose"], 0) + tot
            if r["model"] and r["model"] not in d["models"] and (r["calls"] or r["unreported"]):
                d["models"].append(r["model"])
        for d in out.values():
            d["at_least"] = d["unreported"] > 0
            d["active_ms"] = None if d["duration_ms"] is None else d["duration_ms"] - (d["waiting_ms"] or 0)
        return list(out.values())

    def usage_kept_from(self) -> Optional[int]:
        """The oldest ledger row's start: how far back the numbers go."""
        self.flush()
        with self._lock:
            row = self._conn.execute("SELECT MIN(started_at) AS m FROM turn_usage").fetchone()
        return row["m"]

    def approver_steps(self, start: int, end: int, *, persona_id: Optional[str] = None) -> list[dict[str, Any]]:
        """The auto-approver checks, and the tool steps a person let through anyway, of turns
        that started in the period (the Auto-approve strip, section 7.3). Steps, not the ledger:
        the strip reads Phase 1 fields that only steps hold."""
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.kind, s.shadow, s.duration_ms, s.attrs, s.parent_step_id FROM steps s "
                "JOIN turns t ON t.turn_id = s.turn_id WHERE t.started_at >= ? AND t.started_at < ? "
                "AND (s.kind = 'auto_approver' OR (s.kind = 'tool' AND s.attrs LIKE '%allow_anyway%'))"
                # one coworker: by the ledger's coworker id, which stands in for 1.2 turns
                + (" AND EXISTS (SELECT 1 FROM turn_usage u WHERE u.turn_id = t.turn_id AND u.persona_id = ?)"
                   if persona_id else ""),
                (start, end, persona_id) if persona_id else (start, end)).fetchall()
        return [{"kind": r["kind"], "shadow": bool(r["shadow"]), "duration_ms": r["duration_ms"],
                 "parent_step_id": r["parent_step_id"], "attrs": json.loads(r["attrs"] or "{}")} for r in rows]

    def outside_calls_between(self, start: int, end: int) -> dict[str, int]:
        """Model steps with no turn (proxy calls) in [start, end): in no pill, so outside the
        Usage page's total, on their own line (TD3-4)."""
        self.flush()
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS n, SUM(COALESCE(input_fresh,0)+COALESCE(cache_read,0)+COALESCE(cache_write,0)"
                "+COALESCE(output,0)) AS tok FROM steps WHERE turn_id IS NULL AND kind = 'model' "
                "AND usage_reported = 1 AND started_at >= ? AND started_at < ?", (start, end)).fetchone()
        return {"calls": int(r["n"] or 0), "total": int(r["tok"] or 0)}

    def turn_seqs(self, turn_ids: list[str]) -> dict[str, int]:
        """Each turn's number in its session ("turn 4"), for turns the store still has."""
        if not turn_ids:
            return {}
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                f"SELECT turn_id, seq FROM turns WHERE turn_id IN ({', '.join('?' * len(turn_ids))})",
                list(turn_ids)).fetchall()
        return {r["turn_id"]: r["seq"] for r in rows}

    def team_turns(self, session_ids: list[str]) -> list[dict[str, Any]]:
        """The member sessions' turns, oldest first, with `wake` (1.3): what TeamView's event
        list and the lead-polling series read."""
        if not session_ids:
            return []
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT turn_id, session_id, seq, started_at, duration_ms, status, wake, stamp FROM turns "
                f"WHERE session_id IN ({', '.join('?' * len(session_ids))}) ORDER BY started_at, turn_id",
                list(session_ids)).fetchall()
        return [{"turn_id": r["turn_id"], "session_id": r["session_id"], "seq": r["seq"],
                 "started_at": r["started_at"], "duration_ms": r["duration_ms"], "status": r["status"],
                 "wake": json.loads(r["wake"]) if r["wake"] else None,
                 "stamp": json.loads(r["stamp"] or "{}")} for r in rows]

    def last_turn_automation(self, session_id: str) -> Optional[dict[str, Any]]:
        """The session's latest turn's `turn.automation` (1.3), or None when that turn was not
        an automation run's own (a follow-up typed later, or no turn yet)."""
        self.flush()
        with self._lock:
            r = self._conn.execute("SELECT automation FROM turns WHERE session_id = ? "
                                   "ORDER BY started_at DESC, seq DESC LIMIT 1", (session_id,)).fetchone()
        if r is None or not r["automation"]:
            return None
        try:
            return json.loads(r["automation"])
        except ValueError:
            return None

    def last_tool_step(self, session_id: str, name: str, *, before_ms: Optional[int] = None) -> Optional[dict[str, Any]]:
        """The session's last tool step of this name started at or before `before_ms` (any
        time when None): {step_id, seq, turn_id}. What a hand-off's `transition` step is found
        by, without loading the whole recording (P3B-5)."""
        self.flush()
        sql = "SELECT step_id, seq, turn_id FROM steps WHERE session_id = ? AND kind = 'tool' AND name = ?"
        args: list[Any] = [session_id, name]
        if before_ms is not None:
            sql += " AND COALESCE(started_at, 0) <= ?"
            args.append(before_ms)
        with self._lock:
            r = self._conn.execute(sql + " ORDER BY started_at DESC, seq DESC LIMIT 1", args).fetchone()
        return None if r is None else {"step_id": r["step_id"], "seq": r["seq"], "turn_id": r["turn_id"]}

    def team_approval_cards(self, session_ids: list[str]) -> list[dict[str, Any]]:
        """The members' approval cards in order: each wait step of kind approval with its
        tool step's name and args_hash (`same_approval_twice`, signals 0.6)."""
        if not session_ids:
            return []
        self.flush()
        marks = ", ".join("?" * len(session_ids))
        with self._lock:
            waits = self._conn.execute(
                "SELECT s.step_id, s.turn_id, s.session_id, s.started_at, s.attrs, t.seq FROM steps s "
                "JOIN turns t ON t.turn_id = s.turn_id "
                f"WHERE s.kind = 'wait' AND s.session_id IN ({marks}) ORDER BY s.started_at, s.step_id",
                list(session_ids)).fetchall()
            approvals = []
            for w in waits:
                a = json.loads(w["attrs"] or "{}")
                if a.get("kind") == "approval" and a.get("for_call_id"):
                    approvals.append((w, a))
            # Only the tool steps the cards ask about, by (session, call id): not every tool
            # step of every member (P3B-5).
            tools = {}
            wanted = sorted({(w["session_id"], a["for_call_id"]) for w, a in approvals})
            for i in range(0, len(wanted), 400):
                part = wanted[i:i + 400]
                where = " OR ".join("(session_id = ? AND call_id = ?)" for _ in part)
                for r in self._conn.execute(
                        f"SELECT session_id, call_id, name, attrs FROM steps WHERE kind = 'tool' AND ({where})",
                        [v for pair in part for v in pair]).fetchall():
                    tools[(r["session_id"], r["call_id"])] = r
        out = []
        for w, a in approvals:
            tool = tools.get((w["session_id"], a.get("for_call_id")))
            if tool is None:
                continue
            out.append({"tool": tool["name"], "args_hash": json.loads(tool["attrs"] or "{}").get("args_hash"),
                        "session": w["session_id"], "outcome": a.get("outcome"), "turn_id": w["turn_id"],
                        "seq": w["seq"], "at": w["started_at"],
                        "step": {"step_id": w["step_id"], "turn_id": w["turn_id"]}})
        return out

    def scan_steps(self, session_ids: Any, *, since_ms: int = 0) -> list[dict[str, Any]]:
        """Tool steps whose `scanner.action` is `scan` (1.3), of one or more sessions, oldest
        first: what a finding's evidence and the coverage checks read."""
        ids = [session_ids] if isinstance(session_ids, str) else list(session_ids)
        if not ids:
            return []
        self.flush()
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.step_id, s.turn_id, s.session_id, s.call_id, s.seq, s.started_at, s.status, s.attrs, "
                "t.seq AS turn_seq FROM steps s LEFT JOIN turns t ON t.turn_id = s.turn_id "
                f"WHERE s.kind = 'tool' AND s.session_id IN ({', '.join('?' * len(ids))}) AND s.started_at >= ? "
                "AND s.attrs LIKE '%\"scanner\": {%' ORDER BY s.started_at, s.step_id", [*ids, since_ms]).fetchall()
        out = []
        for r in rows:
            a = json.loads(r["attrs"] or "{}")
            sc = a.get("scanner") or {}
            if sc.get("action") != "scan":
                continue
            out.append({"step_id": r["step_id"], "turn_id": r["turn_id"], "session_id": r["session_id"],
                        "call_id": r["call_id"], "seq": r["seq"], "turn_seq": r["turn_seq"],
                        "started_at": r["started_at"], "status": r["status"], "scanner": sc,
                        "exit_code": a.get("exit_code"), "result_sha256": a.get("result_sha256")})
        return out

    def read_turn_issues(self, turn_id: str) -> Optional[dict[str, Any]]:
        """One turn's stored Issues result with `seen`, or None."""
        self.flush()
        with self._lock:
            r = self._conn.execute("SELECT issues, issues_seen FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        if r is None or not r["issues"]:
            return None
        try:
            return {**json.loads(r["issues"]), "seen": bool(r["issues_seen"])}
        except ValueError:
            return None

    # -- feedback (Phase 3, E27a) ----------------------------------------------------------------
    def put_feedback(self, row: dict[str, Any]) -> dict[str, Any]:
        """Store (or replace) a turn's rating. Changing a rating replaces the row; `rated_at`
        stays the first rating's time and `changed_at` says when it last changed."""
        now = int(time.time() * 1000)
        with self._lock, self._conn:
            old = self._conn.execute("SELECT rated_at FROM feedback WHERE turn_id = ?", (row["turn_id"],)).fetchone()
            rated_at = old["rated_at"] if old is not None else now
            changed = now if old is not None else None
            self._conn.execute(
                "INSERT OR REPLACE INTO feedback (turn_id, session_id, rating, reasons, note, auto_approved, "
                "rated_at, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["turn_id"], row["session_id"], row["rating"], json.dumps(list(row.get("reasons") or [])),
                 row.get("note"), json.dumps(list(row.get("auto_approved") or [])), rated_at, changed))
            self._conn.execute("UPDATE turn_usage SET rating = ? WHERE turn_id = ?", (row["rating"], row["turn_id"]))
        return self.get_feedback(row["turn_id"]) or {}

    def get_feedback(self, turn_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM feedback WHERE turn_id = ?", (turn_id,)).fetchone()
        return None if r is None else _feedback_record(r)

    def delete_feedback(self, turn_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM feedback WHERE turn_id = ?", (turn_id,))
            self._conn.execute("UPDATE turn_usage SET rating = NULL WHERE turn_id = ?", (turn_id,))

    def session_feedback(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM feedback WHERE session_id = ? ORDER BY rated_at",
                                      (session_id,)).fetchall()
        return [_feedback_record(r) for r in rows]

    def feedback_since(self, since_ms: int, limit: int = 100, *,
                       unsent_for: Optional[str] = None) -> list[dict[str, Any]]:
        """Ratings given or changed at or after `since_ms`, oldest change first: the
        exporter's queue (E27b). With `unsent_for`, only the versions with no export mark for
        that destination yet (mark `feedback|<turn_id>|<changed_at or rated_at>`), so the
        queue keeps moving past the first `limit` once those have gone (P3B-2)."""
        sql = "SELECT * FROM feedback f WHERE COALESCE(f.changed_at, f.rated_at) >= ?"
        args: list[Any] = [since_ms]
        if unsent_for is not None:
            sql += (" AND NOT EXISTS (SELECT 1 FROM export_marks m WHERE m.destination = ? AND m.turn_id = f.turn_id"
                    " AND m.item_id = 'feedback|' || f.turn_id || '|' || COALESCE(f.changed_at, f.rated_at))")
            args.append(unsent_for)
        sql += " ORDER BY COALESCE(f.changed_at, f.rated_at) LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [_feedback_record(r) for r in rows]

    # -- coworker versions (Phase 3, E25c) -------------------------------------------------------
    def note_persona_version(self, persona_id: str, fp: str, name: Optional[str],
                             files: dict[str, bytes]) -> bool:
        """Keep a coworker version the first time its fingerprint is seen: its name, each
        file's sha256 and line count, and a copy of the files for "Show diff" (TD3-13). Kept
        locally only, never exported. Returns whether it was new. A later sighting only
        updates the name, so the Usage page names a renamed coworker by its latest name."""
        with self._lock, self._conn:
            have = self._conn.execute("SELECT 1 FROM persona_versions WHERE persona_id = ? AND fp = ?",
                                      (persona_id, fp)).fetchone()
            if have is not None:
                if name:
                    self._conn.execute("UPDATE persona_versions SET name = ? WHERE persona_id = ? AND fp = ?",
                                       (name, persona_id, fp))
                return False
            meta = {path: {"sha256": sha256(data).hexdigest(), "lines": _line_count(data)}
                    for path, data in files.items()}
            self._conn.execute(
                "INSERT INTO persona_versions (persona_id, fp, name, first_seen, files) VALUES (?, ?, ?, ?, ?)",
                (persona_id, fp, name, int(time.time() * 1000), json.dumps(meta, sort_keys=True)))
            self._conn.executemany(
                "INSERT OR REPLACE INTO persona_files (persona_id, fp, path, data) VALUES (?, ?, ?, ?)",
                [(persona_id, fp, path, zlib.compress(data)) for path, data in files.items()])
        return True

    def persona_versions(self, persona_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM persona_versions" + (" WHERE persona_id = ?" if persona_id else "")
                + " ORDER BY first_seen", (persona_id,) if persona_id else ()).fetchall()
        return [{"persona_id": r["persona_id"], "fp": r["fp"], "name": r["name"], "first_seen": r["first_seen"],
                 "files": json.loads(r["files"])} for r in rows]

    def persona_file(self, persona_id: str, fp: str, path: str) -> Optional[bytes]:
        with self._lock:
            r = self._conn.execute("SELECT data FROM persona_files WHERE persona_id = ? AND fp = ? AND path = ?",
                                   (persona_id, fp, path)).fetchone()
        return None if r is None else zlib.decompress(r["data"])

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
    for key in ("woken_by", "wake", "automation"):  # 1.2 and 1.3: absent when not recorded
        if d.get(key) is None:
            d.pop(key, None)
        else:
            d[key] = json.loads(d[key])
    return d


def _line_count(data: bytes) -> int:
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _feedback_record(r: sqlite3.Row) -> dict[str, Any]:
    """A feedback row as recording-format 1.3 `feedback` (the note in `content`)."""
    return {"turn_id": r["turn_id"], "session_id": r["session_id"], "rating": r["rating"],
            "reasons": json.loads(r["reasons"] or "[]"), "auto_approved": json.loads(r["auto_approved"] or "[]"),
            "rated_at": r["rated_at"], "changed_at": r["changed_at"], "content": {"note": r["note"]}}


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
