"""MCP OAuth destination checks (issue #527).

A malicious MCP server's metadata can advertise token/registration/authorize
URLs on loopback, RFC1918, or link-local (cloud metadata). The SDK fetches those
verbatim; we refuse before the socket opens. Same-origin as the user-chosen
server stays allowed so local MCP and same-host well-known still work; federated
public authorization servers stay allowed via guard.check_url.
"""

from __future__ import annotations

import socket

import httpx
import pytest
from mcp.client.auth import OAuthClientProvider

from coworker.mcp import oauth as mcp_oauth
from coworker.mcp.ssrf import (
    UnsafeMcpUrl,
    check_destination,
    check_metadata,
    mcp_httpx_client_factory,
    refuse_destination,
)
from coworker.secrets import SecretStore
from coworker.web import guard

SERVER = "https://mcp.example.com/mcp"


def _resolves_to(monkeypatch, ip: str):
    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))],
    )


@pytest.fixture(autouse=True)
def _reset_authorize_url():
    mcp_oauth.last_authorize_url = None
    mcp_oauth._expected_state = None
    yield
    mcp_oauth.last_authorize_url = None
    mcp_oauth._expected_state = None


# -- destination policy --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.example.com/mcp",
        "https://mcp.example.com:443/.well-known/oauth-protected-resource",
        "https://mcp.example.com/register",
        "https://MCP.EXAMPLE.COM/token",
    ],
)
def test_same_origin_as_user_chosen_server_is_allowed(url):
    assert check_destination(url, SERVER) is None


def test_same_origin_loopback_is_allowed():
    """Users (and tests) connect to local MCP servers; that origin is trusted."""
    assert (
        check_destination("http://127.0.0.1:3000/token", "http://127.0.0.1:3000/mcp")
        is None
    )


@pytest.mark.parametrize(
    "url,needle",
    [
        ("http://169.254.169.254/latest/meta-data/", "link-local"),
        ("http://127.0.0.1:11434/api/tags", "loopback"),
        ("http://10.0.0.5/admin", "private"),
        ("http://192.168.1.1/", "private"),
        ("http://100.64.0.1/", "CGNAT"),
        ("file:///etc/passwd", "http:// or https://"),
        ("javascript:alert(1)", "http:// or https://"),
        ("ftp://example.com/x", "http:// or https://"),
    ],
)
def test_cross_origin_private_and_non_http_are_refused(url, needle):
    reason = check_destination(url, SERVER)
    assert reason and needle in reason


def test_cross_origin_loopback_from_public_server_is_refused():
    """Local MCP is allowed only when the *user* picked that origin."""
    reason = check_destination("http://127.0.0.1:3000/token", SERVER)
    assert reason and "loopback" in reason


