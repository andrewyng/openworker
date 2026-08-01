from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from coworker.browser_security import BrowserProxyHost


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"proxied local response"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _unused_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_proxy_host_is_fail_closed_and_grants_exact_local_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    allowed = f"http://127.0.0.1:{server.server_port}"
    denied = f"http://127.0.0.1:{_unused_port()}"
    host = BrowserProxyHost()
    try:
        config = host.create_session("one")
        auth = (config["username"], config["password"])
        proxy = httpx.Proxy(config["server"], auth=auth)
        with httpx.Client(proxy=proxy) as client:
            assert client.get(allowed).status_code == 403
        host.grant_local_origin("one", allowed)
        with httpx.Client(proxy=proxy) as client:
            response = client.get(allowed)
            assert response.status_code == 200
            assert response.text == "proxied local response"
            assert client.get(denied).status_code == 403
        host.close_session("one")
        try:
            with httpx.Client(proxy=proxy) as client:
                client.get(allowed)
        except httpx.TransportError:
            pass
        else:
            raise AssertionError("stopped proxy unexpectedly accepted a request")
    finally:
        host.close()
        server.shutdown()
        thread.join(timeout=5)


def test_proxy_host_ignores_public_names_as_local_grants():
    host = BrowserProxyHost()
    try:
        host.create_session(
            "public",
            local_origin_grants=["https://public.example"],
        )
        host.grant_local_origin("public", "https://second.example")
        proxy = host._proxies["public"]
        assert proxy._policy.local_origin_grants == frozenset()
    finally:
        host.close()
