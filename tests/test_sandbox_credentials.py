"""Credential grants (design doc, section 11b): the machine's list, the copy-in, the hosts,
and, on a Mac, the copies inside a real Seatbelt sandbox with SSH going through the proxy.
"""

from __future__ import annotations

import os
import socket
import stat
import sys
import threading
from pathlib import Path

import pytest

from coworker.config import Config, load_config
from coworker.sandbox import credentials as creds
from coworker.sandbox import netproxy
from coworker.sandbox.providers import openshell_policy


def _home(tmp_path) -> Path:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY\n")
    (home / ".ssh" / "id_ed25519.pub").write_text("ssh-ed25519 AAAA test\n")
    (home / ".ssh" / "config").write_text("Host work\n  HostName git.example.com\n")
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gh" / "hosts.yml").write_text("github.com:\n  oauth_token: gho_x\n")
    (home / ".aws").mkdir()
    (home / ".aws" / "credentials").write_text("[default]\naws_access_key_id = AKIA\n")
    (home / ".kube").mkdir()
    (home / ".kube" / "config").write_text("clusters:\n- cluster:\n    server: https://k8s.example.com:6443\n")
    (home / ".gitconfig").write_text("[user]\n\tname = Sam\n\temail = sam@example.com\n")
    (home / "Documents").mkdir()
    return home


# -- the list ---------------------------------------------------------------------------
def test_nothing_is_granted_unless_switched_on(tmp_path):
    home = _home(tmp_path)
    assert creds.granted(None, home=str(home)) == []
    assert [e["enabled"] for e in creds.entries(None)] == [False] * 4


def test_a_user_entry_edits_or_adds_by_name(tmp_path):
    home = _home(tmp_path)
    configured = [
        {"name": "ssh", "enabled": True},
        {"name": "aws", "enabled": True, "path": "~/.aws-work"},  # edited path, missing here
        {"name": "npm", "enabled": True, "path": "~/.npmrc", "hosts": ["registry.npmjs.org:443"]},  # added
        {"name": "bogus", "enabled": True, "path": "~"},  # never the home folder itself
        {"name": "outside", "enabled": True, "path": "/etc"},  # never outside the home folder
    ]
    (home / ".npmrc").write_text("//registry.npmjs.org/:_authToken=x\n")
    got = {g.name: g for g in creds.granted(configured, home=str(home))}
    assert set(got) == {"ssh", "npm"}
    assert got["ssh"].relative == ".ssh" and got["ssh"].hosts == ["github.com:22", "gitlab.com:22"]
    assert got["npm"].hosts == ["registry.npmjs.org:443"]


def test_kube_hosts_come_from_the_kubeconfig(tmp_path):
    home = _home(tmp_path)
    (got,) = creds.granted([{"name": "kube", "enabled": True}], home=str(home))
    assert got.hosts == ["k8s.example.com:6443"]


def test_the_setting_is_machine_level_only(tmp_path, monkeypatch):
    g = tmp_path / "global.toml"
    g.write_text('[[sandbox_credentials]]\nname = "ssh"\nenabled = true\n')
    ws = tmp_path / "repo"
    (ws / ".coworker").mkdir(parents=True)
    (ws / ".coworker" / "config.toml").write_text('[[sandbox_credentials]]\nname = "aws"\nenabled = true\n')
    cfg = load_config(ws, global_path=g, workspace_trusted=True)
    assert cfg.sandbox_credentials == [{"name": "ssh", "enabled": True}]
    assert Config().sandbox_credentials == []


