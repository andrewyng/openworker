"""Browser OAuth for remote MCP servers (OAuth 2.1 + PKCE + Dynamic Client Registration).

The official SDK's `OAuthClientProvider` drives the whole spec flow — protected-resource
metadata discovery, DCR, PKCE, token refresh — as an httpx auth plugged into the
streamable-HTTP transport. We supply its three integration points:

  - token persistence  → the SecretStore (profile `mcp-oauth:<server>`; 0600 file,
    never the mcp.json config, which is plain text and paste-shareable)
  - redirect           → open the system browser at the authorize URL
  - callback           → the sidecar's loopback `GET /mcp/oauth/callback` resolves a
    single-slot pending future (one interactive sign-in at a time — the flow is
    user-driven, so concurrency is meaningless)

DCR means there is no client id/secret registered anywhere up front — nothing for the
ocw-connect broker to hold, so unlike the managed connectors this flow is fully local.
First server: Granola (https://mcp.granola.ai/mcp).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from ..secrets import SecretStore

logger = logging.getLogger(__name__)

PROFILE_PREFIX = "mcp-oauth:"
CALLBACK_PATH = "/mcp/oauth/callback"
# How long the connect waits for the user to finish the browser sign-in.
FLOW_TIMEOUT_SECONDS = 300

CLIENT_NAME = "OpenWorker"


def redirect_base() -> str:
    """The sidecar's own loopback origin — the DCR-registered redirect must match it."""
    port = os.environ.get("COWORKER_PORT") or "8765"
    return f"http://127.0.0.1:{port}"


def _profile(name: str) -> str:
    return PROFILE_PREFIX + name


class SecretStoreTokenStorage(TokenStorage):
    """SDK TokenStorage over our SecretStore: one profile per server holding the token
    set and the DCR-issued client registration (re-used across sign-ins)."""

    def __init__(self, server_name: str, secrets: SecretStore) -> None:
        self._name = server_name
        self._secrets = secrets

    def _data(self) -> dict[str, Any]:
        return self._secrets.get(_profile(self._name)) or {}

    def _merge(self, patch: dict[str, Any]) -> None:
        self._secrets.put(_profile(self._name), {**self._data(), **patch})

    async def get_tokens(self) -> Optional[OAuthToken]:
        raw = self._data().get("tokens")
        if not raw:
            return None
        try:
            return OAuthToken.model_validate(raw)
        except Exception:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._merge({"tokens": tokens.model_dump(mode="json", exclude_none=True)})

    async def get_client_info(self) -> Optional[OAuthClientInformationFull]:
        raw = self._data().get("client_info")
        if not raw:
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None

    async def set_client_info(self, info: OAuthClientInformationFull) -> None:
        self._merge({"client_info": info.model_dump(mode="json", exclude_none=True)})


# -- single-slot interactive flow ------------------------------------------------
_pending: Optional[asyncio.Future] = None
# The last authorize URL we sent the user to — surfaced over REST so the GUI can offer
# a "reopen sign-in page" link if the browser popup was lost.
last_authorize_url: Optional[str] = None


def deliver_callback(code: str, state: Optional[str]) -> bool:
    """Called by the loopback route. Resolves the waiting flow; False if none waits."""
    global _pending
    pending, _pending = _pending, None
    if pending is None or pending.done():
        return False
    pending.set_result((code, state))
    return True


async def _open_browser(url: str) -> None:
    global last_authorize_url
    last_authorize_url = url
    import webbrowser

    logger.info("mcp oauth: opening browser for sign-in")
    await asyncio.get_running_loop().run_in_executor(None, webbrowser.open, url)


async def _wait_for_callback() -> tuple[str, Optional[str]]:
    global _pending
    if _pending is not None and not _pending.done():
        _pending.cancel()  # a stale flow lost its browser tab; the new one wins
    _pending = asyncio.get_running_loop().create_future()
    try:
        return await asyncio.wait_for(_pending, timeout=FLOW_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise RuntimeError(
            "sign-in timed out — the browser window was not completed in "
            f"{FLOW_TIMEOUT_SECONDS // 60} minutes"
        )
    finally:
        _pending = None


def build_auth(
    server_name: str, server_url: str, secrets: SecretStore
) -> OAuthClientProvider:
    """The httpx auth for one OAuth MCP server (pass as streamablehttp_client(auth=…))."""
    metadata = OAuthClientMetadata.model_validate(
        {
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_base() + CALLBACK_PATH],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            # Public client: DCR issues no secret a native app could keep anyway.
            "token_endpoint_auth_method": "none",
        }
    )
    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=SecretStoreTokenStorage(server_name, secrets),
        redirect_handler=_open_browser,
        callback_handler=_wait_for_callback,
    )


def has_tokens(server_name: str, secrets: SecretStore) -> bool:
    return bool((secrets.get(_profile(server_name)) or {}).get("tokens"))


def sign_out(server_name: str, secrets: SecretStore) -> bool:
    """Forget tokens AND the DCR registration; next connect runs a fresh flow."""
    return secrets.delete(_profile(server_name))
