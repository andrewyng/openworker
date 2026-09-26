"""Settings ▸ Sandbox: the machine-level snapshot and updates behind /v1/settings/sandbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker import config as app_config
from coworker.sandbox import settings


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    monkeypatch.setattr(app_config, "global_config_path", lambda: target)
    monkeypatch.delenv("OPENWORKER_SANDBOX_PROVIDER", raising=False)
    return target


def test_snapshot_reports_the_default_rule_and_the_shipped_entries(config_file):
    snap = settings.snapshot()
    assert snap["provider"] == "" and snap["effective_provider"] == "direct" and snap["refused"] == ""
    assert [p["name"] for p in snap["providers"]] == ["direct", "seatbelt", "openshell"]
    assert next(p for p in snap["providers"] if p["name"] == "direct")["usable"]
    assert snap["network_profile"] == "strict" and [n["name"] for n in snap["network_profiles"]] == ["strict", "standard"]
    assert [(e["name"], e["enabled"]) for e in snap["credentials"]] == [("ssh", False), ("gh", False), ("aws", False), ("kube", False)]
    assert snap["config_path"] == str(config_file)


def test_update_writes_only_what_differs_from_the_shipped_entries(config_file):
    out = settings.update(
        {
            "network_profile": "standard",
            "credentials": [
                {"name": "ssh", "enabled": True},
                {"name": "gh", "enabled": False},
                {"name": "aws", "enabled": True, "path": "~/.aws-work", "hosts": ["*.amazonaws.com:443"]},
                {"name": "npm", "enabled": True, "path": "~/.npmrc", "hosts": ["registry.npmjs.org:443"], "title": "npm"},
            ],
        }
    )
    assert out["ok"], out
    text = config_file.read_text()
    assert 'sandbox_network_profile = "standard"' in text
    assert text.count("[[sandbox_credentials]]") == 3  # gh is untouched, so not written
    assert 'path = "~/.aws-work"' in text and 'hosts = ["registry.npmjs.org:443"]' in text
    snap = settings.snapshot()
    by = {e["name"]: e for e in snap["credentials"]}
    assert by["ssh"]["enabled"] and not by["gh"]["enabled"] and by["aws"]["path"] == "~/.aws-work" and by["npm"]["title"] == "npm"
    assert app_config.load_config().sandbox_credentials[0] == {"name": "ssh", "enabled": True}


def test_update_refuses_bad_input_without_writing(config_file):
    assert settings.update({"provider": "bwrap"})["ok"] is False
    assert settings.update({"network_profile": "wide-open"})["ok"] is False
    assert settings.update({"credentials": [{"name": "x", "enabled": True, "path": "etc"}]})["ok"] is False
    assert settings.update({"credentials": [{"name": "x", "enabled": True, "hosts": ["nohost"]}]})["ok"] is False
    assert settings.update({"credentials": [{"enabled": True}]})["ok"] is False
    assert not config_file.exists()


def test_removing_a_shipped_entry_and_going_back_to_the_default_rule(config_file):
    settings.update({"provider": "direct", "credentials": [{"name": "ssh", "enabled": True}]})
    assert 'sandbox_provider = "direct"' in config_file.read_text()
    out = settings.update({"provider": "", "credentials": []})
    assert out["ok"] and out["provider"] == "" and out["effective_provider"] == "direct"
    text = config_file.read_text()
    assert "sandbox_provider" not in text and "[[sandbox_credentials]]" not in text


def test_an_explicit_provider_that_cannot_be_used_shows_as_refused(config_file, monkeypatch):
    from coworker.sandbox import selection
    from coworker.sandbox.providers.openshell import OpenShellUnavailable

    monkeypatch.setattr(selection, "openshell_problem", lambda fresh=False: "OpenShell is not installed")
    assert settings.update({"provider": "openshell"})["ok"]
    snap = settings.snapshot()
    assert snap["provider"] == "openshell" and snap["effective_provider"] == ""
    assert "no session will start" in snap["refused"] and "not installed" in snap["refused"]
    assert not next(p for p in snap["providers"] if p["name"] == "openshell")["usable"]
    with pytest.raises(OpenShellUnavailable):
        selection.select("openshell")