# -- the copy ---------------------------------------------------------------------------
def test_copy_in_makes_a_private_home_and_the_tools_environment(tmp_path):
    home = _home(tmp_path)
    grants = creds.granted([{"name": n, "enabled": True} for n in ("ssh", "gh", "aws", "kube")], home=str(home))
    run = tmp_path / "run"
    run.mkdir()
    copied = creds.copy_in(grants, str(run), home=str(home), ssh_proxy_command="/usr/bin/nc -X connect -x 127.0.0.1:4545 %h %p")
    sb = Path(copied.home)
    assert sb == run / "home"
    assert (sb / ".ssh" / "id_ed25519").read_text() == "PRIVATE KEY\n"
    assert stat.S_IMODE((sb / ".ssh" / "id_ed25519").stat().st_mode) == 0o600
    assert stat.S_IMODE((sb / ".ssh").stat().st_mode) == 0o700
    assert (sb / ".gitconfig").exists()  # git's settings always come along
    assert not (sb / "Documents").exists()  # nothing that was not granted
    config = (sb / ".ssh" / "config").read_text()
    assert config.startswith("# Added by OpenWorker") and "ProxyCommand /usr/bin/nc -X connect -x 127.0.0.1:4545 %h %p" in config
    assert f"IdentityFile {sb / '.ssh' / 'id_ed25519'}" in config and "IdentityAgent none" in config
    assert config.endswith("Host work\n  HostName git.example.com\n")  # the user's own config still there, after ours
    assert copied.env["GIT_SSH_COMMAND"] == f'/usr/bin/ssh -F "{sb / ".ssh" / "config"}"'
    assert (sb / "bin" / "ssh").read_text().startswith("#!/bin/sh") and copied.env["OPENWORKER_PATH_PREPEND"] == str(sb / "bin")
    assert copied.env["HOME"] == str(sb)
    assert copied.env["GH_CONFIG_DIR"] == str(sb / ".config" / "gh")
    assert copied.env["AWS_SHARED_CREDENTIALS_FILE"] == str(sb / ".aws" / "credentials")
    assert copied.env["KUBECONFIG"] == str(sb / ".kube" / "config")
    assert copied.hosts == ["*.amazonaws.com:443", "api.github.com:443", "github.com:22", "github.com:443", "gitlab.com:22", "k8s.example.com:6443"]
    # the real files were not touched
    assert (home / ".ssh" / "config").read_text() == "Host work\n  HostName git.example.com\n"
    text = creds.context_lines(copied)
    assert "SSH keys (.ssh): you can push and pull over SSH" in text and "deleted when the session ends" in text


def test_no_grants_means_no_lines_for_the_agent(tmp_path):
    assert creds.context_lines(None) == ""
    (tmp_path / "run").mkdir()
    copied = creds.copy_in([], str(tmp_path / "run"), home=str(_home(tmp_path)))
    assert creds.context_lines(copied) == ""
    assert copied.hosts == [] and (Path(copied.home) / ".gitconfig").exists()


# -- the network ------------------------------------------------------------------------
def test_proxy_allows_the_grants_hosts_including_ssh_and_wildcards():
    proxy = netproxy.AllowListProxy("strict", extra_hosts=["github.com:22", "*.amazonaws.com:443", "k8s.example.com:6443"])
    try:
        assert proxy.allows("github.com", 22) and proxy.allows("github.com", 443)
        assert proxy.allows("s3.us-east-1.amazonaws.com", 443) and not proxy.allows("amazonaws.com", 443)
        assert proxy.allows("k8s.example.com", 6443) and not proxy.allows("k8s.example.com", 443)
        assert not proxy.allows("gitlab.com", 22) and not proxy.allows("example.com", 443)
    finally:
        proxy.close()


def test_openshell_policy_carries_the_home_and_the_hosts():
    policy = openshell_policy.render(
        [{"path": "/home/sam/proj", "writable": True}], home="/tmp/ow-x/home", extra_hosts=["github.com:22", "*.amazonaws.com:443"]
    )
    assert "/tmp/ow-x/home" in policy["filesystem_policy"]["read_write"]
    extra = policy["network_policies"]["credentials"]
    assert extra["endpoints"] == [{"host": "github.com", "port": 22}, {"host": "*.amazonaws.com", "port": 443}]
    mounts = openshell_policy.mounts([{"path": "/home/sam/proj", "writable": True}], "/opt/r", "/tmp/ow-x/home")["docker"]["mounts"]
    assert {"type": "bind", "source": "/tmp/ow-x/home", "target": "/tmp/ow-x/home", "read_only": False} in mounts


