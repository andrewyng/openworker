"""`seatbelt`: the tool runner as a process on this Mac, confined by the macOS sandbox.

The same runner and the same protocol as every other provider. What differs is the wall:
the daemon is started through `sandbox-exec` with a profile rendered from the session's
folders (seatbelt_profile.py), and every command it starts inherits that profile. The
kernel enforces it; a process cannot take it off.

- Files: the session's folders, one temporary folder, the system, a list of developer
  toolchains. Nothing else under the home folder.
- Network: only the server's allow-list proxy (netproxy.py).
- Environment: the server's own variables are not passed on when their name says they hold
  a secret, so a model key in the server's environment never reaches a command.

The attach relay runs OUTSIDE the sandbox: it is our code, it only moves bytes between the
server and the daemon's Unix socket, and nothing the agent says is executed by it.
"""

from __future__ import annotations

import os
import re
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
from ..launch import read_paths, runner_command
from ..transport import PipeTransport, Transport
from . import seatbelt_profile

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION", re.IGNORECASE)
_OWN_PREFIXES = ("COWORKER_", "OPENWORKER_")
# Caches that tools keep under the home folder, moved into the sandbox's temporary folder.
_CACHE_VARIABLES = {
    "npm_config_cache": "npm",
    "PIP_CACHE_DIR": "pip",
    "UV_CACHE_DIR": "uv",
    "XDG_CACHE_HOME": "xdg",
    "GOCACHE": "go-build",
    "YARN_CACHE_FOLDER": "yarn",
    "PYTHONPYCACHEPREFIX": "pycache",
}


class SeatbeltUnavailable(RuntimeError):
    """The macOS sandbox cannot be used here. The message says why."""


