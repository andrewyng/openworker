"""The reverse channel: a small RPC framing over the box's dialed-out WebSocket.

This is a TRANSPORT ADAPTER, not a protocol of its own. Controller → box
frames carry an ordinary HTTP request shape (`method`, `path`, `body`); the
box dispatches them into its OWN ASGI app in-process and answers with the
response. Every existing /v1 endpoint therefore works remotely with zero
handler changes. Box → controller `event` frames are reserved for the live
session stream (P1c).

Frame vocabulary (JSON text frames):
  handshake   box → ctrl   {"type": "hello", "protocol_version", "app_version",
                            "pubkey", "name", "token"?}
              ctrl → box   {"type": "challenge", "nonce"}
              box → ctrl   {"type": "auth", "signature"}
              ctrl → box   {"type": "welcome", "machine_id", "name"}
                         | {"type": "reject", "reason", "detail"?}
  steady      ctrl → box   {"type": "rpc", "id", "method", "path",
                            "body_b64"?, "content_type"?}
              box → ctrl   {"type": "rpc_result", "id", "status",
                            "body_b64", "content_type"}
  wallet      ctrl → box   {"type": "secret_deploy", "id", "sealed_b64"}
  (sealed)    ctrl → box   {"type": "secret_revoke", "id", "profiles"}
              box → ctrl   rpc_result for both (status 200/400)
  websocket   ctrl → box   {"type": "ws_open", "stream", "path"}
  (bridged)   ctrl → box   {"type": "ws_msg", "stream", "text"}
              ctrl → box   {"type": "ws_close", "stream"}
              box → ctrl   {"type": "ws_msg", "stream", "text"}
              box → ctrl   {"type": "ws_close", "stream"}

PROTOCOL_VERSION is checked with STRICT EQUALITY in P1 (owner ruling
2026-08-24); the field existing in the very first version is the
un-retrofittable escape hatch that lets later versions negotiate a window.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Awaitable, Callable, Optional

# v2: hello carries seal_sig — the sealing key attested by the identity key.
PROTOCOL_VERSION = 2

# One in-flight RPC must never wedge the channel; the controller-side proxy
# turns a timeout into a 504 while the socket stays healthy.
RPC_TIMEOUT_SECONDS = 60.0


def app_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    # "coworker" = the distribution's name before it was published as "openworker".
    for dist in ("openworker", "coworker"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
        except Exception:
            break
    return "0.0.0"


def encode_body(body: bytes | None) -> str:
    return base64.b64encode(body or b"").decode("ascii")


def decode_body(body_b64: str | None) -> bytes:
    return base64.b64decode((body_b64 or "").encode("ascii"))


ACTOR_HEADER = "x-openworker-actor"


class RpcClient:
    """Controller side: pending-future bookkeeping over an arbitrary send callable.

    The owning WebSocket handler runs the receive loop and feeds every
    `rpc_result` frame to `resolve()`; `request()` is what the proxy route
    awaits. Sends are serialized — concurrent proxy calls share one socket.
    """

    def __init__(self, send_text: Callable[[str], Awaitable[None]]) -> None:
        self._send_text = send_text
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._counter = 0
        self._closed = False

    async def send_frame(self, frame: dict[str, Any]) -> None:
        """Send an arbitrary channel frame (ws_open/ws_msg/ws_close) on the
        same socket, serialized with in-flight RPC sends."""
        if self._closed:
            raise ConnectionError("machine channel is closed")
        async with self._send_lock:
            await self._send_text(json.dumps(frame))

    async def request_frame(
        self, frame: dict[str, Any], timeout: float = RPC_TIMEOUT_SECONDS
    ) -> tuple[int, bytes, str]:
        """Send any id-carrying frame and await its rpc_result (allocates the id)."""
        if self._closed:
            raise ConnectionError("machine channel is closed")
        self._counter += 1
        rpc_id = f"r{self._counter}"
        frame = {**frame, "id": rpc_id}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = fut
        try:
            async with self._send_lock:
                await self._send_text(json.dumps(frame))
            result = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rpc_id, None)
        return result

    async def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout: float = RPC_TIMEOUT_SECONDS,
        actor: str = "",
    ) -> tuple[int, bytes, str]:
        frame: dict[str, Any] = {
            "type": "rpc",
            "method": method.upper(),
            "path": path,
            **({"actor": actor} if actor else {}),
        }
        if body:
            frame["body_b64"] = encode_body(body)
            frame["content_type"] = content_type or "application/json"
        return await self.request_frame(frame, timeout=timeout)

    def resolve(self, frame: dict[str, Any]) -> None:
        fut = self._pending.get(str(frame.get("id")))
        if fut is not None and not fut.done():
            fut.set_result(
                (
                    int(frame.get("status", 502)),
                    decode_body(frame.get("body_b64")),
                    str(frame.get("content_type") or "application/json"),
                )
            )

    def close(self, reason: str = "machine disconnected") -> None:
        self._closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(reason))
        self._pending.clear()


class AsgiDispatcher:
    """Box side: dispatch an `rpc` frame into the box's own ASGI app in-process."""

    def __init__(self, app: Any) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://joined-box",
            timeout=RPC_TIMEOUT_SECONDS,
        )

    async def dispatch(self, frame: dict[str, Any]) -> dict[str, Any]:
        rpc_id = str(frame.get("id"))
        try:
            body = decode_body(frame.get("body_b64"))
            headers = {}
            if body:
                headers["content-type"] = str(
                    frame.get("content_type") or "application/json"
                )
            # The controller-verified actor becomes a header only HERE — the box
            # has no listener, so nothing else can present one.
            if frame.get("actor"):
                headers[ACTOR_HEADER] = str(frame["actor"])
            response = await self._client.request(
                str(frame.get("method", "GET")),
                str(frame.get("path", "/")),
                content=body or None,
                headers=headers,
            )
            return {
                "type": "rpc_result",
                "id": rpc_id,
                "status": response.status_code,
                "body_b64": encode_body(response.content),
                "content_type": response.headers.get(
                    "content-type", "application/json"
                ),
            }
        except Exception as exc:  # a broken dispatch must answer, not wedge the channel
            return {
                "type": "rpc_result",
                "id": rpc_id,
                "status": 502,
                "body_b64": encode_body(
                    json.dumps({"error": f"dispatch failed: {exc}"}).encode()
                ),
                "content_type": "application/json",
            }

    async def aclose(self) -> None:
        await self._client.aclose()


