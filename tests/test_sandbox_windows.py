"""The Windows sandbox provider (design doc `sandbox-windows-design.md`, section 7).

Live tests, Windows only. They need setup to have run on the machine (`openworker machine
sandbox setup`) and skip otherwise; there is no weaker mode. Setup itself is only exercised
with OPENWORKER_TEST_WINDOWS_SETUP=1, because it changes the machine (two accounts, a
firewall rule, loopback filters, a folder under ProgramData).
"""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Windows sandbox exists only on Windows")

from coworker.sandbox.bundle import build_runner_zipapp  # noqa: E402
from coworker.sandbox.workspace import RunnerWorkspace  # noqa: E402


def _has_setup() -> bool:
    if sys.platform != "win32":
        return False
    from coworker.sandbox.providers import windows_setup

    return windows_setup.account("open") is not None and windows_setup.account("closed") is not None




def _open(tmp_path, *, extra_roots=(), network=False, profile="allowlist"):
    from coworker.sandbox.providers.windows import WindowsProvider

    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "a.txt").write_text("hello\n")
    zipapp = build_runner_zipapp(tmp_path / "dist")
    roots = [{"path": str(project), "writable": True}, *extra_roots]
    provider = WindowsProvider(roots=roots, cwd=project, runner_path=zipapp, network=network, profile=profile)
    ws = RunnerWorkspace(provider, cwd=project)
    ws.executor.default_timeout = 60
    return ws, provider, project


full = pytest.mark.skipif(not _has_setup(), reason="needs `openworker machine sandbox setup` on this machine")


@full
def test_writes_are_limited_to_the_sessions_folders_and_the_entries_come_and_go(tmp_path):
    from coworker.sandbox import winsec

    ws, provider, project = _open(tmp_path)
    try:
        inside = ws.executor.run("Set-Content -Path new.txt -Value made; Get-Content new.txt")
        assert inside["exit_code"] == 0 and "made" in inside["output"]
        beside = ws.executor.run(f"Set-Content -Path '{tmp_path / 'beside.txt'}' -Value x")  # the parent, never granted
        assert beside["exit_code"] != 0 and not (tmp_path / "beside.txt").exists()
        assert winsec.entries_for(str(project), provider.session_sid)
        assert winsec.entries_for(str(tmp_path), provider.session_sid)  # traverse only: the write above was refused
        assert winsec.entries_for(os.path.expanduser("~"), provider.session_sid)
    finally:
        ws.close()
    for folder in (str(project), str(tmp_path), os.path.expanduser("~")):
        assert not winsec.entries_for(folder, provider.session_sid), folder
    assert not os.path.exists(provider._dir)


@full
def test_a_new_folder_is_usable_without_a_restart(tmp_path):
    ws, provider, project = _open(tmp_path)
    try:
        extra = tmp_path / "extra"
        extra.mkdir()
        before = ws.client.instance_id
        assert ws.executor.run(f"Set-Content -Path '{extra / 'x.txt'}' -Value x")["exit_code"] != 0
        provider.regrant([{"path": str(project), "writable": True}, {"path": str(extra), "writable": True}])
        assert ws.executor.run(f"Set-Content -Path '{extra / 'x.txt'}' -Value x")["exit_code"] == 0
        assert ws.client.instance_id == before  # the same daemon, the same shells
        provider.regrant([{"path": str(project), "writable": True}])
        assert ws.executor.run(f"Set-Content -Path '{extra / 'y.txt'}' -Value x")["exit_code"] != 0
    finally:
        ws.close()


@full
def test_a_session_workspace_goes_through_the_registry(tmp_path, monkeypatch):
    from coworker.sandbox.registry import SandboxRegistry
    from coworker.sandbox.workspace import open_workspace

    monkeypatch.setenv("OPENWORKER_SANDBOX_PROVIDER", "windows")
    ws = open_workspace(cwd=tmp_path, session_id="s-win", agent="cowork")
    try:
        assert ws.describe()["provider"] == "windows" and ws.describe()["enforcement"] == "full"
        assert ws.executor.run("echo via-sandbox")["output"].strip() == "via-sandbox"
        rows = SandboxRegistry().list()
        assert [r["session_id"] for r in rows] == ["s-win"] and rows[0]["enforcement"] == "full"
        assert rows[0]["profile"] == "open"  # the Windows default
    finally:
        ws.close()
    assert SandboxRegistry().list() == []


@full
def test_the_provider_is_known_to_selection_and_settings(monkeypatch, tmp_path):
    from coworker.sandbox import selection, settings

    monkeypatch.setenv("OPENWORKER_SANDBOX_PROVIDER", "windows")
    assert selection.select().provider == "windows"
    monkeypatch.delenv("OPENWORKER_SANDBOX_PROVIDER")
    names = {p["name"]: p for p in settings.snapshot()["providers"]}
    assert names["windows"]["usable"] and "seatbelt" not in names