def preflight() -> None:
    if sys.platform != "darwin":
        raise SeatbeltUnavailable("The Seatbelt sandbox exists only on macOS.")
    if not os.access(SANDBOX_EXEC, os.X_OK):
        raise SeatbeltUnavailable(f"{SANDBOX_EXEC} is missing on this Mac.")
    probe = subprocess.run(
        [SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if probe.returncode != 0:
        # The usual cause: this process is itself inside a sandbox, and macOS does not nest them.
        raise SeatbeltUnavailable(f"sandbox-exec could not apply a profile: {probe.stderr.strip() or probe.returncode}")


def clean_environment(base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """The server's environment without anything whose name says it is a secret, and without
    OpenWorker's own variables (one of them is the server's API token)."""
    source = os.environ if base is None else base
    return {k: v for k, v in source.items() if not k.startswith(_OWN_PREFIXES) and not _SECRET_NAME.search(k)}


class SeatbeltProvider:
    name = "seatbelt"

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
    ) -> None:
        """`credentials`: the grants (credentials.granted) to copy into the sandbox."""
        self.roots = [{"path": seatbelt_profile.real(r["path"]), "writable": bool(r.get("writable"))} for r in roots]
        self.grants = list(credentials)
        self.copied: Optional[creds.CopiedCredentials] = None
        self.cwd = seatbelt_profile.real(cwd)
        self.profile = network_profiles.check(profile)
        self.network = network
        self._runner = Path(runner_path) if runner_path is not None else build_runner_zipapp()
        self._relay_silence = relay_silence_seconds
        self.sandbox = f"sb-{uuid.uuid4().hex[:12]}"
        # Short, because a Unix socket path is limited to about 100 bytes; resolved, because
        # Seatbelt matches real paths and `/tmp` is a link on macOS.
        self._dir = seatbelt_profile.real(tempfile.mkdtemp(prefix="owr-", dir="/tmp"))
        self.socket_path = os.path.join(self._dir, "r.sock")
        self._daemon: Optional[subprocess.Popen] = None
        self._proxy: Optional[netproxy.AllowListProxy] = None

    def describe(self) -> dict[str, Any]:
        network = f"only the hosts of the '{self.profile}' profile, through the allow-list proxy" if self.network else "none"
        return {
            "provider": self.name,
            "sandbox": self.sandbox,
            "enforcement": "full",
            "reason": f"macOS sandbox: files limited to the session's folders; network: {network}",
            "credentials": self.copied.describe() if self.copied is not None else [],
        }

    def profile_text(self) -> str:
        from ... import toolchain

        return seatbelt_profile.render(
            self.roots,
            runtime_dir=self._dir,
            read_only=[str(self._runner), *read_paths(), str(toolchain.bin_dir())],
            proxy_port=self._proxy.port if self._proxy is not None else None,
        )

    def _environment(self) -> dict[str, str]:
        env = clean_environment()
        if self.copied is not None:
            env.update(self.copied.env)  # HOME is the sandbox's own; the copies live there
        tmp = os.path.join(self._dir, "tmp")
        env["TMPDIR"] = tmp + "/"
        for variable, folder in _CACHE_VARIABLES.items():
            env[variable] = os.path.join(tmp, "cache", folder)
        if self._proxy is not None:
            env.update(netproxy.environment(self._proxy))
        return env

    def create(self) -> None:
        try:
            self._create()
        except Exception:
            self.destroy()  # never leave a copied credential behind
            raise

    def _create(self) -> None:
        preflight()
        os.makedirs(os.path.join(self._dir, "tmp", "cache"), exist_ok=True)
        if self.network:
            hosts = sorted({h for g in self.grants for h in g.hosts})
            # A session with grants gets its own proxy, because its allow list is its own.
            self._proxy = netproxy.AllowListProxy(self.profile, extra_hosts=hosts) if hosts else netproxy.shared(self.profile)
        else:
            self._proxy = None
        self.copied = creds.copy_in(
            self.grants,
            self._dir,
            proxy_port=self._proxy.port if self._proxy is not None else None,
            ssh_proxy_command=creds.mac_ssh_proxy_command(self._proxy.port) if self._proxy is not None else None,
        )
        log = open(os.path.join(self._dir, "daemon.log"), "wb")
        self._daemon = subprocess.Popen(
            [
                SANDBOX_EXEC, "-p", self.profile_text(),
                *runner_command(self._runner),
                "serve", "--socket", self.socket_path, "--cwd", self.cwd, "--exit-with-parent",
            ],  # fmt: skip
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,  # a file, not a pipe: nobody reads a pipe while the daemon lives
            env=self._environment(),
            cwd="/",
            start_new_session=True,
        )
        log.close()
        deadline = time.monotonic() + 15
        while not os.path.exists(self.socket_path):
            if self._daemon.poll() is not None:
                said = Path(self._dir, "daemon.log").read_text(errors="replace").strip()
                raise SeatbeltUnavailable(f"the sandboxed tool runner exited at once (code {self._daemon.returncode}). {said[-400:]}")
            if time.monotonic() > deadline:
                raise SeatbeltUnavailable("the sandboxed tool runner did not come up in 15 seconds")
            time.sleep(0.02)

    def open_runner(self) -> Transport:
        argv = [*runner_command(self._runner), "attach", "--socket", self.socket_path]
        if self._relay_silence is not None:
            argv += ["--silence-seconds", str(self._relay_silence)]
        return PipeTransport(
            subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        )

    def verify(self, client: Any) -> None:
        """Every folder is reachable inside, and the wall is really up: the home folder,
        which no session is given as a whole, must NOT be listable."""
        from ..runner.protocol import RunnerError

        for root in self.roots:
            client.call("fs.list", {"path": root["path"], "limit": 1}, timeout=15)
        home = seatbelt_profile.real("~")
        if any(home == r["path"] or home.startswith(r["path"] + os.sep) for r in self.roots):
            return  # the user granted the home folder itself; nothing to prove
        try:
            client.call("fs.list", {"path": home, "limit": 1}, timeout=15)
        except RunnerError:
            return
        raise SeatbeltUnavailable("the sandbox did not take effect: the home folder can be listed from inside")

    def regrant(self, roots: Sequence[dict[str, Any]]) -> None:
        """The session's folders changed. A profile is fixed when a process starts, so the
        daemon is started again with a new one; the client sees a runner restart."""
        self.roots = [{"path": seatbelt_profile.real(r["path"]), "writable": bool(r.get("writable"))} for r in roots]
        self._stop_daemon()
        self.create()

    def restart_daemon(self) -> None:
        """Tests only: what a sandbox restart looks like from the client's side."""
        self._stop_daemon()
        self.create()

    def _stop_daemon(self) -> None:
        if self._daemon is not None and self._daemon.poll() is None:
            self._daemon.terminate()
            try:
                self._daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._daemon.kill()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    def destroy(self) -> None:
        self._stop_daemon()
        if self._proxy is not None and self._proxy is not netproxy._proxies.get(self.profile):
            self._proxy.close()  # this session's own proxy; the shared one stays
        shutil.rmtree(self._dir, ignore_errors=True)
