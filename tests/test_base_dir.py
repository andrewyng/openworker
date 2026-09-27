"""OPENWORKER_BASE_DIR (spec §Fly sandboxes, "Base directory"): a confined box
keeps state and scratch under the base and refuses folders outside it with a
plain error; an unconfined box behaves exactly as before."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from coworker.basedir import OutsideBaseDir, base_dir, ensure_under_base
from coworker.secrets import state_dir
from coworker.server.manager import SessionManager


def test_unconfined_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENWORKER_BASE_DIR", raising=False)
    assert base_dir() is None
    assert ensure_under_base(tmp_path / "anywhere") == (tmp_path / "anywhere").resolve()


def test_confined_paths_resolve_under_base_and_outsiders_are_refused(tmp_path, monkeypatch):
    base = tmp_path / "data"
    (base / "proj").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setenv("OPENWORKER_BASE_DIR", str(base))
    assert ensure_under_base(base) == base.resolve()
    assert ensure_under_base(base / "proj") == (base / "proj").resolve()
    with pytest.raises(OutsideBaseDir) as exc:
        ensure_under_base(outside, "folder")
    assert str(base.resolve()) in str(exc.value) and "folder" in str(exc.value)
    # A symlink inside the base pointing out does not slip through.
    link = base / "escape"
    link.symlink_to(outside)
    with pytest.raises(OutsideBaseDir):
        ensure_under_base(link)
    # `..` tricks neither.
    with pytest.raises(OutsideBaseDir):
        ensure_under_base(base / ".." / "elsewhere")


def test_state_dir_defaults_under_the_base(tmp_path, monkeypatch):
    monkeypatch.delenv("COWORKER_STATE_DIR", raising=False)
    monkeypatch.setenv("OPENWORKER_BASE_DIR", str(tmp_path / "data"))
    assert state_dir() == tmp_path / "data" / "state"
    # An explicit state dir still wins (the image sets both, consistently).
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "explicit"))
    assert state_dir() == tmp_path / "explicit"


def test_manager_confines_workspaces_scratch_and_roots(tmp_path, monkeypatch):
    base = tmp_path / "data"
    inside = base / "repo"
    inside.mkdir(parents=True)
    outside = tmp_path / "home-project"
    outside.mkdir()
    monkeypatch.setenv("OPENWORKER_BASE_DIR", str(base))
    monkeypatch.delenv("COWORKER_SCRATCH_BASE", raising=False)
    manager = SessionManager(data_dir=tmp_path / "mgr")

    assert manager.open_workspace(str(inside))["ok"] is True
    refused = manager.open_workspace(str(outside))
    assert refused["ok"] is False and "only works under" in refused["error"]
    # Creating a new folder outside is refused before anything is made.
    assert manager.open_workspace(str(tmp_path / "new-one"), create=True)["ok"] is False
    assert not (tmp_path / "new-one").exists()

    assert manager.resolve_workspace(str(inside)) == str(inside.resolve())
    assert manager.resolve_workspace(str(outside)) is None

    # Scratch lives under the base, and a stray pref cannot move it out.
    assert manager.scratch_base() == (base / "workspaces").resolve()
    manager._prefs["scratch_base"] = str(outside)
    assert manager.scratch_base() == (base / "workspaces").resolve()
    scratch = Path(manager._provision_scratch("s-confined"))
    assert scratch.is_relative_to(base.resolve())

    assert "only works under" in manager.add_root("s-confined", str(outside))["error"]
    assert "only works under" in manager.promote_workspace("s-confined", str(outside))["error"]
    assert "only works under" in manager.save_temp_as_project("s-confined", str(outside / "saved"))["error"]


def test_unconfined_manager_accepts_any_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENWORKER_BASE_DIR", raising=False)
    anywhere = tmp_path / "anywhere"
    anywhere.mkdir()
    manager = SessionManager(data_dir=tmp_path / "mgr")
    assert manager.open_workspace(str(anywhere))["ok"] is True


def test_deployed_provider_key_adopts_a_runnable_default(tmp_path, monkeypatch):
    """A fresh box defaults to a model nobody can run; the first provider key
    that arrives (by deploy, not just Settings) makes its recommended model
    the default — a working default is never stolen."""
    monkeypatch.delenv("OPENWORKER_BASE_DIR", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager = SessionManager(data_dir=tmp_path / "mgr")
    from coworker.secrets import SecretStore

    manager.secrets = SecretStore(tmp_path / "secrets.json")
    assert manager.get_settings()["model_ready"] is False
    # The deploy path writes the profile then calls adopt — same as the joiner.
    manager.secrets.put("provider:anthropic", {"api_key": "sk-ant-test"})
    adopted = manager.adopt_provider_default("anthropic")
    assert adopted and adopted.startswith("anthropic:")
    s = manager.get_settings()
    assert s["model"] == adopted and s["model_ready"] is True
    # A second key does not steal a working default.
    manager.secrets.put("provider:openai", {"api_key": "sk-test"})
    assert manager.adopt_provider_default("openai") is None
    assert manager.get_settings()["model"] == adopted


# -- store_managed_grant_bundle: the ONE staging routine (desktop + box) ------


def test_managed_grant_bundle_handles_every_family(tmp_path):
    from coworker.connectors.setup import store_managed_grant_bundle
    from coworker.secrets import SecretStore

    grant = {"user_id": "usr_1", "machine_credential": "mc_1"}
    # Generic (Notion-style, multi-account layer): stamps ride the profile.
    s = SecretStore(tmp_path / "a.json")
    out = store_managed_grant_bundle(s, "notion", {"provider": "notion", "connector": "notion", "connection_id": "conn_n", "access_token": "tok", "account": "Acme", "account_id": "ws_1"}, grant)
    assert out.get("ok"), out
    prof = s.get("notion:account:ws_1") or {}
    assert prof["access_token"] == "tok" and prof["broker_user_id"] == "usr_1" and prof["machine_credential"] == "mc_1"
    assert store_managed_grant_bundle(s, "notion", {"connector": "notion"}, grant)["ok"] is False
    # GitHub: one profile per installation + pointer, all stamped, no tokens.
    s = SecretStore(tmp_path / "b.json")
    import json as _json
    out = store_managed_grant_bundle(s, "github", {"connection_id": "conn_g", "installation_id": "1", "account_login": "a", "account_type": "User", "repo_selection": "all", "github_login": "me", "installations": _json.dumps([{"installation_id": "1", "account_login": "a", "account_type": "User", "repo_selection": "all"}, {"installation_id": "2", "account_login": "org", "account_type": "Organization", "repo_selection": "selected"}])}, grant)
    assert out.get("ok"), out
    for key in ("github:install:1", "github:install:2"):
        p = s.get(key) or {}
        assert p["connection_id"] == "conn_g" and p["broker_user_id"] == "usr_1" and p["machine_credential"] == "mc_1"
        assert "access_token" not in p
    assert (s.get("github:default") or {}).get("machine_credential") == "mc_1"
    assert store_managed_grant_bundle(s, "github", {"connection_id": "x"}, grant)["ok"] is False
    # Slack: per-workspace bot token + pointer, stamped.
    s = SecretStore(tmp_path / "c.json")
    out = store_managed_grant_bundle(s, "slack", {"connection_id": "conn_s", "access_token": "xoxb-1", "team_id": "T1", "team_domain": "acme", "bot_user_id": "B1", "slack_user_id": "U1", "account": "Acme"}, grant)
    assert out.get("ok"), out
    p = s.get("slack:team:T1") or {}
    assert p.get("bot_token", p.get("access_token")) and p["machine_credential"] == "mc_1" and p["connection_id"] == "conn_s"
    assert (s.get("slack:default") or {}).get("broker_user_id") == "usr_1"