# -- credential grants ---------------------------------------------------------------------


def _fake_home(tmp_path):
    """A home with a real (throwaway) ssh key, made by Windows' own ssh-keygen."""
    import subprocess

    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    subprocess.run([r"C:\Windows\System32\OpenSSH\ssh-keygen.exe", "-q", "-t", "ed25519", "-N", "", "-f", str(home / ".ssh" / "id_ed25519")], check=True, capture_output=True)
    (home / ".gitconfig").write_text("[user]\n\tname = Sam\n\temail = sam@example.com\n")
    return home


def _ssh_probe(ws) -> str:
    """Reaches GitHub over SSH through whatever the sandbox provides. A throwaway key gets
    "Permission denied (publickey)": the tunnel, the config and the key all worked. Through
    cmd, because PowerShell 5 turns a native command's first stderr line into an error
    record and drops the rest of its output."""
    return ws.executor.run('cmd.exe /d /c "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1"')["output"]


@full
def test_a_granted_ssh_key_reaches_github_through_the_proxy(tmp_path):
    from coworker.sandbox import credentials as creds
    from coworker.sandbox.providers.windows import WindowsProvider

    home = _fake_home(tmp_path)
    grants = creds.granted([{"name": "ssh", "enabled": True}], home=str(home))
    project = tmp_path / "project"
    project.mkdir()
    zipapp = build_runner_zipapp(tmp_path / "dist")
    provider = WindowsProvider(roots=[{"path": str(project), "writable": True}], cwd=project, runner_path=zipapp, network=True, credentials=grants)
    ws = RunnerWorkspace(provider, cwd=project)
    ws.executor.default_timeout = 90
    try:
        assert ws.describe()["credentials"][0]["name"] == "ssh"
        config = ws.executor.run("Get-Content (Join-Path $env:USERPROFILE '.ssh\\config')")["output"]
        assert "ProxyCommand" in config and "connect 127.0.0.1" in config and "IdentityFile" in config
        said = _ssh_probe(ws)
        assert "Permission denied (publickey)" in said, said
        inside = ws.executor.run("Join-Path $env:USERPROFILE '.ssh'")["output"].strip()
        assert inside.lower().startswith(r"c:\users\owsandboxclosednet")
    finally:
        ws.close()


@full
def test_full_mode_removes_the_copies_when_the_sandbox_ends(tmp_path):
    """The account's profile persists between sandboxes; the copies must not."""
    from coworker.sandbox import credentials as creds
    from coworker.sandbox.providers.windows import WindowsProvider

    home = _fake_home(tmp_path)
    grants = creds.granted([{"name": "ssh", "enabled": True}], home=str(home))
    project = tmp_path / "project"
    project.mkdir()
    zipapp = build_runner_zipapp(tmp_path / "dist")

    def open_one(with_grants):
        provider = WindowsProvider(roots=[{"path": str(project), "writable": True}], cwd=project, runner_path=zipapp, network=False, credentials=grants if with_grants else ())
        ws = RunnerWorkspace(provider, cwd=project)
        ws.executor.default_timeout = 60
        return ws

    ws = open_one(True)
    try:
        assert ws.executor.run("Test-Path (Join-Path $env:USERPROFILE '.ssh\\id_ed25519')")["output"].strip() == "True"
    finally:
        ws.close()
    ws = open_one(False)
    try:
        assert ws.executor.run("Test-Path (Join-Path $env:USERPROFILE '.ssh')")["output"].strip() == "False"
    finally:
        ws.close()


# -- setup and the full mode ---------------------------------------------------------------


def test_without_setup_the_provider_refuses_and_settings_say_why(monkeypatch, tmp_path):
    """No weaker mode: an explicit choice that cannot be honoured refuses (ruling 18)."""
    from coworker.sandbox import selection, settings
    from coworker.sandbox.providers import windows, windows_setup

    monkeypatch.setattr(windows_setup, "state", lambda: None)
    with pytest.raises(windows.WindowsUnavailable, match="setup has not run"):
        windows.WindowsProvider(roots=[{"path": str(tmp_path), "writable": True}], cwd=tmp_path, runner_path=tmp_path / "r.pyz")
    monkeypatch.setenv("OPENWORKER_SANDBOX_PROVIDER", "windows")
    with pytest.raises(windows.WindowsUnavailable, match="no session will start"):
        selection.select()
    monkeypatch.delenv("OPENWORKER_SANDBOX_PROVIDER")
    row = next(p for p in settings.snapshot()["providers"] if p["name"] == "windows")
    assert not row["usable"] and "setup has not run" in row["why"]


