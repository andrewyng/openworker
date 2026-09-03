"""Subscription sign-in for the `openai-codex` provider (OAuth 2.0 + PKCE).

Instead of an API key, the user signs in with their ChatGPT plan: a browser flow
against the vendor's auth service using their public subscription client id, with the
loopback redirect that id is registered for (the port is FIXED — any other port fails
the redirect-uri check server-side). Tokens land in the SecretStore profile
`provider:openai-codex` — the same local-only storage every provider profile uses,
never a plaintext config file — mirroring `mcp/oauth.py` (tokens + `tokens_issued_at`).

The pieces:

  - `sign_in()`         — async, explicit-action only: bind the loopback port, open the
    browser, wait for the redirect, exchange the code, persist tokens + account id.
  - `CodexTokenStore`   — persistence + proactive refresh (JWT `exp`, sync httpx: the
    provider is called from engine worker threads, `asyncio.to_thread` like its peers).
  - `verify()`          — the Test-button probe: one cheap authenticated request that
    distinguishes signed-out vs expired vs OK.

The account id rides the token JWTs (the `https://api.openai.com/auth` claim); we
decode without verification — the backend verifies the token, we only route with it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets as pysecrets
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

logger = logging.getLogger(__name__)

AUTH_ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = AUTH_ISSUER + "/oauth/authorize"
TOKEN_URL = AUTH_ISSUER + "/oauth/token"
# The public subscription client id (ships in the vendor's own tooling — not a secret).
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
# Registered redirect for CLIENT_ID, verbatim — host and port are not ours to choose.
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPE = "openid profile email offline_access"
ORIGINATOR = "openworker"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
PROFILE = "provider:openai-codex"
FLOW_TIMEOUT_SECONDS = 300
# Refresh this close to the JWT `exp` instead of sending an about-to-die bearer.
REFRESH_MARGIN_SECONDS = 300
_ACCOUNT_CLAIM = "https://api.openai.com/auth"
# Smallest curated model — the verify probe should cost as close to nothing as possible.
_VERIFY_MODEL = "gpt-5.1-codex-mini"

SIGNED_OUT_ERROR = (
    "Not signed in to ChatGPT — connect your account in Settings ▸ Models to use "
    "the subscription provider."
)
EXPIRED_ERROR = "ChatGPT session expired — sign in again in Settings ▸ Models."
PLAN_LIMIT_ERROR = (
    "ChatGPT plan limit reached — your subscription's rolling usage window (about "
    "5 hours) is used up. Wait for it to reset, upgrade the plan, or switch to an "
    "API-key provider."
)
PORT_BUSY_ERROR = (
    f"Port {CALLBACK_PORT} is already in use — the OpenAI Codex CLI is the usual "
    "holder. Quit it and start the sign-in again."
)


class CodexAuthError(RuntimeError):
    """A subscription-auth failure with a user-readable message."""


class CodexSignInRequired(CodexAuthError):
    """No usable tokens — the fix is an explicit sign-in, never a silent browser."""


# -- PKCE / JWT helpers -----------------------------------------------------------


def create_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge) per RFC 7636."""
    verifier = pysecrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(state: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # The simplified-flow switch the subscription client id expects, plus the
        # client-name tag the backend requires on every call.
        "codex_cli_simplified_flow": "true",
        "originator": ORIGINATOR,
    }
    return AUTHORIZE_URL + "?" + urlencode(params)


def _jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload WITHOUT verification — we only read routing claims
    (`exp`, the account object); the backend is the one verifying signatures."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def account_id_from(tokens: dict[str, Any]) -> str:
    """The ChatGPT account id, from the auth claim of the id/access token."""
    for key in ("id_token", "access_token"):
        auth = _jwt_claims(tokens.get(key) or "").get(_ACCOUNT_CLAIM) or {}
        if isinstance(auth, dict):
            acct = auth.get("chatgpt_account_id") or auth.get("account_id") or ""
            if acct:
                return str(acct)
    return ""


def backend_headers(account_id: str, session_id: str) -> dict[str, str]:
    """The non-auth headers every backend request must carry (auth is the bearer)."""
    return {
        "chatgpt-account-id": account_id,
        "originator": ORIGINATOR,
        "OpenAI-Beta": "responses=experimental",
        "session-id": session_id,
    }


# -- token persistence + refresh ----------------------------------------------------


def _token_post(data: dict[str, str], timeout: float = 30.0) -> Any:
    """One POST to the token endpoint (module-level so tests stub the wire here)."""
    import httpx

    return httpx.post(
        TOKEN_URL, data=data, headers={"Accept": "application/json"}, timeout=timeout
    )


