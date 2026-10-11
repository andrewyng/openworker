"""SSRF controls for outbound MCP HTTP (OAuth discovery, token POSTs, redirects).

The user-chosen MCP server URL is trusted as an *origin* — local MCP servers are
real, and a LAN host the user pasted is a LAN host they meant to talk to. Every
other URL the client is steered at is untrusted: `WWW-Authenticate
resource_metadata`, `authorization_servers`, `registration_endpoint`,
`token_endpoint`, redirects the SDK follows (`follow_redirects=True`), and the
authorize page handed to `webbrowser.open`.

Untrusted destinations must be `http(s)` and must not resolve to the machine's
own network. The address policy is `coworker.web.guard` (loopback, RFC1918,
link-local / cloud metadata, CGNAT, multicast, reserved). Cross-origin public
hosts are allowed so federated authorization servers (Okta, Entra, Atlassian)
keep working.

Same-origin comparison is scheme + host + effective port. A local MCP that
redirects to a *different* port on loopback is refused — that is the same
confused-deputy shape as a metadata document pointing at 169.254.169.254.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from ..web.guard import check_url

logger = logging.getLogger(__name__)

# Endpoints the SDK (or we) actually fetch or POST credentials to. Extra URL
# fields on the AS metadata document are validated too so a future SDK bump
# cannot start calling them unvetted.
_METADATA_URL_ATTRS = (
    "authorization_endpoint",
    "token_endpoint",
    "registration_endpoint",
    "revocation_endpoint",
    "introspection_endpoint",
    "jwks_uri",
)


class UnsafeMcpUrl(RuntimeError):
    """The MCP HTTP client refused a destination (SSRF / non-http scheme)."""


def _origin(url: str) -> Optional[tuple[str, str, int]]:
    """(scheme, hostname, port) or None when the URL is not a usable http(s) origin."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return (parts.scheme.lower(), parts.hostname.lower(), port)


def require_http_url(url: str) -> Optional[str]:
    """None if `url` is http(s) with a host, else a refusal reason.

    Used as the last-line gate on `webbrowser.open` (no server origin in that
    helper) and as the first clause of `check_destination`.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "url must start with http:// or https://"
    if not parts.hostname:
        return "url has no host"
    return None


def check_destination(url: str, server_url: str) -> Optional[str]:
    """None if the MCP HTTP client may call `url`, else a human refusal reason.

    Same origin as `server_url` is always allowed (the user picked it). Any other
    destination is run through `guard.check_url`.
    """
    reason = require_http_url(url)
    if reason:
        return reason
    server_origin = _origin(server_url)
    dest_origin = _origin(url)
    if server_origin is not None and dest_origin == server_origin:
        return None
    return check_url(url)


def refuse_destination(url: str, server_url: str, *, what: str = "URL") -> None:
    """Raise `UnsafeMcpUrl` when `url` is not a safe destination; no-op otherwise."""
    reason = check_destination(url, server_url)
    if reason:
        logger.warning("mcp http: refusing %s %s (%s)", what, url, reason)
        raise UnsafeMcpUrl(f"refusing {what}: {reason}")


def metadata_endpoints(md: Any) -> list[str]:
    """Advertised http(s) endpoints on an OAuthMetadata (model or dict)."""
    urls: list[str] = []
    for key in _METADATA_URL_ATTRS:
        val = md.get(key) if isinstance(md, dict) else getattr(md, key, None)
        if val is None:
            continue
        urls.append(str(val))
    return urls


def check_metadata(md: Any, server_url: str) -> Optional[str]:
    """None if every advertised endpoint is a safe destination, else a reason."""
    for url in metadata_endpoints(md):
        reason = check_destination(url, server_url)
        if reason:
            return f"{url}: {reason}"
    return None


def refuse_metadata(md: Any, server_url: str, *, what: str = "OAuth metadata") -> None:
    reason = check_metadata(md, server_url)
    if reason:
        logger.warning("mcp http: refusing %s (%s)", what, reason)
        raise UnsafeMcpUrl(f"refusing {what}: {reason}")


def mcp_httpx_client_factory(server_url: str):
    """httpx client factory for `streamablehttp_client`.

    The SDK enables `follow_redirects=True` and never re-enters Auth on a
    Location hop, so a public metadata URL that 302s to loopback would skip an
    auth-flow-only check. A request hook sees every hop, including redirects,
    and raises before the socket is opened.
    """

    async def _vet_request(request: httpx.Request) -> None:
        refuse_destination(str(request.url), server_url, what="MCP HTTP request")

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "event_hooks": {"request": [_vet_request]},
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        else:
            kwargs["timeout"] = httpx.Timeout(30.0, read=300.0)
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    return factory