# -- inside a real sandbox (macOS) ------------------------------------------------------
def _seatbelt_usable() -> bool:
    if sys.platform != "darwin":
        return False
    from coworker.sandbox.providers import seatbelt

    try:
        seatbelt.preflight()
        return True
    except seatbelt.SeatbeltUnavailable:
        return False


@pytest.mark.skipif(not _seatbelt_usable(), reason="needs a Mac where sandbox-exec can apply a profile")
def test_ssh_and_gh_copies_work_inside_seatbelt_and_ssh_goes_through_the_proxy(tmp_path, monkeypatch):
    """A fake `.ssh` and `.config/gh` are granted. Inside the sandbox: HOME is the copy,
    `gh` finds its file, the real home is still hidden, and `ssh` reaches the listed host
    through the proxy (a local listener stands in for github.com:22: the CONNECT arrives
    at the proxy and is tunnelled; the SSH banner comes back)."""
    from coworker.sandbox.bundle import build_runner_zipapp
    from coworker.sandbox.providers import seatbelt
    from coworker.sandbox.workspace import RunnerWorkspace

    home = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    grants = creds.granted([{"name": "ssh", "enabled": True}, {"name": "gh", "enabled": True}], home=str(home))
    # a stand-in for github.com:22 on this machine; the proxy resolves the host, so patch that
    fake = socket.socket()
    fake.bind(("127.0.0.1", 0))
    fake.listen(1)

    def banner() -> None:
        conn, _ = fake.accept()
        with conn:
            conn.sendall(b"SSH-2.0-OpenSSH_9.9 fake\r\n")
            conn.recv(64)

    threading.Thread(target=banner, daemon=True).start()
    real_connect = netproxy.socket.create_connection

    def connect(address, *a, **k):
        if address == ("github.com", 22):
            return real_connect(fake.getsockname(), *a, **k)
        return real_connect(address, *a, **k)

    monkeypatch.setattr(netproxy.socket, "create_connection", connect)
    provider = seatbelt.SeatbeltProvider(
        roots=[{"path": str(project), "writable": True}], cwd=project, runner_path=build_runner_zipapp(tmp_path / "dist"), credentials=grants
    )
    ws = RunnerWorkspace(provider, cwd=project)
    try:
        run = ws.executor.run
        assert run("echo $HOME")["output"].strip() == provider.copied.home
        assert "PRIVATE KEY" in run("cat ~/.ssh/id_ed25519")["output"]
        assert "gho_x" in run('cat "$GH_CONFIG_DIR/hosts.yml"')["output"]
        assert "not permitted" in run(f"ls {home} 2>&1")["output"]  # the real home stays hidden
        assert ws.describe()["credentials"][0]["name"] == "ssh"
        assert "SSH keys" in ws.context()
        # ssh: the ProxyCommand in the copied config carries the connection through the proxy
        assert run("command -v ssh")["output"].strip() == os.path.join(provider.copied.home, "bin", "ssh")  # the wrapper wins
        out = run("ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -v git@github.com true 2>&1 | grep -i 'remote protocol version\\|proxy\\|banner' | head -3")
        assert "SSH-2.0-OpenSSH_9.9 fake" in out["output"] or "Remote protocol version 2.0" in out["output"], out["output"]
    finally:
        ws.close()
        fake.close()
    assert not Path(provider.copied.home).exists()  # the copies died with the sandbox


def test_a_failed_creation_leaves_no_copied_credential_behind(tmp_path, monkeypatch):
    from coworker.sandbox.providers import seatbelt

    home = _home(tmp_path)
    grants = creds.granted([{"name": "ssh", "enabled": True}], home=str(home))
    provider = seatbelt.SeatbeltProvider(roots=[{"path": str(tmp_path), "writable": True}], cwd=tmp_path, credentials=grants, network=False)
    folder = provider._dir

    def broken() -> None:
        raise seatbelt.SeatbeltUnavailable("no sandbox today")

    monkeypatch.setattr(seatbelt, "preflight", broken)
    with pytest.raises(seatbelt.SeatbeltUnavailable):
        provider.create()
    assert not os.path.exists(folder)
