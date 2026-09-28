"""Settings ▸ Sandbox: the machine-level snapshot and updates behind /v1/settings/sandbox."""

from __future__ import annotations

import sys
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
    native = "windows" if sys.platform == "win32" else "seatbelt"  # the operating system's own sandbox
    assert [p["name"] for p in snap["providers"]] == ["direct", native, "openshell"]
    assert next(p for p in snap["providers"] if p["name"] == "direct")["usable"]
    from coworker.sandbox import network_profiles

    assert snap["network_profile"] == network_profiles.default_profile()  # strict; `open` on Windows
    assert [n["name"] for n in snap["network_profiles"]] == ["strict", "standard", "open"]
    assert [(e["name"], e["enabled"]) for e in snap["credentials"]] == [("ssh", False), ("gh", False), ("aws", False), ("kube", False)]
    assert all(e["shipped"] and "kind" in e and e["label"] in ("credential", "configuration") for e in snap["credentials"])
    assert snap["config_path"] == str(config_file)
    assert (snap["windows_setup"] is None) == (sys.platform != "win32")
    from coworker.sandbox import toolchains

    assert [e["name"] for e in snap["toolchains"]] == [e["name"] for e in toolchains.defaults()]
    assert all(e["shipped"] and e["enabled"] and "exists" in e for e in snap["toolchains"])


def test_toolchain_switches_and_additions_are_written_slim(config_file):
    from coworker.sandbox import toolchains

    rows = [dict(e) for e in toolchains.defaults()]
    if rows:
        rows[0]["enabled"] = False
    rows.append({"name": "mytools", "title": "My tools", "path": "~/tools", "enabled": True})
    out = settings.update({"toolchains": rows})
    assert out["ok"], out
    text = config_file.read_text()
    assert text.count("[[sandbox_toolchains]]") == (2 if rows[:-1] else 1)  # one switch, one addition; nothing else
    assert 'name = "mytools"' in text and 'path = "~/tools"' in text
    by = {e["name"]: e for e in settings.snapshot()["toolchains"]}
    assert by["mytools"]["enabled"] and not by["mytools"]["shipped"]
    if rows[:-1]:
        assert by[rows[0]["name"]]["enabled"] is False
    assert settings.update({"toolchains": [{"name": "x", "path": "relative/path"}]})["ok"] is False


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
    assert settings.update({"credentials": [{"name": "x", "enabled": True, "path": "~/x", "label": "secret"}]})["ok"] is False
    assert not config_file.exists()


def test_a_label_is_written_and_read_back(config_file):
    out = settings.update({"credentials": [{"name": "aws", "enabled": True, "path": "~/.aws", "label": "credential"}, {"name": "npmrc", "enabled": True, "path": "~/.npmrc", "label": "configuration"}]})
    assert out["ok"], out
    by = {e["name"]: e for e in settings.snapshot()["credentials"]}
    assert by["aws"]["label"] == "credential" and by["aws"]["path"] == "~/.aws" and by["npmrc"]["label"] == "configuration"
    assert by["gh"]["label"] == "credential" and by["gh"]["shipped"] and not by["npmrc"]["shipped"]


def test_windows_setup_runs_checks_and_only_then_chooses_the_sandbox(config_file, monkeypatch):
    """UX-053: "Set up now" runs the elevated setup, proves the wall in a throwaway sandbox,
    and makes the Windows sandbox the choice; a failed check leaves the choice alone."""
    from types import SimpleNamespace

    from coworker.sandbox import selection
    from coworker.sandbox.providers import windows_setup

    monkeypatch.setattr(settings.sys, "platform", "win32")
    # Off Windows the provider module cannot even import; the snapshot's two probes are stubbed.
    monkeypatch.setattr(settings, "_availability", lambda name: (True, "", "ready"))
    monkeypatch.setattr(selection, "select", lambda name=None: SimpleNamespace(provider=name or "direct"))
    monkeypatch.setattr(windows_setup, "info", lambda: {"state": "ready", "set_up_at": "2026-09-28T10:00:00Z", "problem": "", "can_elevate": True, "command": "x"})
    monkeypatch.setattr(windows_setup, "run_setup", lambda: (True, "ok S-1 S-2"))
    monkeypatch.setattr(settings, "_throwaway_check", lambda: (False, "the home folder can be listed from inside"))
    out = settings.run_windows_setup()
    assert not out["ok"] and "throwaway sandbox failed" in out["error"] and "sandbox_provider" not in (config_file.read_text() if config_file.exists() else "")
    monkeypatch.setattr(settings, "_throwaway_check", lambda: (True, "proved"))
    out = settings.run_windows_setup()
    assert out["ok"] and out["checked"] == "proved" and out["provider"] == "windows" and out["windows_setup"]["state"] == "ready"
    monkeypatch.setattr(windows_setup, "run_setup", lambda: (False, "The operation was canceled by the user."))
    out = settings.run_windows_setup()
    assert not out["ok"] and "canceled" in out["error"] and out["provider"] == "windows"  # the earlier choice stands
    monkeypatch.setattr(windows_setup, "run_remove", lambda: (True, "removed"))
    out = settings.run_windows_remove()
    assert out["ok"] and out["provider"] == "direct"
    monkeypatch.setattr(settings.sys, "platform", "darwin")
    assert not settings.run_windows_setup()["ok"] and not settings.run_windows_remove()["ok"]


