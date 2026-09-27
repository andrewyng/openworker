"""The public CLI surface: `openworker join` and `openworker up` (the everyday pair),
`openworker machine …` (status, keys, logs, service, leave), `version`, and help that lists
them. There is one spelling for each command; typing one at the wrong level says where it is."""

import pytest

from coworker import cli as ow_cli
from coworker.remote import joiner


def test_no_arguments_prints_help_that_lists_every_public_command(capsys):
    ow_cli.main([])
    out = capsys.readouterr().out
    assert "usage: openworker <command>" in out
    for verb in ("join <link>", "up", "machine", "status", "keys", "logs", "service", "leave", "version"):
        assert verb in out
    # Unlisted until tested as a product surface.
    for hidden in ("tui", "sessions", "inbox", "doctor"):
        assert hidden not in out


@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_help_flags_print_the_same_help(flag, capsys):
    ow_cli.main([flag])
    assert "usage: openworker <command>" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["version", "--version", "-V"])
def test_version(flag, capsys):
    ow_cli.main([flag])
    out = capsys.readouterr().out.strip()
    assert out.startswith("openworker ") and out.split()[1][0].isdigit()


def test_machine_group_runs_the_management_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        ow_cli.main(["machine", "status"])
    assert exc.value.code == 0
    assert "not joined" in capsys.readouterr().out


