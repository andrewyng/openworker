"""Authenticated, fail-closed loopback proxy for Browser Use.

The proxy supports ordinary HTTP proxy requests plus HTTPS/WSS CONNECT tunnels.
Every connection is resolved and pinned through ``DestinationPolicy``.  It has no
direct-network fallback; stopping it makes the browser's configured proxy endpoint
unreachable.

Runtime launch contract:

* pass ``endpoint.proxy_url`` plus ``endpoint.username`` / ``endpoint.token`` as the
  Chromium proxy credentials;
* disable Chromium's loopback proxy bypass, QUIC, and non-proxied WebRTC UDP;
* block service workers for the MVP;
* do not expose the token to the model, transcript, audit log, or command line.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from .destination import (
    DestinationDecision,
    DestinationPolicy,
    DestinationPolicyError,
)


_MAX_HEADER_BYTES = 64 * 1024
_MAX_REQUEST_BODY = 16 * 1024 * 1024
_IO_TIMEOUT = 30.0
_ALLOWED_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT"}
)


@dataclass(frozen=True)
class ProxyEndpoint:
    proxy_url: str
    username: str
    token: str


class FailClosedLoopbackProxy:
    """Small HTTP/1.1 proxy whose outbound socket always uses a pinned IP."""

    def __init__(
        self,
        destination_policy: DestinationPolicy,
        *,
        host: str = "127.0.0.1",
        token: Optional[str] = None,
    ) -> None:
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("Browser proxy must bind to an explicit loopback address")
        self._policy = destination_policy
        self._host = host
        self._token = token or secrets.token_urlsafe(32)
        if len(self._token) < 32:
            raise ValueError("Browser proxy token is too short")
        self._username = "openworker"
        self._server: Optional[asyncio.AbstractServer] = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopping = False

    @property
    def endpoint(self) -> ProxyEndpoint:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("Browser proxy has not started")
        port = int(self._server.sockets[0].getsockname()[1])
        display_host = f"[{self._host}]" if ":" in self._host else self._host
        return ProxyEndpoint(
            proxy_url=f"http://{display_host}:{port}",
            username=self._username,
            token=self._token,
        )

    async def start(self) -> ProxyEndpoint:
        if self._server is not None:
            return self.endpoint
        self._stopping = False
        self._server = await asyncio.start_server(self._accept, self._host, 0)
        return self.endpoint

    def grant_local_origin(self, url: str) -> None:
        """Allow one exact local origin for subsequent proxy connections.

        The host calls this only after an explicit agent site approval or direct
        user navigation. Cloud metadata remains unconditionally blocked by
        :class:`DestinationPolicy`.
        """

        self._policy = self._policy.with_local_origin_grant(url)

    async def stop(self) -> None:
        self._stopping = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def __aenter__(self) -> "FailClosedLoopbackProxy":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._stopping:
            writer.close()
            return
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        upstream_writer: Optional[asyncio.StreamWriter] = None
        try:
            raw_head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=_IO_TIMEOUT
            )
            if len(raw_head) > _MAX_HEADER_BYTES:
                raise _ProxyRequestError(431, "Request Header Fields Too Large")
            request = _parse_request(raw_head)
            if not self._authorized(request.headers):
                await _write_response(
                    writer,
                    407,
                    "Proxy Authentication Required",
                    extra_headers=[("Proxy-Authenticate", 'Basic realm="OpenWorker"')],
                )
                return
            if request.method not in _ALLOWED_METHODS:
                raise _ProxyRequestError(405, "Method Not Allowed")
            if request.method == "CONNECT":
                decision = self._connect_decision(request.target)
                upstream_reader, upstream_writer = await self._open_pinned(decision)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await _relay_bidirectional(
                    reader, writer, upstream_reader, upstream_writer
                )
                upstream_writer = None
                return

            decision = self._policy.evaluate(request.target)
            if decision.origin.scheme not in {"http", "ws"}:
                raise _ProxyRequestError(400, "Absolute HTTP proxy URL required")
            body = await _read_request_body(reader, request.headers)
            upstream_reader, upstream_writer = await self._open_pinned(decision)
            is_websocket = _is_websocket_upgrade(request.headers)
            upstream_writer.write(
                _upstream_request_bytes(
                    request, decision, body, websocket=is_websocket
                )
            )
            await upstream_writer.drain()
            if is_websocket:
                await _relay_bidirectional(
                    reader, writer, upstream_reader, upstream_writer
                )
                upstream_writer = None
            else:
                await _copy_stream(upstream_reader, writer)
            return
        except asyncio.IncompleteReadError:
            pass
        except asyncio.LimitOverrunError:
            await _write_response(writer, 431, "Request Header Fields Too Large")
        except asyncio.TimeoutError:
            await _write_response(writer, 504, "Gateway Timeout")
        except _ProxyRequestError as exc:
            await _write_response(writer, exc.status, exc.reason)
        except DestinationPolicyError:
            # Do not reveal internal addresses or resolution details to the page.
            await _write_response(writer, 403, "Destination Blocked")
        except (ConnectionError, OSError):
            await _write_response(writer, 502, "Bad Gateway")
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _authorized(self, headers: tuple[tuple[str, str], ...]) -> bool:
        provided = _header_value(headers, "proxy-authorization")
        expected = base64.b64encode(
            f"{self._username}:{self._token}".encode("utf-8")
        ).decode("ascii")
        return bool(provided) and hmac.compare_digest(
            provided.strip(), f"Basic {expected}"
        )

    def _connect_decision(self, authority: str) -> DestinationDecision:
        host, port = _parse_authority(authority)
        display_host = f"[{host}]" if ":" in host else host
        errors: list[DestinationPolicyError] = []
        # CONNECT does not reveal whether the tunneled protocol is HTTPS or WSS.
        # Evaluate both only so an exact local WSS grant can authorize its tunnel.
        for scheme in ("https", "wss"):
            try:
                return self._policy.evaluate(f"{scheme}://{display_host}:{port}/")
            except DestinationPolicyError as exc:
                errors.append(exc)
        raise errors[0]

    async def _open_pinned(
        self, decision: DestinationDecision
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=decision.connect_host,
                port=decision.connect_port,
            ),
            timeout=_IO_TIMEOUT,
        )
        peer = writer.get_extra_info("peername")
        if not peer:
            writer.close()
            raise DestinationPolicyError(
                "PEER_ADDRESS_INVALID", "Connected peer address is unavailable"
            )
        try:
            decision.verify_peer(str(peer[0]))
        except DestinationPolicyError:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            raise
        return reader, writer


@dataclass(frozen=True)
class _Request:
    method: str
    target: str
    version: str
    headers: tuple[tuple[str, str], ...]


class _ProxyRequestError(ValueError):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _parse_request(raw_head: bytes) -> _Request:
    if b"\x00" in raw_head or b"\n " in raw_head or b"\n\t" in raw_head:
        raise _ProxyRequestError(400, "Bad Request")
    try:
        text = raw_head.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise _ProxyRequestError(400, "Bad Request") from exc
    lines = text[:-4].split("\r\n")
    if not lines:
        raise _ProxyRequestError(400, "Bad Request")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not all(parts):
        raise _ProxyRequestError(400, "Bad Request")
    method, target, version = parts
    method = method.upper()
    if version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise _ProxyRequestError(505, "HTTP Version Not Supported")
    headers: list[tuple[str, str]] = []
    seen_content_lengths: set[str] = set()
    for line in lines[1:]:
        if not line or ":" not in line:
            raise _ProxyRequestError(400, "Bad Request")
        name, value = line.split(":", 1)
        if not name or any(char.isspace() for char in name):
            raise _ProxyRequestError(400, "Bad Request")
        lowered = name.lower()
        clean_value = value.strip()
        if lowered == "content-length":
            seen_content_lengths.add(clean_value)
        headers.append((lowered, clean_value))
    if len(seen_content_lengths) > 1:
        raise _ProxyRequestError(400, "Bad Request")
    if len([1 for name, _ in headers if name == "host"]) > 1:
        raise _ProxyRequestError(400, "Bad Request")
    return _Request(method, target, version, tuple(headers))


def _parse_authority(value: str) -> tuple[str, int]:
    if any(char in value for char in "/?#@"):
        raise _ProxyRequestError(400, "Bad CONNECT Authority")
    try:
        parsed = urlsplit(f"//{value}")
        if parsed.hostname is None or parsed.port is None:
            raise ValueError
        return parsed.hostname, parsed.port
    except ValueError as exc:
        raise _ProxyRequestError(400, "Bad CONNECT Authority") from exc


async def _read_request_body(
    reader: asyncio.StreamReader, headers: tuple[tuple[str, str], ...]
) -> bytes:
    transfer_encoding = _header_value(headers, "transfer-encoding")
    if transfer_encoding:
        # Streaming request bodies/downloads are outside the MVP and request smuggling
        # defenses are much smaller when chunked requests are rejected.
        raise _ProxyRequestError(501, "Transfer Encoding Not Supported")
    if _header_value(headers, "expect"):
        raise _ProxyRequestError(417, "Expectation Failed")
    length_text = _header_value(headers, "content-length")
    if not length_text:
        return b""
    try:
        length = int(length_text, 10)
    except ValueError as exc:
        raise _ProxyRequestError(400, "Bad Content Length") from exc
    if length < 0 or length > _MAX_REQUEST_BODY:
        raise _ProxyRequestError(413, "Content Too Large")
    if not length:
        return b""
    try:
        return await asyncio.wait_for(
            reader.readexactly(length), timeout=_IO_TIMEOUT
        )
    except asyncio.IncompleteReadError as exc:
        raise _ProxyRequestError(400, "Incomplete Request Body") from exc


def _upstream_request_bytes(
    request: _Request,
    decision: DestinationDecision,
    body: bytes,
    *,
    websocket: bool,
) -> bytes:
    parsed = urlsplit(decision.canonical_url)
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    connection_tokens: set[str] = set()
    for value in _header_values(request.headers, "connection"):
        connection_tokens.update(
            token.strip().lower() for token in value.split(",") if token.strip()
        )
    stripped = {
        "proxy-authorization",
        "proxy-connection",
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
    } | connection_tokens
    if websocket:
        stripped.discard("upgrade")
    else:
        stripped.add("upgrade")
    headers = [
        (name, value)
        for name, value in request.headers
        if name not in stripped and name != "host"
    ]
    display_host = (
        f"[{decision.origin.host}]"
        if ":" in decision.origin.host
        else decision.origin.host
    )
    default_port = 80
    host_header = (
        display_host
        if decision.origin.port == default_port
        else f"{display_host}:{decision.origin.port}"
    )
    headers.insert(0, ("host", host_header))
    if websocket:
        headers.append(("connection", "Upgrade"))
    else:
        headers.append(("connection", "close"))
    start = f"{request.method} {target} HTTP/1.1\r\n"
    encoded_headers = "".join(f"{name}: {value}\r\n" for name, value in headers)
    return (start + encoded_headers + "\r\n").encode("iso-8859-1") + body


def _is_websocket_upgrade(headers: tuple[tuple[str, str], ...]) -> bool:
    upgrade = (_header_value(headers, "upgrade") or "").casefold()
    connection = ",".join(_header_values(headers, "connection")).casefold()
    return upgrade == "websocket" and "upgrade" in {
        token.strip() for token in connection.split(",")
    }


def _header_value(
    headers: tuple[tuple[str, str], ...], name: str
) -> Optional[str]:
    values = _header_values(headers, name)
    return values[-1] if values else None


def _header_values(
    headers: tuple[tuple[str, str], ...], name: str
) -> list[str]:
    lowered = name.lower()
    return [value for key, value in headers if key == lowered]


async def _copy_stream(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    while True:
        chunk = await reader.read(64 * 1024)
        if not chunk:
            break
        writer.write(chunk)
        await writer.drain()


async def _relay_bidirectional(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    async def pump(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await _copy_stream(reader, writer)
        finally:
            with contextlib.suppress(Exception):
                writer.write_eof()

    tasks = [
        asyncio.create_task(pump(client_reader, upstream_writer)),
        asyncio.create_task(pump(upstream_reader, client_writer)),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*done, *pending, return_exceptions=True)
    upstream_writer.close()
    with contextlib.suppress(Exception):
        await upstream_writer.wait_closed()


async def _write_response(
    writer: asyncio.StreamWriter,
    status: int,
    reason: str,
    *,
    extra_headers: Optional[list[tuple[str, str]]] = None,
) -> None:
    if writer.is_closing():
        return
    headers = [
        ("Content-Length", "0"),
        ("Connection", "close"),
        ("Cache-Control", "no-store"),
        *(extra_headers or []),
    ]
    payload = (
        f"HTTP/1.1 {status} {reason}\r\n"
        + "".join(f"{name}: {value}\r\n" for name, value in headers)
        + "\r\n"
    ).encode("ascii")
    writer.write(payload)
    with contextlib.suppress(ConnectionError):
        await writer.drain()
