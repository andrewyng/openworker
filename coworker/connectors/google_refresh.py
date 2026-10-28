"""Local renewal for manual Google connector tokens (#753).

A Google access token lives about an hour, so a pasted one is dead by the next
sit-down — and the managed one-click that renews automatically is paused
pending Google CASA verification, so manual paste is the only way in today. The
manual form therefore takes an optional refresh token (plus the OAuth client it
belongs to); renewal then runs HERE, against Google's own token endpoint —
credentials stay in this machine's secret store, the local-first arrangement the
issue asks for, not a cloud broker in disguise.

The boundary with `cloud.ensure_fresh_connector_token` is deliberate and each
side holds one half: the broker renews MANAGED profiles and never touches manual
ones; this renews MANUAL ones and never touches managed. Neither helper guessing
about the other's profiles keeps the two paths from drifting apart.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from ..secrets import SecretStore

GOOGLE_CONNECTORS = ("gmail", "google_calendar", "google_drive")

_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _now() -> float:
    return time.time()


def ensure_fresh_google_token(
    secrets: SecretStore,
    connector: str,
    *,
    profile_key: Optional[str] = None,
    leeway: int = 120,
) -> None:
    """Refresh-on-expiry hook for connector tools: a MANUAL Google profile
    carrying a refresh token renews in place, locally. No-op for managed
    profiles (the broker owns those), for non-Google connectors (their refresh,
    if any, is not Google's endpoint), and for profiles with no refresh token
    (a pasted access token is used as-is until it dies)."""
    key = profile_key or f"{connector}:default"
    profile = secrets.get(key) or {}
    if profile.get("managed") or connector not in GOOGLE_CONNECTORS:
        return
    if not profile.get("refresh_token"):
        return
    expires = float(profile.get("expires") or 0)
    if expires and expires > _now() + leeway:
        return
    _refresh_local(secrets, key, profile)


def _refresh_local(
    secrets: SecretStore, key: str, profile: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Exchange the stored refresh token for a fresh access token, updating the
    profile in place.

    A manually pasted token carries no expiry — its remaining life is unknown
    and short — so the first tool call refreshes immediately and pins the clock
    from Google's `expires_in`; after that, renewal runs only inside the leeway
    window. A failed exchange leaves the profile untouched: the pasted access
    token may still be alive, and the tool call proceeds with it rather than
    failing here."""
    form: dict[str, Any] = {
        "grant_type": "refresh_token",
        "refresh_token": profile["refresh_token"],
    }
    for k in ("client_id", "client_secret"):
        if profile.get(k):
            form[k] = profile[k]
    try:
        resp = httpx.post(_TOKEN_URL, data=form, timeout=20)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    fresh = resp.json()
    profile["access_token"] = fresh.get("access_token", "")
    if fresh.get("refresh_token"):
        profile["refresh_token"] = fresh["refresh_token"]
    profile["expires"] = _now() + int(fresh.get("expires_in") or 3600) - 60
    secrets.put(key, profile)
    return profile
