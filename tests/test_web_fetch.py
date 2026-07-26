"""Tests for `web_fetch` — text extraction and the transfer-level download cap."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from coworker.web.fetch import _MAX_DOWNLOAD_BYTES, make_web_fetch_tool

_CHUNK = b"x" * 65536
_HUGE_CHUNKS = 400  # ~25 MB


class _Handler(BaseHTTPRequestHandler):
    bytes_sent = 0

    def do_GET(self):  # noqa: N802
        if self.path == "/huge":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            try:
                for _ in range(_HUGE_CHUNKS):
                    self.wfile.write(_CHUNK)
                    type(self).bytes_sent += len(_CHUNK)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if self.path == "/page":
            body = (
                b"<html><head><style>p { color: red }</style></head>"
                b"<body><p>hello world</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def server_url():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_rejects_non_http_urls():
    web_fetch = make_web_fetch_tool()
    assert "error" in web_fetch("ftp://example.com/file")
    assert "error" in web_fetch("file:///etc/hosts")


def test_html_stripped_to_text(server_url):
    web_fetch = make_web_fetch_tool()
    result = web_fetch(server_url + "/page")
    assert "error" not in result
    assert "hello world" in result["text"]
    assert "color: red" not in result["text"]
    assert result["truncated"] is False


def test_huge_body_download_is_capped(server_url):
    """The client must hang up near the byte budget, not buffer the whole body."""
    _Handler.bytes_sent = 0
    web_fetch = make_web_fetch_tool()
    result = web_fetch(server_url + "/huge")
    assert "error" not in result
    assert result["truncated"] is True
    assert len(result["text"]) <= 20000
    # 3x budget allows for OS socket buffering on loopback; the server offered ~25 MB.
    assert _Handler.bytes_sent < 3 * _MAX_DOWNLOAD_BYTES
