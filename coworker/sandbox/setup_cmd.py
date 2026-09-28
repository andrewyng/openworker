"""`openworker machine sandbox status|setup`: get a machine ready to run agents in
OpenShell sandboxes (design doc, ruling 23).

`setup` shows every change before it makes it and asks first (or `--yes`). Someone who
brings their own OpenShell can ignore `setup` and use `status` to see what is missing.

The same steps serve Settings ▸ Sandbox (OPE-207): `steps()` is the readiness list the page
shows, and `setup_job.SetupJob` runs the fixes, with progress. One list of checks and fixes,
two front ends.

Two steps need an administrator on Linux: installing the OpenShell package, and `linger`
(the gateway is a user service and stops at logout otherwise). The app runs them itself
when it can do so without holding a password: through `sudo` when this user may use it
without one, or through the system's own password prompt (`pkexec`) on a Linux desktop.
Where neither exists (WSL has no prompt), the two are folded into ONE command for the
user to run, and the job stops there until "check again". A Mac needs no administrator:
Homebrew installs OpenShell as the user, and there is no linger. The app never asks for,
sees or stores a password.

Hidden from `openworker --help` until it has been tried on a fresh machine.
"""

from __future__ import annotations

import ctypes
import getpass
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .. import config as app_config
from .providers import openshell
from .providers.openshell import PINNED_VERSION
from .selection import openshell_problem, select

_INSTALLER = "https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh"
# Where the page's "Guide" links point: how OpenShell fits, what a machine needs, setup.
GUIDE_URL = "https://github.com/andrewyng/openworker/blob/main/docs/openshell.md"
DOCKER_INSTALL_URL = "https://docs.docker.com/engine/install/"
_BIND_MOUNTS = "[openshell.drivers.docker]\nenable_bind_mounts = true\n"
# The base image is pulled by the first `sandbox create` otherwise, which on a slow link
# outlives the create's timeout and hangs the first session (OPE-205). So it is a check of
# its own, and `setup` offers the download with Docker's own progress and no time limit.
IMAGE_ROW = "the sandbox base image is downloaded (about 5 GB, one time)"
# Unpacked, the image is larger than its download; this much free space avoids a failed
# pull halfway (OPE-207).
IMAGE_FREE_GB = 10

# Row titles, by key. Kept as constants: tests and `setup()` look rows up by title.
ROWS = {
    "docker": "Docker is installed and this user can use it",
    "openshell": f"OpenShell {PINNED_VERSION} is installed",
    "bind_mounts": "the gateway allows bind mounts (your folders reach a sandbox this way)",
    "linger": "the gateway keeps running after you log out (linger)",
    "gateway": "the gateway is running",
    "grpcio": "the `grpcio` package is installed",
    "landlock": "the kernel supports Landlock (OpenShell requires it)",
    "config": "this machine is set to use OpenShell",
    "disk": f"enough free disk space for the base image (about {IMAGE_FREE_GB} GB)",
    "image": IMAGE_ROW,
}
# Steps the app may fix on its own without an administrator. `openshell` joins them at
# run time when `admin_prefix()` finds a way to run as one; see `steps()`.
FIXABLE = {"bind_mounts", "config", "image", "linger"}


@dataclass
class Step:
    key: str
    what: str
    ok: bool
    hint: str = ""  # a note: what was found, or why it failed. Never a command.
    fixable: bool = False  # `SetupJob` can do this one itself
    command: str = ""  # what to run in a terminal on this machine when the app cannot
    docs: str = ""  # a page that explains this requirement

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "what": self.what, "ok": self.ok, "hint": self.hint, "fixable": self.fixable, "command": self.command, "docs": self.docs}