class AsgiWsBridge:
    """Box side: one bridged WebSocket stream into the box's own ASGI app.

    The controller's GUI-facing socket is spliced to this: `ws_msg` frames feed
    the app as `websocket.receive` events, and everything the app sends comes
    back as `ws_msg` frames. The session WebSocket protocol is JSON text both
    ways, so only text frames are bridged.
    """

    def __init__(
        self,
        app: Any,
        stream_id: str,
        path: str,
        send_frame: Callable[[dict[str, Any]], Awaitable[None]],
        actor: str = "",
    ) -> None:
        self._app = app
        self.stream_id = stream_id
        self._path = path
        self._send_frame = send_frame
        self._actor = actor
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._closed = False

    async def run(self) -> None:
        path, _, query = self._path.partition("?")
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": (
                [(ACTOR_HEADER.encode(), self._actor.encode())] if self._actor else []
            ),
            "client": ("channel", 0),
            "server": ("joined-box", 0),
            "subprotocols": [],
        }
        await self._incoming.put({"type": "websocket.connect"})
        try:
            await self._app(scope, self._incoming.get, self._send)
        except Exception:
            pass  # app-side failure closes the stream below, never the channel
        finally:
            await self._close_out()

    def feed_text(self, text: str) -> None:
        self._incoming.put_nowait({"type": "websocket.receive", "text": text})

    def feed_close(self) -> None:
        self._closed = True  # controller-initiated: no ws_close echo needed
        self._incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})

    async def _send(self, message: dict[str, Any]) -> None:
        kind = message["type"]
        if kind == "websocket.send":
            text = message.get("text")
            if text is None and message.get("bytes") is not None:
                text = bytes(message["bytes"]).decode("utf-8", "replace")
            if text is not None and not self._closed:
                await self._send_frame(
                    {"type": "ws_msg", "stream": self.stream_id, "text": text}
                )
        elif kind == "websocket.close":
            await self._close_out()

    async def _close_out(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._send_frame({"type": "ws_close", "stream": self.stream_id})
        except Exception:
            pass


def reject_frame(reason: str, detail: Optional[str] = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "reject", "reason": reason}
    if detail:
        frame["detail"] = detail
    return frame
