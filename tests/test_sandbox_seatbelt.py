"""The Seatbelt provider: the tool runner inside the macOS sandbox.

The first half runs anywhere: the profile text, the allow-list proxy, the environment that
is passed on. The second half runs only on a Mac where `sandbox-exec` works, and checks the
wall itself with real commands.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import pytest

from coworker.agents.base import AgentContext  # noqa: E402  (before catalog: import order)
from coworker import catalog  # noqa: E402, I001
from coworker.roots import RootDir
from coworker.sandbox import netproxy, network_profiles
from coworker.sandbox.providers import seatbelt, seatbelt_profile
from coworker.sandbox.selection import select


# -- the profile ----------------------------------------------------------------------
def test_profile_lists_the_folders_and_nothing_else_under_home(tmp_path):
    home = tmp_path / "home"
    (home / ".nvm").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / ".gitconfig").write_text("[user]\n")
    project, reference = tmp_path / "project", tmp_path / "reference"
    project.mkdir()
    reference.mkdir()
    text = seatbelt_profile.render(
        [{"path": str(project), "writable": True}, {"path": str(reference), "writable": False}],
        runtime_dir=str(tmp_path / "run"),
        home=str(home),
    )
    assert text.startswith("(version 1)\n(deny default)\n")
    real = os.path.realpath
    write_rule = next(line for line in text.splitlines() if line.startswith("(allow file-read* file-write* (subpath"))
    assert real(project) in write_rule and real(tmp_path / "run") in write_rule
    assert real(reference) not in write_rule  # a read-only folder is never writable
    read_rule = next(line for line in text.splitlines() if line.startswith("(allow file-read* (subpath") and real(reference) in line)
    assert real(home / ".nvm") in read_rule and real(home / ".gitconfig") in read_rule
    assert ".ssh" not in text
    assert "remote tcp" not in text  # no proxy port given: no network at all


def test_profile_allows_only_the_proxy_port_and_quotes_paths(tmp_path):
    odd = tmp_path / 'has "quotes" and spaces'
    odd.mkdir()
    text = seatbelt_profile.render([{"path": str(odd), "writable": True}], runtime_dir=str(tmp_path), proxy_port=45678, home=str(tmp_path))
    assert '(allow network-outbound (remote tcp "localhost:45678"))' in text
    assert text.count("remote tcp") == 1
    assert 'has \\"quotes\\" and spaces' in text


def test_secrets_in_the_servers_environment_are_not_passed_on():
    env = seatbelt.clean_environment(
        {
            "PATH": "/usr/bin",
            "LANG": "en_US.UTF-8",
            "NVM_DIR": "/Users/sam/.nvm",
            "ANTHROPIC_API_KEY": "x",
            "GITHUB_TOKEN": "x",
            "AWS_SECRET_ACCESS_KEY": "x",
            "AWS_SESSION_TOKEN": "x",
            "COWORKER_API_TOKEN": "x",
            "OPENWORKER_HEADLESS": "1",
            "SSH_AUTH_SOCK": "/tmp/agent",
            "DB_PASSWORD": "x",
        }
    )
    assert env == {"PATH": "/usr/bin", "LANG": "en_US.UTF-8", "NVM_DIR": "/Users/sam/.nvm"}


# -- the allow-list proxy -------------------------------------------------------------
def _ask(proxy: netproxy.AllowListProxy, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
        s.sendall(request)
        s.settimeout(5)
        chunks = []
        while True:
            try:
                data = s.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b"pong" in data:
                break
        return b"".join(chunks)


def test_proxy_refuses_hosts_that_are_not_listed_and_says_why():
    proxy = netproxy.AllowListProxy("strict")
    try:
        answer = _ask(proxy, b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        assert answer.startswith(b"HTTP/1.1 403 ")
        assert b"example.com:443 is not on this sandbox's allow list" in answer
        assert list(proxy.denied) == ["example.com:443"]
        # a listed host on another port, and plain HTTP, are refused too
        assert _ask(proxy, b"CONNECT github.com:22 HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 403 ")
        assert _ask(proxy, b"GET http://github.com/ HTTP/1.1\r\nHost: github.com\r\n\r\n").startswith(b"HTTP/1.1 403 ")
    finally:
        proxy.close()


def test_proxy_tunnels_a_listed_host(monkeypatch):
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)

    def serve() -> None:
        conn, _ = upstream.accept()
        with conn:
            if conn.recv(16) == b"ping":
                conn.sendall(b"pong")

    threading.Thread(target=serve, daemon=True).start()
    wanted: list[tuple[str, int]] = []
    real_connect = socket.create_connection

    def fake_connect(address, *args, **kwargs):
        if address[0] == "github.com":
            wanted.append(address)
            return real_connect(upstream.getsockname(), *args, **kwargs)
        return real_connect(address, *args, **kwargs)

    monkeypatch.setattr(netproxy.socket, "create_connection", fake_connect)
    proxy = netproxy.AllowListProxy("strict")
    try:
        with real_connect(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n\r\n")
            s.settimeout(5)
            assert s.recv(4096).startswith(b"HTTP/1.0 200 ")
            s.sendall(b"ping")
            assert s.recv(16) == b"pong"
        assert wanted == [("github.com", 443)]
    finally:
        proxy.close()
        upstream.close()


def test_every_profile_has_hosts_and_an_unknown_one_is_refused():
    assert "github.com" in network_profiles.hosts("strict")
    assert set(network_profiles.hosts("strict")) < set(network_profiles.hosts("standard"))
    with pytest.raises(ValueError):
        netproxy.AllowListProxy("wide-open")


# -- selection ------------------------------------------------------------------------
def test_an_explicit_seatbelt_setting_refuses_when_it_cannot_be_used(monkeypatch):
    def broken() -> None:
        raise seatbelt.SeatbeltUnavailable("The Seatbelt sandbox exists only on macOS.")

    monkeypatch.delenv("OPENWORKER_SANDBOX_PROVIDER", raising=False)
    monkeypatch.setattr(seatbelt, "preflight", broken)
    with pytest.raises(seatbelt.SeatbeltUnavailable, match="no session will start"):
        select("seatbelt")
    monkeypatch.setattr(seatbelt, "preflight", lambda: None)
    assert select("seatbelt") == type(select("direct"))("seatbelt", explicit=True)


# -- the wall itself (macOS only) -----------------------------------------------------
def _usable() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        seatbelt.preflight()
        return True
    except seatbelt.SeatbeltUnavailable:
        return False


live = pytest.mark.skipif(not _usable(), reason="needs a Mac where sandbox-exec can apply a profile")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """(workspace, project folder, read-only folder, a folder that was NOT granted)."""
    from coworker.sandbox.bundle import build_runner_zipapp
    from coworker.sandbox.workspace import RunnerWorkspace

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    project, reference, private = tmp_path / "project", tmp_path / "reference", tmp_path / "private"
    for folder in (project, reference, private):
        folder.mkdir()
    (reference / "ref.txt").write_text("reference\n")
    (private / "secret.txt").write_text("classified-content\n")
    roots = [RootDir(path=project, writable=True), RootDir(path=reference, writable=False)]
    provider = seatbelt.SeatbeltProvider(
        roots=[{"path": str(r.path), "writable": r.writable} for r in roots],
        cwd=project,
        runner_path=build_runner_zipapp(tmp_path / "dist"),
    )
    ws = RunnerWorkspace(provider, cwd=project, live_roots=roots)
    ws.executor.default_timeout = 20
    yield ws, project, reference, private, roots
    ws.close()


@live
def test_files_outside_the_sessions_folders_cannot_be_read_or_written(sandbox):
    ws, project, reference, private, _ = sandbox
    run = ws.executor.run
    assert ws.describe()["enforcement"] == "full"
    assert run("echo made > made.txt && cat made.txt")["output"].strip() == "made"
    assert (project / "made.txt").read_text() == "made\n"
    assert "reference" in run(f"cat {reference}/ref.txt")["output"]
    assert run(f"echo no > {reference}/new.txt")["exit_code"] != 0
    assert not (reference / "new.txt").exists()
    assert "classified-content" not in run(f"cat {private}/secret.txt 2>&1")["output"]
    assert run(f"echo no > {private}/new.txt")["exit_code"] != 0
    home = Path.home()
    assert "not permitted" in run(f"ls {home} 2>&1")["output"]
    assert run(f"echo no > {home}/openworker-seatbelt-test.txt")["exit_code"] != 0
    assert not (home / "openworker-seatbelt-test.txt").exists()
    assert run("touch /usr/local/bin/openworker-seatbelt-test")["exit_code"] != 0


@live
def test_the_servers_secrets_do_not_reach_a_command(sandbox):
    ws, *_ = sandbox
    out = ws.executor.run("env")["output"]
    assert "not-a-real-key" not in out and "ANTHROPIC_API_KEY" not in out
    assert "COWORKER_" not in out


@live
def test_the_network_is_only_the_proxy(sandbox):
    """No internet needed: a listener on this machine stands in for 'somewhere else'."""
    ws, *_ = sandbox
    elsewhere = socket.socket()
    elsewhere.bind(("127.0.0.1", 0))
    elsewhere.listen(1)
    try:
        port = elsewhere.getsockname()[1]
        direct = ws.executor.run(f"curl -sS -m 5 --noproxy '*' http://127.0.0.1:{port}/ 2>&1")
        assert direct["exit_code"] != 0  # the kernel refuses the connection
        refused = ws.executor.run("curl -sS -m 5 https://example.com 2>&1")
        assert "403" in refused["output"]
    finally:
        elsewhere.close()


@live
def test_our_own_tools_run_behind_the_wall(sandbox):
    ws, project, reference, private, roots = sandbox
    ctx = AgentContext(workspace=project, executor=ws.executor, roots=roots, sandbox=ws)
    tools = {t.__name__: t for t in catalog.expand(["code_files", "search"], ctx)}
    tools["write_file"]("notes.md", "one\ntwo\n")
    assert (project / "notes.md").read_text() == "one\ntwo\n"
    assert "two" in tools["read_file"]("notes.md")["content"]
    assert "reference" in tools["read_file"](str(reference / "ref.txt"))["content"]
    with pytest.raises(PermissionError):
        tools["write_file"](str(reference / "new.txt"), "no")


@live
def test_granting_a_folder_restarts_the_sandbox_and_says_so(sandbox):
    from coworker.sandbox.workspace import RESTART_NOTICE

    ws, project, reference, private, roots = sandbox
    run = ws.executor.run
    run("export KEEP=yes")
    assert "classified-content" not in run(f"cat {private}/secret.txt 2>&1")["output"]
    before = ws.client.restarts
    roots.append(RootDir(path=private, writable=True))
    granted = run(f"cat {private}/secret.txt; echo KEEP=$KEEP")
    assert "classified-content" in granted["output"]
    assert granted["sandbox_notice"] == RESTART_NOTICE
    assert "KEEP=yes" not in granted["output"]  # a new shell, as the notice says
    assert ws.client.restarts == before + 1
    assert "sandbox_notice" not in run("true")  # said once
    roots.pop()  # taken away again: the wall goes back up
    assert "classified-content" not in run(f"cat {private}/secret.txt 2>&1")["output"]