def _run(argv: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def _openshell_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "openshell"


def installer_command() -> str:
    """NVIDIA's installer, pinned to the tested release. It downloads the package from
    their GitHub release, checks its checksum and installs it. On Linux it needs `sudo`
    (it asks itself when it has a terminal); on a Mac it uses Homebrew as the user."""
    return f"curl -LsSf {_INSTALLER} | OPENSHELL_VERSION=v{PINNED_VERSION} sh"


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def admin_prefix() -> Optional[list[str]]:
    """How this process can run a command as an administrator WITHOUT holding a password,
    or None. `sudo -n` when this user may use sudo without a password (a dev VM, a cloud
    box); else `pkexec` on a Linux desktop, where polkit shows the system's own password
    prompt and the app never sees what is typed. WSL has no such prompt, so None there:
    the caller hands the command over. `SUDO_USER` tells NVIDIA's installer which user's
    gateway service to set up; sudo sets it by itself, pkexec does not."""
    if not sys.platform.startswith("linux"):
        return None
    if shutil.which("sudo") and _run(["sudo", "-n", "true"], 15).returncode == 0:
        return ["sudo", "-n"]
    if shutil.which("pkexec") and not is_wsl() and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return ["pkexec", "env", f"SUDO_USER={getpass.getuser()}", f"PATH={os.environ.get('PATH', '/usr/bin:/bin')}"]
    return None


def install_handover_command() -> str:
    """The ONE command a user runs in a terminal when the app cannot run the administrator
    steps itself: linger first (the installer needs the user's systemd manager, which does
    not run without it on WSL), then the installer. On a Mac, the installer alone."""
    if sys.platform.startswith("linux"):
        return f"sudo loginctl enable-linger {getpass.getuser()} && {installer_command()}"
    return installer_command()


def landlock_available() -> Optional[bool]:
    """Whether this kernel has Landlock: True/False on Linux, None elsewhere (a Mac's
    sandboxes run in Docker Desktop's own Linux VM, which cannot be asked from here).
    `landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)` returns the ABI
    version; the syscall number 444 is the same on x86-64 and arm64."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.syscall(444, None, 0, 1) >= 0
    except Exception:
        return None


def image_store_free_gb() -> Optional[float]:
    """Free space where the driver keeps images, in GB, or None when it cannot be told."""
    try:
        tool = openshell.image_tool()
        if tool is None:
            return None
        root = _run([tool, "info", "--format", "{{.DockerRootDir}}" if tool == "docker" else "{{.Store.GraphRoot}}"], 30).stdout.strip()
        if not root or not os.path.isdir(root):
            # Docker Desktop's storage lives in its VM: the host's disk is the next best guess.
            root = str(Path.home())
        return shutil.disk_usage(root).free / 1e9
    except Exception:  # no openshell, no docker, an odd `info`: "cannot tell", never a red row
        return None


def steps() -> list[Step]:
    """Everything OpenShell sandboxes need on this machine, in the order `setup` handles it."""
    out: list[Step] = []
    docker = shutil.which("docker")
    docker_ok = bool(docker) and _run(["docker", "info", "--format", "{{.ServerVersion}}"], 30).returncode == 0
    out.append(Step("docker", ROWS["docker"], docker_ok, "" if docker_ok else "install Docker, then add this user to the `docker` group and log in again", docs="" if docker_ok else DOCKER_INSTALL_URL))
    exe = shutil.which("openshell")
    version = _run([exe, "--version"], 15).stdout.strip().split()[-1] if exe else ""
    openshell_ok = version == PINNED_VERSION
    # The app installs it when it can run as an administrator without a password (or
    # needs none, on a Mac); otherwise the row carries the one command to run.
    can_install = sys.platform == "darwin" or (sys.platform.startswith("linux") and admin_prefix() is not None)
    out.append(
        Step(
            "openshell",
            ROWS["openshell"],
            openshell_ok,
            "" if openshell_ok or not version else f"found {version}, not the tested release",
            fixable=not openshell_ok and can_install,
            command="" if openshell_ok or can_install else install_handover_command(),
            docs="" if openshell_ok else GUIDE_URL,
        )
    )
    gateway_toml = _openshell_home() / "gateway.toml"
    binds = gateway_toml.is_file() and "enable_bind_mounts = true" in gateway_toml.read_text(encoding="utf-8")
    out.append(Step("bind_mounts", ROWS["bind_mounts"], binds, fixable=True))
    if sys.platform.startswith("linux"):
        linger = _run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger"], 15).stdout.strip() == "Linger=yes"
        # Fixable: `enable_linger` tries as the user, then as an administrator; when both
        # are refused the job hands this command over and goes on.
        out.append(Step("linger", ROWS["linger"], linger, fixable=True, command="" if linger else f"sudo loginctl enable-linger {getpass.getuser()}"))
        landlock = landlock_available()
        if landlock is not None:
            out.append(Step("landlock", ROWS["landlock"], landlock, "" if landlock else "update the kernel (on WSL: run `wsl --update` from Windows)"))
    problem = openshell_problem(fresh=True) if exe else "OpenShell is not installed"
    # A missing image is not a gateway problem: it gets its own row below.
    image_missing = openshell.is_image_problem(problem)
    gateway_ok = problem is None or image_missing
    out.append(Step("gateway", ROWS["gateway"], gateway_ok, "" if gateway_ok else (problem or "")))
    try:
        import grpc  # noqa: F401

        grpc_ok = True
    except ImportError:
        grpc_ok = False
    out.append(Step("grpcio", ROWS["grpcio"], grpc_ok, command="" if grpc_ok else "pip install 'openworker[openshell]'"))
    configured = app_config.load_config().sandbox_provider
    out.append(Step("config", ROWS["config"], configured == "openshell", "" if configured == "openshell" else f"sandbox_provider = {configured!r} in {app_config.global_config_path()}", fixable=True))
    if gateway_ok and exe:
        present = openshell.image_present()
        if present is not None:  # only a local docker/podman driver can be asked; others get no row
            if not present:
                free = image_store_free_gb()
                if free is not None:
                    out.append(Step("disk", ROWS["disk"], free >= IMAGE_FREE_GB, "" if free >= IMAGE_FREE_GB else f"{free:.1f} GB free; free up space before the download"))
            out.append(Step("image", IMAGE_ROW, present, fixable=True, command="" if present else f"{openshell.image_tool() or 'docker'} pull {openshell.sandbox_image()}"))
    return out


def checks() -> list[tuple[str, bool, str]]:
    """(what, ok, detail) rows: the shape `status` prints and older callers read."""
    return [(s.what, s.ok, s.command or s.hint) for s in steps()]


# -- the fixes, one function each: `setup` (with prompts) and `SetupJob` (with progress) --


def bind_mounts_change() -> str:
    """What `apply_bind_mounts` will do, for showing before asking."""
    home = _openshell_home()
    return (
        f"Allow bind mounts, so a sandbox can be given your folders (and nothing else):\n  write to {home / 'gateway.toml'}:\n    "
        + _BIND_MOUNTS.replace("\n", "\n    ").rstrip()
        + f"\n  point the gateway at it in {home / 'gateway.env'}, then restart the gateway (running sandboxes restart too)"
    )


def apply_bind_mounts() -> Optional[str]:
    """Write the gateway config, point the service at it, restart it. Returns an error
    text when the config already has a docker table (never edit that by hand-off)."""
    home = _openshell_home()
    gateway_toml, gateway_env = home / "gateway.toml", home / "gateway.env"
    home.mkdir(parents=True, exist_ok=True)
    existing = gateway_toml.read_text(encoding="utf-8") if gateway_toml.is_file() else ""
    if "[openshell.drivers.docker]" in existing:
        return f"{gateway_toml} already has a [openshell.drivers.docker] table; add `enable_bind_mounts = true` to it by hand."
    gateway_toml.write_text((existing.rstrip() + "\n\n" if existing.strip() else "") + _BIND_MOUNTS, encoding="utf-8")
    env_line = f"OPENSHELL_GATEWAY_CONFIG={gateway_toml}\n"
    env_text = gateway_env.read_text(encoding="utf-8") if gateway_env.is_file() else ""
    if "OPENSHELL_GATEWAY_CONFIG=" not in env_text:
        gateway_env.write_text(env_text + env_line, encoding="utf-8")
    _run(["systemctl", "--user", "restart", "openshell-gateway"], 120)
    return None


def enable_linger() -> Optional[str]:
    """Try as the user, then as an administrator without a password (`admin_prefix`);
    return the command to run when both are refused."""
    user = getpass.getuser()
    if _run(["loginctl", "enable-linger", user], 30).returncode == 0:
        return None
    prefix = admin_prefix()
    if prefix is not None and _run(prefix + ["loginctl", "enable-linger", user], 120).returncode == 0:
        return None
    return f"sudo loginctl enable-linger {user}"


def _stream(argv: list[str], on_line: Optional[Callable[[str], None]], cancel: Optional[threading.Event]) -> tuple[Optional[int], str]:
    """Run `argv` with stdin closed, feeding each output line to `on_line`. Returns the
    exit code (None when cancelled) and the last line seen."""
    last = ""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as exc:
        return 127, f"could not run {argv[0]}: {exc}"
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            return None, last
        line = line.strip()
        if line:
            last = line[:160]
            if on_line is not None:
                on_line(last)
    return proc.wait(), last


def install_openshell(on_line: Optional[Callable[[str], None]] = None, cancel: Optional[threading.Event] = None) -> Optional[str]:
    """Install the pinned OpenShell from the app (Settings ▸ Set up sandbox). On a Mac the
    installer runs as the user. On Linux it runs as an administrator through
    `admin_prefix()`, with linger enabled FIRST in the same run (the installer needs the
    user's systemd manager, which does not run without linger on WSL and on a headless
    box). Returns an error text, or None when it succeeded."""
    if sys.platform == "darwin":
        argv = ["sh", "-c", installer_command()]
    else:
        prefix = admin_prefix()
        if prefix is None:
            return "this machine has no way to run an administrator step without a password; run: " + install_handover_command()
        argv = prefix + ["sh", "-c", f"loginctl enable-linger {getpass.getuser()} && {installer_command()}"]
    code, last = _stream(argv, on_line, cancel)
    if code is None:
        return "installation cancelled"
    if code != 0:
        return f"the installer exited with {code}: {last}"
    return None


def apply_config() -> None:
    app_config.set_global_value("sandbox_provider", "openshell")


@dataclass
class PullProgress:
    layers_total: int = 0
    layers_done: int = 0
    last_line: str = ""
    layers: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"layers_total": self.layers_total, "layers_done": self.layers_done, "last_line": self.last_line}


def pull_image(on_progress: Optional[Callable[[PullProgress], None]] = None, cancel: Optional[threading.Event] = None) -> Optional[str]:
    """`docker pull` (or podman), streaming its per-layer lines into a PullProgress.
    No time limit: on a slow link this is the 20-minute step. Returns an error text on
    failure or cancellation, None when the image is there."""
    tool, image = openshell.image_tool() or "docker", openshell.sandbox_image()
    progress = PullProgress()
    try:
        proc = subprocess.Popen([tool, "pull", image], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as exc:
        return f"could not run {tool}: {exc}"
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            return "download cancelled"
        line = line.strip()
        if not line:
            continue
        progress.last_line = line[:160]
        # Without a terminal Docker prints "<layer id>: <status>" once per change.
        if ": " in line:
            layer, status = line.split(": ", 1)
            if len(layer) == 12 and layer.isalnum():
                progress.layers[layer] = status
                progress.layers_total = len(progress.layers)
                progress.layers_done = sum(1 for s in progress.layers.values() if s.startswith(("Pull complete", "Already exists")))
        if on_progress is not None:
            on_progress(progress)
    code = proc.wait()
    if code != 0:
        return f"{tool} pull exited with {code}: {progress.last_line}"
    return None


# -- the terminal front end -------------------------------------------------------------


def seatbelt_check() -> tuple[str, bool, str]:
    from .providers import seatbelt

    try:
        seatbelt.preflight()
        return ("the macOS sandbox (Seatbelt) can be used", True, "")
    except seatbelt.SeatbeltUnavailable as exc:
        return ("the macOS sandbox (Seatbelt) can be used", False, str(exc))


def windows_rows(print_fn: Callable[[str], None]) -> None:
    """The Windows sandbox's own lines of `status`; the OpenShell lines follow them."""
    from .providers import windows, windows_setup

    for what, ok, detail in windows_setup.checks():
        print_fn(f"  [{'ok' if ok else '--'}] {what}" + (f"  ({detail})" if detail and not ok else ""))
    problem = windows_setup.problem()
    if problem:
        print_fn(f"       the Windows sandbox cannot be used yet: {problem}")
    elif app_config.load_config().sandbox_provider == "windows":
        print_fn("       the Windows sandbox is ready, and this machine is set to use it")
    else:
        print_fn(f"       the Windows sandbox is ready; to use it: set `sandbox_provider = \"windows\"` in {app_config.global_config_path()}")
    print_fn("\nOpenShell on this machine:")


def windows_setup_command(*, confirm: Callable[[str], bool], print_fn: Callable[[str], None]) -> int:
    from .providers import windows_setup

    from . import netproxy

    ports = netproxy.WINDOWS_PORTS
    print_fn(
        "The full Windows sandbox runs agents' commands as a hidden local account. Setup asks for administrator rights once and:\n"
        f"  - creates two accounts with random passwords nobody sees, hidden from the sign-in screen, with remote and network\n"
        f"    logon denied: `{windows_setup.ACCOUNTS['open']}` (network open) and `{windows_setup.ACCOUNTS['closed']}` (allow list only)\n"
        f"  - adds one Windows Firewall rule, \"{windows_setup.FIREWALL_RULE}\", that blocks every outbound connection for the\n"
        f"    closed account, and four loopback filters that leave it only the proxy's ports {ports.start}-{ports.stop - 1}\n"
        f"  - stores the passwords in {windows_setup.CRED_FILE}, readable by you and administrators only\n"
        f"  - removes the account and rules of an earlier setup, and records what it changed in {windows_setup.STATE_FILE}"
    )
    if not confirm("Run the setup now (one administrator prompt)?"):
        return 1
    ok, said = windows_setup.run_setup()
    if not ok:
        print_fn(f"setup failed; see above for what it managed to do: {said}")
        return 1
    configured = app_config.load_config().sandbox_provider
    if configured != "windows":
        print_fn(f"Set this machine to run agents in the Windows sandbox:\n  sandbox_provider = \"windows\" in {app_config.global_config_path()}")
        if confirm("Make this change?"):
            app_config.set_global_value("sandbox_provider", "windows")
    print_fn("")
    status(print_fn)  # the OpenShell lines may say "--" here; the Windows setup itself succeeded
    return 0


def windows_remove_command(*, confirm: Callable[[str], bool], print_fn: Callable[[str], None]) -> int:
    from .providers import windows_setup

    print_fn(
        "This removes everything `sandbox setup` made on this machine (one administrator prompt): the two sandbox accounts\n"
        f"and their profiles, the firewall rule, the loopback filters, and {windows_setup.ROOT}."
    )
    if not confirm("Remove the Windows sandbox setup now?"):
        return 1
    ok, said = windows_setup.run_remove()
    print_fn("removed" if ok else f"remove failed: {said}")
    return 0 if ok else 1


def remove(*, yes: bool = False, ask: Optional[Callable[[str], bool]] = None, print_fn: Callable[[str], None] = print) -> int:
    confirm = ask or (lambda question: yes or input(f"{question} [y/N] ").strip().lower() in ("y", "yes"))
    if sys.platform == "win32":
        return windows_remove_command(confirm=confirm, print_fn=print_fn)
    print_fn("`sandbox remove` undoes the Windows setup; there is nothing to remove on this platform.")
    return 2


def status(print_fn: Callable[[str], None] = print) -> int:
    if sys.platform == "win32":
        windows_rows(print_fn)
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


def _offer_image_download(rows: dict[str, bool], confirm: Callable[[str], bool], print_fn: Callable[[str], None]) -> Optional[int]:
    """When the base image is missing: show the command, ask, and run the pull with Docker's
    own progress and no time limit. Returns an exit code to stop with, or None to go on."""
    if rows.get(IMAGE_ROW, True):
        return None
    image, tool = openshell.sandbox_image(), openshell.image_tool() or "docker"
    print_fn(f"The sandbox base image is not on this machine. It is about 5 GB and is downloaded once;\nuntil then the first session cannot start:\n  {tool} pull {image}")
    if not confirm("Download it now? (10 to 20 minutes on a slow connection)"):
        return 1
    if subprocess.run([tool, "pull", image], stdin=subprocess.DEVNULL).returncode != 0:
        print_fn("the download failed; nothing else was changed")
        return 1
    return None


def setup(*, yes: bool = False, ask: Optional[Callable[[str], bool]] = None, print_fn: Callable[[str], None] = print) -> int:
    confirm = ask or (lambda question: yes or input(f"{question} [y/N] ").strip().lower() in ("y", "yes"))
    if sys.platform == "win32":
        return windows_setup_command(confirm=confirm, print_fn=print_fn)
    if not sys.platform.startswith("linux"):
        print_fn("`sandbox setup` configures a Linux machine. On a Mac, install OpenShell yourself and use `openworker machine sandbox status`.")
        # The one step that is the same on a Mac with Docker Desktop: the image download.
        rows = {what: ok for what, ok, _ in checks()}
        stop = _offer_image_download(rows, confirm, print_fn)
        return 2 if stop is None else stop
    rows = {what: ok for what, ok, _ in checks()}
    if not rows[ROWS["docker"]]:
        print_fn("Docker is needed first, and installing it needs an administrator:\n  https://docs.docker.com/engine/install/\n  sudo usermod -aG docker $USER   (then log in again)")
        return 1
    if not rows.get(ROWS["linger"], True):
        # Before the installer: it starts the gateway through the user's systemd manager,
        # which on WSL and on a headless box only runs once linger is on.
        print_fn(f"The gateway is a user service and stops when you log out. Keeping it running needs an administrator:\n  sudo loginctl enable-linger {getpass.getuser()}")
        if not confirm("Run it now (sudo will ask for your password)?"):
            return 1
        if subprocess.run(["sudo", "loginctl", "enable-linger", getpass.getuser()]).returncode != 0:
            print_fn("enable-linger failed; nothing else was changed")
            return 1
    if not rows[ROWS["openshell"]]:
        command = installer_command()
        print_fn(f"OpenShell {PINNED_VERSION} is not installed. NVIDIA's installer downloads a package from their GitHub release,\nchecks its checksum and installs it (it will ask for sudo itself):\n  {command}\nGuide: {GUIDE_URL}")
        if not confirm("Run NVIDIA's installer now?"):
            return 1
        if subprocess.run(["sh", "-c", command]).returncode != 0:
            print_fn("the installer failed; nothing else was changed")
            return 1
    if not rows[ROWS["bind_mounts"]]:
        print_fn(bind_mounts_change())
        if not confirm("Make this change?"):
            return 1
        problem = apply_bind_mounts()
        if problem:
            print_fn(problem)
            return 1
    if not rows[ROWS["config"]]:
        print_fn(f"Set this machine to run agents in OpenShell sandboxes, and to REFUSE sessions when OpenShell is not running:\n  sandbox_provider = \"openshell\" in {app_config.global_config_path()}")
        if confirm("Make this change?"):
            apply_config()
    # Last, because it is the slow one: the image the gateway builds sandboxes from. The
    # gateway may only just have started (bind-mount change above), so ask again.
    if IMAGE_ROW not in rows:
        rows = {what: ok for what, ok, _ in checks()}
    stop = _offer_image_download(rows, confirm, print_fn)
    if stop is not None:
        return stop
    print_fn("")
    return status(print_fn)