def test_windows_setup_info_states(monkeypatch):
    from coworker.sandbox.providers import windows_setup

    monkeypatch.setattr(windows_setup, "can_elevate", lambda: True)
    monkeypatch.setattr(windows_setup, "state", lambda: None)
    assert windows_setup.info()["state"] == "not_set_up"
    monkeypatch.setattr(windows_setup, "state", lambda: {"version": 1})
    assert windows_setup.info()["state"] == "older"
    monkeypatch.setattr(windows_setup, "state", lambda: {"version": windows_setup.SETUP_VERSION, "set_up_at": "2026-09-28T10:00:00Z"})
    monkeypatch.setattr(windows_setup, "problem", lambda: "the sandbox account for the open network mode is not usable by this user")
    info = windows_setup.info()
    assert info["state"] == "broken" and info["set_up_at"] == "2026-09-28T10:00:00Z" and info["can_elevate"] and info["command"]
    monkeypatch.setattr(windows_setup, "problem", lambda: None)
    assert windows_setup.info()["state"] == "ready"


def test_removing_a_shipped_entry_and_going_back_to_the_default_rule(config_file):
    settings.update({"provider": "direct", "credentials": [{"name": "ssh", "enabled": True}]})
    assert 'sandbox_provider = "direct"' in config_file.read_text()
    out = settings.update({"provider": "", "credentials": []})
    assert out["ok"] and out["provider"] == "" and out["effective_provider"] == "direct"
    text = config_file.read_text()
    assert "sandbox_provider" not in text and "[[sandbox_credentials]]" not in text


def test_a_missing_base_image_shows_as_needs_download_not_as_ready(config_file, monkeypatch):
    # OPE-205: the page said "ready" while the first session was bound to hang on the
    # one-time 5 GB download. Now that state has a name of its own, and the refusal a
    # session would get carries the same message.
    from coworker.sandbox import selection
    from coworker.sandbox.providers.openshell import IMAGE_MISSING_PREFIX

    message = f"{IMAGE_MISSING_PREFIX} (about 5 GB, one time). Run `openworker machine sandbox setup`."
    monkeypatch.setattr(selection, "openshell_problem", lambda fresh=False: message)
    snap = settings.snapshot()
    row = next(p for p in snap["providers"] if p["name"] == "openshell")
    assert row == {"name": "openshell", "usable": False, "why": message, "state": "needs_download"}
    assert next(p for p in snap["providers"] if p["name"] == "direct")["state"] == "ready"
    assert settings.update({"provider": "openshell"})["ok"]  # the choice is allowed; the download is what is missing
    assert "not downloaded yet" in settings.snapshot()["refused"]
    monkeypatch.setattr(selection, "openshell_problem", lambda fresh=False: "The OpenShell gateway is not running.")
    assert next(p for p in settings.snapshot()["providers"] if p["name"] == "openshell")["state"] == "unavailable"


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


def test_changing_the_provider_rebuilds_live_engines_built_under_the_old_rule(tmp_path, monkeypatch):
    """Seen 2026-09-28 on WSL: sessions opened before the switch kept running direct while
    the page said sessions were refused. A provider change now drops idle engines built
    under another provider (a running one is rebuilt when its turn ends) and the response
    names them, so the app reconnects the one on screen."""
    from fastapi.testclient import TestClient

    from coworker.sandbox import settings as sandbox_settings
    from coworker.server import create_app
    from tests.test_persona_connections import _mgr

    monkeypatch.setattr(sandbox_settings, "openshell_problem", lambda fresh=False: "OpenShell is not installed", raising=False)
    from coworker.sandbox import selection

    monkeypatch.setattr(selection, "openshell_problem", lambda fresh=False: "OpenShell is not installed")
    mgr = _mgr(tmp_path, monkeypatch)

    class Direct:
        def describe(self):
            return {"provider": "direct", "enforcement": "none"}

    class Boxed:
        def describe(self):
            return {"provider": "openshell", "enforcement": "full"}

    class Engine:
        def __init__(self, ws):
            self.sandbox_workspace = ws

    closed: list[str] = []
    mgr._engines.update({"direct-idle": Engine(Direct()), "direct-busy": Engine(Direct()), "boxed": Engine(Boxed())})
    monkeypatch.setattr(mgr, "_close_sandbox", lambda engine: closed.append(type(engine.sandbox_workspace).__name__))
    mgr.mark_running("direct-busy")
    client = TestClient(create_app(mgr))
    res = client.post("/v1/settings/sandbox", json={"provider": "openshell"}).json()
    assert res["ok"] and sorted(res["rebuilt_sessions"]) == ["direct-busy", "direct-idle"]
    assert "direct-idle" not in mgr._engines  # dropped now: its next connection is rebuilt (here: refused)
    assert "direct-busy" in mgr._engines  # mid-turn: kept until the turn ends
    assert "boxed" in mgr._engines  # already under the new rule
    mgr.mark_idle("direct-busy")
    assert "direct-busy" not in mgr._engines
    # Switching off: the boxed engine is the odd one out now, and its sandbox is closed.
    res = client.post("/v1/settings/sandbox", json={"provider": ""}).json()
    assert res["ok"] and res["rebuilt_sessions"] == ["boxed"] and closed[-1] == "Boxed"
    # A save that does not touch the provider rebuilds nothing.
    mgr._engines["d"] = Engine(Direct())
    res = client.post("/v1/settings/sandbox", json={"network_profile": "strict"}).json()
    assert res["ok"] and "rebuilt_sessions" not in res and "d" in mgr._engines
