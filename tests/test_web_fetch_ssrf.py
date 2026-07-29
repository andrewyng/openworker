"""SSRF protections for the `web_fetch` tool.

Hermetic: no real network or DNS. `socket.getaddrinfo` is monkeypatched to return a fixed
address, and HTTP is served by an in-memory `httpx.MockTransport`, so the redirect / IP
checks are exercised without leaving the process.
"""

from __future__ import annotations

import ipaddress
import socket

import httpx
import pytest

from coworker.web.fetch import _blocked_host_reason, _ip_is_public, make_web_fetch_tool


# -- IP classification ---------------------------------------------------------
@pytest.mark.parametrize(
    "addr",
    [
        "93.184.216.34",  # example.com — public
        "1.1.1.1",
        "2606:4700:4700::1111",  # public IPv6
    ],
)
def test_public_addresses_pass(addr):
    assert _ip_is_public(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # RFC1918
        "172.16.5.4",  # RFC1918
        "192.168.1.1",  # RFC1918
        "169.254.169.254",  # link-local / cloud metadata
        "0.0.0.0",  # unspecified
        "::1",  # IPv6 loopback
        "fd00::1",  # IPv6 ULA (private)
        "fe80::1",  # IPv6 link-local
        "::ffff:10.0.0.1",  # IPv4-mapped private — must be unwrapped and blocked
    ],
)
def test_non_public_addresses_are_blocked(addr):
    assert _ip_is_public(ipaddress.ip_address(addr)) is False


# -- literal-IP refusal (no DNS, no network) -----------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/admin",
        "http://[::1]:8765/",
        "http://0.0.0.0/",
    ],
)
def test_web_fetch_refuses_private_literal_ips(url):
    web_fetch = make_web_fetch_tool()
    out = web_fetch(url)
    assert "error" in out
    assert "non-public" in out["error"]


def test_scheme_guard_still_rejects_non_http():
    web_fetch = make_web_fetch_tool()
    assert "http" in web_fetch("file:///etc/passwd")["error"]
    assert "http" in web_fetch("gopher://127.0.0.1/")["error"]


# -- redirect handling (MockTransport, patched DNS) ----------------------------
def _pin_dns(monkeypatch, ip: str = "93.184.216.34") -> None:
    """Resolve every hostname to a single public IP."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))
        ],
    )


def _use_transport(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_with_transport(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_with_transport)


def test_public_fetch_succeeds(monkeypatch):
    _pin_dns(monkeypatch)

    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><h1>Hi</h1><p>Body text</p></body></html>",
        )

    _use_transport(monkeypatch, handler)
    out = make_web_fetch_tool()("http://example.com/page")
    assert "error" not in out
    assert "Hi" in out["text"] and "Body text" in out["text"]
    assert out["url"] == "http://example.com/page"


def test_redirect_to_private_is_blocked(monkeypatch):
    _pin_dns(monkeypatch)

    def handler(request):
        # A public page bounces to the cloud-metadata endpoint — the classic
        # public→private SSRF pivot. The second hop must never be requested.
        if request.url.host == "example.com":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        raise AssertionError("private redirect target must not be fetched")

    _use_transport(monkeypatch, handler)
    out = make_web_fetch_tool()("http://example.com/")
    assert "error" in out
    assert "169.254.169.254" in out["error"] and "non-public" in out["error"]


def test_redirect_to_non_http_scheme_is_blocked(monkeypatch):
    _pin_dns(monkeypatch)

    def handler(request):
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    _use_transport(monkeypatch, handler)
    out = make_web_fetch_tool()("http://example.com/")
    assert "error" in out
    assert "non-http" in out["error"]


def test_redirect_cap(monkeypatch):
    _pin_dns(monkeypatch)

    def handler(request):
        # Endless same-host (public) redirect loop.
        return httpx.Response(302, headers={"location": "http://example.com/next"})

    _use_transport(monkeypatch, handler)
    out = make_web_fetch_tool()("http://example.com/")
    assert "error" in out
    assert "too many redirects" in out["error"]


def test_relative_public_redirect_is_followed(monkeypatch):
    _pin_dns(monkeypatch)

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, text="landed"
        )

    _use_transport(monkeypatch, handler)
    out = make_web_fetch_tool()("http://example.com/start")
    assert "error" not in out
    assert out["text"] == "landed"
    assert out["url"].endswith("/final")


# -- resolver-level guard ------------------------------------------------------
def test_blocked_host_reason_rejects_resolved_private(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.1.2.3", 0))
        ],
    )
    reason = _blocked_host_reason("internal.example.com")
    assert reason is not None and "non-public" in reason


def test_blocked_host_reason_allows_resolved_public(monkeypatch):
    _pin_dns(monkeypatch)
    assert _blocked_host_reason("example.com") is None
