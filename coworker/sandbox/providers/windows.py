"""`windows`: the tool runner on Windows, confined by Windows' own account boundary.

Same runner, same protocol. The one-time setup (windows_setup.py) must have run; without
it the provider refuses (ruling 18: an explicit choice that cannot be honoured refuses,
it never runs open). The daemon is logged on as a hidden local account
(CreateProcessWithLogonW, the secondary logon service; no privilege needed), chosen by the
network profile: `OWSandboxClosedNet` for the allow-list profiles, `OWSandboxOpenNet` for
`open` (ruling 3d.2). Windows keeps both out of the person's profile: `.ssh`, `.aws`,
documents, OpenWorker's own state and keys cannot be read. The session's folders get one
inheritable entry each for the account (Modify for writable, Read for read-only); the
sandbox's private folder lives under `C:\\ProgramData\\OpenWorker\\sandbox\\sandboxes`. For the
closed account a firewall rule from setup blocks every outbound connection and WFP filters
close loopback except the proxy's port range (netproxy.WINDOWS_PORTS), so the internet is
reachable only through our allow-list proxy; `verify()` proves both from inside before the
session starts. The open account has no rules: any host, any local port, files still
confined. Folders outside the profile (`C:\\work`) are readable by any local account by
default (spike finding A); that is stated, not hidden (ruling 3d.5).

The entries are removed at close, every process the daemon starts inherits
the confinement, and the daemon lives in a job object that ends with the sandbox.

Learned on the VM (2026-09-22): the restricting list must hold the LOGON SID or no process
starts at all (0xC0000142); the token's default DACL must name the session SID or the
process cannot start children (error 5); a console is fine once both hold; a process logged
on as another account needs a desktop of ours that names it, or its PowerShell prints
nothing.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Sequence

from .. import credentials as creds
from .. import netproxy, network_profiles
from ..bundle import build_runner_zipapp
from ..launch import runner_command, serve_arguments, spawn_kwargs, wait_for_runner
from ..runner import winpipe
from ..transport import PipeTransport, Transport
from . import windows_setup

FULL = "full"
_FULL_REASON = (
    "Windows sandbox account: the user's profile (keys, documents, OpenWorker's state) is out of reach; files limited to"
    " the session's folders plus what any local account may read outside profiles"
)


class WindowsUnavailable(RuntimeError):
    """The Windows sandbox cannot be used here. The message says why."""


def preflight() -> None:
    """Usable here: Windows, this process can read its token, setup of this version has run
    and this user can read the accounts' credentials. The message says what is missing."""
    if sys.platform != "win32":
        raise WindowsUnavailable("The Windows sandbox exists only on Windows.")
    from .. import winsec

    try:
        winsec.current_user_sid()
    except OSError as exc:
        raise WindowsUnavailable(f"cannot read this process's token: {exc}") from None
    problem = windows_setup.problem()
    if problem:
        raise WindowsUnavailable(problem)