def exchange_code(code: str, verifier: str, timeout: float = 30.0) -> dict[str, Any]:
    """authorization_code + PKCE verifier → the token set. Blocking (httpx sync);
    `sign_in` runs it via `asyncio.to_thread`."""
    resp = _token_post(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
        timeout,
    )
    if resp.status_code >= 300:
        raise CodexAuthError(
            f"Sign-in failed — token exchange returned HTTP {resp.status_code}."
        )
    return resp.json()


class CodexTokenStore:
    """Token set + account metadata in the `provider:openai-codex` SecretStore profile.

    `access_token()` is what the provider calls per request: it hands back a live
    bearer, refreshing proactively near the JWT `exp` and clearing the profile to a
    clean signed-out state when the refresh token is rejected — never a crash loop.
    """

    def __init__(self, secrets: Any) -> None:
        self._secrets = secrets

    def _data(self) -> dict[str, Any]:
        if self._secrets is None:
            return {}
        return self._secrets.get(PROFILE) or {}

    def _merge(self, patch: dict[str, Any]) -> None:
        self._secrets.put(PROFILE, {**self._data(), **patch})

    def signed_in(self) -> bool:
        return bool(self._data().get("tokens"))

    def account_label(self) -> Optional[str]:
        data = self._data()
        return data.get("account_email") or data.get("account_id") or None

    def save(self, tokens: dict[str, Any]) -> None:
        """Persist a token response, keeping prior values a refresh omitted (the
        refresh grant often returns no new refresh/id token)."""
        existing = self._data().get("tokens") or {}
        merged = {
            k: (tokens.get(k) or existing.get(k))
            for k in ("access_token", "refresh_token", "id_token")
        }
        merged = {k: v for k, v in merged.items() if v}
        patch: dict[str, Any] = {
            "tokens": merged,
            "tokens_issued_at": int(time.time()),
        }
        account_id = account_id_from(merged) or self._data().get("account_id")
        if account_id:
            patch["account_id"] = account_id
        email = _jwt_claims(merged.get("id_token") or "").get("email") or self._data().get(
            "account_email"
        )
        if email:
            patch["account_email"] = email
        self._merge(patch)

    def clear(self) -> bool:
        if self._secrets is None:
            return False
        return bool(self._secrets.delete(PROFILE))

    def access_token(self) -> tuple[str, str]:
        """(live access token, account id) — refreshing first when stale/absent."""
        data = self._data()
        tokens = data.get("tokens") or {}
        access = tokens.get("access_token") or ""
        if not access and not tokens.get("refresh_token"):
            raise CodexSignInRequired(SIGNED_OUT_ERROR)
        exp = _jwt_claims(access).get("exp")
        stale = not access or (
            isinstance(exp, (int, float)) and exp - time.time() < REFRESH_MARGIN_SECONDS
        )
        if stale:
            return self.refresh()
        return access, data.get("account_id") or ""

    def refresh(self) -> tuple[str, str]:
        """refresh_token grant → fresh (access token, account id). A rejected refresh
        token blanks the profile — the provider reads as cleanly signed out."""
        refresh = (self._data().get("tokens") or {}).get("refresh_token") or ""
        if not refresh:
            self.clear()
            raise CodexSignInRequired(EXPIRED_ERROR)
        try:
            resp = _token_post(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": CLIENT_ID,
                }
            )
        except Exception as exc:
            raise CodexAuthError(
                "Couldn't reach the sign-in service to refresh the ChatGPT session "
                f"({exc.__class__.__name__})."
            ) from exc
        if 400 <= resp.status_code < 500:
            self.clear()
            raise CodexSignInRequired(EXPIRED_ERROR)
        if resp.status_code >= 300:
            raise CodexAuthError(
                f"ChatGPT session refresh failed (HTTP {resp.status_code}) — try again."
            )
        self.save(resp.json())
        data = self._data()
        return (data.get("tokens") or {}).get("access_token") or "", (
            data.get("account_id") or ""
        )


# -- interactive sign-in flow -------------------------------------------------------

# The last authorize URL, surfaced over REST so the GUI can offer "reopen sign-in
# page" if the popup was lost (same affordance as mcp/oauth.py).
last_authorize_url: Optional[str] = None
_active_server: Optional[asyncio.AbstractServer] = None

_PAGE = """<!doctype html><meta charset="utf-8"><title>OpenWorker</title>
<body style="font-family: system-ui; margin: 4rem auto; max-width: 28rem; text-align: center;">
<h2>{title}</h2><p>{body}</p></body>"""


