"""Audit export: the box ships its audit EVENTS to a sink, only when policy says so.

Owner ruling 2026-09-02 (remote-home-design.md §Audit export): OFF by default —
a personal or self-hosted machine sends nothing anywhere. Central governance
turns it on through the org policy the controller hands the box (welcome frame
+ `policy` pushes); `audit_export` is that policy's field. What leaves the box is
the audit ROW, not the conversation: tool name, connector, redacted argument
preview, approval decision, token counts, session/agent ids, resource
references. Never result bodies, never the model's reasons, never transcripts —
the cloud stays content-blind.

Two sinks, one emitter:
- `cloud`: batches ride the existing machine channel as `audit` frames; the
  controller answers `audit_ack`. No new auth — the socket authenticated at
  the handshake. Our hosted store powers the Admin SPA from these.
- `http`: batches POST to a URL (a customer's SIEM/HEC) with optional headers.

At-least-once: the cursor (last exported row id) advances only on ack and is
persisted beside the state, so a restart resumes where it left off; a sink
that refuses backs the emitter off rather than dropping rows.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

_OCSF_VERSION = "1.1.0"
_CLASS_API_ACTIVITY = 6003  # OCSF Application Activity › API Activity
_CATEGORY_APPLICATION = 6
_ACTIVITY_OTHER = 99

POLICY_FILE = "policy.json"
CURSOR_FILE = "audit-export.json"

# Columns of the local audit row that may NOT leave the box (content).
_CONTENT_COLUMNS = ("result_preview", "reason")


# -- policy ---------------------------------------------------------------------


@dataclass
class ExportPolicy:
    enabled: bool = False
    sink: str = "cloud"  # "cloud" | "http"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    batch: int = 200
    flush_seconds: float = 5.0

    @classmethod
    def from_policy(cls, policy: Optional[dict[str, Any]]) -> "ExportPolicy":
        """`policy` is the whole org policy record; this reads its `audit_export`
        field. Anything malformed reads as OFF — governance never turns on by
        accident."""
        raw = (policy or {}).get("audit_export") if isinstance(policy, dict) else None
        if not isinstance(raw, dict) or not raw.get("enabled"):
            return cls()
        sink = str(raw.get("sink") or "cloud").lower()
        if sink not in ("cloud", "http"):
            return cls()
        url = str(raw.get("url") or "")
        if sink == "http" and not url.startswith(("https://", "http://")):
            return cls()
        headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
        try:
            batch = max(1, min(int(raw.get("batch") or 200), 1000))
            flush = max(1.0, min(float(raw.get("flush_seconds") or 5.0), 300.0))
        except (TypeError, ValueError):
            batch, flush = 200, 5.0
        return cls(
            enabled=True,
            sink=sink,
            url=url,
            headers={str(k): str(v) for k, v in headers.items()},
            batch=batch,
            flush_seconds=flush,
        )


def load_policy(state: Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(state) / POLICY_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_policy(state: Path, policy: dict[str, Any]) -> None:
    """Persist the org policy the controller handed us, so the machine can SHOW
    what governs it even while offline (visible-governance rule)."""
    path = Path(state) / POLICY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**policy, "received_at": time.time()}, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass


# -- OCSF -----------------------------------------------------------------------


def to_ocsf(
    row: dict[str, Any], *, machine_id: str = "", machine_name: str = "", org_id: str = ""
) -> dict[str, Any]:
    """One local audit row → one OCSF API Activity event. Content columns are
    dropped here, structurally, not by a filter someone could forget."""
    status = str(row.get("status") or "")
    status_id = 1 if status in ("ok", "ran", "success", "allow") else 2 if status in (
        "error", "denied", "deny", "failed", "blocked"
    ) else 99
    tokens = {
        k: int(row.get(k) or 0)
        for k in ("tokens_in", "tokens_out", "cache_read", "cache_write")
    }
    try:
        ts = row.get("timestamp") or ""
        # SQLite CURRENT_TIMESTAMP is UTC "YYYY-MM-DD HH:MM:SS".
        from datetime import datetime, timezone

        t_ms = int(
            datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            * 1000
        )
    except (TypeError, ValueError):
        t_ms = int(time.time() * 1000)
    workspace = str(row.get("workspace") or "")
    event: dict[str, Any] = {
        "class_uid": _CLASS_API_ACTIVITY,
        "category_uid": _CATEGORY_APPLICATION,
        "activity_id": _ACTIVITY_OTHER,
        "activity_name": str(row.get("stage") or "tool_call"),
        "type_uid": _CLASS_API_ACTIVITY * 100 + _ACTIVITY_OTHER,
        "time": t_ms,
        "severity_id": 1,
        "status_id": status_id,
        "status": status,
        "metadata": {
            "version": _OCSF_VERSION,
            "product": {"name": "OpenWorker", "vendor_name": "OpenWorker"},
            "uid": f"{machine_id}:{row.get('id')}",
        },
        "actor": {
            # The PERSON behind the session (controller-verified login); the coworker
            # persona rides `unmapped.agent`. Empty user = automated (event-spawned).
            "user": {
                "name": str(row.get("actor") or ""),
                **({"email_addr": str(row["actor"])} if "@" in str(row.get("actor") or "") else {}),
                **({"org": {"uid": org_id}} if org_id else {}),
            },
            "session": {"uid": str(row.get("session_id") or "")},
        },
        "api": {
            "operation": str(row.get("tool") or ""),
            "service": {"name": str(row.get("connector") or "")},
        },
        "device": {"uid": machine_id, "name": machine_name},
        "unmapped": {
            "row_id": row.get("id"),
            "agent": str(row.get("agent") or ""),
            "automated": not str(row.get("actor") or ""),
            "approval": str(row.get("approval") or ""),
            "approved_by": str(row.get("approved_by") or ""),
            # Already secret-stripped by the local store; still a PREVIEW, not content.
            "args": row.get("args") if isinstance(row.get("args"), dict) else {},
            "resource": str(row.get("resource") or ""),
            "call_id": str(row.get("call_id") or ""),
            # Basename only: a path is a name, its contents are not ours to ship.
            "workspace": workspace.rstrip("/").rsplit("/", 1)[-1] if workspace else "",
            **tokens,
        },
    }
    for col in _CONTENT_COLUMNS:
        assert col not in event["unmapped"]
    return event


# -- the emitter ---------------------------------------------------------------


SendBatch = Callable[[list[dict[str, Any]]], Awaitable[bool]]


class AuditExporter:
    """Watches the local audit store and ships new rows to the policy's sink."""

    def __init__(
        self,
        state: Path,
        store,
        *,
        machine_id: str = "",
        machine_name: str = "",
        org_id: str = "",
        policy: Optional[ExportPolicy] = None,
        backoff_start: float = 5.0,
        backoff_cap: float = 120.0,
    ) -> None:
        self.state = Path(state)
        self.store = store
        self.machine_id = machine_id
        self.machine_name = machine_name
        self.org_id = org_id
        self.policy = policy or ExportPolicy()
        self._backoff_start = backoff_start
        self._backoff_cap = backoff_cap
        self._wake = asyncio.Event()
        self._cursor = self._load_cursor()
        self.exported = 0
        self.last_error = ""
        self.last_sent_at: float = 0.0
        self._pending_acks: dict[str, asyncio.Future] = {}
        self._counter = 0
        # Woken by every local append, so a tool call ships within one flush window.
        subscribe = getattr(store, "subscribe", None)
        if subscribe is not None:
            subscribe(lambda _row: self._wake.set())

    # -- state ------------------------------------------------------------------
    def _cursor_path(self) -> Path:
        return self.state / CURSOR_FILE

    def _load_cursor(self) -> int:
        try:
            return int(json.loads(self._cursor_path().read_text()).get("last_id") or 0)
        except (OSError, ValueError, AttributeError):
            return 0

    def _save_cursor(self) -> None:
        try:
            self._cursor_path().parent.mkdir(parents=True, exist_ok=True)
            self._cursor_path().write_text(
                json.dumps({"last_id": self._cursor, "sink": self.policy.sink, "at": time.time()})
            )
        except OSError:
            pass

    def set_policy(self, policy: ExportPolicy) -> None:
        self.policy = policy
        self._wake.set()

    def pending(self) -> int:
        rows = self.store.list_since(self._cursor, limit=1)
        if not rows:
            return 0
        # Cheap estimate: the store's newest id minus our cursor.
        newest = self.store.list(limit=1)
        return max(0, int(newest[0]["id"]) - self._cursor) if newest else 0

    def status(self) -> dict[str, Any]:
        """What the machine SHOWS about its governance (visible-governance rule)."""
        return {
            "enabled": self.policy.enabled,
            "sink": self.policy.sink if self.policy.enabled else "",
            "url": self.policy.url if self.policy.sink == "http" else "",
            "exported": self.exported,
            "last_id": self._cursor,
            "pending": self.pending() if self.policy.enabled else 0,
            "last_error": self.last_error,
            "last_sent_at": self.last_sent_at,
        }

    # -- cloud sink: the channel ------------------------------------------------
    def ack(self, frame: dict[str, Any]) -> None:
        """Controller's `audit_ack` for one of our `audit` frames."""
        fut = self._pending_acks.pop(str(frame.get("id")), None)
        if fut is not None and not fut.done():
            fut.set_result(bool(frame.get("accepted")))

    def channel_sender(self, send_frame: Callable[[dict[str, Any]], Awaitable[None]]) -> SendBatch:
        async def send(events: list[dict[str, Any]]) -> bool:
            self._counter += 1
            frame_id = f"a{self._counter}"
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending_acks[frame_id] = fut
            try:
                await send_frame({"type": "audit", "id": frame_id, "events": events})
                return await asyncio.wait_for(fut, timeout=30.0)
            except asyncio.TimeoutError:
                return False
            finally:
                self._pending_acks.pop(frame_id, None)

        return send

    # -- http sink -----------------------------------------------------------------
    def http_sender(self) -> SendBatch:
        async def send(events: list[dict[str, Any]]) -> bool:
            import httpx

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(
                        self.policy.url,
                        json={"events": events},
                        headers={"content-type": "application/json", **self.policy.headers},
                    )
                return 200 <= resp.status_code < 300
            except httpx.HTTPError:
                return False

        return send

    # -- the loop -------------------------------------------------------------------
    async def flush_once(self, send: SendBatch) -> int:
        """Ship everything past the cursor in batches; returns rows exported.
        Stops at the first refused batch (the cursor never skips a row)."""
        total = 0
        while True:
            rows = self.store.list_since(self._cursor, limit=self.policy.batch)
            if not rows:
                return total
            events = [
                to_ocsf(
                    r,
                    machine_id=self.machine_id,
                    machine_name=self.machine_name,
                    org_id=self.org_id,
                )
                for r in rows
            ]
            ok = await send(events)
            if not ok:
                self.last_error = f"sink refused {len(events)} events"
                raise ConnectionError(self.last_error)
            self._cursor = int(rows[-1]["id"])
            self._save_cursor()
            self.exported += len(rows)
            self.last_sent_at = time.time()
            self.last_error = ""
            total += len(rows)

    async def run(self, send: Optional[SendBatch] = None) -> None:
        """Serve until cancelled. `send` overrides the sink (tests, or the channel
        sender for one connection's lifetime). With the policy off this idles."""
        backoff = self._backoff_start
        while True:
            self._wake.clear()
            if not self.policy.enabled:
                await self._wake.wait()
                continue
            sender = send or (self.http_sender() if self.policy.sink == "http" else None)
            if sender is None:
                # cloud sink without a channel (offline): wait for one.
                await self._wake.wait()
                continue
            try:
                await self.flush_once(sender)
                backoff = self._backoff_start
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.policy.flush_seconds)
                except asyncio.TimeoutError:
                    pass
            except ConnectionError:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_cap)
