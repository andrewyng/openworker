"""The journal store — case-keyed knowledge that outlives boards and teams.

Split from the board log on purpose (decided 2026-08-16): a board is a team-scoped
artifact and can be archived with its team, but a journal case follows the
INVESTIGATION — it may span two boards, survive a team, or belong to an Ops case no
board ever references. So cases live in their own store, hash-chained per case, with
their own grant table. What stays unified with the board is the record shape and the
discipline: attributed, timestamped, append-only, taint-flagged — the policy/audit
choke point is the API layer, not table co-location.

Access model: the user is never gated. Everyone else needs a grant on the case:
- creating a case (first append) grants its creator;
- assignment feeds grants automatically (assign an item carrying a case → the
  assignee gains it; reassignment moves it) — "sharing rides assignment";
- explicit grants cover cross-team sharing.

Backing is SQLite for now (same as everything else in the state dir); the store is
deliberately small enough to swap the backing later without touching the verb
surface. Retrieval order stays: filters (here) → entity index → vectors as a
derived index.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .model import (
    JOURNAL_BODY_LIMIT,
    JOURNAL_KINDS,
    Actor,
    AuthorityError,
    BoardError,
    ChainError,
    Role,
)
from .store import GENESIS, _canonical, _hash

_HASHED_FIELDS = (
    "ts",
    "case_id",
    "kind",
    "actor",
    "actor_role",
    "space",
    "item_id",
    "payload",
    "taint",
    "prev_hash",
)


class JournalStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS journal_entries (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                case_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                actor TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                persona TEXT DEFAULT '',
                model TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                space TEXT,
                item_id INTEGER,
                payload TEXT NOT NULL,
                taint INTEGER NOT NULL DEFAULT 0,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_journal_case
                ON journal_entries (case_id, seq);
            CREATE INDEX IF NOT EXISTS idx_journal_item
                ON journal_entries (case_id, space, item_id, seq);
            CREATE TABLE IF NOT EXISTS journal_grants (
                case_id TEXT NOT NULL,
                principal TEXT NOT NULL,
                source TEXT NOT NULL,
                space TEXT DEFAULT '',
                item_id INTEGER,
                UNIQUE (case_id, principal, source, space, item_id)
            );
            CREATE TABLE IF NOT EXISTS journal_meta (
                case_id TEXT PRIMARY KEY,
                head_hash TEXT NOT NULL,
                created_ts TEXT NOT NULL
            );
            """)
        self._conn.commit()

    # ---------------------------------------------------------------------- verbs

    def append(
        self,
        actor: Actor,
        case: str,
        body: str,
        *,
        kind: str = "note",
        space: Optional[str] = None,
        item: Optional[int] = None,
        entities: Optional[list[str]] = None,
        refs: Optional[list[str]] = None,
        taint: bool = False,
    ) -> dict[str, Any]:
        if not (case or "").strip():
            raise BoardError("case is required")
        if not (body or "").strip():
            raise BoardError("entry body is required")
        if kind not in JOURNAL_KINDS:
            raise BoardError(f"unknown entry kind: {kind} (use one of {JOURNAL_KINDS})")
        if len(body) > JOURNAL_BODY_LIMIT:
            raise BoardError(
                f"entry body over {JOURNAL_BODY_LIMIT} chars — save the full"
                " capture to a file and journal an excerpt that references it"
            )
        with self._lock:
            exists = self._case_exists(case)
            if exists:
                self._check_access(actor, case)
            ts = datetime.now(timezone.utc).isoformat()
            prev = self._head_hash(case)
            record = {
                "ts": ts,
                "case_id": case,
                "kind": kind,
                "actor": actor.id,
                "actor_role": actor.role.value,
                "space": space,
                "item_id": item,
                "payload": _canonical(
                    {
                        "body": body,
                        "entities": sorted(set(entities or [])),
                        "refs": [str(ref) for ref in refs or []],
                    }
                ),
                "taint": 1 if taint else 0,
                "prev_hash": prev,
            }
            record["hash"] = _hash(record, fields=_HASHED_FIELDS)
            try:
                cursor = self._conn.execute(
                    """
                    INSERT INTO journal_entries
                        (ts, case_id, kind, actor, actor_role, persona, model,
                         session_id, space, item_id, payload, taint, prev_hash, hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        case,
                        kind,
                        actor.id,
                        actor.role.value,
                        actor.persona,
                        actor.model,
                        actor.session_id,
                        space,
                        item,
                        record["payload"],
                        record["taint"],
                        prev,
                        record["hash"],
                    ),
                )
                if not exists:
                    self._conn.execute(
                        "INSERT INTO journal_meta (case_id, head_hash, created_ts)"
                        " VALUES (?, ?, ?)",
                        (case, record["hash"], ts),
                    )
                    # A new case belongs to whoever opened it.
                    self._grant_locked(case, actor.id, source="creator")
                else:
                    self._conn.execute(
                        "UPDATE journal_meta SET head_hash = ? WHERE case_id = ?",
                        (record["hash"], case),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {**record, "seq": cursor.lastrowid}

    def read(
        self,
        actor: Actor,
        case: str,
        *,
        item: Optional[int] = None,
        author: Optional[str] = None,
        kind: Optional[str] = None,
        entity: Optional[str] = None,
        since_seq: int = 0,
        include_raw: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Filtered read. `raw` captures are skipped unless asked for (by
        `kind="raw"` or `include_raw`) so dumps never bury the signal entries."""
        with self._lock:
            self._check_access(actor, case)
            where = ["case_id = ?", "seq > ?"]
            params: list[Any] = [case, since_seq]
            if item is not None:
                where.append("item_id = ?")
                params.append(item)
            if author:
                where.append("actor = ?")
                params.append(author)
            if kind:
                if kind not in JOURNAL_KINDS:
                    raise BoardError(f"unknown entry kind: {kind}")
                where.append("kind = ?")
                params.append(kind)
            elif not include_raw:
                where.append("kind != 'raw'")
            rows = self._conn.execute(
                "SELECT * FROM journal_entries WHERE "
                + " AND ".join(where)
                + " ORDER BY seq",
                params,
            ).fetchall()
        out = []
        cap = max(1, min(int(limit or 100), 1000))
        for row in rows:
            entry = _row_to_entry(row)
            if entity and entity not in entry["entities"]:
                continue
            out.append(entry)
            if len(out) >= cap:
                break
        return out

    def overview(self, actor: Actor) -> list[dict[str, Any]]:
        """Case list with entry counts and last activity — the rail's summary view."""
        visible = self.cases(actor)
        if not visible:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT case_id, COUNT(*) AS entries, MAX(ts) AS last_ts"
                " FROM journal_entries GROUP BY case_id"
            ).fetchall()
        counts = {row["case_id"]: dict(row) for row in rows}
        return [
            {
                "case": case,
                "entries": counts.get(case, {}).get("entries", 0),
                "last_ts": counts.get(case, {}).get("last_ts") or "",
            }
            for case in visible
        ]

    def cases(self, actor: Actor) -> list[str]:
        """Cases visible to this actor (all of them for the user)."""
        with self._lock:
            if actor.role == Role.USER:
                rows = self._conn.execute(
                    "SELECT case_id FROM journal_meta ORDER BY case_id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT DISTINCT case_id FROM journal_grants WHERE principal = ?"
                    " ORDER BY case_id",
                    (actor.id,),
                ).fetchall()
        return [row["case_id"] for row in rows]

    def export(
        self,
        actor: Actor,
        case: str,
        *,
        store: Optional[Any] = None,
        format: str = "markdown",
        include_raw: bool = False,
    ) -> str:
        """Compile a journal case and linked items into a standalone report."""
        with self._lock:
            if not self._case_exists(case):
                raise BoardError(f"no case '{case}'")
            self._check_access(actor, case)
            meta_row = self._conn.execute(
                "SELECT * FROM journal_meta WHERE case_id = ?", (case,)
            ).fetchone()
            meta = dict(meta_row) if meta_row else {}

        entries = self.read(actor, case, include_raw=include_raw, limit=1000)
        items: list[dict[str, Any]] = []
        if store is not None:
            spaces = {e.get("space") for e in entries if e.get("space")}
            if hasattr(store, "spaces"):
                try:
                    spaces.update(store.spaces())
                except Exception:
                    pass
            for space in spaces:
                try:
                    space_items = store.list_items(space, actor)
                    items.extend(
                        it
                        for it in space_items
                        if it.get("case_id") == case or it.get("case") == case
                    )
                except Exception:
                    pass
            known_ids = {it.get("id") for it in items}
            for e in entries:
                item_id = e.get("item")
                space = e.get("space")
                if item_id and space and item_id not in known_ids:
                    try:
                        it = store.get_item(space, item_id, actor=actor)
                        items.append(it)
                        known_ids.add(item_id)
                    except Exception:
                        pass

        return format_case_report(
            case, entries, meta=meta, items=items, format=format
        )

    # ---------------------------------------------------------------------- grants

    def grant(self, actor: Actor, case: str, principal: str) -> None:
        """Explicit cross-team sharing. The user may grant any case; a lead may
        grant cases it holds. Workers never grant — evidence flows up, access
        flows down."""
        if actor.role == Role.WORKER or actor.role == Role.SYSTEM:
            raise AuthorityError("only the user or a lead may grant a case")
        with self._lock:
            if not self._case_exists(case):
                raise BoardError(f"no case '{case}'")
            if actor.role == Role.LEAD:
                self._check_access(actor, case)
            self._grant_locked(case, principal, source="grant")
            self._conn.commit()

    def revoke(self, actor: Actor, case: str, principal: str) -> None:
        if actor.role == Role.WORKER or actor.role == Role.SYSTEM:
            raise AuthorityError("only the user or a lead may revoke a case grant")
        with self._lock:
            if actor.role == Role.LEAD:
                self._check_access(actor, case)
            self._conn.execute(
                "DELETE FROM journal_grants WHERE case_id = ? AND principal = ?"
                " AND source = 'grant'",
                (case, principal),
            )
            self._conn.commit()

    def ensure_case(self, case: str, creator: str) -> None:
        """Create a case (empty, chain at genesis) if it doesn't exist, granting
        its creator. Called by the board when an item attaches a case ref — so
        the case belongs to whoever attached it, not to whichever assignee
        happens to journal first. Standalone cases (no board) are still created
        by their first append."""
        if not (case or "").strip():
            return
        with self._lock:
            if not self._case_exists(case):
                self._conn.execute(
                    "INSERT INTO journal_meta (case_id, head_hash, created_ts)"
                    " VALUES (?, ?, ?)",
                    (case, GENESIS, datetime.now(timezone.utc).isoformat()),
                )
                self._grant_locked(case, creator, source="creator")
                self._conn.commit()

    def sync_assignment(
        self,
        case: str,
        *,
        space: str,
        item_id: int,
        assignee: str,
        previous: str = "",
    ) -> None:
        """Called by the board on assign: access rides assignment. The previous
        assignee loses the grant THIS item carried (grants from its other items
        or explicit shares survive)."""
        if not case:
            return
        with self._lock:
            if previous:
                self._conn.execute(
                    "DELETE FROM journal_grants WHERE case_id = ? AND principal = ?"
                    " AND source = 'assignment' AND space = ? AND item_id = ?",
                    (case, previous, space, item_id),
                )
            self._grant_locked(
                case, assignee, source="assignment", space=space, item_id=item_id
            )
            self._conn.commit()

    # ----------------------------------------------------------------- integrity

    def verify_chain(self, case: str) -> int:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM journal_entries WHERE case_id = ? ORDER BY seq",
                (case,),
            ).fetchall()
        prev = GENESIS
        for row in rows:
            record = {key: row[key] for key in _HASHED_FIELDS}
            if row["prev_hash"] != prev:
                raise ChainError(f"entry {row['seq']}: chain linkage broken")
            if _hash(record, fields=_HASHED_FIELDS) != row["hash"]:
                raise ChainError(f"entry {row['seq']}: content does not match hash")
            prev = row["hash"]
        # Tail truncation is invisible to the chain itself; the stored head sees it.
        if rows and prev != self._head_hash(case):
            raise ChainError("case log ends before the recorded head — tail deleted")
        return len(rows)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ internals

    def _check_access(self, actor: Actor, case: str) -> None:
        if actor.role == Role.USER:
            return
        row = self._conn.execute(
            "SELECT 1 FROM journal_grants WHERE case_id = ? AND principal = ?"
            " LIMIT 1",
            (case, actor.id),
        ).fetchone()
        if row is None:
            raise AuthorityError(f"{actor.id} has no grant on case '{case}'")

    def _grant_locked(
        self,
        case: str,
        principal: str,
        *,
        source: str,
        space: str = "",
        item_id: Optional[int] = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO journal_grants"
            " (case_id, principal, source, space, item_id) VALUES (?, ?, ?, ?, ?)",
            (case, principal, source, space, item_id),
        )

    def _case_exists(self, case: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM journal_meta WHERE case_id = ?", (case,)
            ).fetchone()
            is not None
        )

    def _head_hash(self, case: str) -> str:
        row = self._conn.execute(
            "SELECT head_hash FROM journal_meta WHERE case_id = ?", (case,)
        ).fetchone()
        return row["head_hash"] if row else GENESIS


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    entry = dict(row)
    try:
        payload = json.loads(entry.pop("payload") or "{}")
    except json.JSONDecodeError:
        payload = {}
    entry["body"] = payload.get("body")
    entry["entities"] = payload.get("entities") or []
    entry["refs"] = payload.get("refs") or []
    entry["author"] = entry.pop("actor")
    entry["role"] = entry.pop("actor_role")
    entry["item"] = entry.pop("item_id")
    return entry


