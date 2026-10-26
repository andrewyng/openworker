"""The OpenShell provider: one OpenShell sandbox per agent, the tool runner inside it.

Lifecycle goes through the `openshell` CLI (create, get, delete). The runner's pipe is the
gateway's gRPC method `ExecSandboxInteractive`, a live two-way stream with no terminal.
Design: ocw-context/docs/sandbox-design.md. What the spike on OpenShell 0.0.116 taught us
is in ocw-context/docs/sandbox-spike-openshell.md and is referred to below by letter.

What a sandbox gets:
- the session's folders as bind mounts at the SAME absolute paths, read-only where the
  session has them read-only, and nothing else of the machine;
- the runner as a read-only mount (never part of an image), started as the sandbox's main
  command, so it lives as long as the sandbox and a cut stream only loses the relay;
- a whole policy rendered by us: the folders, the uid and gid that own them (E), a
  network profile, and Landlock as a hard requirement;
- no credential providers at all. Keys never enter an agent's sandbox.
"""

from __future__ import annotations

import json
from collections import deque
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import yaml

from ...secrets import state_dir
from ..bundle import build_runner_zipapp
from ..registry import REGISTRY_LABEL, registry_id
from ..transport import Transport
from .. import credentials as creds
from . import openshell_policy as policy
from . import openshell_wire as wire

log = logging.getLogger(__name__)

PINNED_VERSION = "0.1.2"  # one pinned release (ruling 24). 0.0.x and 0.1.x cannot talk to each other
DEFAULT_IMAGE = "ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e"
LABEL = "openworker"
_SOCKET = f"{policy.RUNTIME_DIR}/owr/r.sock"
_CLI_TIMEOUT = 120


class OpenShellUnavailable(RuntimeError):
    """OpenShell is not installed, not running, or not the version we test against. The
    message says what to do; callers show it as it is."""


class OpenShellImageMissing(OpenShellUnavailable):
    """OpenShell works, but the base image is not on this machine yet. The first `sandbox
    create` would pull it (about 5 GB) and, on a slow link, outlive our timeout: the session
    would hang for ten minutes and then fail with no word about why (OPE-205). So the image
    is checked up front, and the message says how to get it."""


IMAGE_MISSING_PREFIX = "The sandbox base image is not downloaded yet"


def is_image_problem(problem: Optional[str]) -> bool:
    """Whether a preflight message is the missing-image one (the only problem that
    `setup` can fix by downloading, and that Settings shows as "needs download")."""
    return bool(problem) and str(problem).startswith(IMAGE_MISSING_PREFIX)


def sandbox_image() -> str:
    """The image a sandbox is created from: the pinned one, unless the environment says."""
    return os.environ.get("OPENWORKER_SANDBOX_IMAGE") or DEFAULT_IMAGE