def _http_response(status: str, title: str, body: str) -> bytes:
    html = _PAGE.format(title=title, body=body).encode("utf-8")
    head = (
        f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(html)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + html


async def _start_callback_server(
    expected_state: str,
) -> tuple[asyncio.AbstractServer, "asyncio.Future[str]"]:
    """Bind the fixed loopback port and resolve the future with the auth code when
    the redirect (carrying the matching `state`) lands."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            while True:  # drain headers; the redirect is a bare GET
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            parts = request_line.decode("ascii", errors="replace").split()
            target = urlsplit(parts[1] if len(parts) > 1 else "/")
            if target.path != CALLBACK_PATH:
                writer.write(_http_response("404 Not Found", "Not found", ""))
                return
            query = parse_qs(target.query)
            error = (query.get("error") or [""])[0]
            code = (query.get("code") or [""])[0]
            state = (query.get("state") or [""])[0]
            if error:
                writer.write(
                    _http_response(
                        "400 Bad Request",
                        "Sign-in failed",
                        "The service reported an error. Return to OpenWorker and try again.",
                    )
                )
                if not future.done():
                    future.set_exception(
                        CodexAuthError(f"Sign-in failed — the service returned: {error}")
                    )
                return
            # Same loopback gate as mcp/oauth.py: a stray local hit with the wrong
            # state must not consume the flow — only the genuine redirect resolves it.
            if not code or not pysecrets.compare_digest(state, expected_state):
                writer.write(
                    _http_response(
                        "400 Bad Request",
                        "Nothing waiting for this sign-in",
                        "The sign-in may have timed out. Return to OpenWorker and start it again.",
                    )
                )
                return
            writer.write(
                _http_response(
                    "200 OK",
                    "Signed in",
                    "You can close this tab and return to OpenWorker.",
                )
            )
            if not future.done():
                future.set_result(code)
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(handle, "127.0.0.1", CALLBACK_PORT)
    except OSError as exc:
        raise CodexAuthError(PORT_BUSY_ERROR) from exc
    return server, future


async def sign_in(
    secrets: Any,
    *,
    timeout: float = FLOW_TIMEOUT_SECONDS,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Run the full interactive flow: loopback server → browser → code → tokens.

    Explicit-action only (a Settings button) — never called from an engine turn, so
    unlike mcp/oauth.py it needs no non-interactive refusal path.
    """
    global last_authorize_url, _active_server
    if _active_server is not None:
        # A stale flow lost its browser tab; the new one takes the port.
        _active_server.close()
        await _active_server.wait_closed()
        _active_server = None
    verifier, challenge = create_pkce()
    state = pysecrets.token_urlsafe(24)
    url = build_authorize_url(state, challenge)
    last_authorize_url = url
    server, code_future = await _start_callback_server(state)
    _active_server = server
    try:
        if open_browser:
            import webbrowser

            logger.info("codex auth: opening browser for sign-in")
            await asyncio.get_running_loop().run_in_executor(None, webbrowser.open, url)
        try:
            code = await asyncio.wait_for(code_future, timeout)
        except asyncio.TimeoutError:
            raise CodexAuthError(
                "Sign-in timed out — the browser window was not completed in "
                f"{int(timeout) // 60} minutes."
            )
    finally:
        server.close()
        await server.wait_closed()
        if _active_server is server:
            _active_server = None
    tokens = await asyncio.to_thread(exchange_code, code, verifier)
    store = CodexTokenStore(secrets)
    store.save(tokens)
    if not (store._data().get("tokens") or {}).get("access_token"):
        store.clear()
        raise CodexAuthError("Sign-in failed — the token response had no access token.")
    return {"ok": True, "account": store.account_label()}


# -- verify probe -------------------------------------------------------------------


def verify(secrets: Any, timeout: float = 10.0) -> dict[str, Any]:
    """Test-button probe: one cheap authenticated request against the backend.

    Distinguishes signed-out (no/rejected tokens) vs expired (401 with a bearer we
    thought was live) vs OK. Never raises; {ok, error?, state?} like the other
    provider verifies.
    """
    import httpx

    store = CodexTokenStore(secrets)
    if not store.signed_in():
        return {"ok": False, "error": SIGNED_OUT_ERROR, "state": "signed_out"}
    try:
        token, account = store.access_token()
    except CodexSignInRequired as exc:
        return {"ok": False, "error": str(exc), "state": "signed_out"}
    except CodexAuthError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        resp = httpx.post(
            CODEX_BASE_URL + "/responses",
            headers={
                "Authorization": f"Bearer {token}",
                **backend_headers(account, str(uuid.uuid4())),
            },
            json={
                "model": _VERIFY_MODEL,
                "input": "Reply with OK.",
                "store": False,
                "stream": True,
                "max_output_tokens": 16,
            },
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Couldn't reach the ChatGPT backend ({exc.__class__.__name__}).",
        }
    if resp.status_code < 300:
        return {"ok": True, "account": store.account_label()}
    if resp.status_code in (401, 403):
        return {"ok": False, "error": EXPIRED_ERROR, "state": "expired"}
    if resp.status_code == 429:
        # Auth is fine — the plan window is just used up right now.
        return {"ok": True, "account": store.account_label(), "note": PLAN_LIMIT_ERROR}
    return {
        "ok": False,
        "error": f"The ChatGPT backend returned HTTP {resp.status_code}.",
    }