def test_machine_with_no_command_shows_its_help_without_join_or_up(capsys):
    with pytest.raises(SystemExit) as exc:
        ow_cli.main(["machine"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: openworker machine" in out and "{status,logs,keys,leave,service}" in out


def test_up_is_a_top_level_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        ow_cli.main(["up"])
    assert exc.value.code == 2  # never joined
    assert "openworker join <link>" in capsys.readouterr().err


def test_join_is_a_top_level_command_and_checks_its_link(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        ow_cli.main(["join", "http://host:1/wrong/tok"])
    assert exc.value.code == 2


def test_join_with_a_bare_address_means_approve_a_code_there(tmp_path, monkeypatch):
    """One verb for both ways in: a link enrolls directly; a controller's address starts the
    approve-a-code flow (what used to be spelled `auth join`)."""
    seen = {}
    monkeypatch.setattr(joiner, "_cmd_auth_join", lambda state, url, name: seen.update(url=url, name=name) or 0)
    assert joiner.cli(["join", "https://machines.example.com", "--name", "box"]) == 0
    assert seen == {"url": "https://machines.example.com", "name": "box"}
    assert joiner._is_controller_address("http://127.0.0.1:8765/")
    assert not joiner._is_controller_address("http://127.0.0.1:8765/j/abc")
    assert not joiner._is_controller_address("http://host:1/wrong/tok")


@pytest.mark.parametrize(
    "typed, now",
    [
        (["status"], "machine status"),
        (["secrets", "list"], "machine keys"),
        (["service", "install"], "machine service"),
        (["machine", "up"], "up"),
        (["machine", "join", "http://h/j/t"], "join"),
        (["auth", "join", "http://h"], "join <address>"),
    ],
)
def test_a_command_typed_at_the_wrong_level_says_where_it_lives(typed, now, capsys):
    with pytest.raises(SystemExit) as exc:
        ow_cli.main(typed)
    assert exc.value.code == 2
    assert f"is `openworker {now}`" in capsys.readouterr().err


def test_keys_stores_a_key_and_never_prints_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    assert joiner.cli(["keys", "set", "openai", "api_key=sk-local"]) == 0
    assert joiner.cli(["keys", "list"]) == 0
    out = capsys.readouterr().out
    assert "openai" in out and "sk-local" not in out


# -- status --json, logs, and the macOS service ---------------------------------------

import argparse
import json
import plistlib
from pathlib import Path


def _joined(tmp_path: Path, name: str = "mini") -> Path:
    state = tmp_path / "state"
    state.mkdir()
    joiner.save_remote_config(
        state, {"controller": "http://10.0.0.5:9787", "name": name, "machine_id": "m1"}
    )
    return state


def test_status_json_is_machine_readable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    assert joiner.cli(["status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["joined"] is False and data["controller"] is None and data["identity"] is None
    assert data["state_dir"] == str(tmp_path) and data["version"][0].isdigit()

    joiner.save_remote_config(tmp_path, {"controller": "http://c:1", "name": "box", "machine_id": "m9"})
    assert joiner.cli(["status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["joined"] is True and data["name"] == "box" and data["machine_id"] == "m9"


def test_logs_command_per_platform(tmp_path):
    state = _joined(tmp_path, "cloud vm")
    assert joiner.logs_command(state, "linux", None, 50, True) == [
        "journalctl", "--user", "-u", "openworker-cloud-vm.service", "-n", "50", "-f",
    ]
    assert joiner.logs_command(state, "linux", "openworker.service", 10, False)[3] == "openworker.service"
    mac = joiner.logs_command(state, "darwin", None, 200, False)
    assert mac == ["tail", "-n", "200", str(state / "logs" / "machine.log")]
    assert joiner.logs_command(state, "win32", None, 200, False) is None


def test_logs_on_macos_without_a_service_log_explains(tmp_path, capsys):
    state = _joined(tmp_path)
    args = argparse.Namespace(unit=None, lines=200, follow=False)
    assert joiner._cmd_logs(state, args, platform="darwin") == 1
    assert "service install" in capsys.readouterr().err


def test_macos_service_install_writes_a_launchd_agent(tmp_path, monkeypatch, capsys):
    state = _joined(tmp_path, "mac mini")
    calls: list[tuple] = []
    monkeypatch.setattr(joiner, "_launchctl", lambda *a: calls.append(a) or True)
    args = argparse.Namespace(service_command="install", force=False)
    assert joiner._cmd_service(state, args, platform="darwin", home=tmp_path) == 0

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.openworker.machine.mac-mini.plist"
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"] == "com.openworker.machine.mac-mini"
    assert data["ProgramArguments"][-1] == "up"
    assert data["EnvironmentVariables"]["COWORKER_STATE_DIR"] == str(state)
    assert data["EnvironmentVariables"]["PATH"]  # launchd's own PATH is too bare for a coworker
    assert data["RunAtLoad"] is True and data["KeepAlive"] is True
    assert data["StandardOutPath"] == str(state / "logs" / "machine.log")
    assert any(c[0] == "bootstrap" and c[-1] == str(plist_path) for c in calls)
    assert "openworker machine logs -f" in capsys.readouterr().out

    # list shows it with its state dir; uninstall unloads and removes it.
    assert joiner._cmd_service(state, argparse.Namespace(service_command="list"), platform="darwin", home=tmp_path) == 0
    assert f"com.openworker.machine.mac-mini  state={state}" in capsys.readouterr().out
    assert joiner._cmd_service(
        state, argparse.Namespace(service_command="uninstall", unit=None), platform="darwin", home=tmp_path
    ) == 0
    assert not plist_path.exists()
    assert any(c[0] == "bootout" for c in calls)


def test_macos_service_install_refuses_a_twin_on_the_same_state_dir(tmp_path, monkeypatch, capsys):
    state = _joined(tmp_path, "mini")
    monkeypatch.setattr(joiner, "_launchctl", lambda *a: True)
    agent_dir = tmp_path / "Library" / "LaunchAgents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "com.openworker.machine.old.plist").write_bytes(
        joiner._launchd_plist(state, "/old/openworker", "com.openworker.machine.old")
    )
    args = argparse.Namespace(service_command="install", force=False)
    assert joiner._cmd_service(state, args, platform="darwin", home=tmp_path) == 2
    assert "com.openworker.machine.old already runs" in capsys.readouterr().err
    assert not (agent_dir / "com.openworker.machine.mini.plist").exists()

    args.force = True
    assert joiner._cmd_service(state, args, platform="darwin", home=tmp_path) == 0
    assert not (agent_dir / "com.openworker.machine.old.plist").exists()
    assert (agent_dir / "com.openworker.machine.mini.plist").exists()


def test_service_needs_a_joined_machine_and_a_supported_platform(tmp_path, capsys):
    args = argparse.Namespace(service_command="install", force=False)
    assert joiner._cmd_service(tmp_path, args, platform="darwin", home=tmp_path) == 2
    assert "openworker join <link>" in capsys.readouterr().err
    assert joiner._cmd_service(tmp_path, args, platform="win32", home=tmp_path) == 2
    assert "systemd" in capsys.readouterr().err


# -- packaging/install.sh --------------------------------------------------------------

import shutil
import subprocess

INSTALL_SH = Path(__file__).resolve().parents[1] / "packaging" / "install.sh"


def test_install_script_is_valid_posix_shell_and_runs_nothing_if_truncated():
    text = INSTALL_SH.read_text()
    # One function, called on the last line: a download cut off half-way defines nothing runnable.
    assert text.rstrip().endswith('main "$@"')
    assert text.count("\nmain() {") == 1
    for shell in ("sh", "dash"):
        exe = shutil.which(shell)
        if exe:
            assert subprocess.run([exe, "-n", str(INSTALL_SH)]).returncode == 0
    # No sudo, and it points people at the public command spelling.
    assert "sudo" not in text.replace("(no sudo)", "")
    assert "openworker join <link>" in text


def test_install_script_refuses_an_unsupported_os(tmp_path):
    fake = tmp_path / "uname"
    fake.write_text("#!/bin/sh\necho Plan9\n")
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
    done = subprocess.run(["sh", str(INSTALL_SH)], env=env, capture_output=True, text=True)
    assert done.returncode == 1 and "Linux and macOS" in done.stderr
