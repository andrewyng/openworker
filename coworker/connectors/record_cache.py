"""Local record cache with keyword (FTS5) search, not tied to any connector.

Pulls an external dataset into a SQLite table once, then serves searches from
that copy instead of re-fetching the source each time. Rows are scoped by
source_id so multiple sources can share one cache file.

upsert() is keyed on (source_id, record_key) and idempotent. Falls back to a
LIKE scan if FTS5 isn't available.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_MAX_TEXT_CHARS = 20_000
_MAX_TITLE_CHARS = 500
_MAX_URL_CHARS = 2_000

_TOKEN_RE = re.compile(r"[\w][\w'-]*\*?", re.UNICODE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_sources (
    source_id    TEXT PRIMARY KEY,
    connector    TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    fetched_at   REAL,
    record_count INTEGER NOT NULL DEFAULT 0,
    data         TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    TEXT NOT NULL,
    record_key   TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    search_text  TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    data         TEXT NOT NULL,
    first_seen   REAL NOT NULL,
    fetched_at   REAL NOT NULL,
    UNIQUE(source_id, record_key)
);
CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_id, fetched_at DESC);
"""


@dataclass(frozen=True)
class FieldMap:
    """Which fields of an arbitrary JSON item become the cache's indexed columns.

    `key_field`/`title_field`/`url_field` are dotted paths ("meta.id", a numeric
    segment indexes into a list). `text_fields` is a tuple of dotted paths to index
    for search; an empty tuple means "flatten every string value in the item".
    """

    key_field: str = "url"
    title_field: str = "title"
    url_field: str = "url"
    text_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class CachedRecord:
    key: str
    title: str
    url: str
    data: dict[str, Any]
    rank: int  # 1-based position in this result set
    score: float  # higher is better; 0.0 in "like" mode (no numeric score available)
    snippet: str
    fetched_at: float


def extract(item: Any, path: str) -> Any:
    """Dotted-path lookup ("meta.id", "items.0.name" — a numeric segment indexes a
    list). Returns None on any miss instead of raising, since field-mapping config
    is user-supplied and a wrong path must degrade gracefully, not crash a refresh."""
    if not path:
        return None
    cur = item
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            return None
        if cur is None:
            return None
    return cur


def flatten_strings(value: Any) -> str:
    """Every string leaf in `value`, newline-joined. Dict KEYS are excluded — they
    are schema (field names), not content, and would pollute every query with the
    item's own shape. No length cap here; callers cap the result themselves."""
    parts: list[str] = []

    def _walk(v: Any) -> None:
        if isinstance(v, str):
            s = v.strip()
            if s:
                parts.append(s)
        elif isinstance(v, dict):
            for vv in v.values():
                _walk(vv)
        elif isinstance(v, (list, tuple)):
            for vv in v:
                _walk(vv)
        elif v is None or isinstance(v, bool):
            return
        elif isinstance(v, (int, float)):
            return  # numbers alone are rarely useful search terms; skip them
        else:
            parts.append(str(v))

    _walk(value)
    return "\n".join(parts)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _content_hash(item: Any, title: str, url: str, search_text: str) -> str:
    # Hashes the derived columns TOO, not just the raw item: if a user edits the
    # field-mapping config (e.g. widens text_fields), the same raw item must count
    # as "updated" so its derived columns aren't left stale behind an unchanged hash.
    h = hashlib.sha256()
    for part in (_canonical_json(item), title, url, search_text):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


def map_item(item: Any, fmap: FieldMap) -> dict[str, Any]:
    """Map one arbitrary JSON item to cache columns. Never raises, never drops data:
    a missing/blank key field falls back to a stable hash of the whole item, so a
    keyless dataset still caches (and re-runs stay idempotent) rather than losing
    records or crashing the refresh."""
    title = str(extract(item, fmap.title_field) or "")[:_MAX_TITLE_CHARS]
    url = str(extract(item, fmap.url_field) or "")[:_MAX_URL_CHARS]

    if fmap.text_fields:
        parts = [flatten_strings(extract(item, f)) for f in fmap.text_fields]
        full_text = "\n".join(p for p in parts if p)
    else:
        full_text = flatten_strings(item)
    truncated = len(full_text) > _MAX_TEXT_CHARS
    search_text = full_text[:_MAX_TEXT_CHARS]

    key_raw = extract(item, fmap.key_field)
    key = str(key_raw).strip() if key_raw not in (None, "") else ""
    if not key:
        key = "sha1:" + hashlib.sha1(_canonical_json(item).encode("utf-8")).hexdigest()[:16]

    return {
        "record_key": key,
        "title": title,
        "url": url,
        "search_text": search_text,
        "content_hash": _content_hash(item, title, url, search_text),
        "data": _canonical_json(item),
        "truncated": truncated,
    }