@pytest.mark.skipif(os.environ.get("OPENWORKER_TEST_WINDOWS_SETUP") != "1", reason="changes the machine; OPENWORKER_TEST_WINDOWS_SETUP=1 to run")
def test_setup_creates_the_two_hidden_accounts_and_status_reports_it():
    import subprocess

    from coworker.sandbox import setup_cmd, windows_wfp
    from coworker.sandbox.providers import windows_setup

    said: list[str] = []
    assert setup_cmd.setup(ask=lambda q: True, print_fn=said.append) == 0, "\n".join(said)
    for kind in ("open", "closed"):
        name, sid, password = windows_setup.account(kind)
        assert name == windows_setup.ACCOUNTS[kind] and sid.startswith("S-1-5-21-") and len(password) > 40
        users = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-LocalUser {name}).Enabled"], capture_output=True, text=True).stdout
        assert "True" in users
    for legacy in windows_setup.LEGACY_ACCOUNTS:  # the first build's account is gone
        gone = subprocess.run(["powershell", "-NoProfile", "-Command", f"Get-LocalUser {legacy} -ErrorAction SilentlyContinue; 'end'"], capture_output=True, text=True).stdout
        assert legacy not in gone
    rule = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-NetFirewallRule -DisplayName '{windows_setup.FIREWALL_RULE}').Enabled"], capture_output=True, text=True).stdout
    assert "True" in rule
    assert windows_setup.filters_recorded() and windows_wfp.present() in (True, None)
    windows_rows = said[: next((i for i, line in enumerate(said) if line.strip().startswith("OpenShell on this machine")), len(said))]
    assert any("setup has run" in line for line in windows_rows) and all("[--]" not in line for line in windows_rows)
    info = windows_setup.info()
    assert info["state"] == "ready" and info["set_up_at"].endswith("Z") and info["can_elevate"]


@full
def test_full_mode_hides_the_profile_and_gives_the_sessions_folders(tmp_path):
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "ref.txt").write_text("read me\n")
    ws, provider, project = _open(tmp_path, extra_roots=[{"path": str(reference), "writable": False}])
    try:
        assert ws.describe()["enforcement"] == "full" and ws.describe()["account"] == "OWSandboxClosedNet"
        who = ws.executor.run("$env:USERNAME")["output"].strip()
        assert who.lower() == "owsandboxclosednet"
        # the project sits under the person's profile: the shell can enter it (traverse
        # entries on the parents), write there, and still not list the parents
        assert ws.executor.run("(Get-Location).Path")["output"].strip().lower() == str(project).lower()
        made = ws.executor.run("Set-Content -Path new.txt -Value made; Get-Content new.txt")
        assert made["exit_code"] == 0 and made["output"].strip() == "made", made
        assert ws.executor.run(f"Get-ChildItem '{tmp_path}'")["exit_code"] != 0
        assert "read me" in ws.executor.run(f"Get-Content '{reference / 'ref.txt'}'")["output"]
        assert ws.executor.run(f"Set-Content -Path '{reference / 'no.txt'}' -Value x")["exit_code"] != 0
        assert ws.executor.run("Get-ChildItem $env:USERPROFILE\\..\\Administrator")["exit_code"] != 0  # another account's profile
        listing = ws.executor.run(f"Get-ChildItem '{os.path.expanduser('~')}'")
        assert listing["exit_code"] != 0  # the person's profile: denied
        assert ws.executor.run(f"Set-Content -Path '{tmp_path / 'beside.txt'}' -Value x")["exit_code"] != 0
    finally:
        ws.close()
    from coworker.sandbox import winsec

    assert not winsec.entries_for(str(project), provider.session_sid)
    assert not winsec.entries_for(str(reference), provider.session_sid)
    assert not winsec.entries_for(str(tmp_path), provider.session_sid)  # the traverse entry is gone too
    assert not winsec.entries_for(os.path.expanduser("~"), provider.session_sid)
    assert not os.path.exists(provider._dir)


