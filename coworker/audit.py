"""Durable local audit log for connector/tool actions."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from .connectors import connector_for_tool

# Matched as substrings of a lowercased key name, at EVERY level of a structure (#397):
# an HTTP-shaped or MCP tool takes its credential in a nested `headers` / `auth` / `config`
# object, and `_truncate` keeps the first 500 characters, so an unredacted bearer token
# lands in the log whole.
_SECRET_KEYS = (
    "token",
    "secret",
    "password",
    "api_key",
    "access_token",
    "bot_token",
    "app_token",
    "authorization",
    "cookie",
    "credential",
    "private_key",
    "raw",
)
_BODY_KEYS = ("body", "content", "html")
# A tool RESULT carries the same content the argument policy redacts, under other names:
# a shell command's stdout, an email body, one message's text (#525). The audit row is for
# triage — who ran what, against which resource — never for replaying the content.
_RESULT_BODY_KEYS = _BODY_KEYS + ("output", "stdout", "stderr", "text", "snippet")
# The engine's own preview length, so a rebuilt preview is the same size as the one it
# replaces.
_PREVIEW_LIMIT = 300
# How deep the walk goes before it stops describing a structure. Past this the keys are no
# longer being checked, so the value is dropped rather than copied through.
_MAX_DEPTH = 6


class AuditStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                agent TEXT,
                workspace TEXT,
                connector TEXT,
                tool TEXT,
                stage TEXT,
                status TEXT,
                approval TEXT,
                args TEXT,
                result_preview TEXT,
                reason TEXT,
                resource TEXT,
                call_id TEXT,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cache_read INTEGER DEFAULT 0,
                cache_write INTEGER DEFAULT 0
            )
            """)
        # Existing databases predate the reviewer columns (2026-08-12): call_id joins a
        # shadow verdict to the human's decision on the same tool call, tokens_in/out are
        # the reviewer metering (§1.7). ALTER is idempotent-by-error: "duplicate column"
        # means an already-migrated file.
        for column, decl in (
            ("call_id", "TEXT"),
            ("tokens_in", "INTEGER DEFAULT 0"),
            ("tokens_out", "INTEGER DEFAULT 0"),
            # Cached-prefix share of a reviewer check (2026-08-22). Without these the
            # metering badge could only ever see the FRESH tokens — ~75 of a ~1,500-token
            # check once the provider caches the instruction prefix — so it under-reported
            # cost by more the longer a session ran. Same defect class as OPE-101, one
            # layer further out.
            ("cache_read", "INTEGER DEFAULT 0"),
            ("cache_write", "INTEGER DEFAULT 0"),
        ):
            try:
                self._conn.execute(
                    f"ALTER TABLE audit_events ADD COLUMN {column} {decl}"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    def append(self, event: dict[str, Any]) -> None:
        tool = str(event.get("tool") or event.get("tool_name") or "")
        connector = str(event.get("connector") or connector_for_tool(tool) or "")
        args = _sanitize_args(tool, event.get("arguments") or {})
        resource = _resource(
            tool, event.get("arguments") or {}, event.get("result") or {}
        )
        preview = _result_preview(tool, event)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO audit_events
                    (session_id, agent, workspace, connector, tool, stage, status, approval, args, result_preview, reason, resource, call_id, tokens_in, tokens_out, cache_read, cache_write)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("session_id") or "",
                    event.get("agent") or "",
                    event.get("workspace") or "",
                    connector,
                    tool,
                    event.get("stage") or "",
                    event.get("status") or "",
                    event.get("approval") or "",
                    json.dumps(args, default=str),
                    preview,
                    _truncate(str(event.get("reason") or "")),
                    _truncate(str(resource or "")),
                    str(event.get("call_id") or ""),
                    int(event.get("tokens_in") or 0),
                    int(event.get("tokens_out") or 0),
                    int(event.get("cache_read") or 0),
                    int(event.get("cache_write") or 0),
                ),
            )
            self._conn.commit()

    def reviewer_stats(self, session_id: str) -> dict[str, Any]:
        """Per-session Auto-Approve metering (§1.7), computed from the durable rows so it
        survives restarts and engine rebuilds. `live` counts stage=reviewer_verdict (the
        mode actually deciding); `shadow` counts stage=reviewer_shadow (recording only)."""

        def _bucket(stage: str) -> dict[str, int]:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT status, COUNT(*) AS n,
                           COALESCE(SUM(tokens_in), 0) AS tin,
                           COALESCE(SUM(tokens_out), 0) AS tout,
                           COALESCE(SUM(cache_read), 0) AS cread,
                           COALESCE(SUM(cache_write), 0) AS cwrite
                    FROM audit_events
                    WHERE session_id = ? AND stage = ?
                    GROUP BY status
                    """,
                    (session_id, stage),
                ).fetchall()
            out = {
                "checks": 0, "allow": 0, "deny": 0, "unsure": 0,
                "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0,
            }
            for row in rows:
                status = str(row["status"])
                if status in ("allow", "deny", "unsure"):
                    out[status] += int(row["n"])
                out["checks"] += int(row["n"])
                out["tokens_in"] += int(row["tin"])
                out["tokens_out"] += int(row["tout"])
                out["cache_read"] += int(row["cread"])
                out["cache_write"] += int(row["cwrite"])
            return out

        return {"live": _bucket("reviewer_verdict"), "shadow": _bucket("reviewer_shadow")}

    def list(
        self,
        *,
        limit: int = 100,
        session_id: Optional[str] = None,
        connector: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        if connector:
            where.append("connector = ?")
            params.append(connector)
        if tool:
            where.append("tool = ?")
            params.append(tool)
        sql = "SELECT * FROM audit_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 500)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["args"] = json.loads(item.get("args") or "{}")
            except json.JSONDecodeError:
                item["args"] = {}
            out.append(item)
        return out

    def close(self) -> None:
        self._conn.close()


def _sanitize_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments, with every secret-like and body-like value replaced by a marker."""
    if not isinstance(args, dict):
        return {}
    return _sanitize_mapping(tool, args, _BODY_KEYS, 0)


