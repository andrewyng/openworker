"""Websockets that cannot hold the server up (T3-3).

The server hung at "Shutting down" after Ctrl+C when a browser tab had stopped reading its
websocket (a frozen or discarded tab, or a laptop that slept) while the server kept pushing live
events. The unread data filled the connection's send buffer, so its close frame could never go
out and the connection never closed. uvicorn waits for every connection to close before it stops,
and since Python 3.12 `Server.wait_closed()` also waits for every transport, so even a second
Ctrl+C could not get past it.

Two guards, both by aborting the connection's transport (an abort drops what is still buffered;
a close waits to send it):

- `WebsocketGuard` (ASGI middleware): on the GUI's own websockets, a send that cannot finish
  within `send_timeout` seconds means the client stopped reading, and the connection is
  aborted. The machine links (`/ws/machines/…`) are left alone: they carry large frames over
  slow networks.
- `GuardedServer` (uvicorn): at shutdown, every websocket still open is aborted before uvicorn
  waits for its connections.
"""

from __future__ import annotations

import asyncio
import logging
import os
import weakref
from typing import Any, Awaitable, Callable, Optional

import uvicorn
from starlette.websockets import WebSocketDisconnect

log = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 15.0
# The GUI's own websockets: the session stream and the app-wide event stream.
GUARDED_PATHS = ("/ws/session/", "/ws/events")

_open: "weakref.WeakSet[Any]" = weakref.WeakSet()


def _send_timeout() -> float:
    try:
        value = float(os.environ.get("COWORKER_WS_SEND_TIMEOUT", "") or SEND_TIMEOUT_SECONDS)
    except ValueError:
        return SEND_TIMEOUT_SECONDS
    return value if value > 0 else SEND_TIMEOUT_SECONDS


def _transport_of(send: Callable[..., Any]) -> Optional[asyncio.BaseTransport]:
    """The connection's transport, from uvicorn's websocket protocol (its `send` is a bound
    method of the protocol, for both the websockets and the wsproto implementation). None under
    a test client or another server."""
    owner = getattr(send, "__self__", None)
    transport = getattr(owner, "transport", None)
    return transport if isinstance(transport, asyncio.BaseTransport) else None


def _abort(transport: Optional[asyncio.BaseTransport]) -> None:
    if transport is None:
        return
    try:
        transport.abort()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - already gone
        pass


def open_count() -> int:
    """How many websocket transports are open now (for tests and logs)."""
    return sum(1 for t in list(_open) if not t.is_closing())


def abort_open_websockets() -> int:
    """Abort every websocket still open. Returns how many were aborted."""
    n = 0
    for transport in list(_open):
        if not transport.is_closing():
            n += 1
        _abort(transport)
    _open.clear()
    return n


class WebsocketGuard:
    """ASGI middleware: track every websocket's transport, and abort a GUI websocket whose
    client stopped reading. Plain HTTP passes straight through."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, send_timeout: Optional[float] = None) -> None:
        self.app = app
        self.send_timeout = send_timeout

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "websocket":
            await self.app(scope, receive, send)
            return
        transport = _transport_of(send)
        if transport is not None:
            _open.add(transport)
        path = str(scope.get("path") or "")
        timeout = self.send_timeout if self.send_timeout is not None else _send_timeout()

        async def guarded_send(message: dict[str, Any]) -> None:
            try:
                await asyncio.wait_for(send(message), timeout)
            except asyncio.TimeoutError:
                log.warning("websocket %s: the client stopped reading for %.0f s; dropping it", path, timeout)
                _abort(transport)
                # what a handler already expects when its client goes away
                raise WebSocketDisconnect(code=1011, reason="client stopped reading")

        try:
            # only the GUI's own websockets get the send timeout; every one is tracked
            await self.app(scope, receive, guarded_send if path.startswith(GUARDED_PATHS) else send)
        finally:
            if transport is not None:
                _open.discard(transport)


class GuardedServer(uvicorn.Server):
    """uvicorn's server, but at shutdown the websockets still open are aborted first, so it
    never waits on a client that stopped reading."""

    async def shutdown(self, sockets: Any = None) -> None:
        n = abort_open_websockets()
        if n:
            log.info("aborted %d open websocket(s) before shutting down", n)
        await super().shutdown(sockets=sockets)
