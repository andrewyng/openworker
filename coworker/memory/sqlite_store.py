"""SQLite-backed memory store (the default adapter)."""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .base import MemoryItem, MemoryStore, Scope


class SQLiteMemoryStore(MemoryStore):
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the server runs the WS handler on a different thread
        # than the store was created on; a lock serializes access.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                key TEXT,
                content TEXT NOT NULL,
                summary TEXT,
                workspace TEXT,
                session_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """)
        # Databases created before the summary column existed: rows without one fall
        # back to a truncated first line of content at render time (no data migration).
        cols = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "summary" not in cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN summary TEXT")
        self._conn.commit()

        # FTS5 full-text search index (companion virtual table + triggers)
        self._fts_enabled = False
        try:
            fts_exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
            ).fetchone()
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    content,
                    summary,
                    content=memories,
                    content_rowid=id
                )
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content, summary)
                    VALUES (new.id, new.content, new.summary);
                END;
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content, summary)
                    VALUES('delete', old.id, old.content, old.summary);
                END;
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content, summary)
                    VALUES('delete', old.id, old.content, old.summary);
                    INSERT INTO memories_fts(rowid, content, summary)
                    VALUES (new.id, new.content, new.summary);
                END;
            """)
            if not fts_exists:
                mem_count = self._conn.execute(
                    "SELECT count(*) FROM memories"
                ).fetchone()[0]
                if mem_count > 0:
                    self._conn.execute(
                        "INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"
                    )
            self._conn.commit()
            self._fts_enabled = True
        except sqlite3.OperationalError:
            self._fts_enabled = False

    def add(
        self,
        content: str,
        *,
        scope: Scope = Scope.WORKSPACE,
        key: Optional[str] = None,
        summary: Optional[str] = None,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> MemoryItem:
        scope = Scope(scope)
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO memories (scope, key, content, summary, workspace, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (scope.value, key, content, summary, workspace, session_id),
            )
            self._conn.commit()
            item = self.get(cursor.lastrowid)
        assert item is not None
        return item

    def get(self, item_id: int) -> Optional[MemoryItem]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id = ?", (item_id,)
            ).fetchone()
        return _row_to_item(row) if row else None

    def list(
        self,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[MemoryItem]:
        query = "SELECT * FROM memories WHERE 1 = 1"
        params: list[object] = []
        if scope is not None:
            query += " AND scope = ?"
            params.append(Scope(scope).value)
        if workspace is not None:
            query += " AND workspace = ?"
            params.append(workspace)
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def update(
        self, item_id: int, content: str, *, summary: Optional[str] = None
    ) -> Optional[MemoryItem]:
        with self._lock:
            if summary is not None:
                self._conn.execute(
                    "UPDATE memories SET content = ?, summary = ? WHERE id = ?",
                    (content, summary, item_id),
                )
            else:
                self._conn.execute(
                    "UPDATE memories SET content = ? WHERE id = ?", (content, item_id)
                )
            self._conn.commit()
        return self.get(item_id)

    def delete(self, item_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM memories WHERE id = ?", (item_id,))
            self._conn.commit()
        return cursor.rowcount > 0

    def delete_all(self, *, scope: Optional[Scope] = None) -> int:
        """Delete every memory (optionally one scope). Returns the number removed."""
        with self._lock:
            if scope is not None:
                cursor = self._conn.execute(
                    "DELETE FROM memories WHERE scope = ?", (Scope(scope).value,)
                )
            else:
                cursor = self._conn.execute("DELETE FROM memories")
            self._conn.commit()
        return cursor.rowcount

    def rekey_workspace(self, old: str, new: str) -> int:
        """Re-key workspace-scoped memories from one project key to another — the
        twentieth-pass one-time path→git migration. Rows are independent, so a
        collision with existing rows under `new` is just a union. Returns the
        number of rows moved."""
        if old == new:
            return 0
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE memories SET workspace = ? WHERE workspace = ? AND scope = ?",
                (new, old, Scope.WORKSPACE.value),
            )
            self._conn.commit()
        return cursor.rowcount

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
    ) -> list[MemoryItem]:
        """Search memories using FTS5 full-text index with fallback to LIKE matching."""
        if not query or not query.strip():
            return []
        if not getattr(self, "_fts_enabled", False):
            return self._search_like(
                query, limit=limit, scope=scope, workspace=workspace
            )

        tokens = re.findall(r"\w+", query)
        if not tokens:
            return []
        fts_query = " ".join(f'"{t}"*' for t in tokens)

        sql = (
            "SELECT m.* FROM memories m "
            "JOIN memories_fts f ON m.id = f.rowid "
            "WHERE memories_fts MATCH ?"
        )
        params: list[object] = [fts_query]
        if scope is not None:
            sql += " AND m.scope = ?"
            params.append(Scope(scope).value)
            if workspace is not None:
                sql += " AND m.workspace = ?"
                params.append(workspace)
        elif workspace is not None:
            sql += (
                " AND (m.scope = 'global' OR (m.scope = 'workspace' AND m.workspace = ?))"
            )
            params.append(workspace)
        sql += " ORDER BY f.rank LIMIT ?"
        params.append(limit)

        with self._lock:
            try:
                rows = self._conn.execute(sql, params).fetchall()
                return [_row_to_item(row) for row in rows]
            except sqlite3.OperationalError:
                return self._search_like(
                    query, limit=limit, scope=scope, workspace=workspace
                )

    def _search_like(
        self,
        query: str,
        *,
        limit: int = 5,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
    ) -> list[MemoryItem]:
        like_pattern = f"%{query.strip()}%"
        sql = "SELECT * FROM memories WHERE (content LIKE ? OR summary LIKE ?)"
        params: list[object] = [like_pattern, like_pattern]
        if scope is not None:
            sql += " AND scope = ?"
            params.append(Scope(scope).value)
            if workspace is not None:
                sql += " AND workspace = ?"
                params.append(workspace)
        elif workspace is not None:
            sql += (
                " AND (scope = 'global' OR (scope = 'workspace' AND workspace = ?))"
            )
            params.append(workspace)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def rebuild_index(self) -> None:
        """Rebuild the FTS5 search index."""
        if not getattr(self, "_fts_enabled", False):
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _row_to_item(row: sqlite3.Row) -> MemoryItem:
    return MemoryItem(
        id=row["id"],
        scope=Scope(row["scope"]),
        content=row["content"],
        key=row["key"],
        summary=row["summary"],
        workspace=row["workspace"],
        session_id=row["session_id"],
        created_at=row["created_at"],
    )