def _sanitize_result(tool: str, result: Any) -> Any:
    """A tool result under the same policy, plus the result-side content keys."""
    return _sanitize_value(tool, None, result, _RESULT_BODY_KEYS, 0)


def _redaction_marker(
    tool: str, lower_key: str, body_keys: tuple[str, ...]
) -> Optional[str]:
    """The marker this key's value must be replaced by, or None to keep the value."""
    if any(s in lower_key for s in _SECRET_KEYS):
        return "[redacted]"
    if tool == "browser_type" and lower_key == "text":
        return "[redacted input]"
    if any(b == lower_key or lower_key.endswith("_" + b) for b in body_keys):
        return "[redacted body]"
    return None


def _sanitize_mapping(
    tool: str, mapping: dict[Any, Any], body_keys: tuple[str, ...], depth: int
) -> dict[str, Any]:
    return {
        str(key): _sanitize_value(tool, key, value, body_keys, depth)
        for key, value in list(mapping.items())[:20]
    }


def _sanitize_value(
    tool: str, key: Any, value: Any, body_keys: tuple[str, ...], depth: int
) -> Any:
    if key is not None:
        marker = _redaction_marker(tool, str(key).lower(), body_keys)
        if marker is not None:
            return marker
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth >= _MAX_DEPTH:
        # Stringifying it here would copy through the very keys we stopped checking.
        return "[nested]"
    if isinstance(value, list):
        # An item carries no key of its own; a dict item is checked when we recurse
        # into it, and a list under a secret-like key never reaches here at all.
        return [
            _sanitize_value(tool, None, v, body_keys, depth + 1) for v in value[:10]
        ]
    if isinstance(value, dict):
        return _sanitize_mapping(tool, value, body_keys, depth + 1)
    return _truncate(str(value))


def _result_preview(tool: str, event: dict[str, Any]) -> str:
    """The stored preview of a tool result, redacted at the structured stage.

    The caller's `result_preview` is already flattened to a string, so nothing can be
    redacted in it by key any more — when the raw `result` rides along, the preview is
    rebuilt from the sanitized structure instead (#525).
    """
    result = event.get("result")
    if result is None:
        return _truncate(str(event.get("result_preview") or ""))
    sanitized = _sanitize_result(tool, result)
    text = sanitized if isinstance(sanitized, str) else json.dumps(sanitized, default=str)
    return _truncate(text, limit=_PREVIEW_LIMIT)


def _resource(tool: str, args: dict[str, Any], result: Any) -> str:
    for key in (
        "url",
        "owner",
        "repo",
        "issue_key",
        "page_id",
        "ticket_id",
        "calendar_id",
        "message_id",
    ):
        if isinstance(args, dict) and args.get(key):
            return str(args[key])
    if isinstance(args, dict) and args.get("subdomain"):
        return f"{args['subdomain']}.zendesk.com"
    if isinstance(result, dict) and result.get("url"):
        return str(result["url"])
    return ""


def _truncate(text: str, limit: int = 500) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."
