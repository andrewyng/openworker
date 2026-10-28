"""#753: local renewal for manual Google connector tokens.

A pasted Google access token is dead in about an hour; with the managed
one-click paused (CASA), manual paste is the only way in. A manual profile
that carries a refresh token renews HERE, against Google's own endpoint —
credentials never leave the secret store. The boundary with the cloud
broker's renewal is tested from both sides: managed profiles are the
broker's, manual-refreshless ones are nobody's to renew.
"""

from __future__ import annotations

import time

import pytest

from coworker.connectors import google_refresh, integration_tools
from coworker.connectors.google_refresh import ensure_fresh_google_token
from coworker.connectors.setup import connect_connector
from coworker.secrets import SecretStore


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    return SecretStore()


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def _manual_google_profile(**extra) -> dict:
    return {
        "type": "oauth",
        "enabled": True,
        "access_token": "ya29.pasted",
        "refresh_token": "1//r",
        "client_id": "cid.apps.googleusercontent.com",
        "client_secret": "cs",
        **extra,
    }


def _no_network(monkeypatch):
    def boom(url, **kwargs):
        raise AssertionError("no token-endpoint call was expected")

    monkeypatch.setattr(google_refresh.httpx, "post", boom)


# --- renewal ------------------------------------------------------------------


def test_expired_manual_profile_renews_locally(secrets, monkeypatch):
    secrets.put("gmail:default", _manual_google_profile(expires=time.time() - 10))

    def fake_post(url, data=None, timeout=None):
        assert url == "https://oauth2.googleapis.com/token"
        assert data == {
            "grant_type": "refresh_token",
            "refresh_token": "1//r",
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "cs",
        }
        return FakeResponse(200, {"access_token": "ya29.fresh", "expires_in": 3600})

    monkeypatch.setattr(google_refresh.httpx, "post", fake_post)
    ensure_fresh_google_token(secrets, "gmail")
    profile = secrets.get("gmail:default")
    assert profile["access_token"] == "ya29.fresh"
    assert profile["expires"] > time.time() + 3400  # pinned from expires_in
    assert profile["refresh_token"] == "1//r"  # response omitted one — prior survives


def test_pasted_token_with_no_expiry_refreshes_on_first_use(secrets, monkeypatch):
    """A manually pasted token carries no clock, and its remaining life is
    unknown and short — the first tool call pins one from Google's expires_in."""
    secrets.put("gmail:default", _manual_google_profile())  # no `expires` key
    seen = {}

    def fake_post(url, data=None, timeout=None):
        seen["called"] = True
        return FakeResponse(200, {"access_token": "ya29.fresh", "expires_in": 3600})

    monkeypatch.setattr(google_refresh.httpx, "post", fake_post)
    ensure_fresh_google_token(secrets, "gmail")
    assert seen["called"]
    assert secrets.get("gmail:default")["access_token"] == "ya29.fresh"


def test_fresh_expiry_refreshes_nothing(secrets, monkeypatch):
    _no_network(monkeypatch)
    secrets.put(
        "gmail:default", _manual_google_profile(expires=time.time() + 3600)
    )
    ensure_fresh_google_token(secrets, "gmail")
    assert secrets.get("gmail:default")["access_token"] == "ya29.pasted"


def test_failed_exchange_leaves_the_profile_untouched(secrets, monkeypatch):
    """A 400 (revoked refresh token, bad client) must not clobber the profile —
    the pasted access token may still be alive, and the tool call proceeds."""
    secrets.put("gmail:default", _manual_google_profile(expires=time.time() - 10))
    before = secrets.get("gmail:default")
    monkeypatch.setattr(
        google_refresh.httpx,
        "post",
        lambda *a, **k: FakeResponse(400, {"error": "invalid_grant"}),
    )
    ensure_fresh_google_token(secrets, "gmail")
    assert secrets.get("gmail:default") == before


def test_rotated_refresh_token_is_kept(secrets, monkeypatch):
    secrets.put("gmail:default", _manual_google_profile(expires=time.time() - 10))
    monkeypatch.setattr(
        google_refresh.httpx,
        "post",
        lambda *a, **k: FakeResponse(
            200, {"access_token": "ya29.fresh", "refresh_token": "1//new",
                  "expires_in": 3600}
        ),
    )
    ensure_fresh_google_token(secrets, "gmail")
    assert secrets.get("gmail:default")["refresh_token"] == "1//new"


def test_network_error_is_swallowed_not_raised(secrets, monkeypatch):
    secrets.put("gmail:default", _manual_google_profile(expires=time.time() - 10))
    before = secrets.get("gmail:default")

    def down(url, **kwargs):
        raise google_refresh.httpx.ConnectError("offline")

    monkeypatch.setattr(google_refresh.httpx, "post", down)
    ensure_fresh_google_token(secrets, "gmail")
    assert secrets.get("gmail:default") == before


# --- the boundary with the broker --------------------------------------------


