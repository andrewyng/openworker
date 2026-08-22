"""The board event store — an append-only log per space; the board and per-agent
deliveries are projections of it.

Doctrine (agent-teams design): board events and chat messages are one attributed,
timestamped, immutable record shape in one space-scoped log. One write path to police
and audit, one injection surface to defend, several read-side views. Nothing is ever
updated or deleted — a change of mind is a new event. Journal entries share the shape
and discipline but live in their own case-keyed store (teams.journal): cases outlive
boards and teams, so their lifecycle can't be chained to a board's.

Mechanics, kept boring:
- Append and projection-fold happen in the same transaction via the same `_apply`
  used by `rebuild()` — the materialized board can always be reproduced by replay.
- Events hash-chain per space (entry carries the previous hash) → `verify_chain`
  detects out-of-band edits. Tamper-evidence, not tamper-proofing.
- `taint` marks records authored after touching untrusted content; readers render it
  as provenance ("treat as evidence, not instructions").
- Per-agent delivery is the FEED projection over the one log (never a second write
  path): interest follows the assignment relation — a worker is subscribed to its
  slice, cursors mark consumption. The `recipient` column is retired plumbing
  (kept in the schema; no longer written).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .model import (
    EDGES,
    LINK_KINDS,
    WORKER_TARGETS,
    Actor,
    AuthorityError,
    BoardError,
    ChainError,
    ItemState,
    Role,
)

GENESIS = "genesis"

# Board-level claim policy: "open" (default) lets any worker self-assign an open,
# unassigned item — the board works as a pull queue for a fleet of workers, local or
# external. "lead-only" turns claims off; assignment stays with the lead/user. A lead
# on an open board can still reserve individual items by assigning them to itself.
CLAIM_POLICIES = ("open", "lead-only")

# Event kinds. Chat lands later with the chat surface; the record shape already fits.
# Journal entries live in their own case-keyed store (teams.journal) — cases outlive
# boards, so they don't belong in a board's space-scoped log.
ITEM_CREATED = "item_created"
ITEM_TRANSITIONED = "item_transitioned"
ITEM_COMMENTED = "item_commented"
ITEM_ASSIGNED = "item_assigned"
ITEM_LINKED = "item_linked"

_HASHED_FIELDS = (
    "ts",
    "space",
    "kind",
    "actor",
    "actor_role",
    "item_id",
    "case_id",
    "recipient",
    "payload",
    "taint",
    "prev_hash",
)


class TeamStore:
    def __init__(self, db_path: str | Path, *, journal: Any = None) -> None:
        # `journal` is a teams.journal.JournalStore when wired: assignment feeds
        # case grants ("sharing rides assignment"). Optional so the board works
        # standalone (tests, boards with no journal).
        self.journal = journal
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS team_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                space TEXT NOT NULL,
                kind TEXT NOT NULL,
                actor TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                persona TEXT DEFAULT '',
                model TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                item_id INTEGER,
                case_id TEXT,
                recipient TEXT,
                payload TEXT NOT NULL,
                taint INTEGER NOT NULL DEFAULT 0,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_team_events_space
                ON team_events (space, seq);
            CREATE INDEX IF NOT EXISTS idx_team_events_item
                ON team_events (space, item_id, seq);
            CREATE INDEX IF NOT EXISTS idx_team_events_case
                ON team_events (space, case_id, seq);
            CREATE INDEX IF NOT EXISTS idx_team_events_recipient
                ON team_events (recipient, seq);
            CREATE TABLE IF NOT EXISTS team_items (
                space TEXT NOT NULL,
                id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                criteria TEXT NOT NULL,
                state TEXT NOT NULL,
                assignee TEXT DEFAULT '',
                creator TEXT NOT NULL DEFAULT '',
                case_id TEXT DEFAULT '',
                refs TEXT NOT NULL DEFAULT '[]',
                created_ts TEXT NOT NULL,
                updated_seq INTEGER NOT NULL,
                PRIMARY KEY (space, id)
            );
            CREATE TABLE IF NOT EXISTS team_links (
                space TEXT NOT NULL,
                src INTEGER NOT NULL,
                kind TEXT NOT NULL,
                dst INTEGER NOT NULL,
                UNIQUE (space, src, kind, dst)
            );
            CREATE TABLE IF NOT EXISTS team_meta (
                space TEXT PRIMARY KEY,
                head_hash TEXT NOT NULL,
                watermark INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS team_cursors (
                cursor_key TEXT PRIMARY KEY,
                consumed_seq INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS team_settings (
                space TEXT PRIMARY KEY,
                claims TEXT NOT NULL DEFAULT 'open'
            );
            """)
        self._conn.commit()

    # ------------------------------------------------------------------ events core

    def append_event(
        self,
        space: str,
        kind: str,
        actor: Actor,
        *,
        item_id: Optional[int] = None,
        case_id: Optional[str] = None,
        recipient: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        taint: bool = False,
    ) -> dict[str, Any]:
        """Append one record and fold it into the projections, atomically."""
        if not space:
            raise BoardError("space is required")
        with self._lock:
            try:
                return self._append_locked(
                    space,
                    kind,
                    actor,
                    item_id=item_id,
                    case_id=case_id,
                    recipient=recipient,
                    payload=payload or {},
                    taint=taint,
                )
            except Exception:
                self._conn.rollback()
                raise

    def _append_locked(
        self,
        space: str,
        kind: str,
        actor: Actor,
        *,
        item_id: Optional[int],
        case_id: Optional[str],
        recipient: Optional[str],
        payload: dict[str, Any],
        taint: bool,
    ) -> dict[str, Any]:
        prev = self._head_hash(space)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "space": space,
            "kind": kind,
            "actor": actor.id,
            "actor_role": actor.role.value,
            "item_id": item_id,
            "case_id": case_id,
            "recipient": recipient,
            "payload": _canonical(payload),
            "taint": 1 if taint else 0,
            "prev_hash": prev,
        }
        record["hash"] = _hash(record)
        cursor = self._conn.execute(
            """
            INSERT INTO team_events
                (ts, space, kind, actor, actor_role, persona, model, session_id,
                 item_id, case_id, recipient, payload, taint, prev_hash, hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["ts"],
                space,
                kind,
                actor.id,
                actor.role.value,
                actor.persona,
                actor.model,
                actor.session_id,
                item_id,
                case_id,
                recipient,
                record["payload"],
                record["taint"],
                prev,
                record["hash"],
            ),
        )
        seq = cursor.lastrowid
        self._apply(space, seq, record["ts"], kind, actor.id, item_id, payload)
        self._conn.execute(
            """
            INSERT INTO team_meta (space, head_hash, watermark) VALUES (?, ?, ?)
            ON CONFLICT(space) DO UPDATE SET head_hash = ?, watermark = ?
            """,
            (space, record["hash"], seq, record["hash"], seq),
        )
        self._conn.commit()
        return {**record, "seq": seq, "payload": payload}

    def events(
        self,
        space: str,
        *,
        kinds: Optional[list[str]] = None,
        item_id: Optional[int] = None,
        case_id: Optional[str] = None,
        since_seq: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        where = ["space = ?", "seq > ?"]
        params: list[Any] = [space, since_seq]
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if item_id is not None:
            where.append("item_id = ?")
            params.append(item_id)
        if case_id is not None:
            where.append("case_id = ?")
            params.append(case_id)
        sql = (
            "SELECT * FROM team_events WHERE "
            + " AND ".join(where)
            + " ORDER BY seq LIMIT ?"
        )
        params.append(max(1, min(int(limit or 500), 2000)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def for_recipient(
        self, recipient: str, *, since_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Everything addressed to one agent, in order — the delivery projection."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_events WHERE recipient = ? AND seq > ?"
                " ORDER BY seq LIMIT ?",
                (recipient, since_seq, max(1, min(int(limit or 200), 2000))),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    # -------------------------------------------------- delivery (durable feed)

    # The per-agent durable feed is a PROJECTION over the one log, never a second
    # write path — and INTEREST FOLLOWS THE ASSIGNMENT RELATION (owner ruling
    # 2026-08-17): a worker is subscribed to events on its slice (items assigned
    # to it or filed by it — subscription ≡ visibility, one boundary), with no
    # per-event addressing decisions in the write path. "Consumed" is a cursor;
    # durable-until-consumed (a crash before consume replays on the next drain);
    # coalescing happens at dequeue. "Mailbox" is banned as a concept.

    def feed_for(
        self, space: str, actor_id: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Unconsumed events this actor is subscribed to, in order: everything on
        its current slice, plus assignment events that START its interest (newly
        assigned to it) or END it (just reassigned away — it hears that, then
        goes quiet). Its own events never appear."""
        key = f"feed:{actor_id}:{space}"
        events = self.events(space, since_seq=self._cursor(key), limit=limit)
        with self._lock:
            slice_ids = self._worker_slice(space, actor_id)
        out = []
        for event in events:
            if event["actor"] == actor_id:
                continue
            payload = event.get("payload") or {}
            if event["kind"] == ITEM_ASSIGNED and actor_id in (
                payload.get("assignee"),
                payload.get("previous"),
            ):
                out.append(event)
                continue
            if event.get("item_id") in slice_ids:
                out.append(event)
        return out

    def consume_feed(self, space: str, actor_id: str, upto_seq: int) -> None:
        self._set_cursor(f"feed:{actor_id}:{space}", int(upto_seq))

    # Lead subscriptions: an ALLOWLIST of decision-demanding event classes — a
    # worker moving its item to review/blocked, or filing a new item. Journal
    # appends and routine comments never wake anyone.
    SUBSCRIBED_TRANSITIONS = ("review", "blocked")

    def subscribed_events(
        self, space: str, subscriber: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Unconsumed subscription-worthy events on a space for one subscriber."""
        key = f"sub:{subscriber}:{space}"
        events = self.events(
            space,
            kinds=[ITEM_TRANSITIONED, ITEM_CREATED, ITEM_ASSIGNED],
            since_seq=self._cursor(key),
            limit=limit,
        )
        out = []
        for event in events:
            if event["actor"] == subscriber:
                continue  # your own verbs never wake you
            if (
                event["kind"] == ITEM_TRANSITIONED
                and event["payload"].get("to") not in self.SUBSCRIBED_TRANSITIONS
            ):
                continue
            # Assignments only surface when they are CLAIMS — the lead supervises
            # self-service by exception; its own (and the user's) assigns are not news.
            if event["kind"] == ITEM_ASSIGNED and not event["payload"].get("claimed"):
                continue
            out.append(event)
        return out

    def consume_subscription(self, space: str, subscriber: str, upto_seq: int) -> None:
        self._set_cursor(f"sub:{subscriber}:{space}", upto_seq)

    def _cursor(self, key: str) -> int:
        row = self._conn.execute(
            "SELECT consumed_seq FROM team_cursors WHERE cursor_key = ?", (key,)
        ).fetchone()
        return int(row["consumed_seq"]) if row else 0

    def _set_cursor(self, key: str, seq: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO team_cursors (cursor_key, consumed_seq) VALUES (?, ?)"
                " ON CONFLICT(cursor_key) DO UPDATE SET consumed_seq ="
                " MAX(consumed_seq, ?)",
                (key, int(seq), int(seq)),
            )
            self._conn.commit()

    def spaces(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT space FROM team_meta ORDER BY space"
            ).fetchall()
        return [row["space"] for row in rows]

    def verify_chain(self, space: str) -> int:
        """Recompute the chain; return the number of verified events.

        Raises ChainError at the first record whose hash or linkage does not match —
        the log was edited out of band.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_events WHERE space = ? ORDER BY seq", (space,)
            ).fetchall()
        prev = GENESIS
        for row in rows:
            record = {key: row[key] for key in _HASHED_FIELDS}
            if row["prev_hash"] != prev:
                raise ChainError(f"event {row['seq']}: chain linkage broken")
            if _hash(record) != row["hash"]:
                raise ChainError(f"event {row['seq']}: content does not match hash")
            prev = row["hash"]
        # The chain alone can't see TAIL truncation (a shortened log still links);
        # the stored head can.
        if rows and prev != self._head_hash(space):
            raise ChainError("log ends before the recorded head — tail deleted")
        return len(rows)

    def rebuild(self, space: str) -> None:
        """Drop the space's projections and replay its log through `_apply`.

        The recovery path (projection bug fix, cache corruption) — never the hot
        path; live appends fold incrementally in `append_event`.
        """
        with self._lock:
            self._conn.execute("DELETE FROM team_items WHERE space = ?", (space,))
            self._conn.execute("DELETE FROM team_links WHERE space = ?", (space,))
            rows = self._conn.execute(
                "SELECT seq, ts, kind, actor, item_id, payload FROM team_events"
                " WHERE space = ? ORDER BY seq",
                (space,),
            ).fetchall()
            for row in rows:
                self._apply(
                    space,
                    row["seq"],
                    row["ts"],
                    row["kind"],
                    row["actor"],
                    row["item_id"],
                    json.loads(row["payload"]),
                )
            self._conn.commit()

    # ------------------------------------------------------------------ board verbs

    def create_item(
        self,
        space: str,
        actor: Actor,
        *,
        title: str,
        criteria: str,
        description: str = "",
        parent: Optional[int] = None,
        case: Optional[str] = None,
    ) -> dict[str, Any]:
        """New item, `open` and unassigned. Acceptance criteria are load-bearing —
        required.

        Workers may create too — a bug spotted in passing, a follow-up — because
        filing is harmless: nothing runs until the item is ASSIGNED, and assign
        authority stays with the lead/user (the lead triages worker filings:
        assign or cancel)."""
        self._require(actor, {Role.USER, Role.LEAD, Role.WORKER}, "create_item")
        if not (title or "").strip():
            raise BoardError("title is required")
        if not (criteria or "").strip():
            raise BoardError(
                "acceptance criteria are required — they are what gets verified at"
                " review"
            )
        with self._lock:
            if parent is not None:
                parent_item = self._item(space, parent)
                if case is None:
                    case = parent_item["case_id"] or None
            item_id = self._next_item_id(space)
            event = self.append_event(
                space,
                ITEM_CREATED,
                actor,
                item_id=item_id,
                case_id=case,
                payload={
                    "title": title.strip(),
                    "description": description,
                    "criteria": criteria.strip(),
                    "parent": parent,
                    "case": case,
                },
            )
            if self.journal is not None and case:
                self.journal.ensure_case(case, actor.id)
        return self.get_item(space, item_id, seq=event["seq"])

    def list_items(
        self,
        space: str,
        actor: Actor,
        *,
        state: Optional[str] = None,
        assignee: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Items in a space. Workers see only their slice: assigned items plus items
        directly linked to those."""
        where = ["space = ?"]
        params: list[Any] = [space]
        if state:
            where.append("state = ?")
            params.append(ItemState(state).value)
        if assignee:
            where.append("assignee = ?")
            params.append(assignee)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_items WHERE "
                + " AND ".join(where)
                + " ORDER BY id",
                params,
            ).fetchall()
            items = [_row_to_item(row) for row in rows]
            if actor.role == Role.WORKER:
                visible = self._worker_slice(space, actor.id)
                # On an open-claims board the claimable pool is visible too — a
                # pull queue nobody can see is not a queue (drill-caught: an
                # external worker with no assignment saw an empty board). Under
                # lead-only policy workers can't act on it, so it stays hidden.
                claims_open = self.policy(space)["claims"] == "open"
                items = [
                    item
                    for item in items
                    if item["id"] in visible
                    or (
                        claims_open
                        and item["state"] == ItemState.OPEN.value
                        and not item["assignee"]
                    )
                ]
            for item in items:
                item["links"] = self._links_of(space, item["id"])
        return items

    def get_item(
        self, space: str, item_id: int, *, seq: Optional[int] = None
    ) -> dict[str, Any]:
        with self._lock:
            item = self._item(space, item_id)
            item["links"] = self._links_of(space, item_id)
            item["comments"] = self.comments(space, item_id)
        if seq is not None:
            item["seq"] = seq
        return item

    def transition(
        self,
        space: str,
        actor: Actor,
        item_id: int,
        to: str,
        *,
        comment: str = "",
        refs: Optional[list[str]] = None,
        taint: bool = False,
    ) -> dict[str, Any]:
        target = ItemState(to)
        with self._lock:
            item = self._item(space, item_id)
            current = ItemState(item["state"])
            if target not in EDGES[current]:
                raise BoardError(
                    f"illegal transition {current.value} → {target.value}"
                )
            self._check_transition_authority(actor, item, current, target)
            # No per-event addressing: delivery is the FEED projection — interest
            # follows the assignment relation (see feed_for), so a send-back, an
            # unblock, a cancel, or an acceptance reaches whoever holds the item
            # without the store editorializing about who cares.
            event = self.append_event(
                space,
                ITEM_TRANSITIONED,
                actor,
                item_id=item_id,
                case_id=item["case_id"] or None,
                payload={
                    "from": current.value,
                    "to": target.value,
                    "comment": comment,
                    "refs": list(refs or []),
                },
                taint=taint,
            )
        return self.get_item(space, item_id, seq=event["seq"])

    def comment(
        self,
        space: str,
        actor: Actor,
        item_id: int,
        body: str,
        *,
        refs: Optional[list[str]] = None,
        taint: bool = False,
    ) -> dict[str, Any]:
        if not (body or "").strip():
            raise BoardError("comment body is required")
        with self._lock:
            item = self._item(space, item_id)
            if actor.role == Role.WORKER and item_id not in self._worker_slice(
                space, actor.id
            ):
                raise AuthorityError(
                    f"worker {actor.id} may only comment on its assigned items"
                    " and items linked to them"
                )
            return self.append_event(
                space,
                ITEM_COMMENTED,
                actor,
                item_id=item_id,
                case_id=item["case_id"] or None,
                payload={"body": body, "refs": list(refs or [])},
                taint=taint,
            )

    def assign(
        self, space: str, actor: Actor, item_id: int, assignee: str
    ) -> dict[str, Any]:
        """Set the assignee. Not a message: the feed projection delivers it — the
        new assignee's interest starts with this event, and the previous
        assignee's interest ends with it (both hear it; see feed_for)."""
        self._require(actor, {Role.USER, Role.LEAD}, "assign")
        if not (assignee or "").strip():
            raise BoardError("assignee is required")
        with self._lock:
            item = self._item(space, item_id)
            state = ItemState(item["state"])
            if state in (ItemState.DONE, ItemState.CANCELED):
                raise BoardError(
                    f"cannot assign an item in state {state.value} — reopen it first"
                )
            event = self.append_event(
                space,
                ITEM_ASSIGNED,
                actor,
                item_id=item_id,
                case_id=item["case_id"] or None,
                payload={"assignee": assignee, "previous": item["assignee"] or ""},
            )
            if self.journal is not None and item["case_id"]:
                self.journal.sync_assignment(
                    item["case_id"],
                    space=space,
                    item_id=item_id,
                    assignee=assignee,
                    previous=item["assignee"] or "",
                )
        return self.get_item(space, item_id, seq=event["seq"])

    def claim(self, space: str, actor: Actor, item_id: int) -> dict[str, Any]:
        """Self-assign an open, unassigned item. Nobody stamps a claim — the store
        arbitrates: the open+unassigned check runs under the write lock, so when two
        workers race for the same item, exactly one wins and the other gets a clean
        error. A claim is a normal assignment event attributed to the claimer —
        visible in the lead's subscription feed and revocable like any assignment
        (reassign or cancel). Gated by the board's claim policy."""
        self._require(actor, {Role.USER, Role.LEAD, Role.WORKER}, "claim")
        with self._lock:
            if actor.role == Role.WORKER and self.policy(space)["claims"] != "open":
                raise AuthorityError(
                    "claims are lead-only on this board — ask the lead to assign"
                    " the item to you"
                )
            item = self._item(space, item_id)
            if ItemState(item["state"]) is not ItemState.OPEN:
                raise BoardError(
                    f"item #{item_id} is {item['state']} — only open items can be"
                    " claimed"
                )
            if item["assignee"]:
                raise BoardError(
                    f"item #{item_id} is already claimed by {item['assignee']}"
                )
            event = self.append_event(
                space,
                ITEM_ASSIGNED,
                actor,
                item_id=item_id,
                case_id=item["case_id"] or None,
                payload={"assignee": actor.id, "previous": "", "claimed": True},
            )
            if self.journal is not None and item["case_id"]:
                self.journal.sync_assignment(
                    item["case_id"],
                    space=space,
                    item_id=item_id,
                    assignee=actor.id,
                    previous="",
                )
        return self.get_item(space, item_id, seq=event["seq"])

    def policy(self, space: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT claims FROM team_settings WHERE space = ?", (space,)
            ).fetchone()
        return {"claims": row["claims"] if row else "open"}

    def set_policy(self, space: str, actor: Actor, *, claims: str) -> dict[str, Any]:
        """Board-level policy. Settings, not history — like cursors, this is
        infrastructure the log doesn't narrate."""
        self._require(actor, {Role.USER, Role.LEAD}, "set_policy")
        if claims not in CLAIM_POLICIES:
            raise BoardError(
                f"unknown claim policy: {claims} (use one of {CLAIM_POLICIES})"
            )
        with self._lock:
            self._conn.execute(
                "INSERT INTO team_settings (space, claims) VALUES (?, ?)"
                " ON CONFLICT(space) DO UPDATE SET claims = ?",
                (space, claims, claims),
            )
            self._conn.commit()
        return {"claims": claims}

    def link(
        self, space: str, actor: Actor, src: int, kind: str, dst: int
    ) -> dict[str, Any]:
        self._require(actor, {Role.USER, Role.LEAD}, "link")
        if kind not in LINK_KINDS:
            raise BoardError(f"unknown link kind: {kind} (use one of {LINK_KINDS})")
        if src == dst:
            raise BoardError("an item cannot link to itself")
        with self._lock:
            self._item(space, src)
            self._item(space, dst)
            if kind == "parent" and self._would_cycle(space, src, dst):
                raise BoardError("parent link would create a cycle")
            return self.append_event(
                space,
                ITEM_LINKED,
                actor,
                item_id=src,
                payload={"src": src, "kind": kind, "dst": dst},
            )

    def comments(self, space: str, item_id: int) -> list[dict[str, Any]]:
        """Attributed comments on an item — standalone comments plus the notes
        carried on transitions (a `blocked` explanation lives with its event)."""
        out = []
        for event in self.events(
            space, kinds=[ITEM_COMMENTED, ITEM_TRANSITIONED], item_id=item_id
        ):
            body = (
                event["payload"].get("body")
                if event["kind"] == ITEM_COMMENTED
                else event["payload"].get("comment")
            )
            if body:
                out.append(
                    {
                        "seq": event["seq"],
                        "ts": event["ts"],
                        "author": event["actor"],
                        "role": event["actor_role"],
                        "body": body,
                        "taint": event["taint"],
                    }
                )
        return out

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------- internals

    def _apply(
        self,
        space: str,
        seq: int,
        ts: str,
        kind: str,
        actor_id: str,
        item_id: Optional[int],
        payload: dict[str, Any],
    ) -> None:
        """Fold one event into the projections. The ONLY writer of team_items and
        team_links — shared by live appends and rebuild(), so replay always
        reproduces the materialized state."""
        if kind == ITEM_CREATED:
            self._conn.execute(
                """
                INSERT INTO team_items
                    (space, id, title, description, criteria, state, assignee,
                     creator, case_id, refs, created_ts, updated_seq)
                VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, '[]', ?, ?)
                """,
                (
                    space,
                    item_id,
                    payload.get("title") or "",
                    payload.get("description") or "",
                    payload.get("criteria") or "",
                    ItemState.OPEN.value,
                    actor_id,
                    payload.get("case") or "",
                    ts,
                    seq,
                ),
            )
            if payload.get("parent") is not None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO team_links (space, src, kind, dst)"
                    " VALUES (?, ?, 'parent', ?)",
                    (space, item_id, payload["parent"]),
                )
        elif kind == ITEM_TRANSITIONED:
            self._conn.execute(
                "UPDATE team_items SET state = ?, updated_seq = ?"
                " WHERE space = ? AND id = ?",
                (payload.get("to"), seq, space, item_id),
            )
            self._merge_refs(space, item_id, payload.get("refs"))
        elif kind == ITEM_COMMENTED:
            self._merge_refs(space, item_id, payload.get("refs"))
        elif kind == ITEM_ASSIGNED:
            self._conn.execute(
                "UPDATE team_items SET assignee = ?, updated_seq = ?"
                " WHERE space = ? AND id = ?",
                (payload.get("assignee") or "", seq, space, item_id),
            )
        elif kind == ITEM_LINKED:
            self._conn.execute(
                "INSERT OR IGNORE INTO team_links (space, src, kind, dst)"
                " VALUES (?, ?, ?, ?)",
                (space, payload.get("src"), payload.get("kind"), payload.get("dst")),
            )
        # Comment bodies and journal entries have no materialized state: their
        # projections read straight off the (indexed) log. Only the artifact
        # refs a comment carries fold onto the item.

    def _merge_refs(
        self, space: str, item_id: Optional[int], refs: Optional[list]
    ) -> None:
        if not refs or item_id is None:
            return
        row = self._conn.execute(
            "SELECT refs FROM team_items WHERE space = ? AND id = ?",
            (space, item_id),
        ).fetchone()
        if row is None:
            return
        merged = json.loads(row["refs"] or "[]")
        merged.extend(str(ref) for ref in refs if str(ref) not in merged)
        self._conn.execute(
            "UPDATE team_items SET refs = ? WHERE space = ? AND id = ?",
            (json.dumps(merged), space, item_id),
        )

    def _check_transition_authority(
        self, actor: Actor, item: dict[str, Any], current: ItemState, target: ItemState
    ) -> None:
        if actor.role == Role.SYSTEM:
            raise AuthorityError("system events cannot transition items")
        if target == ItemState.DONE and actor.role == Role.WORKER:
            raise AuthorityError(
                "workers finish by moving to review — done is the verdict after"
                " verification"
            )
        if actor.role == Role.WORKER:
            if item["assignee"] != actor.id:
                raise AuthorityError(
                    f"worker {actor.id} is not assigned item #{item['id']}"
                )
            if target not in WORKER_TARGETS:
                raise AuthorityError(
                    f"workers may move their item to"
                    f" {sorted(state.value for state in WORKER_TARGETS)} only"
                )

    def _worker_slice(self, space: str, worker_id: str) -> set[int]:
        # Assigned items, items the worker filed itself, and items directly
        # linked to either — its slice of the board, nothing more.
        rows = self._conn.execute(
            "SELECT id FROM team_items WHERE space = ?"
            " AND (assignee = ? OR creator = ?)",
            (space, worker_id, worker_id),
        ).fetchall()
        mine = {row["id"] for row in rows}
        if not mine:
            return set()
        linked = self._conn.execute(
            "SELECT src, dst FROM team_links WHERE space = ?", (space,)
        ).fetchall()
        out = set(mine)
        for row in linked:
            if row["src"] in mine:
                out.add(row["dst"])
            if row["dst"] in mine:
                out.add(row["src"])
        return out

    def _links_of(self, space: str, item_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT src, kind, dst FROM team_links WHERE space = ?"
            " AND (src = ? OR dst = ?)",
            (space, item_id, item_id),
        ).fetchall()
        out = []
        for row in rows:
            if row["src"] == item_id:
                out.append({"kind": row["kind"], "item": row["dst"]})
            else:
                inverse = "child" if row["kind"] == "parent" else "blocked_by"
                out.append({"kind": inverse, "item": row["src"]})
        return out

    def _would_cycle(self, space: str, src: int, dst: int) -> bool:
        # Walking up from dst: if we reach src, making dst the parent of src closes
        # a loop.
        current, hops = dst, 0
        while hops < 1000:
            row = self._conn.execute(
                "SELECT dst FROM team_links WHERE space = ? AND src = ?"
                " AND kind = 'parent'",
                (space, current),
            ).fetchone()
            if row is None:
                return False
            if row["dst"] == src:
                return True
            current, hops = row["dst"], hops + 1
        return True

    def _item(self, space: str, item_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM team_items WHERE space = ? AND id = ?", (space, item_id)
        ).fetchone()
        if row is None:
            raise BoardError(f"no item #{item_id} in space '{space}'")
        return _row_to_item(row)

    def _next_item_id(self, space: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(id) AS top FROM team_items WHERE space = ?", (space,)
        ).fetchone()
        return int(row["top"] or 0) + 1

    def _head_hash(self, space: str) -> str:
        row = self._conn.execute(
            "SELECT head_hash FROM team_meta WHERE space = ?", (space,)
        ).fetchone()
        return row["head_hash"] if row else GENESIS

    def _require(self, actor: Actor, roles: set[Role], verb: str) -> None:
        if actor.role not in roles:
            raise AuthorityError(
                f"{verb} requires one of"
                f" {sorted(role.value for role in roles)} (actor {actor.id} is"
                f" {actor.role.value})"
            )


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(record: dict[str, Any], *, fields: tuple[str, ...] = _HASHED_FIELDS) -> str:
    material = _canonical({key: record[key] for key in fields})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["refs"] = json.loads(item.get("refs") or "[]")
    except json.JSONDecodeError:
        item["refs"] = []
    return item


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    event = dict(row)
    try:
        event["payload"] = json.loads(event.get("payload") or "{}")
    except json.JSONDecodeError:
        event["payload"] = {}
    return event
