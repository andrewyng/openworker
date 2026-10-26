"""Machine event polling — the box's inbound transport for managed Slack.

A machine-held connection's events queue at the broker, sealed to this
machine's pinned key (machines spec §Managed events). This transport drains
that queue and hands the decoded frames to the SAME RelayHub / adapter
pipeline the desktop's WebSocket transport feeds — downstream code cannot
tell which transport delivered a message.

Auth is the machine credential minted at delegation (bearer), plus the
opaque user/connection ids stamped into the profile. The broker answers a
uniform 401 for anything revoked or wrong; polling then backs off and keeps
trying quietly — a re-handoff (fresh credential) arrives as a profile
rewrite and a gateway refresh, which rebuilds this transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable, Optional

logger = logging.getLogger("coworker.connectors")

_UNAUTHORIZED_BACKOFF = 60.0  # revoked/rotated credential: stay quiet
_ERROR_BACKOFF = 15.0  # broker unreachable / 5xx: transient, retry soon


class MachinePollTransport:
    """RelayTransport over short-polling (open/recv/close, frame = dict).

    `recv()` never signals a dropped connection (there is none to drop) — it
    polls forever until `close()`, so the RelayHub's reconnect machinery
    stays idle. Errors are handled here with backoff.
    """

    def __init__(
        self,
        base_url: str,
        *,
        connection_id: str,
        user_id: str,
        credential: str,
        unseal: Callable[[str], bytes],
        interval: float = 3.0,
        cursor_path: Optional[Any] = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/machine/events/poll"
        self._connection_id = connection_id
        self._user_id = user_id
        self._credential = credential
        self._unseal = unseal
        self._interval = interval
        # The cursor persists (spec §10 drill finding 2026-09-04): the broker
        # keeps ~15 min of sealed events, so a restart with a fresh cursor
        # replayed a PR-opened event into a duplicate session.
        self._cursor_path = cursor_path
        self._cursor = self._load_cursor()
        self._buffer: deque[dict] = deque()
        self._closed = asyncio.Event()
        self._client = None
        self._polls = 0  # observable for tests

    async def open(self) -> None:
        import httpx

        self._client = httpx.AsyncClient(timeout=10)

    async def _sleep(self, seconds: float) -> None:
        """Interruptible sleep: close() ends it immediately."""
        try:
            await asyncio.wait_for(self._closed.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _poll_once(self) -> Optional[list[dict[str, Any]]]:
        """One poll round-trip: the sealed events, or None on any refusal
        (already logged and backed off by the caller's returned delay)."""
        import httpx

        try:
            resp = await self._client.post(
                self._url,
                json={
                    "connection_id": self._connection_id,
                    "user_id": self._user_id,
                    "cursor": self._cursor,
                },
                headers={"Authorization": f"Bearer {self._credential}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("machine event poll unreachable: %s", type(exc).__name__)
            await self._sleep(_ERROR_BACKOFF)
            return None
        if resp.status_code == 401:
            # Revoked or rotated credential — a re-handoff replaces this
            # transport; until then, stay quiet rather than hammering.
            logger.warning("machine event poll unauthorized — delegation revoked?")
            await self._sleep(_UNAUTHORIZED_BACKOFF)
            return None
        if resp.status_code != 200:
            await self._sleep(_ERROR_BACKOFF)
            return None
        body = resp.json()
        new_cursor = str(body.get("cursor") or self._cursor)
        if new_cursor != self._cursor:
            self._cursor = new_cursor
            self._save_cursor()
        return list(body.get("events") or [])

    def _load_cursor(self) -> str:
        if not self._cursor_path:
            return ""
        try:
            from pathlib import Path

            return Path(self._cursor_path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _save_cursor(self) -> None:
        if not self._cursor_path:
            return
        try:
            from pathlib import Path

            path = Path(self._cursor_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._cursor, encoding="utf-8")
        except OSError:
            logger.warning("could not persist the poll cursor at %s", self._cursor_path)

    async def recv(self) -> Optional[dict]:
        while not self._closed.is_set():
            if self._buffer:
                return self._buffer.popleft()
            if self._polls:
                await self._sleep(self._interval)
                if self._closed.is_set():
                    break
            self._polls += 1
            events = await self._poll_once()
            for item in events or []:
                try:
                    frame = json.loads(self._unseal(str(item.get("sealed_b64", ""))))
                # Broad on purpose: unseal is an opaque callable whose crypto
                # errors (InvalidTag etc.) we must not let kill the loop.
                except Exception as exc:
                    logger.warning("machine event unseal failed: %s", type(exc).__name__)
                    continue
                if isinstance(frame, dict):
                    self._buffer.append(frame)
        return None

    async def close(self) -> None:
        self._closed.set()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
