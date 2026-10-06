"""OpenRouter PKCE login. Account keys stay server-side; no refresh token is issued."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

PROFILE = "provider:openrouter-account"
BASE_URL = "https://openrouter.ai/api/v1"


async def exchange_key(code: str, verifier: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{BASE_URL}/auth/keys",
            json={
                "code": code,
                "code_verifier": verifier,
                "code_challenge_method": "S256",
            },
        )
        response.raise_for_status()
        key = response.json().get("key")
        if not isinstance(key, str) or not key.startswith("sk-or-"):
            raise ValueError("Invalid key response")
        # /models is public and cannot establish that a credential is valid.
        check = await client.get(
            f"{BASE_URL}/auth/key", headers={"Authorization": f"Bearer {key}"}
        )
        check.raise_for_status()
        return key


class OpenRouterAuth:
    """One sidecar's login state, accessed only on its asyncio event loop."""

    def __init__(self, secrets: Any, on_changed: Callable[[], None] = lambda: None):
        self.secrets = secrets
        self.on_changed = on_changed
        self.attempt_id: str | None = None
        self.verifier: str | None = None
        self.authorize_url: str | None = None
        self.error: str | None = None
        self.server: asyncio.Server | None = None
        self.timer: asyncio.TimerHandle | None = None
        self.exchanging = False

    def status(self) -> dict[str, Any]:
        profile = self.secrets.get("provider:openrouter") or {}
        return {
            "connected": bool((self.secrets.get(PROFILE) or {}).get("api_key")),
            "active": profile.get("auth_method") == "account"
            and bool(profile.get("account_connected")),
            "authorizing": self.attempt_id is not None,
            "attempt_id": self.attempt_id,
            "authorize_url": self.authorize_url,
            "error": self.error,
        }

    def cancel(self) -> dict[str, Any]:
        self.attempt_id = self.verifier = self.authorize_url = None
        self.exchanging = False
        if self.server:
            self.server.close()
            self.server = None
        if self.timer:
            self.timer.cancel()
            self.timer = None
        return self.status()

    def _expire(self) -> None:
        self.cancel()
        self.error = "Sign-in expired. Please try again."

    async def start(self, manual: bool = False) -> dict[str, Any]:
        self.cancel()
        self.error = None
        self.attempt_id = secrets.token_urlsafe(24)
        attempt = self.attempt_id
        self.verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        params = {"code_challenge": challenge, "code_challenge_method": "S256"}
        try:
            if manual:
                params["key_label"] = "OpenWorker"
            else:
                path = f"/callback/{attempt}"
                server = await asyncio.start_server(
                    lambda reader, writer: self._callback(
                        reader, writer, attempt, path
                    ),
                    "127.0.0.1",
                    0,
                )
                if attempt != self.attempt_id:
                    server.close()
                    return self.status()
                self.server = server
                port = self.server.sockets[0].getsockname()[1]
                params["callback_url"] = f"http://127.0.0.1:{port}{path}"
            self.authorize_url = "https://openrouter.ai/auth?" + urlencode(params)
            self.timer = asyncio.get_running_loop().call_later(600, self._expire)
        except Exception:
            if attempt == self.attempt_id:
                self.cancel()
                self.error = "Could not start sign-in. Try the manual code option."
        return self.status()

    async def _callback(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        attempt: str,
        path: str,
    ) -> None:
        status, message = "400 Bad Request", "Invalid callback."
        try:
            line = (await asyncio.wait_for(reader.readline(), 5)).decode("ascii")
            method, target, _ = line.split()
            parsed = urlsplit(target)
            if method == "GET" and parsed.path == path and attempt == self.attempt_id:
                query = parse_qs(parsed.query)
                code = query.get("code", [""])[0]
                if code:
                    result = await self.complete(code, attempt)
                    status = "200 OK" if not result["error"] else "400 Bad Request"
                    message = "Return to OpenWorker to see your connection status."
                elif query.get("error"):
                    self.cancel()
                    self.error = (
                        "OpenRouter authorization was declined. Please try again."
                    )
                    message = "Authorization declined. Return to OpenWorker."
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            pass
        finally:
            body = message.encode()
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            try:
                await writer.drain()
            except ConnectionError:
                pass
            writer.close()

    async def complete(self, code: Any, attempt_id: Any) -> dict[str, Any]:
        if (
            not attempt_id
            or attempt_id != self.attempt_id
            or self.exchanging
            or not self.verifier
        ):
            return {
                **self.status(),
                "error": "This sign-in attempt is no longer available.",
            }
        if not isinstance(code, str) or not code.strip() or len(code) > 4096:
            return {**self.status(), "error": "Enter a valid authorization code."}
        self.exchanging = True
        try:
            key = await exchange_key(code.strip(), self.verifier)
            if attempt_id != self.attempt_id:
                return self.status()
            self.secrets.put(PROFILE, {"api_key": key})
            profile = dict(self.secrets.get("provider:openrouter") or {})
            profile.update(auth_method="account", account_connected=True)
            self.secrets.put("provider:openrouter", profile)
            self.cancel()
            self.on_changed()
        except Exception:
            if attempt_id == self.attempt_id:
                self.cancel()
                self.error = "OpenRouter sign-in failed. The code may have expired; please try again."
        return self.status()

    def disconnect(self) -> dict[str, Any]:
        self.cancel()
        self.error = None
        self.secrets.delete(PROFILE)
        profile = dict(self.secrets.get("provider:openrouter") or {})
        profile["account_connected"] = False
        self.secrets.put("provider:openrouter", profile)
        self.on_changed()
        return self.status()