def format_case_report(
    case: str,
    entries: list[dict[str, Any]],
    *,
    meta: Optional[dict[str, Any]] = None,
    items: Optional[list[dict[str, Any]]] = None,
    format: str = "markdown",
) -> str:
    """Format a journal case and any linked work items into a report."""
    if format.lower() == "json":
        data = {
            "case": case,
            "meta": meta or {},
            "entries": entries,
            "items": items or [],
            "summary": {
                "total_entries": len(entries),
                "total_items": len(items or []),
                "findings": sum(
                    1 for e in entries if e.get("kind") == "finding"
                ),
                "evidence": sum(
                    1 for e in entries if e.get("kind") == "evidence"
                ),
                "decisions": sum(
                    1 for e in entries if e.get("kind") == "decision"
                ),
                "notes": sum(1 for e in entries if e.get("kind") == "note"),
            },
        }
        return json.dumps(data, indent=2)

    lines = [f"# Journal Case Report: {case}", ""]
    if meta:
        created = meta.get("created_ts") or meta.get("created") or ""
        head = meta.get("head_hash") or ""
        if created:
            lines.append(f"- **Created**: {created}")
        if head:
            lines.append(f"- **Head Hash**: `{head}`")
    lines.append(f"- **Total Entries**: {len(entries)}")
    if items:
        lines.append(f"- **Linked Items**: {len(items)}")
    lines.append("")

    findings = [e for e in entries if e.get("kind") == "finding"]
    decisions = [e for e in entries if e.get("kind") == "decision"]
    evidence = [e for e in entries if e.get("kind") == "evidence"]

    lines.append("## Executive Summary")
    lines.append(f"- **Findings**: {len(findings)}")
    lines.append(f"- **Decisions**: {len(decisions)}")
    lines.append(f"- **Evidence**: {len(evidence)}")
    if items:
        lines.append(f"- **Linked Items**: {len(items)}")
    lines.append("")

    if findings:
        lines.append("## Findings")
        for f in findings:
            ts = f.get("ts", "")
            author = f.get("author", "unknown")
            body = (f.get("body") or "").strip()
            lines.append(
                f"### Finding #{f.get('seq', '?')} ({author} at {ts})"
            )
            lines.append(body)
            if f.get("entities"):
                lines.append(f"- **Entities**: {', '.join(f['entities'])}")
            if f.get("refs"):
                lines.append(f"- **References**: {', '.join(f['refs'])}")
            lines.append("")

    if decisions:
        lines.append("## Decisions")
        for d in decisions:
            ts = d.get("ts", "")
            author = d.get("author", "unknown")
            body = (d.get("body") or "").strip()
            lines.append(f"- **[{ts}] {author}**: {body}")
        lines.append("")

    if evidence:
        lines.append("## Evidence")
        for ev in evidence:
            ts = ev.get("ts", "")
            author = ev.get("author", "unknown")
            body = (ev.get("body") or "").strip()
            lines.append(
                f"### Evidence #{ev.get('seq', '?')} ({author} at {ts})"
            )
            lines.append(body)
            if ev.get("refs"):
                lines.append(f"- **References**: {', '.join(ev['refs'])}")
            lines.append("")

    if items:
        lines.append("## Linked Work Items")
        lines.append("| ID | Title | State | Assignee | Criteria |")
        lines.append("|---|---|---|---|---|")
        for it in items:
            iid = f"#{it.get('id', '?')}"
            title = str(it.get("title", "")).replace("|", "\\|")
            state = it.get("state", "")
            assignee = it.get("assignee") or "-"
            criteria = (
                str(it.get("criteria", ""))
                .replace("|", "\\|")
                .replace("\n", " ")
            )
            lines.append(
                f"| {iid} | {title} | {state} | {assignee} | {criteria} |"
            )
        lines.append("")

    lines.append("## Chronological Activity Log")
    for e in entries:
        seq = e.get("seq", "?")
        kind = (e.get("kind") or "note").upper()
        author = e.get("author", "unknown")
        ts = e.get("ts", "")
        body = (e.get("body") or "").strip()
        lines.append(f"#### [{seq}] {kind} · {author} · {ts}")
        lines.append(body)
        if e.get("entities"):
            lines.append(f"- *Entities*: {', '.join(e['entities'])}")
        if e.get("refs"):
            lines.append(f"- *Refs*: {', '.join(e['refs'])}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"
