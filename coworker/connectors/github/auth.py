"""Brokerless GitHub OAuth device authorization.

The OAuth client id is public configuration; the device code and resulting token
stay inside the local sidecar.  GUI callers only receive an opaque flow id plus
the user-facing code and verification URL.
"""

from __future__ import annotations

import math
import secrets as random
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ...secrets import SecretStore

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "OpenWorker",
}


@dataclass
class _PendingFlow:
    client_id: str
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float
    expires_at_monotonic: float
    next_poll_at: float


class GitHubDeviceAuth:
    """Owns in-memory device flows and persists completed user credentials."""

    def __init__(self, secrets: SecretStore) -> None:
        self._secrets = secrets
        self._flows: dict[str, _PendingFlow] = {}
        self._lock = threading.Lock()

    def start(self, client_id: str, scopes: str = "repo") -> dict[str, Any]:
        client_id = str(client_id or "").strip()
        if not client_id:
            return {
                "ok": False,
                "error": (
                    "GitHub device sign-in is not configured. "
                    "Set github_oauth_client_id in config.toml."
                ),
            }

        payload = {"client_id": client_id}
        normalized_scopes = " ".join(str(scopes or "").split())
        if normalized_scopes:
            payload["scope"] = normalized_scopes
        try:
            response = httpx.post(
                DEVICE_CODE_URL,
                data=payload,
                headers=_HEADERS,
                timeout=20.0,
            )
            data = _json_object(response)
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "error": "Could not reach GitHub to start sign-in."}

        if response.status_code >= 400 or data.get("error"):
            return {
                "ok": False,
                "error": _error_message(data, "GitHub rejected the device sign-in request."),
            }

        required = ("device_code", "user_code", "verification_uri", "expires_in")
        if not all(data.get(key) for key in required):
            return {"ok": False, "error": "GitHub returned an incomplete device sign-in response."}

        try:
            expires_in = max(1, int(data["expires_in"]))
            interval = max(1, int(data.get("interval") or 5))
        except (TypeError, ValueError):
            return {"ok": False, "error": "GitHub returned invalid device sign-in timing."}

        now = time.monotonic()
        flow_id = random.token_urlsafe(24)
        flow = _PendingFlow(
            client_id=client_id,
            device_code=str(data["device_code"]),
            user_code=str(data["user_code"]),
            verification_uri=str(data["verification_uri"]),
            interval=interval,
            expires_at=time.time() + expires_in,
            expires_at_monotonic=now + expires_in,
            # RFC 8628 §3.5 requires waiting the server-provided interval
            # before the first token request as well as every later request.
            next_poll_at=now + interval,
        )
        with self._lock:
            self._prune_locked(now)
            self._flows[flow_id] = flow
        return {
            "ok": True,
            "flow_id": flow_id,
            "user_code": flow.user_code,
            "verification_uri": flow.verification_uri,
            "expires_in": expires_in,
            "expires_at": flow.expires_at,
            "interval": interval,
        }

    def poll(self, flow_id: str) -> dict[str, Any]:
        flow_id = str(flow_id or "").strip()
        now = time.monotonic()
        with self._lock:
            flow = self._flows.get(flow_id)
            if flow is None:
                self._prune_locked(now)
                return {"ok": False, "state": "error", "error": "Device sign-in flow not found."}
            if now >= flow.expires_at_monotonic:
                self._flows.pop(flow_id, None)
                return {
                    "ok": False,
                    "state": "expired",
                    "error": "The GitHub device code expired. Start again.",
                }
            if now < flow.next_poll_at:
                return {
                    "ok": True,
                    "state": "pending",
                    "retry_after": max(1, math.ceil(flow.next_poll_at - now)),
                }
            # Reserve the next interval before releasing the lock so concurrent
            # GUI polls cannot make GitHub requests too quickly.
            flow.next_poll_at = now + flow.interval

        try:
            response = httpx.post(
                ACCESS_TOKEN_URL,
                data={
                    "client_id": flow.client_id,
                    "device_code": flow.device_code,
                    "grant_type": GRANT_TYPE,
                },
                headers=_HEADERS,
                timeout=20.0,
            )
            data = _json_object(response)
        except (httpx.HTTPError, ValueError):
            return {
                "ok": True,
                "state": "pending",
                "retry_after": flow.interval,
                "error": "GitHub is temporarily unreachable; retrying.",
            }

        token = str(data.get("access_token") or "")
        if response.status_code < 400 and token:
            identity = self._identity(token)
            if not identity.get("ok"):
                self.cancel(flow_id)
                return {"ok": False, "state": "error", "error": identity["error"]}
            # Linearize completion against cancellation. If cancel won the
            # lock while either GitHub request was in flight, never persist.
            with self._lock:
                if self._flows.get(flow_id) is not flow:
                    return {
                        "ok": False,
                        "state": "error",
                        "error": "Device sign-in was cancelled.",
                    }
                self._store_token(data, identity)
                self._flows.pop(flow_id, None)
            return {
                "ok": True,
                "state": "complete",
                "account": identity["login"],
            }

        error = str(data.get("error") or "")
        if error == "authorization_pending":
            return {"ok": True, "state": "pending", "retry_after": flow.interval}
        if error == "slow_down":
            with self._lock:
                current = self._flows.get(flow_id)
                if current is not None:
                    current.interval = max(
                        current.interval + 5,
                        _positive_int(data.get("interval"), current.interval + 5),
                    )
                    current.next_poll_at = time.monotonic() + current.interval
                    retry_after = current.interval
                else:
                    retry_after = flow.interval + 5
            return {"ok": True, "state": "pending", "retry_after": retry_after}
        if error in {"expired_token", "token_expired"}:
            self.cancel(flow_id)
            return {
                "ok": False,
                "state": "expired",
                "error": "The GitHub device code expired. Start again.",
            }
        if error == "access_denied":
            self.cancel(flow_id)
            return {
                "ok": False,
                "state": "denied",
                "error": "GitHub sign-in was cancelled.",
            }

        self.cancel(flow_id)
        return {
            "ok": False,
            "state": "error",
            "error": _error_message(data, "GitHub could not complete device sign-in."),
        }

    def cancel(self, flow_id: str) -> dict[str, Any]:
        with self._lock:
            removed = self._flows.pop(str(flow_id or ""), None) is not None
        return {"ok": True, "cancelled": removed}

    def _identity(self, token: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "OpenWorker",
                },
                timeout=20.0,
            )
            data = _json_object(response)
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "error": "Could not validate the GitHub account."}
        login = str(data.get("login") or "")
        if response.status_code >= 400 or not login or not data.get("id"):
            return {"ok": False, "error": "GitHub returned a token without a valid account."}
        return {"ok": True, "login": login, "id": str(data["id"])}

    def _store_token(self, token_data: dict[str, Any], identity: dict[str, Any]) -> None:
        existing = self._secrets.get("github:default") or {}
        relay = existing.get("mode") == "relay"
        profile = dict(existing) if relay else {}
        profile.update(
            {
                "type": "oauth",
                "auth_method": "device",
                "token": str(token_data["access_token"]),
                "token_type": str(token_data.get("token_type") or "bearer"),
                "scope": str(token_data.get("scope") or ""),
                "account": identity["login"],
                "account_id": identity["id"],
                "enabled": True,
            }
        )
        if token_data.get("expires_in"):
            profile["expires_at"] = time.time() + _positive_int(
                token_data["expires_in"], 0
            )
        else:
            profile.pop("expires_at", None)
        if token_data.get("refresh_token"):
            profile["refresh_token"] = str(token_data["refresh_token"])
            profile["refresh_token_expires_at"] = time.time() + _positive_int(
                token_data.get("refresh_token_expires_in"), 0
            )
        else:
            profile.pop("refresh_token", None)
            profile.pop("refresh_token_expires_at", None)
        self._secrets.put("github:default", profile)

    def _prune_locked(self, now: float) -> None:
        expired = [
            flow_id
            for flow_id, flow in self._flows.items()
            if now >= flow.expires_at_monotonic
        ]
        for flow_id in expired:
            self._flows.pop(flow_id, None)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


def _positive_int(value: Any, fallback: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


def _error_message(data: dict[str, Any], fallback: str) -> str:
    code = str(data.get("error") or "")
    messages = {
        "device_flow_disabled": (
            "GitHub Device Flow is disabled for this OAuth app. "
            "Ask the app owner to enable it."
        ),
        "incorrect_client_credentials": "The configured GitHub OAuth client ID is invalid.",
        "incorrect_device_code": "GitHub rejected the device code. Start again.",
        "unsupported_grant_type": "GitHub rejected the device authorization grant.",
    }
    return messages.get(code) or str(data.get("error_description") or fallback)
