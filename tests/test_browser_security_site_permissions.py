from __future__ import annotations

import json

import pytest

from coworker.browser_security.destination import DestinationPolicy
from coworker.browser_security.site_permissions import (
    BrowserSitePermissionStore,
    normalize_host,
)


def _store(tmp_path):
    return BrowserSitePermissionStore(
        tmp_path / "browser-settings.json",
        destination_policy=DestinationPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )


def test_default_asks_once_then_persists_exact_hostname(tmp_path):
    store = _store(tmp_path)

    first = store.evaluate_url("https://Example.COM/private?q=secret")
    assert first.needs_user is True
    assert first.host == "example.com"
    assert first.origin == "https://example.com"
    assert "private" not in first.reason
    assert "secret" not in first.reason

    store.allow_host(first.host)
    assert store.evaluate_url("https://example.com/elsewhere").allowed is True

    restarted = _store(tmp_path)
    assert restarted.evaluate_url("https://example.com").allowed is True
    assert restarted.settings()["allowed_hosts"] == ["example.com"]


def test_blocked_hostname_wins_over_allowed_and_modes(tmp_path):
    store = _store(tmp_path)
    settings = store.update(
        site_access_mode="allow",
        allowed_hosts=["example.com", "other.example"],
        blocked_hosts=["EXAMPLE.com"],
    )

    assert settings["allowed_hosts"] == ["other.example"]
    decision = store.evaluate_url("https://example.com/path")
    assert decision.allowed is False
    assert decision.needs_user is False
    assert decision.blocked is True


@pytest.mark.parametrize("mode", ["auto", "allow"])
def test_permissive_modes_allow_public_but_not_unsaved_local_hosts(
    tmp_path, mode
):
    store = _store(tmp_path)
    store.update(site_access_mode=mode)

    assert store.evaluate_url("https://public.example").allowed is True
    local = store.evaluate_url("http://127.0.0.1:8080")
    assert local.allowed is False
    assert local.needs_user is True
    assert local.is_public is False

    store.allow_host("127.0.0.1")
    assert store.evaluate_url("http://127.0.0.1:8080").allowed is True


def test_navigation_guard_fails_closed_when_ask_or_destination_invalid(tmp_path):
    store = _store(tmp_path)
    assert store.navigation_allowed("https://unknown.example") is False
    assert store.navigation_allowed("file:///etc/passwd") is False

    store.update(site_access_mode="auto")
    assert store.navigation_allowed("https://unknown.example") is True
    assert store.navigation_allowed("http://169.254.169.254/latest") is False


def test_settings_are_validated_normalized_and_written_owner_only(tmp_path):
    store = _store(tmp_path)
    settings = store.update(
        allowed_hosts=["BÜCHER.example", "https://example.com/a"],
        blocked_hosts=["blocked.example"],
        download_directory="~/Downloads/OpenWorker",
        ask_download_location=True,
        developer_mode=True,
    )

    assert settings["allowed_hosts"] == [
        "example.com",
        "xn--bcher-kva.example",
    ]
    assert settings["download_directory"].endswith(
        "/Downloads/OpenWorker"
    )
    assert settings["ask_download_location"] is True
    assert settings["developer_mode"] is True
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert persisted["allowed_hosts"] == settings["allowed_hosts"]
    assert store.path.stat().st_mode & 0o777 == 0o600

    with pytest.raises(ValueError):
        store.update(site_access_mode="sometimes")
    with pytest.raises(ValueError):
        store.update(allowed_hosts="example.com")
    with pytest.raises(ValueError):
        normalize_host("*.example.com")
