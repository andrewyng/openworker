"""`openworker machine sandbox status|setup`: get a machine ready to run agents in
OpenShell sandboxes (design doc, ruling 23).

`setup` shows every change before it makes it and asks first (or `--yes`). It never runs
`sudo` for you; when a step needs it, it prints the command. Someone who brings their own
OpenShell can ignore `setup` and use `status` to see what is missing.

Hidden from `openworker --help` until it has been tried on a fresh machine.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .. import config as app_config
from .providers.openshell import PINNED_VERSION
from .selection import openshell_problem, select

_INSTALLER = "https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh"
_BIND_MOUNTS = "[openshell.drivers.docker]\nenable_bind_mounts = true\n"


def _run(argv: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def _openshell_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "openshell"


def checks() -> list[tuple[str, bool, str]]:
    """(what, ok, detail) for everything OpenShell sandboxes need on this machine."""
    out: list[tuple[str, bool, str]] = []
    docker = shutil.which("docker")
    docker_ok = bool(docker) and _run(["docker", "info", "--format", "{{.ServerVersion}}"], 30).returncode == 0
    out.append(("Docker is installed and this user can use it", docker_ok, "" if docker_ok else "install Docker, and add this user to the `docker` group"))
    exe = shutil.which("openshell")
    version = _run([exe, "--version"], 15).stdout.strip().split()[-1] if exe else ""
    out.append((f"OpenShell {PINNED_VERSION} is installed", version == PINNED_VERSION, f"found {version or 'none'}"))
    gateway_toml = _openshell_home() / "gateway.toml"
    binds = gateway_toml.is_file() and "enable_bind_mounts = true" in gateway_toml.read_text(encoding="utf-8")
    out.append(("the gateway allows bind mounts (your folders reach a sandbox this way)", binds, str(gateway_toml)))
    if sys.platform.startswith("linux"):
        linger = _run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger"], 15).stdout.strip() == "Linger=yes"
        out.append(("the gateway keeps running after you log out (linger)", linger, "" if linger else f"sudo loginctl enable-linger {getpass.getuser()}"))
    problem = openshell_problem(fresh=True) if exe else "OpenShell is not installed"
    out.append(("the gateway is running", problem is None, problem or ""))
    try:
        import grpc  # noqa: F401

        grpc_ok = True
    except ImportError:
        grpc_ok = False
    out.append(("the `grpcio` package is installed", grpc_ok, "" if grpc_ok else "pip install 'openworker[openshell]'"))
    configured = app_config.load_config().sandbox_provider
    out.append(("this machine is set to use OpenShell", configured == "openshell", f"sandbox_provider = {configured!r} in {app_config.global_config_path()}"))
    return out


def seatbelt_check() -> tuple[str, bool, str]:
    from .providers import seatbelt

    try:
        seatbelt.preflight()
        return ("the macOS sandbox (Seatbelt) can be used", True, "")
    except seatbelt.SeatbeltUnavailable as exc:
        return ("the macOS sandbox (Seatbelt) can be used", False, str(exc))


def status(print_fn: Callable[[str], None] = print) -> int:
    if sys.platform == "darwin":
        what, ok, detail = seatbelt_check()
        print_fn(f"  [{'ok' if ok else '--'}] {what}" + (f"  ({detail})" if detail else ""))
        print_fn("       to use it: set `sandbox_provider = \"seatbelt\"` in " + str(app_config.global_config_path()))
        print_fn("\nOpenShell on this machine:")
    rows = checks()
    for what, ok, detail in rows:
        print_fn(f"  [{'ok' if ok else '--'}] {what}" + (f"  ({detail})" if detail and not ok else ""))
    try:
        chosen = select(app_config.load_config().sandbox_provider, headless=True)
        print_fn(f"\nsessions on this machine run: {chosen.provider}" + (" (set explicitly)" if chosen.explicit else " (default rule)"))
        if chosen.warning:
            print_fn(chosen.warning)
    except Exception as exc:
        print_fn(f"\nsessions on this machine are REFUSED: {exc}")
    return 0 if all(ok for _, ok, _ in rows) else 1


def setup(*, yes: bool = False, ask: Optional[Callable[[str], bool]] = None, print_fn: Callable[[str], None] = print) -> int:
    confirm = ask or (lambda question: yes or input(f"{question} [y/N] ").strip().lower() in ("y", "yes"))
    if not sys.platform.startswith("linux"):
        print_fn("`sandbox setup` configures a Linux machine. On a Mac, install OpenShell yourself and use `openworker machine sandbox status`.")
        return 2
    rows = {what: ok for what, ok, _ in checks()}
    if not rows["Docker is installed and this user can use it"]:
        print_fn("Docker is needed first, and installing it needs an administrator:\n  https://docs.docker.com/engine/install/\n  sudo usermod -aG docker $USER   (then log in again)")
        return 1
    if not rows[f"OpenShell {PINNED_VERSION} is installed"]:
        command = f"curl -LsSf {_INSTALLER} | OPENSHELL_VERSION=v{PINNED_VERSION} sh"
        print_fn(f"OpenShell {PINNED_VERSION} is not installed. NVIDIA's installer downloads a package from their GitHub release,\nchecks its checksum and installs it (it will ask for sudo itself):\n  {command}")
        if not confirm("Run NVIDIA's installer now?"):
            return 1
        if subprocess.run(["sh", "-c", command]).returncode != 0:
            print_fn("the installer failed; nothing else was changed")
            return 1
    home = _openshell_home()
    gateway_toml, gateway_env = home / "gateway.toml", home / "gateway.env"
    if not rows["the gateway allows bind mounts (your folders reach a sandbox this way)"]:
        print_fn(f"Allow bind mounts, so a sandbox can be given your folders (and nothing else):\n  write to {gateway_toml}:\n    " + _BIND_MOUNTS.replace("\n", "\n    ").rstrip() + f"\n  point the gateway at it in {gateway_env}, then restart the gateway (running sandboxes restart too)")
        if not confirm("Make this change?"):
            return 1
        home.mkdir(parents=True, exist_ok=True)
        existing = gateway_toml.read_text(encoding="utf-8") if gateway_toml.is_file() else ""
        if "[openshell.drivers.docker]" in existing:
            print_fn(f"{gateway_toml} already has a [openshell.drivers.docker] table; add `enable_bind_mounts = true` to it by hand.")
            return 1
        gateway_toml.write_text((existing.rstrip() + "\n\n" if existing.strip() else "") + _BIND_MOUNTS, encoding="utf-8")
        env_line = f"OPENSHELL_GATEWAY_CONFIG={gateway_toml}\n"
        env_text = gateway_env.read_text(encoding="utf-8") if gateway_env.is_file() else ""
        if "OPENSHELL_GATEWAY_CONFIG=" not in env_text:
            gateway_env.write_text(env_text + env_line, encoding="utf-8")
        _run(["systemctl", "--user", "restart", "openshell-gateway"], 120)
    if not rows.get("the gateway keeps running after you log out (linger)", True):
        user = getpass.getuser()
        if _run(["loginctl", "enable-linger", user], 30).returncode != 0:
            print_fn(f"The gateway is a user service and stops when you log out. An administrator has to run:\n  sudo loginctl enable-linger {user}")
    if not rows["this machine is set to use OpenShell"]:
        print_fn(f"Set this machine to run agents in OpenShell sandboxes, and to REFUSE sessions when OpenShell is not running:\n  sandbox_provider = \"openshell\" in {app_config.global_config_path()}")
        if confirm("Make this change?"):
            app_config.set_global_value("sandbox_provider", "openshell")
    print_fn("")
    return status(print_fn)