def _cli(*args: str, timeout: float = _CLI_TIMEOUT, check: bool = True) -> subprocess.CompletedProcess:
    exe = shutil.which("openshell")
    if exe is None:
        raise OpenShellUnavailable("OpenShell is not installed on this machine (no `openshell` command). Run `openworker machine sandbox setup`, or install OpenShell yourself.")
    # stdin must be closed: without a terminal, `openshell sandbox exec` reads ALL of stdin
    # before it sends anything (spike finding B), so an inherited open stdin hangs it.
    done = subprocess.run(
        [exe, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, env={**os.environ, "NO_COLOR": "1"}
    )
    if check and done.returncode != 0:
        raise RuntimeError(f"`openshell {' '.join(args[:3])}` failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return done


def preflight() -> dict[str, Any]:
    """Refuse early, with a message a person can act on."""
    version = _cli("--version", timeout=15).stdout.strip().split()[-1]
    if version != PINNED_VERSION:
        raise OpenShellUnavailable(f"OpenShell {version} is installed; OpenWorker is tested with {PINNED_VERSION} only. Install that version (`openworker machine sandbox setup`).")
    status = _cli("status", timeout=30, check=False)
    if status.returncode != 0 or "Connected" not in status.stdout:
        raise OpenShellUnavailable("The OpenShell gateway is not running or cannot be reached (`openshell status`). On a headless machine, check `systemctl --user status openshell-gateway` and `loginctl enable-linger`.")
    if image_present() is False:
        raise OpenShellImageMissing(
            f"{IMAGE_MISSING_PREFIX} (about 5 GB, one time). Run `openworker machine sandbox setup`, "
            f"or `{image_tool() or 'docker'} pull {sandbox_image()}`, then start the session again."
        )
    problem = host_network_problem()
    if problem:
        raise OpenShellUnavailable(problem)
    return {"version": version}


def _host_network_problem() -> Optional[str]:
    """On a Mac: what to do when Docker Desktop's host networking is off, else None.
    OpenShell 0.1 cannot start a sandbox without it, and says only "failed to connect"."""
    if sys.platform != "darwin":
        return None
    from ..setup_cmd import DOCKER_HOST_NETWORK_FIX, docker_host_network

    return f"{DOCKER_HOST_NETWORK_FIX}." if docker_host_network() is False else None


# What `preflight` calls. A name of its own so tests can stand in for it: the real one
# starts a container.
host_network_problem = _host_network_problem


def active_driver() -> str:
    """The gateway's compute driver ("docker", "podman", "kubernetes", "vm"), or "" when it
    cannot be told. Read from `openshell gateway info -o json`."""
    done = _cli("gateway", "info", "-o", "json", timeout=30, check=False)
    if done.returncode != 0:
        return ""
    try:
        drivers = json.loads(done.stdout).get("compute_drivers") or []
        return str(drivers[0].get("name") or "") if drivers else ""
    except (ValueError, AttributeError, IndexError, TypeError):
        return ""


# The command that holds a driver's images, for the drivers whose store is on this machine.
_IMAGE_TOOLS = {"docker": "docker", "podman": "podman"}


def image_tool() -> Optional[str]:
    """The `docker`/`podman` command to ask about (and pull) images, or None when the
    question cannot be answered here: the gateway is remote, its driver keeps images
    elsewhere (Kubernetes, the MicroVM driver on a Mac), or the command is not on PATH."""
    if _gateway_is_remote():
        return None
    name = _IMAGE_TOOLS.get(active_driver())
    return name if name and shutil.which(name) else None


def _gateway_is_remote() -> bool:
    try:
        home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "openshell"
        name = (home / "active_gateway").read_text(encoding="utf-8").strip()
        meta = json.loads((home / "gateways" / name / "metadata.json").read_text(encoding="utf-8"))
        return bool(meta.get("is_remote"))
    except (OSError, ValueError):
        return False


def image_present(image: Optional[str] = None) -> Optional[bool]:
    """Whether the base image is on this machine: True or False when the gateway's driver
    keeps images here and can be asked (`docker`/`podman image inspect`), None when it
    cannot be told. The check is keyed on the driver, not the operating system: the same
    image serves Linux, WSL and macOS (amd64 and arm64), and only the driver knows where
    images live. None never refuses anything: the first create then pulls as before."""
    tool = image_tool()
    if tool is None:
        return None
    done = subprocess.run(
        [tool, "image", "inspect", image or sandbox_image()],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )
    return done.returncode == 0


RUNTIME_DIR_NAME = "sandbox-runtime"  # a sibling of `sandbox/`, which is mounted into sandboxes


def runtime_dir() -> Path:
    """Where a session's private folder lives: on the real filesystem (the gateway can see
    it), owner-only, and mounted into no sandbox as a whole."""
    path = state_dir() / RUNTIME_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def reap_runtime_dirs(alive: Callable[[int], bool]) -> list[str]:
    """Remove the private folders of servers that are gone (`ow-openshell-<pid>-...`).
    Their sandboxes are removed by the registry's reap; this is the matching sweep for the
    copied credentials, which the OS would have cleared from /tmp and no longer does."""
    removed: list[str] = []
    try:
        entries = list(runtime_dir().iterdir())
    except OSError:
        return removed
    for entry in entries:
        parts = entry.name.split("-")
        if len(parts) < 4 or parts[0] != "ow" or parts[1] != "openshell" or not parts[2].isdigit():
            continue
        if alive(int(parts[2])):
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    return removed


def _gateway() -> tuple[str, Path]:
    """(host:port, folder with the client certificate) of the active gateway."""
    home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "openshell"
    name = (home / "active_gateway").read_text(encoding="utf-8").strip()
    meta = json.loads((home / "gateways" / name / "metadata.json").read_text(encoding="utf-8"))
    endpoint = str(meta["gateway_endpoint"]).split("://", 1)[-1].rstrip("/")
    return endpoint, home / "gateways" / name / "mtls"


# A refusal in the sandbox's log, as OpenShell 0.0.116 writes it:
#   [1791068681.547] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(85) -> www.iana.org:443 [policy:- …]
_DENIED_LINE = re.compile(r"^\[(\d+(?:\.\d+)?)\].*\bDENIED\b\s+\S+?\(\d+\)\s+->\s+(\S+:\d+)")


class DenialLog:
    """What a sandbox refused, read from its own log (`openshell logs <name> --tail`).

    The log is followed by one long-lived reader, so asking costs nothing per command. A
    line arrives up to about half a second after the refusal (OpenShell sends its log in
    batches), which is `OpenShellProvider.blocked_lag`. The reader starts with the log's
    recent history, so refusals from before it started are there too."""

    def __init__(self, sandbox_name: str) -> None:
        self.entries: deque[tuple[float, str]] = deque(maxlen=200)
        self._seen: set[tuple[float, str]] = set()
        self._process: Optional[subprocess.Popen] = None
        exe = shutil.which("openshell")
        if exe is None:
            return
        try:
            self._process = subprocess.Popen(
                [exe, "logs", sandbox_name, "--tail", "--source", "sandbox"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env={**os.environ, "NO_COLOR": "1"},
            )  # fmt: skip
        except OSError:
            return
        threading.Thread(target=self._read, name=f"openshell-denials-{sandbox_name}", daemon=True).start()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _read(self) -> None:
        try:
            for line in self._process.stdout:  # type: ignore[union-attr]
                self.take(line)
        except (OSError, ValueError):
            pass

    def take(self, line: str) -> None:
        found = _DENIED_LINE.search(line)
        if not found:
            return
        from ..netproxy import _blocked_entry

        entry = _blocked_entry(found.group(2))  # a host name or nothing: it is shown to the agent and the person
        item = (float(found.group(1)), entry)
        if entry and item not in self._seen:
            self._seen.add(item)
            self.entries.append(item)

    def since(self, since: float) -> list[tuple[float, str]]:
        return [item for item in list(self.entries) if item[0] >= since]

    def stop(self) -> None:
        if self._process is not None:
            try:
                self._process.kill()
            except OSError:
                pass


class OpenShellProvider:
    name = "openshell"

    def __init__(
        self,
        *,
        roots: Sequence[dict[str, Any]],
        cwd: Optional[str] = None,
        profile: str = policy.DEFAULT_PROFILE,
        image: Optional[str] = None,
        label: str = "",
        credentials: Sequence[creds.Grant] = (),
        extra_hosts: Sequence[str] = (),
        registry: Optional[str] = None,
    ) -> None:
        """`roots`: [{"path", "writable"}], primary first. `label`: who this sandbox is for
        (session and agent), stored on the sandbox so leftovers can be found. `extra_hosts`:
        the machine's own additions to the network list ("host:port"). `registry`: the id
        of the registry that records this sandbox (default: this state folder's); only that
        registry's clean-up may delete it."""
        if not roots:
            raise ValueError("an OpenShell sandbox needs at least one folder")
        self.roots = [{"path": str(Path(r["path"]).expanduser().resolve()), "writable": bool(r.get("writable"))} for r in roots]
        self.cwd = str(Path(cwd).expanduser().resolve()) if cwd else self.roots[0]["path"]
        self.profile = policy.network_profiles.check(profile)
        self.extra_hosts = list(extra_hosts)
        self._identity: tuple = (None, None, None)  # uid, gid, home; set when the sandbox is made
        self._denials: Optional[DenialLog] = None  # follows the sandbox's log once it is made
        self.image = image or sandbox_image()
        # A label value may hold letters, digits, '-', '_' and '.', and at most 63 characters.
        self.label = "".join(c if c.isalnum() or c in "-_." else "-" for c in label)[:63].strip("-_.")
        self.registry = registry or registry_id()
        self.sandbox_name = f"ow-{uuid.uuid4().hex[:12]}"
        self.sandbox_id: Optional[str] = None
        self._runner = build_runner_zipapp()
        # The session's private folder (copied credentials, the policy file). NOT under
        # /tmp: the gateway installed by NVIDIA's installer is a systemd service with
        # PrivateTmp=true, so a bind-mount source under /tmp does not exist for it and the
        # sandbox fails to create (OPE-208). NOT under the runner's folder either: that
        # folder is mounted read-only into every sandbox, and copied keys must never be
        # readable from another session's sandbox. The server's
        # pid is in the name so the registry's reap can remove folders of a dead server.
        self._tmp = tempfile.mkdtemp(prefix=f"ow-openshell-{os.getpid()}-", dir=runtime_dir())
        self.grants = list(credentials)
        self.copied: Optional[creds.CopiedCredentials] = None

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "enforcement": "full",
            "reason": f"OpenShell {PINNED_VERSION}: Landlock and seccomp on every process, network {'open (any host)' if self.profile == 'open' else 'profile ' + repr(self.profile)}, no keys inside",
            "sandbox": self.sandbox_name,
            "image": self.image,
            "credentials": self.copied.describe() if self.copied is not None else [],
        }

    # -- lifecycle --------------------------------------------------------------------
    def create(self) -> None:
        self._create_tried = True
        try:
            self._create()
        except Exception:
            self.destroy()  # never leave a copied credential behind
            raise

    def _create(self) -> None:
        preflight()
        for root in self.roots:
            if not os.path.isdir(root["path"]):
                raise RuntimeError(f"folder does not exist on this machine: {root['path']}")
        uid, gid = (os.getuid(), os.getgid()) if sys.platform.startswith("linux") else (None, None)
        # The sandbox's private home with the copied credentials (section 11b), mounted at
        # the same path it has on the machine, read-write. With no grants it holds only
        # git's settings, and HOME stays the runtime folder.
        self.copied = creds.copy_in(self.grants, self._tmp) if self.grants else None
        home = self.copied.home if self.copied is not None else None
        self._identity = (uid, gid, home)
        policy_file = self._write_policy()
        driver_config = json.dumps(policy.mounts(self.roots, str(self._runner.parent), home))
        command = [policy.PYTHON, "-S", f"{policy.RUNNER_MOUNT}/{self._runner.name}", "serve", "--socket", _SOCKET, "--cwd", self.cwd]
        env = {"HOME": policy.RUNTIME_DIR, **(self.copied.env if self.copied is not None else {})}
        args = [
            "sandbox", "create", "--name", self.sandbox_name, "--from", self.image,
            "--no-tty", "--detach", "--no-auto-providers",
            "--policy", str(policy_file), "--driver-config-json", driver_config,
            "--label", f"{LABEL}=1", "--label", f"{REGISTRY_LABEL}={self.registry}", *[x for k, v in env.items() for x in ("--env", f"{k}={v}")],
        ]  # fmt: skip
        if self.label:
            args += ["--label", f"{LABEL}-owner={self.label}"]
        try:
            _cli(*args, "--", *command, timeout=600)  # the first create pulls the image
        except RuntimeError as exc:
            log.warning("sandbox create failed for %s: %s", self.sandbox_name, exc)
            if "bind" in str(exc).lower() and "enable" in str(exc).lower():
                raise OpenShellUnavailable("The OpenShell gateway does not allow bind mounts, so it cannot give a sandbox your folders. Set `enable_bind_mounts = true` under `[openshell.drivers.docker]` in the gateway config (`openworker machine sandbox setup` does this).") from exc
            if "ContainerExited" in str(exc) and sys.platform == "darwin":
                # The container's own log says why; the CLI does not. The usual reason on a
                # Mac is a Docker Desktop kernel without Landlock, which can be asked.
                from ..setup_cmd import DOCKER_LANDLOCK_FIX, docker_landlock

                if docker_landlock() is False:
                    raise OpenShellUnavailable(f"{DOCKER_LANDLOCK_FIX}. Then start the session again.") from exc
            if "failed to connect to OpenShell server" in str(exc) and sys.platform == "darwin":
                # The sandbox's helper could not reach the gateway: on a Mac that is Docker
                # Desktop's host networking being off, which can be asked.
                from ..setup_cmd import DOCKER_HOST_NETWORK_FIX, docker_host_network

                if docker_host_network() is False:
                    raise OpenShellUnavailable(f"{DOCKER_HOST_NETWORK_FIX}. Then start the session again.") from exc
            raise
        self._wait_ready()
        self._denials = DenialLog(self.sandbox_name)  # what it refuses, for the agent and the card
        # Spike finding K: the first exec after a sandbox turns Ready hangs until it times
        # out, and the next one works. Spend that on a throwaway call.
        try:
            _cli("sandbox", "exec", "--name", self.sandbox_name, "--no-tty", "--timeout", "5", "--", "true", timeout=10, check=False)
        except subprocess.TimeoutExpired:
            pass

    def _wait_ready(self, seconds: float = 120) -> None:
        deadline = time.monotonic() + seconds
        phase = "?"
        while time.monotonic() < deadline:
            done = _cli("sandbox", "get", self.sandbox_name, "-o", "json", timeout=30, check=False)
            if done.returncode == 0:
                info = json.loads(done.stdout)
                sandbox = info.get("sandbox", info)
                phase = str(sandbox.get("phase") or sandbox.get("status", {}).get("phase") or "")
                self.sandbox_id = sandbox.get("id") or sandbox.get("metadata", {}).get("id") or self.sandbox_id
                if phase.lower().endswith("ready"):
                    return
                if phase.lower().endswith(("error", "completed")):
                    break
            time.sleep(1.0)
        raise RuntimeError(f"the OpenShell sandbox {self.sandbox_name} did not become ready (phase: {phase})")

    def open_runner(self) -> Transport:
        if not self.sandbox_id:
            raise RuntimeError("the sandbox has not been created")
        command = [policy.PYTHON, "-S", f"{policy.RUNNER_MOUNT}/{self._runner.name}", "attach", "--socket", _SOCKET]
        return GrpcExecTransport(self.sandbox_name, command)

    def verify(self, client: Any) -> None:
        """After connecting: every folder must really be reachable inside (spike finding F:
        a path that already exists in the image can silently block a mount)."""
        for root in self.roots:
            info = client.call("fs.list", {"path": root["path"], "limit": 1}, timeout=30)
            if "entries" not in info:
                raise RuntimeError(f"the folder {root['path']} is not reachable inside the sandbox")

    def _write_policy(self) -> Path:
        """The whole policy as it stands: folders, identity, the network list with the
        machine's additions and the hosts the copied credentials need."""
        uid, gid, home = self._identity
        extra_hosts = sorted({*self.extra_hosts, *(self.copied.hosts if self.copied is not None else [])})
        policy_file = Path(self._tmp) / "policy.yaml"
        policy_file.write_text(yaml.safe_dump(policy.render(self.roots, profile=self.profile, uid=uid, gid=gid, home=home, extra_hosts=extra_hosts), sort_keys=False), encoding="utf-8")
        return policy_file

    def add_hosts(self, hosts: Sequence[str]) -> None:
        """Let the running sandbox reach more "host:port" entries (OPE-219: the person allowed
        a site for this session or for good). OpenShell hot-reloads the network section of a
        policy, so the whole policy is rendered again and set on the sandbox; the static
        sections are the same, `--wait` returns once the sandbox has loaded it."""
        new = [str(h) for h in hosts if str(h) not in self.extra_hosts]
        if not new:
            return
        self.extra_hosts.extend(new)
        if not getattr(self, "_create_tried", False):
            return  # not made yet: the next create renders the list as it stands
        _cli("policy", "set", self.sandbox_name, "--policy", str(self._write_policy()), "--wait", timeout=120)

    def remove_hosts(self, hosts: Sequence[str]) -> None:
        """Take "host:port" entries back from the running sandbox: the policy is rendered
        without them and set again. OpenShell closes connections opened under the old rules."""
        gone = [str(h) for h in hosts if str(h) in self.extra_hosts]
        if not gone:
            return
        self.extra_hosts = [h for h in self.extra_hosts if h not in gone]
        if not getattr(self, "_create_tried", False):
            return
        _cli("policy", "set", self.sandbox_name, "--policy", str(self._write_policy()), "--wait", timeout=120)

    # A refusal reaches the log reader up to about this long after it happened.
    blocked_lag = 0.6

    @property
    def reports_blocked(self) -> bool:
        """Whether the sandbox's log is being followed. When it is not (the reader died with
        the gateway), nothing may be read into an empty answer."""
        return self._denials is not None and self._denials.alive

    def blocked_since(self, since: float) -> list[tuple[float, str]]:
        """(time, "host:port") of the connections the sandbox refused since then, from its
        own log, read from outside (the in-sandbox `policy.local/v1/denials` is switched off
        unless agent policy proposals are on, which we do not use)."""
        return self._denials.since(since) if self._denials is not None else []

    def regrant(self, roots: Sequence[dict[str, Any]], *, before_create: Optional[Callable[[], None]] = None) -> None:
        """The session's folders changed. Mounts and the file policy are fixed when a sandbox
        is created, so this one is deleted and a new one is created with the new folders.
        `before_create` runs once the new name is chosen and before the sandbox exists: the
        workspace reserves the name in the registry there (see RunnerWorkspace)."""
        self._delete()
        self.roots = [{"path": str(Path(r["path"]).expanduser().resolve()), "writable": bool(r.get("writable"))} for r in roots]
        self.sandbox_name = f"ow-{uuid.uuid4().hex[:12]}"
        self.sandbox_id = None
        if before_create is not None:
            before_create()
        self.create()

    def _delete(self) -> None:
        log.info("deleting sandbox %s", self.sandbox_name)
        if self._denials is not None:
            self._denials.stop()
            self._denials = None
        try:
            _cli("sandbox", "delete", self.sandbox_name, timeout=90, check=False)
        except (subprocess.TimeoutExpired, OpenShellUnavailable):
            pass

    def destroy(self) -> None:
        if getattr(self, "_create_tried", False):  # never asked the gateway: nothing to delete there
            self._delete()
        shutil.rmtree(self._tmp, ignore_errors=True)


def list_our_sandboxes(registry: str) -> list[dict[str, Any]]:
    """The sandboxes on this gateway that the given registry made (for its clean-up). Other
    OpenWorker processes of this user share the gateway; their sandboxes are not listed."""
    selector = f"{LABEL}=1,{REGISTRY_LABEL}={registry}"
    done = _cli("sandbox", "list", "--selector", selector, "-o", "json", timeout=60, check=False)
    if done.returncode != 0 or not done.stdout.strip():
        return []
    data = json.loads(done.stdout)
    listed = data.get("sandboxes", data) if isinstance(data, dict) else data
    # Checked here too: whatever the gateway did with the selector, never hand back a
    # sandbox that carries another registry's label (or none).
    return [s for s in listed if (s.get("labels") or {}).get(REGISTRY_LABEL) == registry]


class GrpcExecTransport:
    """The runner's pipe: OpenShell's `ExecSandboxInteractive` stream, no terminal."""

    def __init__(self, sandbox: str, command: list[str]) -> None:
        try:
            import grpc
        except ImportError as exc:  # an optional extra, needed for this provider only
            raise OpenShellUnavailable("The OpenShell provider needs the `grpcio` package: pip install 'openworker[openshell]'.") from exc
        endpoint, certs = _gateway()
        credentials = grpc.ssl_channel_credentials(
            root_certificates=(certs / "ca.crt").read_bytes(),
            private_key=(certs / "tls.key").read_bytes(),
            certificate_chain=(certs / "tls.crt").read_bytes(),
        )
        host = endpoint.rsplit(":", 1)[0]
        options = [("grpc.ssl_target_name_override", "localhost")] if host in ("127.0.0.1", "::1") else []
        self._channel = grpc.secure_channel(endpoint, credentials, options=options)
        self._outgoing: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._incoming: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._buffer = b""
        self._closed = False
        call = self._channel.stream_stream(wire.METHOD, request_serializer=lambda b: b, response_deserializer=lambda b: b)
        # The same workspace the CLI calls above used (its `--workspace`, from the environment).
        workspace = os.environ.get("OPENSHELL_WORKSPACE", "").strip() or wire.DEFAULT_WORKSPACE
        self._call = call(self._requests(wire.encode_start(sandbox, command, workspace)))
        threading.Thread(target=self._pump, daemon=True).start()

    def _requests(self, start: bytes):
        yield start
        while True:
            data = self._outgoing.get()
            if data is None:
                return
            yield wire.encode_stdin(data)

    def _pump(self) -> None:
        try:
            for message in self._call:
                kind, data, code = wire.decode_event(message)
                if kind == "stdout" and data:
                    self._incoming.put(data)
                elif kind == "stderr" and data:
                    log.debug("runner relay: %s", data.decode("utf-8", "replace").rstrip())
                elif kind == "exit":
                    log.debug("runner relay exited with %s", code)
                    break
        except Exception as exc:  # grpc.RpcError: the stream broke; the client reconnects
            if not self._closed:
                log.info("OpenShell exec stream ended: %s", exc)
        self._incoming.put(None)

    def send(self, frame: bytes) -> None:
        if self._closed:
            raise OSError("the stream is closed")
        self._outgoing.put(frame)

    def recv_line(self) -> Optional[bytes]:
        while b"\n" not in self._buffer:
            chunk = self._incoming.get()
            if chunk is None:
                self._incoming.put(None)
                return None
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line + b"\n"

    def close(self) -> None:
        self._closed = True
        self._outgoing.put(None)
        try:
            self._call.cancel()
        except Exception:
            pass
        try:
            self._channel.close()
        except Exception:
            pass