class WindowsProvider:
    name = "windows"

    def __init__(
        self,
        *,
        roots: Sequence[dict[str, Any]],
        cwd: str | Path,
        profile: str = network_profiles.DEFAULT_PROFILE,
        network: bool = True,
        runner_path: Optional[Path] = None,
        relay_silence_seconds: Optional[float] = None,
        credentials: Sequence[creds.Grant] = (),
        tool_dirs: Sequence[str] = (),
    ) -> None:
        """`credentials`: the grants (credentials.granted) to copy into the sandbox.
        `tool_dirs`: developer tool folders under the profile the account may read."""
        self.roots = _clean_roots(roots)
        self.grants = list(credentials)
        self.tool_dirs = [os.path.realpath(p) for p in tool_dirs]
        self.copied: Optional[creds.CopiedCredentials] = None
        self.cwd = os.path.realpath(str(cwd))
        self.profile = network_profiles.check(profile)
        self.network = network
        self._runner = Path(runner_path) if runner_path is not None else build_runner_zipapp()
        self._relay_silence = relay_silence_seconds
        self.sandbox = f"sb-{uuid.uuid4().hex[:12]}"
        preflight()
        self.open_network = network and network_profiles.is_open(self.profile)
        # The account is the network mode: closed (allow list through the proxy) or open.
        self.kind = windows_setup.OPEN if self.open_network else windows_setup.CLOSED
        account = windows_setup.account(self.kind)
        if account is None:
            raise WindowsUnavailable(windows_setup.problem() or f"the {self.kind} sandbox account is not usable; run `openworker machine sandbox setup` again")
        self._account = account
        # The entries on the folders and the pipe's descriptor name the account.
        self.session_sid = account[1]
        windows_setup.SANDBOXES.mkdir(parents=True, exist_ok=True)
        windows_setup.reap_private_folders()  # what a server killed hard left behind
        self._dir = tempfile.mkdtemp(prefix="owr-", dir=str(windows_setup.SANDBOXES))
        self.socket_path = winpipe.pipe_name(os.path.basename(self._dir))
        self._daemon: Any = None
        self._proxy: Optional[netproxy.AllowListProxy] = None
        self._desktop: Any = None
        self._granted: list[tuple[str, str]] = []  # (folder, "write" | "read") entries that exist right now
        self._runner_inside: Path = self._runner  # where the sandbox sees the runner file
        self.notes: list[str] = []  # what the agent must be told beyond the credentials list

    # -- what it is ---------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        if not self.network:
            network = "blocked"
        elif self.open_network:
            network = "open (any host, any local port; the 'open' profile)"
        else:
            network = f"the '{self.profile}' profile through the allow-list proxy; the rest is blocked by the firewall and the loopback filters"
        return {
            "provider": self.name,
            "sandbox": self.sandbox,
            "enforcement": FULL,
            "account": self._account[0],
            "reason": f"{_FULL_REASON}. Network: {network}",
            "credentials": self.copied.describe() if self.copied is not None else [],
            "notes": list(self.notes),
        }

    # -- start ------------------------------------------------------------------------
    def create(self) -> None:
        try:
            self._create()
        except BaseException:
            self.destroy()  # nothing of a failed sandbox stays behind (entries, folder, token)
            raise

    def _create(self) -> None:
        preflight()
        from .. import winsec

        hosts = sorted({h for g in self.grants for h in g.hosts})
        if self.network and not self.open_network:
            self._proxy = netproxy.AllowListProxy(self.profile, extra_hosts=hosts) if hosts else netproxy.shared(self.profile)
        for entry in self._wanted_entries():
            self._grant(*entry)
        self._desktop = winsec.Desktop(self.session_sid)
        log = os.path.join(self._dir, "daemon.log")
        name, sid, password = self._account
        # The runner file lives in the user's state folder, which the account cannot see.
        self._runner_inside = Path(self._dir) / self._runner.name
        shutil.copy2(self._runner, self._runner_inside)
        # The environment goes through a file in the private folder (the account reads it):
        # CreateProcessWithLogonW allows 1024 characters of command line, and PATH alone can
        # be longer than that.
        import json

        env_file = os.path.join(self._dir, "env.json")
        Path(env_file).write_text(json.dumps(self._environment()), encoding="utf-8")
        serve = serve_arguments(self.socket_path, self._dir, also_sids=[sid])
        argv = [*runner_command(self._runner_inside), "serve", *serve, "--env-file", env_file, "--cwd", self.cwd, "--exit-with-parent"]
        self._daemon = winsec.spawn_as_account(argv, account=name, password=password, desktop=self._desktop, cwd=self.cwd, stderr_path=log)
        try:
            wait_for_runner(self.socket_path, self._daemon)
        except RuntimeError as exc:
            said = Path(log).read_text(errors="replace").strip() if os.path.exists(log) else ""
            raise WindowsUnavailable(f"the sandboxed tool runner did not start: {exc} {said[-400:]}".strip()) from None

    def _environment(self) -> dict[str, str]:
        """Only what the account's own environment lacks: the person's PATH (the account's
        knows nothing of their tools) and the proxy. The account has a profile, temp and
        caches of its own."""
        env: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
        if self._proxy is not None:
            env.update(netproxy.environment(self._proxy))
        return env

    def _ssh_proxy_command(self) -> Optional[str]:
        if self._proxy is None:
            return None
        return creds.windows_ssh_proxy_command(sys.executable, str(self._runner_inside), self._proxy.port)

    _CLEARED = (".ssh", ".config/gh", ".aws", ".kube", "bin")

    def provision(self, client: Any) -> None:
        """The copies of granted credentials go into the ACCOUNT's own profile, written by
        the daemon so that the account owns them (Windows OpenSSH refuses a key file owned
        by someone else). Whatever an earlier sandbox left there is removed first; the
        daemon removes them again when it leaves."""
        from ..runner.protocol import RunnerError

        home_inside = str(client.hello.get("home") or "")
        if not home_inside:
            raise WindowsUnavailable("the sandboxed runner did not report its home folder")
        for rel in self._CLEARED:
            try:
                client.call("fs.remove", {"path": os.path.join(home_inside, rel), "recursive": True}, timeout=30)
            except RunnerError:
                pass  # nothing there
        if not self.grants:
            return
        staging = tempfile.mkdtemp(prefix="owc-")
        try:
            self.copied = creds.copy_in(self.grants, staging, inside_home=home_inside, ssh_proxy_command=self._ssh_proxy_command(), windows=True)
            shipped: list[str] = []
            for folder, _dirs, files in os.walk(self.copied.home):
                rel_folder = os.path.relpath(folder, self.copied.home)
                for name in files:
                    rel = name if rel_folder == "." else os.path.join(rel_folder, name)
                    data = Path(folder, name).read_bytes()
                    client.call("fs.write", {"path": os.path.join(home_inside, rel), "data_b64": base64.b64encode(data).decode(), "make_parents": True}, timeout=30)
                    shipped.append(rel.split(os.sep)[0])
            client.call("runner.cleanup_at_exit", {"paths": sorted({os.path.join(home_inside, top) for top in shipped})}, timeout=15)
            client.call("env.set", {"vars": self.copied.env}, timeout=15)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def open_runner(self) -> Transport:
        argv = [*runner_command(self._runner), "attach", "--socket", self.socket_path]
        if self._relay_silence is not None:
            argv += ["--silence-seconds", str(self._relay_silence)]
        return PipeTransport(
            subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, **spawn_kwargs())
        )

    def verify(self, client: Any) -> None:
        """Every folder is reachable inside, and the wall is really up: the user's profile
        cannot be listed, and in the closed mode no local port but the proxy's is reachable."""
        from ..runner.protocol import RunnerError

        for root in self.roots:
            client.call("fs.list", {"path": root["path"], "limit": 1}, timeout=15)
        home = os.path.realpath(os.path.expanduser("~"))
        if any(home == r["path"] or home.startswith(r["path"] + os.sep) for r in self.roots):
            return  # the user granted the home folder itself; nothing to prove
        try:
            client.call("fs.list", {"path": home, "limit": 1}, timeout=15)
        except RunnerError:
            self._verify_loopback(client)
            return
        raise WindowsUnavailable("the sandbox did not take effect: the home folder can be listed from inside")

    def _verify_loopback(self, client: Any) -> None:
        """The closed account may reach the proxy on loopback and nothing else there
        (ruling 3d.2). A listener of ours outside the proxy's range must be unreachable;
        the proxy must be reachable. Anything else means setup is stale: refuse."""
        if self.open_network or not self.network or self._proxy is None:
            return
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            probe.listen(1)
            port = probe.getsockname()[1]
            if port in netproxy.WINDOWS_PORTS:
                return  # the kernel picked a port inside the proxy's range; nothing to prove
            reached = client.call("net.probe", {"host": "127.0.0.1", "port": port}, timeout=15)
        finally:
            probe.close()
        if reached.get("ok"):
            raise WindowsUnavailable(
                "the sandbox did not take effect: a local port outside the proxy's range can be reached from inside"
                " (the loopback filters are missing; run `openworker machine sandbox setup` again)"
            )
        via = client.call("net.probe", {"host": "127.0.0.1", "port": self._proxy.port}, timeout=15)
        if not via.get("ok"):
            raise WindowsUnavailable(f"the sandbox cannot reach the allow-list proxy on port {self._proxy.port}: {via.get('error')}")

    # -- the entries ----------------------------------------------------------------------
    def _wanted_entries(self) -> list[tuple[str, str]]:
        """Every entry this sandbox needs right now: Modify on writable roots and the
        private folder, Read on read-only roots and the tool folders, and Traverse (no
        listing) on each folder between a granted folder and the profile root, so a shell
        can enter a folder under the profile. The profile itself stays unlistable."""
        wanted = [(r["path"], "write" if r["writable"] else "read") for r in self.roots] + [(self._dir, "write")]
        wanted += [(folder, "read") for folder in self.tool_dirs if os.path.isdir(folder)]
        home = os.path.realpath(os.path.expanduser("~"))
        for folder, _kind in list(wanted):
            parent = os.path.dirname(folder)
            while parent.lower().startswith(home.lower()) and len(parent) >= len(home):
                if (parent, "traverse") not in wanted and not any(f.lower() == parent.lower() and k != "traverse" for f, k in wanted):
                    wanted.append((parent, "traverse"))
                if parent.lower() == home.lower():
                    break
                parent = os.path.dirname(parent)
        return wanted

    def _grant(self, folder: str, kind: str) -> None:
        from .. import winsec

        {"write": winsec.grant_write, "read": winsec.grant_read, "traverse": winsec.grant_traverse}[kind](folder, self.session_sid)
        self._granted.append((folder, kind))

    def _revoke(self, folder: str, kind: str) -> None:
        from .. import winsec

        try:
            winsec.revoke(folder, self.session_sid)
        except OSError:
            pass  # a folder that is gone has no entry to remove
        self._granted.remove((folder, kind))

    # -- changes ------------------------------------------------------------------------
    restarts_on_regrant = False

    def regrant(self, roots: Sequence[dict[str, Any]]) -> None:
        """The session's folders changed. The confinement is per sandbox, not per folder,
        so the entries move and the daemon stays: a new folder is usable at once, a removed
        one is closed at once."""
        self.roots = _clean_roots(roots)
        wanted = self._wanted_entries()
        for entry in list(self._granted):
            if entry not in wanted:
                self._revoke(*entry)
        for entry in wanted:
            if entry not in self._granted:
                self._grant(*entry)

    def restart_daemon(self) -> None:
        """Tests only: what a sandbox restart looks like from the client's side."""
        self._stop_daemon()
        for entry in list(self._granted):
            self._revoke(*entry)
        if self._desktop is not None:
            self._desktop.close()
        self._create()

    # -- stop ---------------------------------------------------------------------------
    def _stop_daemon(self) -> None:
        daemon, self._daemon = self._daemon, None
        if daemon is None:
            return
        if daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        daemon.close()

    def destroy(self) -> None:
        self._stop_daemon()
        for entry in list(self._granted):
            self._revoke(*entry)
        if self._desktop is not None:
            self._desktop.close()
        self._desktop = None
        if self._proxy is not None and self._proxy is not netproxy._proxies.get(self.profile):
            self._proxy.close()  # this session's own proxy; the shared one stays
        self._proxy = None
        # The job's last processes (cmd holding the log) may still be going; give them a moment.
        for _ in range(20):
            shutil.rmtree(self._dir, ignore_errors=True)
            if not os.path.exists(self._dir):
                break
            time.sleep(0.1)


def _clean_roots(roots: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"path": os.path.realpath(r["path"]), "writable": bool(r.get("writable"))} for r in roots]
