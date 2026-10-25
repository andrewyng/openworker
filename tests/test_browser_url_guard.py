"""browser_open_url's address guard must cover EVERY navigation, not just the model URL.

check_url vets the URL the model supplied; the browser then follows redirects and runs page
JS, and a later navigation to 127.0.0.1 / 169.254.169.254 was never re-vetted before
browser_read_page / browser_screenshot lifted its content into the agent. The context-level
route guard aborts blocked navigations at request time. These tests exercise that handler
directly (no Playwright needed).
"""

from __future__ import annotations

import socket

import pytest

from coworker.connectors.browser_automation import _guard_route
from coworker.web import guard


def _resolves_to(monkeypatch, ip: str):
    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))],
    )


class _FakeRequest:
    def __init__(self, url: str, is_nav: bool = True):
        self.url = url
        self._is_nav = is_nav

    def is_navigation_request(self) -> bool:
        return self._is_nav


class _FakeRoute:
    def __init__(self, url: str, is_nav: bool = True):
        self.request = _FakeRequest(url, is_nav)
        self.aborted = None
        self.continued = False

    def abort(self, reason: str | None = None):
        self.aborted = reason

    def continue_(self):
        self.continued = True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/api/tags",
        "http://localhost:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8080/",
    ],
)
def test_navigation_to_internal_literal_is_aborted(url):
    route = _FakeRoute(url, is_nav=True)
    _guard_route(route)
    assert route.aborted == "blockedbyclient"
    assert route.continued is False


def test_navigation_to_host_resolving_to_loopback_is_aborted(monkeypatch):
    # DNS rebinding / split-horizon: a public-looking name that resolves to loopback.
    _resolves_to(monkeypatch, "127.0.0.1")
    route = _FakeRoute("http://sneaky.example.com/", is_nav=True)
    _guard_route(route)
    assert route.aborted == "blockedbyclient"


def test_navigation_to_metadata_host_is_aborted(monkeypatch):
    _resolves_to(monkeypatch, "169.254.169.254")
    route = _FakeRoute("http://metadata.example.com/", is_nav=True)
    _guard_route(route)
    assert route.aborted == "blockedbyclient"


def test_allowed_public_navigation_continues(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    route = _FakeRoute("https://example.com/docs", is_nav=True)
    _guard_route(route)
    assert route.aborted is None
    assert route.continued is True


def test_subresource_request_is_not_gated():
    # A non-navigation request (image/script/XHR) can't read internal content back into the
    # agent; gating every one would resolve DNS dozens of times per page. It is let through
    # even to a loopback literal — the read-back path is what the guard closes.
    route = _FakeRoute("http://127.0.0.1:11434/api/tags", is_nav=False)
    _guard_route(route)
    assert route.aborted is None
    assert route.continued is True


def test_guard_fails_closed_when_is_navigation_raises(monkeypatch):
    # A request object that errors on inspection is treated as a navigation and vetted.
    _resolves_to(monkeypatch, "127.0.0.1")

    class _Boom(_FakeRoute):
        def __init__(self):
            super().__init__("http://sneaky.example.com/", is_nav=True)

            def boom():
                raise RuntimeError("no such method")

            self.request.is_navigation_request = boom

    route = _Boom()
    _guard_route(route)
    assert route.aborted == "blockedbyclient"
