"""Which sandbox provider a session gets, and the machine commands around it
(design doc `sandbox-design.md`, rulings 17, 18, 22, 23 and 29)."""

from __future__ import annotations

import json

import pytest

from coworker import config
from coworker.sandbox import selection
from coworker.sandbox.providers.openshell import OpenShellUnavailable


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("OPENWORKER_SANDBOX_PROVIDER", raising=False)
    monkeypatch.delenv("OPENWORKER_HEADLESS", raising=False)


def _openshell(monkeypatch, problem):
    monkeypatch.setattr(selection, "openshell_problem", lambda fresh=False: problem)


def test_the_desktop_and_the_terminal_app_stay_direct(monkeypatch):
    _openshell(monkeypatch, None)  # even with OpenShell installed and running
    chosen = selection.select(None)
    assert (chosen.provider, chosen.explicit, chosen.warning) == ("direct", False, "")


def test_a_headless_machine_uses_openshell_when_it_is_there(monkeypatch):
    _openshell(monkeypatch, None)
    monkeypatch.setenv("OPENWORKER_HEADLESS", "1")
    assert selection.select(None).provider == "openshell"


def test_a_headless_machine_without_openshell_runs_direct_and_says_so_loudly(monkeypatch):
    _openshell(monkeypatch, "The OpenShell gateway is not running.")
    chosen = selection.select(None, headless=True)
    assert chosen.provider == "direct" and not chosen.explicit
    assert "WITHOUT a sandbox" in chosen.warning and "gateway is not running" in chosen.warning
    assert chosen.warning.count("sandbox setup") == 1


def test_an_explicit_openshell_setting_refuses_instead_of_falling_back(monkeypatch):
    _openshell(monkeypatch, "The OpenShell gateway is not running.")
    with pytest.raises(OpenShellUnavailable, match="no session will start"):
        selection.select("openshell")
    _openshell(monkeypatch, None)
    assert selection.select("openshell") == selection.Selection("openshell", explicit=True)


def test_the_environment_wins_over_the_config_and_unknown_names_are_refused(monkeypatch):
    _openshell(monkeypatch, None)
    monkeypatch.setenv("OPENWORKER_SANDBOX_PROVIDER", "direct")
    assert selection.select("openshell", headless=True) == selection.Selection("direct", explicit=True)
    monkeypatch.setenv("OPENWORKER_SANDBOX_PROVIDER", "firejail")
    with pytest.raises(ValueError, match="unknown sandbox provider"):
        selection.select(None)


def test_a_repository_cannot_switch_the_sandbox_off(tmp_path):
    machine = tmp_path / "config.toml"
    config.set_global_value("sandbox_provider", "openshell", path=machine)
    repo = tmp_path / "repo"
    (repo / ".coworker").mkdir(parents=True)
    (repo / ".coworker" / "config.toml").write_text('sandbox_provider = "direct"\nmodel = "from-the-repo"\n')
    loaded = config.load_config(repo, global_path=machine)
    assert loaded.sandbox_provider == "openshell" and loaded.model == "from-the-repo"


def test_setting_a_config_value_keeps_the_rest_of_the_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "x"\n\n[some.table]\nkey = 1\n')
    config.set_global_value("sandbox_provider", "openshell", path=path)
    config.set_global_value("sandbox_provider", "direct", path=path)
    text = path.read_text()
    assert text.count("sandbox_provider") == 1 and text.index("sandbox_provider") < text.index("[some.table]")
    assert config.load_config(global_path=path).sandbox_provider == "direct" and 'model = "x"' in text
    with pytest.raises(ValueError):
        config.set_global_value("not_a_key", "x", path=path)


def test_machine_status_reports_how_sessions_will_run(tmp_path, monkeypatch, capsys):
    from coworker.remote import joiner

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    _openshell(monkeypatch, "OpenShell is not installed on this machine.")
    info = joiner.machine_status(tmp_path)["sandbox"]
    assert info["provider"] == "direct" and "WITHOUT a sandbox" in info["warning"]
    joiner._cmd_status(tmp_path)
    assert "sandbox:     direct" in capsys.readouterr().out

    config.set_global_value("sandbox_provider", "openshell")
    refused = joiner.machine_status(tmp_path)["sandbox"]
    assert refused["provider"] is None and "no session will start" in refused["refused"]
    joiner._cmd_status(tmp_path, as_json=True)
    assert json.loads(capsys.readouterr().out)["sandbox"]["provider"] is None


def test_the_sandbox_command_is_served_but_not_advertised(tmp_path, monkeypatch, capsys):
    from coworker import cli
    from coworker.sandbox import setup_cmd

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    _openshell(monkeypatch, "OpenShell is not installed on this machine.")
    # The same answer on any machine, with or without OpenShell installed.
    monkeypatch.setattr(setup_cmd, "checks", lambda: [(f"OpenShell {setup_cmd.PINNED_VERSION} is installed", False, "found none")])
    assert "sandbox" not in cli.HELP  # hidden until tried on a fresh machine
    with pytest.raises(SystemExit) as done:
        cli.main(["machine", "sandbox", "status"])
    assert done.value.code == 1  # something is missing here
    out = capsys.readouterr().out
    assert "[--] OpenShell" in out and "sessions on this machine run: direct" in out


def test_setup_asks_before_each_change_and_makes_none_when_refused(tmp_path, monkeypatch):
    from coworker.sandbox import setup_cmd

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(setup_cmd.sys, "platform", "linux")
    monkeypatch.setattr(
        setup_cmd,
        "checks",
        lambda: [
            ("Docker is installed and this user can use it", True, ""),
            (f"OpenShell {setup_cmd.PINNED_VERSION} is installed", True, ""),
            ("the gateway allows bind mounts (your folders reach a sandbox this way)", False, ""),
            ("the gateway is running", True, ""),
            ("this machine is set to use OpenShell", False, ""),
        ],
    )
    asked: list[str] = []
    said: list[str] = []
    code = setup_cmd.setup(ask=lambda q: asked.append(q) or False, print_fn=said.append)
    assert code == 1 and asked == ["Make this change?"]
    assert "enable_bind_mounts = true" in "\n".join(said)  # the change was shown first
    assert not (tmp_path / "xdg" / "openshell" / "gateway.toml").exists()
    assert config.load_config().sandbox_provider is None