def test_federated_public_authorization_server_is_allowed(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert check_destination("https://login.microsoftonline.com/oauth2/v2.0/token", SERVER) is None


def test_hostname_resolving_to_metadata_is_refused(monkeypatch):
    _resolves_to(monkeypatch, "169.254.169.254")
    reason = check_destination("https://metadata.example.com/token", SERVER)
    assert reason


def test_different_loopback_port_is_refused():
    """A local MCP redirecting to another loopback port is the same confused deputy."""
    reason = check_destination("http://127.0.0.1:3001/token", "http://127.0.0.1:3000/mcp")
    assert reason and "loopback" in reason


def test_refuse_destination_raises_before_caller_can_send():
    with pytest.raises(UnsafeMcpUrl, match="link-local"):
        refuse_destination(
            "http://169.254.169.254/latest/meta-data/", SERVER, what="token endpoint"
        )


# -- metadata documents --------------------------------------------------------


def test_metadata_with_metadata_ip_token_endpoint_is_unsafe():
    md = {
        "issuer": "https://mcp.example.com",
        "authorization_endpoint": "https://mcp.example.com/authorize",
        "token_endpoint": "http://169.254.169.254/latest/meta-data/",
    }
    reason = check_metadata(md, SERVER)
    assert reason and "169.254.169.254" in reason


def test_metadata_same_origin_endpoints_are_safe():
    md = {
        "issuer": "https://mcp.example.com",
        "authorization_endpoint": "https://mcp.example.com/authorize",
        "token_endpoint": "https://mcp.example.com/token",
        "registration_endpoint": "https://mcp.example.com/register",
    }
    assert check_metadata(md, SERVER) is None


@pytest.mark.asyncio
async def test_cached_metadata_with_unsafe_token_endpoint_is_dropped(tmp_path):
    """Refresh POSTs the cached token_endpoint before rediscovery — poison must
    not survive load."""
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put(
        "mcp-oauth:evil",
        {
            "tokens": {"access_token": "A", "refresh_token": "R"},
            "oauth_metadata": {
                "issuer": "https://mcp.example.com",
                "authorization_endpoint": "https://mcp.example.com/authorize",
                "token_endpoint": "http://169.254.169.254/latest/meta-data/",
            },
        },
    )
    auth = mcp_oauth.build_auth(
        "evil", SERVER, secrets, interactive=False
    )
    await auth._initialize()
    assert auth.context.oauth_metadata is None
    assert "oauth_metadata" not in (secrets.get("mcp-oauth:evil") or {})


@pytest.mark.asyncio
async def test_cached_same_origin_metadata_still_seeds(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    md = {
        "issuer": "https://data.example",
        "authorization_endpoint": "https://data.example/api/auth/authorize",
        "token_endpoint": "https://data.example/api/auth/token",
    }
    secrets.put("mcp-oauth:dlai", {"oauth_metadata": md})
    auth = mcp_oauth.build_auth(
        "dlai", "https://data.example/api/mcp", secrets, interactive=False
    )
    await auth._initialize()
    assert str(auth.context.oauth_metadata.token_endpoint) == md["token_endpoint"]


@pytest.mark.asyncio
async def test_cached_federated_public_metadata_still_seeds(tmp_path, monkeypatch):
    """Atlassian/Granola-style: AS lives on another public host."""
    _resolves_to(monkeypatch, "93.184.216.34")
    secrets = SecretStore(tmp_path / "s.json")
    md = {
        "issuer": "https://login.microsoftonline.com",
        "authorization_endpoint": "https://login.microsoftonline.com/authorize",
        "token_endpoint": "https://login.microsoftonline.com/token",
    }
    secrets.put("mcp-oauth:jira", {"oauth_metadata": md})
    auth = mcp_oauth.build_auth(
        "jira", "https://mcp.atlassian.com/v1/mcp", secrets, interactive=False
    )
    await auth._initialize()
    assert str(auth.context.oauth_metadata.token_endpoint) == md["token_endpoint"]


def test_persist_drops_unsafe_metadata_instead_of_writing_it(tmp_path):
    from mcp.shared.auth import OAuthMetadata

    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("mcp-oauth:evil", {"tokens": {"access_token": "A"}})
    auth = mcp_oauth.build_auth("evil", SERVER, secrets, interactive=False)
    auth.context.oauth_metadata = OAuthMetadata.model_validate(
        {
            "issuer": "https://mcp.example.com",
            "authorization_endpoint": "https://mcp.example.com/authorize",
            "token_endpoint": "http://169.254.169.254/latest/meta-data/",
        }
    )
    auth._persist_metadata()
    assert auth.context.oauth_metadata is None
    assert "oauth_metadata" not in (secrets.get("mcp-oauth:evil") or {})


# -- authorize URL / browser ---------------------------------------------------


@pytest.mark.asyncio
async def test_redirect_handler_refuses_file_scheme_and_does_not_open(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
    secrets = SecretStore(tmp_path / "s.json")
    auth = mcp_oauth.build_auth("granola", SERVER, secrets, interactive=True)
    with pytest.raises(UnsafeMcpUrl, match="http:// or https://"):
        await auth.context.redirect_handler("file:///etc/passwd")
    assert opened == []
    assert mcp_oauth.last_authorize_url is None


@pytest.mark.asyncio
async def test_redirect_handler_refuses_metadata_ip(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
    secrets = SecretStore(tmp_path / "s.json")
    auth = mcp_oauth.build_auth("granola", SERVER, secrets, interactive=True)
    with pytest.raises(UnsafeMcpUrl, match="link-local"):
        await auth.context.redirect_handler(
            "http://169.254.169.254/latest/meta-data/"
        )
    assert opened == []
    assert mcp_oauth.last_authorize_url is None


@pytest.mark.asyncio
async def test_open_browser_scheme_gate_even_without_server_url(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
    with pytest.raises(UnsafeMcpUrl):
        await mcp_oauth._open_browser("file:///tmp/x")
    assert opened == []
    assert mcp_oauth.last_authorize_url is None


@pytest.mark.asyncio
async def test_refuse_browser_does_not_store_file_url():
    with pytest.raises(UnsafeMcpUrl):
        await mcp_oauth._refuse_browser("file:///tmp/x")
    assert mcp_oauth.last_authorize_url is None


# -- SDK auth-flow wrap: refuse before httpx sees the request ------------------


@pytest.mark.asyncio
async def test_auth_flow_refuses_metadata_discovery_url_before_yield(tmp_path, monkeypatch):
    """The SDK yields a Request for resource_metadata taken from WWW-Authenticate.
    We must raise *before* yielding so httpx never sends it."""

    async def evil_flow(self, request):
        yield httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")

    monkeypatch.setattr(OAuthClientProvider, "async_auth_flow", evil_flow)
    secrets = SecretStore(tmp_path / "s.json")
    auth = mcp_oauth.build_auth("evil", SERVER, secrets, interactive=False)
    flow = auth.async_auth_flow(httpx.Request("POST", SERVER))
    with pytest.raises(UnsafeMcpUrl, match="link-local"):
        await flow.asend(None)
    await flow.aclose()


@pytest.mark.asyncio
async def test_auth_flow_allows_same_origin_yield(tmp_path, monkeypatch):
    async def ok_flow(self, request):
        yield httpx.Request("GET", "https://mcp.example.com/.well-known/oauth-protected-resource")

    monkeypatch.setattr(OAuthClientProvider, "async_auth_flow", ok_flow)
    secrets = SecretStore(tmp_path / "s.json")
    auth = mcp_oauth.build_auth("ok", SERVER, secrets, interactive=False)
    flow = auth.async_auth_flow(httpx.Request("POST", SERVER))
    sent = await flow.asend(None)
    assert str(sent.url).startswith("https://mcp.example.com/")
    await flow.aclose()


# -- httpx request hook (redirect hops that never re-enter Auth) ---------------


@pytest.mark.asyncio
async def test_factory_installs_a_request_hook_that_refuses_metadata_urls():
    client = mcp_httpx_client_factory(SERVER)()
    hooks = client.event_hooks.get("request") or []
    assert hooks, "factory must install a request hook"
    hook = hooks[0]
    await hook(httpx.Request("GET", "https://mcp.example.com/mcp"))  # same origin
    with pytest.raises(UnsafeMcpUrl, match="link-local"):
        await hook(httpx.Request("GET", "http://169.254.169.254/latest/meta-data/"))
    await client.aclose()


@pytest.mark.asyncio
async def test_request_hook_blocks_redirect_to_metadata_before_transport_sees_it():
    """follow_redirects=True would otherwise GET Location without Auth seeing it."""
    hops: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if "169.254" in str(request.url):
            pytest.fail("metadata request reached the transport")
        return httpx.Response(
            302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
        )

    async def hook(request: httpx.Request) -> None:
        refuse_destination(str(request.url), SERVER, what="MCP HTTP request")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"request": [hook]},
    ) as client:
        with pytest.raises(UnsafeMcpUrl, match="link-local"):
            await client.get(
                "https://mcp.example.com/.well-known/oauth-protected-resource"
            )
    assert hops == [
        "https://mcp.example.com/.well-known/oauth-protected-resource"
    ]
    assert not any("169.254" in h for h in hops)


@pytest.mark.asyncio
async def test_factory_client_allows_same_origin_request():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    factory = mcp_httpx_client_factory(SERVER)
    probe = factory()
    hook = probe.event_hooks["request"][0]
    await probe.aclose()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"request": [hook]},
    ) as client:
        r = await client.get("https://mcp.example.com/mcp")
        assert r.status_code == 200