def test_managed_profiles_stay_with_the_broker(secrets, monkeypatch):
    """Managed renewal is the cloud broker's (cloud.py) — a manual helper must
    never touch those, exactly as the broker never touches manual ones."""
    _no_network(monkeypatch)
    secrets.put(
        "gmail:default",
        _manual_google_profile(managed=True, expires=time.time() - 10),
    )
    ensure_fresh_google_token(secrets, "gmail")
    assert secrets.get("gmail:default")["access_token"] == "ya29.pasted"


def test_profile_without_refresh_token_is_untouched(secrets, monkeypatch):
    _no_network(monkeypatch)
    profile = _manual_google_profile()
    profile.pop("refresh_token")
    secrets.put("gmail:default", profile)
    ensure_fresh_google_token(secrets, "gmail")
    assert secrets.get("gmail:default")["access_token"] == "ya29.pasted"


def test_non_google_connector_is_not_googles_to_answer(secrets, monkeypatch):
    """The generic account-profile hook serves every account-patterned
    connector; another vendor's refresh_token is not Google's to exchange."""
    _no_network(monkeypatch)
    secrets.put("jira:default", _manual_google_profile(expires=time.time() - 10))
    ensure_fresh_google_token(secrets, "jira")
    assert secrets.get("jira:default")["access_token"] == "ya29.pasted"


# --- the wiring ----------------------------------------------------------------


def test_gmail_tool_profile_renews_through_the_hook(secrets, monkeypatch):
    """The gmail tool layer reaches the renewal the same way it reaches the
    broker's: profile in place, fresh access token out — multi-account key
    (`gmail:account:<email>`) included, so the manual path inherits what the
    managed path already had."""
    secrets.put("gmail:default", _manual_google_profile())  # migrates on resolve

    def fake_post(url, data=None, timeout=None):
        return FakeResponse(200, {"access_token": "ya29.fresh", "expires_in": 3600})

    monkeypatch.setattr(google_refresh.httpx, "post", fake_post)
    email, profile, err = integration_tools._gmail_profile(secrets)
    assert err is None
    assert profile["access_token"] == "ya29.fresh"
    assert secrets.get(f"gmail:account:{email}")["access_token"] == "ya29.fresh"


def test_gcal_tool_profile_renews_through_the_hook(secrets, monkeypatch):
    secrets.put("google_calendar:default", _manual_google_profile())

    def fake_post(url, data=None, timeout=None):
        return FakeResponse(200, {"access_token": "ya29.fresh", "expires_in": 3600})

    monkeypatch.setattr(google_refresh.httpx, "post", fake_post)
    email, profile, err = integration_tools._gcal_profile(secrets)
    assert err is None and profile["access_token"] == "ya29.fresh"


def test_drive_tool_profile_renews_through_the_generic_hook(secrets, monkeypatch):
    """google_drive rides the generic account-profile helper — the gate must
    let the Google trio through it."""
    secrets.put("google_drive:default", _manual_google_profile())

    def fake_post(url, data=None, timeout=None):
        return FakeResponse(200, {"access_token": "ya29.fresh", "expires_in": 3600})

    monkeypatch.setattr(google_refresh.httpx, "post", fake_post)
    account_id, profile, err = integration_tools._account_profile(
        secrets, "google_drive", "", "access_token"
    )
    assert err is None and profile["access_token"] == "ya29.fresh"


# --- manual connect carries the new fields ----------------------------------


def test_manual_connect_stores_the_refresh_fields(secrets):
    out = connect_connector(
        secrets,
        "gmail",
        {
            "access_token": "ya29",
            "refresh_token": "1//r",
            "client_id": "cid",
            "client_secret": "cs",
        },
    )
    assert out["ok"]
    profile = secrets.get("gmail:default")
    assert profile["refresh_token"] == "1//r"
    assert profile["client_id"] == "cid"
    assert profile["client_secret"] == "cs"


def test_reconnect_without_refresh_fields_keeps_the_stored_ones(secrets):
    """Reconnect-safe is the existing rule for secrets (blank means keep) —
    the refresh trio must inherit it, not reset a working renewal."""
    connect_connector(
        secrets,
        "gmail",
        {
            "access_token": "ya29",
            "refresh_token": "1//r",
            "client_id": "cid",
            "client_secret": "cs",
        },
    )
    out = connect_connector(secrets, "gmail", {"access_token": "ya29-2"})
    assert out["ok"]
    profile = secrets.get("gmail:default")
    assert profile["access_token"] == "ya29-2"
    assert profile["refresh_token"] == "1//r"
    assert profile["client_secret"] == "cs"


def test_descriptors_offer_refresh_fields_on_the_google_trio():
    from coworker.connectors.descriptors import get_descriptor

    for name in ("gmail", "google_calendar", "google_drive"):
        keys = {f.key: f for f in get_descriptor(name).fields}
        assert "refresh_token" in keys and not keys["refresh_token"].required
        assert keys["refresh_token"].secret
        assert not keys["client_id"].required
        assert keys["client_secret"].secret and not keys["client_secret"].required
