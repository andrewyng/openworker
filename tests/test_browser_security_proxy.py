from __future__ import annotations

import asyncio
import base64
import contextlib

import pytest

from coworker.browser_security.destination import DestinationPolicy
from coworker.browser_security.proxy import FailClosedLoopbackProxy


async def _read_head(reader):
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)


async def _open_proxy(endpoint):
    parsed = endpoint.proxy_url.rsplit(":", 1)
    return await asyncio.open_connection("127.0.0.1", int(parsed[1]))


def _auth(endpoint):
    value = base64.b64encode(
        f"{endpoint.username}:{endpoint.token}".encode()
    ).decode()
    return f"Proxy-Authorization: Basic {value}\r\n"


@pytest.mark.asyncio
async def test_authenticated_http_proxy_pins_local_grant_and_strips_credentials():
    captured = {}

    async def upstream(reader, writer):
        captured["request"] = (await _read_head(reader)).decode("iso-8859-1")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    policy = DestinationPolicy(
        local_origin_grants=[f"http://unit.test:{port}"],
        resolver=lambda host, target_port: ["127.0.0.1"],
    )
    proxy = FailClosedLoopbackProxy(policy)
    endpoint = await proxy.start()
    try:
        reader, writer = await _open_proxy(endpoint)
        writer.write(
            (
                f"GET http://unit.test:{port}/hello?q=1 HTTP/1.1\r\n"
                f"Host: unit.test:{port}\r\n"
                f"{_auth(endpoint)}"
                "Connection: keep-alive\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        assert b"200 OK" in response and response.endswith(b"OK")
        upstream_request = captured["request"]
        assert upstream_request.startswith("GET /hello?q=1 HTTP/1.1")
        assert "host: unit.test:" in upstream_request.lower()
        assert "proxy-authorization" not in upstream_request.lower()
        assert "connection: close" in upstream_request.lower()
    finally:
        await proxy.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_missing_authentication_and_ungranted_local_address_fail_closed():
    policy = DestinationPolicy(
        resolver=lambda host, port: ["127.0.0.1"],
    )
    proxy = FailClosedLoopbackProxy(policy)
    endpoint = await proxy.start()
    try:
        reader, writer = await _open_proxy(endpoint)
        writer.write(b"GET http://unit.test/ HTTP/1.1\r\nHost: unit.test\r\n\r\n")
        await writer.drain()
        response = await _read_head(reader)
        assert b"407 Proxy Authentication Required" in response

        reader, writer = await _open_proxy(endpoint)
        writer.write(
            (
                "GET http://unit.test/ HTTP/1.1\r\n"
                "Host: unit.test\r\n"
                f"{_auth(endpoint)}\r\n"
            ).encode()
        )
        await writer.drain()
        response = await _read_head(reader)
        assert b"403 Destination Blocked" in response
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_connect_tunnel_uses_pinned_address():
    async def echo(reader, writer):
        data = await reader.read(64)
        writer.write(b"echo:" + data)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    policy = DestinationPolicy(
        local_origin_grants=[f"https://secure.test:{port}"],
        resolver=lambda host, target_port: ["127.0.0.1"],
    )
    proxy = FailClosedLoopbackProxy(policy)
    endpoint = await proxy.start()
    try:
        reader, writer = await _open_proxy(endpoint)
        writer.write(
            (
                f"CONNECT secure.test:{port} HTTP/1.1\r\n"
                f"Host: secure.test:{port}\r\n"
                f"{_auth(endpoint)}\r\n"
            ).encode()
        )
        await writer.drain()
        response = await _read_head(reader)
        assert b"200 Connection Established" in response
        writer.write(b"ping")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(9), timeout=2) == b"echo:ping"
    finally:
        await proxy.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_stopped_proxy_has_no_direct_fallback():
    policy = DestinationPolicy(resolver=lambda host, port: ["1.1.1.1"])
    proxy = FailClosedLoopbackProxy(policy)
    endpoint = await proxy.start()
    port = int(endpoint.proxy_url.rsplit(":", 1)[1])
    await proxy.stop()
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)


@pytest.mark.asyncio
async def test_http_request_smuggling_shapes_are_rejected():
    policy = DestinationPolicy(
        local_origin_grants=["http://unit.test:8080"],
        resolver=lambda host, port: ["127.0.0.1"],
    )
    proxy = FailClosedLoopbackProxy(policy)
    endpoint = await proxy.start()
    try:
        reader, writer = await _open_proxy(endpoint)
        writer.write(
            (
                "POST http://unit.test:8080/ HTTP/1.1\r\n"
                "Host: unit.test\r\n"
                "Host: attacker\r\n"
                f"{_auth(endpoint)}"
                "Content-Length: 1\r\n"
                "Content-Length: 2\r\n\r\nxx"
            ).encode()
        )
        await writer.drain()
        response = await _read_head(reader)
        assert b"400 Bad Request" in response
    finally:
        await proxy.stop()