@full
def test_full_mode_blocks_the_network_except_the_proxy(tmp_path):
    from coworker.sandbox.providers.windows import WindowsProvider

    project = tmp_path / "project"
    project.mkdir()
    zipapp = build_runner_zipapp(tmp_path / "dist")
    provider = WindowsProvider(roots=[{"path": str(project), "writable": True}], cwd=project, runner_path=zipapp, network=True)
    ws = RunnerWorkspace(provider, cwd=project)
    ws.executor.default_timeout = 90
    # curl.exe (shipped with Windows) follows the proxy variables; PowerShell 5's
    # Invoke-WebRequest does not, it uses the system proxy, so it simply sees no network.
    # Windows' own TLS (schannel) checks certificate revocation over plain HTTP, which the
    # firewall blocks: curl needs --ssl-revoke-best-effort; OpenSSL clients (git, Python,
    # Node) do not check that way and are unaffected.
    try:
        direct = ws.executor.run("curl.exe -sS --noproxy '*' -m 8 -o NUL -w '%{http_code}' https://example.com; 'exit ' + $LASTEXITCODE")
        assert "200" not in direct["output"] and "exit 0" not in direct["output"], direct["output"]
        via = ws.executor.run("curl.exe -sS --ssl-revoke-best-effort -m 30 -o NUL -w '%{http_code}' https://api.github.com")
        assert "200" in via["output"], via["output"]
        refused = ws.executor.run("curl.exe -sS --ssl-revoke-best-effort -m 30 -o NUL -w '%{http_code}' https://example.com")
        assert "403" in refused["output"], refused["output"]
        # loopback: the proxy's port range and nothing else (the WFP filters from setup)
        from coworker.sandbox import netproxy

        assert provider._proxy is not None and provider._proxy.port in netproxy.WINDOWS_PORTS
        assert ws.client.call("net.probe", {"host": "127.0.0.1", "port": provider._proxy.port}, timeout=15)["ok"]
        other = ws.client.call("net.probe", {"host": "127.0.0.1", "port": 22}, timeout=15)  # sshd listens on the VM
        assert not other["ok"], other
        assert ws.describe()["enforcement"] == "full"
    finally:
        ws.close()


@full
def test_a_stale_private_folder_is_reaped_when_the_next_sandbox_starts(tmp_path):
    from coworker.sandbox.providers import windows_setup

    stale = windows_setup.SANDBOXES / "owr-stale0000"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "daemon.log").write_text("left behind\n")
    ws, provider, project = _open(tmp_path)
    try:
        assert not stale.exists()  # gone: no daemon listens on its pipe
        assert os.path.isdir(provider._dir)  # the live one stays
    finally:
        ws.close()


@full
def test_full_mode_reads_the_toolchain_folders_it_is_given(tmp_path):
    """A folder under the person's profile (invisible to the account) becomes readable,
    never writable, when it is on the machine's toolchain list."""
    from coworker.sandbox.providers.windows import WindowsProvider

    tools = tmp_path / "AppData" / "Roaming" / "npm"  # tmp_path is under the Administrator's profile
    tools.mkdir(parents=True)
    (tools / "tool.cmd").write_text("@echo tool ran\r\n")
    project = tmp_path / "project"
    project.mkdir()
    zipapp = build_runner_zipapp(tmp_path / "dist")
    provider = WindowsProvider(roots=[{"path": str(project), "writable": True}], cwd=project, runner_path=zipapp, network=False, tool_dirs=[str(tools)])
    ws = RunnerWorkspace(provider, cwd=project)
    ws.executor.default_timeout = 60
    try:
        assert "tool ran" in ws.executor.run(f"& '{tools / 'tool.cmd'}'")["output"]
        assert ws.executor.run(f"Set-Content -Path '{tools / 'no.txt'}' -Value x")["exit_code"] != 0  # read only
        assert ws.executor.run(f"Get-ChildItem '{tmp_path}'")["exit_code"] != 0  # the folder beside it stays hidden
        assert "Administrator" in ws.executor.run("$env:PATH")["output"]  # the person's PATH came along
    finally:
        ws.close()
    from coworker.sandbox import winsec

    assert not winsec.entries_for(str(tools), provider.session_sid)


@full
def test_the_open_profile_runs_as_the_open_account_with_the_network_open(tmp_path):
    ws, provider, project = _open(tmp_path, network=True, profile="open")
    ws.executor.default_timeout = 90
    try:
        assert provider.kind == "open" and provider._proxy is None
        who = ws.executor.run("$env:USERNAME")["output"].strip()
        assert who.lower() == "owsandboxopennet"
        direct = ws.executor.run("curl.exe -sS --ssl-revoke-best-effort -m 20 -o NUL -w '%{http_code}' https://example.com")
        assert "200" in direct["output"], direct["output"]
        assert ws.client.call("net.probe", {"host": "127.0.0.1", "port": 22}, timeout=15)["ok"]  # any local port
        listing = ws.executor.run(f"Get-ChildItem '{os.path.expanduser('~')}'")
        assert listing["exit_code"] != 0  # the files are still the wall
        assert ws.describe()["enforcement"] == "full" and "open" in ws.describe()["reason"]
    finally:
        ws.close()
