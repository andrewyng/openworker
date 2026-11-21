"""SQLite-backed memory store (the default adapter)."""

from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Optional

from .base import MemoryItem, MemoryStore, Scope

_AR_DIACRITICS_RE = re.compile(r"[\u0640\u064B-\u065F\u0670\u06D6-\u06ED]")
_AR_NORM_TABLE = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ة": "ه",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})


def _normalize_search_text(text: Optional[str]) -> str:
    """Normalize Arabic Tashkeel, Tatweel, Alef/Teh-Marbuta variants & Eastern digits for FTS5."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", text)
    s = _AR_DIACRITICS_RE.sub("", s)
    return s.translate(_AR_NORM_TABLE)


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
                norm_content TEXT,
                norm_summary TEXT,
                workspace TEXT,
                session_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """)
        # Databases created before the summary/norm columns existed: migrate cleanly.
        cols = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "summary" not in cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN summary TEXT")
        if "norm_content" not in cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN norm_content TEXT")
        if "norm_summary" not in cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN norm_summary TEXT")
        # Backfill normalized columns for any existing rows
        rows_missing = self._conn.execute(
            "SELECT id, content, summary FROM memories WHERE norm_content IS NULL"
        ).fetchall()
        for r in rows_missing:
            self._conn.execute(
                "UPDATE memories SET norm_content = ?, norm_summary = ? WHERE id = ?",
                (_normalize_search_text(r["content"]), _normalize_search_text(r["summary"]), r["id"]),
            )
        self._conn.commit()

        # FTS5 full-text search index (companion virtual table + triggers over normalized Arabic-safe text)
        self._fts_enabled = False
        try:
            fts_exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
            ).fetchone()
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    norm_content,
                    norm_summary,
                    content=memories,
                    content_rowid=id
                )
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, norm_content, norm_summary)
                    VALUES (new.id, new.norm_content, new.norm_summary);
                END;
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, norm_content, norm_summary)
                    VALUES('delete', old.id, old.norm_content, old.norm_summary);
                END;
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, norm_content, norm_summary)
                    VALUES('delete', old.id, old.norm_content, old.norm_summary);
                    INSERT INTO memories_fts(rowid, norm_content, norm_summary)
                    VALUES (new.id, new.norm_content, new.norm_summary);
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
                "INSERT INTO memories (scope, key, content, summary, norm_content, norm_summary, workspace, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scope.value,
                    key,
                    content,
                    summary,
                    _normalize_search_text(content),
                    _normalize_search_text(summary),
                    workspace,
                    session_id,
                ),
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
                    "UPDATE memories SET content = ?, summary = ?, norm_content = ?, norm_summary = ? WHERE id = ?",
                    (content, summary, _normalize_search_text(content), _normalize_search_text(summary), item_id),
                )
            else:
                self._conn.execute(
                    "UPDATE memories SET content = ?, norm_content = ? WHERE id = ?",
                    (content, _normalize_search_text(content), item_id),
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

        norm_q = _normalize_search_text(query)
        tokens = re.findall(r"\w+", norm_q)
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
        like_pattern = f"%{_normalize_search_text(query).strip()}%"
        sql = "SELECT * FROM memories WHERE (norm_content LIKE ? OR norm_summary LIKE ?)"
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
