"""The chat store — group chat as its own abstraction (eighth pass, 2026-08-16).

A GROUP is `{group_id, name, members[]}` plus an append-only message log and
per-member unread cursors. One group per team in v1 (created at the staffing gate
when chat is enabled), but nothing here knows about boards or teams — groups can
later serve non-team chats and the external-chat dialect.

Wake semantics live in the read side: an agent post is "for" exactly its @mentioned
members; a USER post is for every member ([User] outranks — posting to the channel
is rare and deliberate). Un-mentioned agent chatter wakes nobody, which is what
keeps chat an exception channel structurally.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .model import BoardError


class ChatStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_groups (
                group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                members TEXT NOT NULL,
                created_ts TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                author TEXT NOT NULL,
                author_role TEXT NOT NULL,
                text TEXT NOT NULL,
                mentions TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_chat_group ON chat_messages (group_id, seq);
            CREATE TABLE IF NOT EXISTS chat_cursors (
                cursor_key TEXT PRIMARY KEY,
                read_seq INTEGER NOT NULL
            );
            """)
        self._conn.commit()

    # ---------------------------------------------------------------------- groups

    def create_group(self, name: str, members: list[dict[str, Any]]) -> dict[str, Any]:
        """`members`: [{name, persona, role}] — `name` is the member's handle
        (@mention target). The user participates implicitly and is not a member row."""
        handles = [str(m.get("name", "")).strip() for m in members]
        if not name.strip():
            raise BoardError("group name is required")
        if not all(handles) or len(set(handles)) != len(handles):
            raise BoardError("every member needs a unique name")
        group = {
            "group_id": uuid.uuid4().hex[:12],
            "name": name.strip(),
            "members": [
                {
                    "name": str(m.get("name")),
                    "persona": str(m.get("persona", "")),
                    "role": str(m.get("role", "worker")),
                }
                for m in members
            ],
            "created_ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO chat_groups (group_id, name, members, created_ts)"
                " VALUES (?, ?, ?, ?)",
                (
                    group["group_id"],
                    group["name"],
                    json.dumps(group["members"]),
                    group["created_ts"],
                ),
            )
            self._conn.commit()
        return group

    def get_group(self, group_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_groups WHERE group_id = ?", (group_id,)
            ).fetchone()
        if row is None:
            return None
        group = dict(row)
        group["members"] = json.loads(group.pop("members") or "[]")
        return group

    # -------------------------------------------------------------------- messages

    def post(
        self, group_id: str, author: str, text: str, *, author_role: str = "worker"
    ) -> dict[str, Any]:
        """Append one message. Mentions are parsed against member handles —
        `@name` anywhere in the text — so tagging needs no separate parameter."""
        group = self.get_group(group_id)
        if group is None:
            raise BoardError(f"no chat group '{group_id}'")
        if not (text or "").strip():
            raise BoardError("message text is required")
        handles = {m["name"] for m in group["members"]}
        mentions = sorted(
            {
                m.group(1)
                for m in re.finditer(r"@([\w.-]+)", text)
                if m.group(1) in handles
            }
        )
        message = {
            "group_id": group_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "author": author,
            "author_role": author_role,
            "text": text,
            "mentions": mentions,
        }
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO chat_messages"
                " (group_id, ts, author, author_role, text, mentions)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    group_id,
                    message["ts"],
                    author,
                    author_role,
                    text,
                    json.dumps(mentions),
                ),
            )
            self._conn.commit()
        return {**message, "seq": cursor.lastrowid}

    def messages(
        self, group_id: str, *, since_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chat_messages WHERE group_id = ? AND seq > ?"
                " ORDER BY seq LIMIT ?",
                (group_id, since_seq, max(1, min(int(limit or 200), 2000))),
            ).fetchall()
        return [_row_to_message(row) for row in rows]

    # ------------------------------------------------------- unread / wake reads

    def unread_for(self, group_id: str, member: str) -> list[dict[str, Any]]:
        """Messages this member should be WOKEN for: posts that @mention it, plus
        every user post. Its own posts never count."""
        out = []
        for message in self.messages(group_id, since_seq=self._cursor(group_id, member)):
            if message["author"] == member:
                continue
            if member in message["mentions"] or message["author_role"] == "user":
                out.append(message)
        return out

    def unread_count(self, group_id: str, member: str) -> int:
        """Plain unread count (all messages since the member's cursor) — drives the
        sidebar badge for the USER, whose 'member' key is "user"."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM chat_messages WHERE group_id = ?"
                " AND seq > ? AND author != ?",
                (group_id, self._cursor(group_id, member), member),
            ).fetchone()
        return int(row["n"])

    def consume(self, group_id: str, member: str, upto_seq: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO chat_cursors (cursor_key, read_seq) VALUES (?, ?)"
                " ON CONFLICT(cursor_key) DO UPDATE SET read_seq ="
                " MAX(read_seq, ?)",
                (f"{group_id}:{member}", int(upto_seq), int(upto_seq)),
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _cursor(self, group_id: str, member: str) -> int:
        row = self._conn.execute(
            "SELECT read_seq FROM chat_cursors WHERE cursor_key = ?",
            (f"{group_id}:{member}",),
        ).fetchone()
        return int(row["read_seq"]) if row else 0


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    message = dict(row)
    try:
        message["mentions"] = json.loads(message.get("mentions") or "[]")
    except json.JSONDecodeError:
        message["mentions"] = []
    return message