def _terms(query: str) -> list[str]:
    return _TOKEN_RE.findall(query)


def has_terms(query: str) -> bool:
    """Whether `query` contains anything a search could match on — callers use this
    to distinguish "no results" from "nothing to search for" before calling search()."""
    return bool(_terms(query))


def fts_query(raw: str) -> str:
    """Sanitize free text into safe FTS5 MATCH syntax. A query fully wrapped in
    double quotes becomes one quoted phrase; a token ending in '*' becomes a prefix
    term; otherwise tokens are quoted and AND-joined. Returns "" for no searchable
    terms. Without this, a query like "what's open?" is an FTS5 syntax error, not
    a search."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1].strip()
        return ('"' + inner.replace('"', '""') + '"') if inner else ""
    parts = []
    for tok in _terms(raw):
        if tok.endswith("*") and len(tok) > 1:
            parts.append('"' + tok[:-1].replace('"', '""') + '"*')
        else:
            parts.append('"' + tok.replace('"', '""') + '"')
    return " AND ".join(parts)


def _clean_term(term: str) -> str:
    """Strip the FTS5 prefix-match suffix for LIKE-mode use — a literal trailing
    '*' would never appear in cached text, so left uncleaned it would make the
    term impossible to match in the fallback path."""
    return term[:-1] if term.endswith("*") and len(term) > 1 else term


def _like_snippet(text: str, terms: list[str], *, window: int = 100) -> str:
    lower = text.lower()
    pos = -1
    for t in terms:
        idx = lower.find(t.lower())
        if idx != -1 and (pos == -1 or idx < pos):
            pos = idx
    if pos == -1:
        return text[:window].strip()
    start = max(0, pos - window // 2)
    end = min(len(text), pos + window // 2)
    return text[start:end].strip()


class RecordCache:
    """A local SQLite-backed cache of external records, keyword-searchable via
    FTS5 (falling back to LIKE when FTS5 isn't compiled in). `path` is never
    resolved internally — callers pass the full path (repo convention, see
    `coworker/automation/store.py`, `coworker/memory/sqlite_store.py`) so the
    store stays trivially testable against a tmp_path file."""

    def __init__(self, path: str | Path, *, allow_fts5: bool = True) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        self.search_mode = "like"
        if allow_fts5:
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5("
                    "title, search_text, tokenize='unicode61')"
                )
                self._conn.commit()
                self.search_mode = "fts5"
            except sqlite3.OperationalError:
                self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "RecordCache":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def upsert(
        self,
        source_id: str,
        connector: str,
        items: list[Any],
        *,
        fmap: FieldMap,
        label: str = "",
        fetched_at: Optional[float] = None,
    ) -> dict[str, Any]:
        """Idempotent upsert keyed on (source_id, record_key). One transaction, one
        commit: a mid-batch failure leaves the cache exactly as it was before the
        call, and it is far faster than committing per row."""
        ts = fetched_at if fetched_at is not None else time.time()

        seen: dict[str, dict[str, Any]] = {}
        for item in items:
            mapped = map_item(item, fmap)
            seen.setdefault(mapped["record_key"], mapped)  # first occurrence wins

        new = updated = unchanged = truncated = 0
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                for key, mapped in seen.items():
                    if mapped["truncated"]:
                        truncated += 1
                    row = self._conn.execute(
                        "SELECT id, content_hash FROM records "
                        "WHERE source_id = ? AND record_key = ?",
                        (source_id, key),
                    ).fetchone()
                    if row is None:
                        cur = self._conn.execute(
                            "INSERT INTO records (source_id, record_key, title, url, "
                            "search_text, content_hash, data, first_seen, fetched_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                source_id,
                                key,
                                mapped["title"],
                                mapped["url"],
                                mapped["search_text"],
                                mapped["content_hash"],
                                mapped["data"],
                                ts,
                                ts,
                            ),
                        )
                        if self.search_mode == "fts5":
                            self._conn.execute(
                                "INSERT INTO records_fts (rowid, title, search_text) "
                                "VALUES (?, ?, ?)",
                                (cur.lastrowid, mapped["title"], mapped["search_text"]),
                            )
                        new += 1
                    elif row["content_hash"] == mapped["content_hash"]:
                        self._conn.execute(
                            "UPDATE records SET fetched_at = ? WHERE id = ?",
                            (ts, row["id"]),
                        )
                        unchanged += 1
                    else:
                        self._conn.execute(
                            "UPDATE records SET title = ?, url = ?, search_text = ?, "
                            "content_hash = ?, data = ?, fetched_at = ? WHERE id = ?",
                            (
                                mapped["title"],
                                mapped["url"],
                                mapped["search_text"],
                                mapped["content_hash"],
                                mapped["data"],
                                ts,
                                row["id"],
                            ),
                        )
                        if self.search_mode == "fts5":
                            self._conn.execute(
                                "DELETE FROM records_fts WHERE rowid = ?", (row["id"],)
                            )
                            self._conn.execute(
                                "INSERT INTO records_fts (rowid, title, search_text) "
                                "VALUES (?, ?, ?)",
                                (row["id"], mapped["title"], mapped["search_text"]),
                            )
                        updated += 1

                total = self._conn.execute(
                    "SELECT count(*) FROM records WHERE source_id = ?", (source_id,)
                ).fetchone()[0]
                stats = {
                    "new": new,
                    "updated": updated,
                    "unchanged": unchanged,
                    "truncated": truncated,
                    "fetched": len(items),
                    "total": total,
                    "fetched_at": ts,
                }
                self._conn.execute(
                    "INSERT INTO cache_sources (source_id, connector, label, "
                    "fetched_at, record_count, data) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_id) DO UPDATE SET connector = excluded.connector, "
                    "label = excluded.label, fetched_at = excluded.fetched_at, "
                    "record_count = excluded.record_count, data = excluded.data",
                    (source_id, connector, label, ts, total, json.dumps(stats)),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return stats

    def search(
        self, source_id: str, query: str, *, limit: int = 10
    ) -> tuple[list[CachedRecord], str]:
        """Keyword search scoped to one source. Never touches the network — this
        only ever reads the local table. Returns (results, search_mode)."""
        terms = _terms(query)
        if not terms:
            return [], self.search_mode
        limit = max(1, int(limit))

        with self._lock:
            if self.search_mode == "fts5":
                fq = fts_query(query)
                if not fq:
                    return [], self.search_mode
                rows = self._conn.execute(
                    """
                    SELECT r.record_key, r.title, r.url, r.data, r.fetched_at,
                           bm25(records_fts, 5.0, 1.0) AS rnk,
                           snippet(records_fts, 1, '', '', '…', 20) AS snip
                      FROM records_fts
                      JOIN records r ON r.id = records_fts.rowid
                     WHERE records_fts MATCH ? AND r.source_id = ?
                     ORDER BY rnk
                     LIMIT ?
                    """,
                    (fq, source_id, limit),
                ).fetchall()
                results = [
                    CachedRecord(
                        key=row["record_key"],
                        title=row["title"],
                        url=row["url"],
                        data=json.loads(row["data"]),
                        rank=i,
                        score=-float(row["rnk"]),
                        snippet=row["snip"],
                        fetched_at=row["fetched_at"],
                    )
                    for i, row in enumerate(rows, start=1)
                ]
                return results, "fts5"

            stems = [_clean_term(t) for t in terms]
            patterns = [f"%{s.lower()}%" for s in stems]
            term_clauses = " AND ".join(
                "(lower(title) LIKE ? OR lower(search_text) LIKE ?)" for _ in patterns
            )
            sql = (
                "SELECT record_key, title, url, data, fetched_at, search_text, "
                "(CASE WHEN lower(title) LIKE ? THEN 0 ELSE 1 END) AS rnk "
                "FROM records WHERE source_id = ? AND " + term_clauses + " "
                "ORDER BY rnk ASC, length(search_text) ASC, record_key ASC LIMIT ?"
            )
            params: list[Any] = [patterns[0], source_id]
            for p in patterns:
                params.extend([p, p])
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            results = [
                CachedRecord(
                    key=row["record_key"],
                    title=row["title"],
                    url=row["url"],
                    data=json.loads(row["data"]),
                    rank=i,
                    score=0.0,
                    snippet=_like_snippet(row["search_text"], stems),
                    fetched_at=row["fetched_at"],
                )
                for i, row in enumerate(rows, start=1)
            ]
            return results, "like"

    def status(self, source_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cache_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            count = self._conn.execute(
                "SELECT count(*) FROM records WHERE source_id = ?", (source_id,)
            ).fetchone()[0]
        return {
            "records": count,
            "fetched_at": row["fetched_at"] if row else None,
            "label": row["label"] if row else "",
            "search_mode": self.search_mode,
            "last_refresh": json.loads(row["data"]) if row and row["data"] else {},
        }

    def clear_source(self, source_id: str) -> int:
        """Delete every row for one source. Not wired into any connector's
        disconnect flow yet — exposed so a caller can prune orphaned rows
        without a schema change."""
        with self._lock:
            ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT id FROM records WHERE source_id = ?", (source_id,)
                ).fetchall()
            ]
            if self.search_mode == "fts5":
                for rid in ids:
                    self._conn.execute(
                        "DELETE FROM records_fts WHERE rowid = ?", (rid,)
                    )
            cur = self._conn.execute(
                "DELETE FROM records WHERE source_id = ?", (source_id,)
            )
            self._conn.execute(
                "DELETE FROM cache_sources WHERE source_id = ?", (source_id,)
            )
            self._conn.commit()
        return cur.rowcount
