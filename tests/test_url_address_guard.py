"""`web_fetch` / `browser_read_url` must not reach the machine's own network position.

Both take a URL straight from the model, and the model's input is untrusted by design —
the tools' own descriptions call fetched content "data to evaluate, not instructions".
`web_fetch` is additionally `requires_approval=False`, so nothing prompts the user.
"""

import socket

import pytest

from coworker.web import guard
from coworker.web.fetch import make_web_fetch_tool


def _resolves_to(monkeypatch, ip: str):
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))],
    )


# -- literals -----------------------------------------------------------------

@pytest.mark.parametrize("url,needle", [
    ("http://127.0.0.1:11434/api/tags", "loopback"),
    ("http://localhost:8000/", "loopback"),
    ("http://[::1]:8080/", "loopback"),
    ("http://169.254.169.254/latest/meta-data/", "link-local"),
    ("http://10.0.0.5/admin", "private"),
    ("http://192.168.1.1/", "private"),
    ("http://172.16.4.4/", "private"),
    ("http://0.0.0.0/", "refusing to fetch"),  # 0.0.0.0/8 lands in is_private first
    ("http://100.64.0.1/", "CGNAT"),  # RFC 6598 shared space (Tailscale, CGNAT)
    ("http://100.127.255.254/", "CGNAT"),
])
def test_blocked_literals(url, needle):
    reason = guard.check_url(url)
    assert reason and needle in reason


def test_cgnat_neighbours_still_allowed(monkeypatch):
    """100.64.0.0/10 is blocked, but the adjacent public 100.63/100.128 space is not."""
    _resolves_to(monkeypatch, "100.63.255.255")
    assert guard.check_url("http://below.example/") is None
    _resolves_to(monkeypatch, "100.128.0.0")
    assert guard.check_url("http://above.example/") is None


def test_ipv4_mapped_ipv6_loopback_is_blocked():
    """::ffff:127.0.0.1 must be judged as the v4 address it carries."""
    assert guard.check_url("http://[::ffff:127.0.0.1]/")


def test_public_literal_is_allowed():
    assert guard.check_url("https://93.184.216.34/") is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x",
                                 "gopher://example.com/", "http://"])
def test_non_http_schemes_and_hostless_urls_are_refused(url):
    assert guard.check_url(url)


# -- names --------------------------------------------------------------------

def test_hostname_resolving_to_loopback_is_blocked(monkeypatch):
    """`localtest.me` and friends are public names with private answers."""
    _resolves_to(monkeypatch, "127.0.0.1")
    assert "loopback" in guard.check_url("http://sneaky.example.com/")


def test_hostname_resolving_to_metadata_ip_is_blocked(monkeypatch):
    _resolves_to(monkeypatch, "169.254.169.254")
    assert guard.check_url("http://metadata.example.com/")


def test_any_private_answer_blocks_a_split_horizon_name(monkeypatch):
    """One public and one private A record must not be a way through."""
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ],
    )
    assert guard.check_url("http://split.example.com/")


def test_public_hostname_is_allowed(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert guard.check_url("https://example.com/docs") is None


def test_unresolvable_host_is_refused_not_fetched(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("nodename nor servname provided")
    monkeypatch.setattr(guard.socket, "getaddrinfo", boom)
    assert "could not resolve" in guard.check_url("http://nope.invalid/")


# -- redirects ----------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, location=None, url="https://example.com/"):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.url = _Url(url)
        self.text = "body"

    def raise_for_status(self):
        pass


class _Url(str):
    def join(self, other):
        return other


class _Client:
    """Records what was actually requested, so a blocked hop is provably not fetched."""

    def __init__(self, script):
        self.script = script
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        return self.script.pop(0)


def test_redirect_into_loopback_is_blocked_before_the_second_request(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    client = _Client([_Resp(302, location="http://127.0.0.1:11434/api/tags")])
    with pytest.raises(PermissionError, match="loopback"):
        guard.get_checked(client, "https://example.com/start")
    assert client.requested == ["https://example.com/start"], (
        "the redirect target must never be requested"
    )


def test_allowed_redirect_chain_is_followed(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    client = _Client([_Resp(302, location="https://example.com/b"), _Resp(200)])
    resp = guard.get_checked(client, "https://example.com/a")
    assert resp.status_code == 200
    assert client.requested == ["https://example.com/a", "https://example.com/b"]


def test_redirect_loop_is_bounded(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    client = _Client([_Resp(302, location="https://example.com/loop")] * 50)
    with pytest.raises(RuntimeError, match="too many redirects"):
        guard.get_checked(client, "https://example.com/loop")


# -- the tool -----------------------------------------------------------------

def test_web_fetch_returns_the_refusal_as_a_tool_error(monkeypatch):
    _resolves_to(monkeypatch, "127.0.0.1")
    out = make_web_fetch_tool()("http://sneaky.example.com/")
    assert "loopback" in out["error"]
    assert "text" not in out


def test_web_fetch_still_rejects_non_http_schemes():
    assert "http" in make_web_fetch_tool()("file:///etc/passwd")["error"]


def test_browser_open_url_is_guarded_and_never_launches(monkeypatch):
    """The Playwright browser_open_url is approval gated, but the address guard still
    refuses a blocked URL before the browser is touched (defense in depth)."""
    from coworker.connectors.browser_automation import make_browser_automation_tools

    open_url = {t.__name__: t for t in make_browser_automation_tools()}["browser_open_url"]
    out = open_url("http://169.254.169.254/latest/meta-data/")
    assert "link-local" in out["error"]
    assert out.get("ok") is None
