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
import logging
import os
import queue
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
from ..transport import Transport
from .. import credentials as creds
from . import openshell_policy as policy
from . import openshell_wire as wire

log = logging.getLogger(__name__)

PINNED_VERSION = "0.0.116"  # ruling 24: one pinned release until NVIDIA ships GA
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
    return {"version": version}


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
    ) -> None:
        """`roots`: [{"path", "writable"}], primary first. `label`: who this sandbox is for
        (session and agent), stored on the sandbox so leftovers can be found."""
        if not roots:
            raise ValueError("an OpenShell sandbox needs at least one folder")
        self.roots = [{"path": str(Path(r["path"]).expanduser().resolve()), "writable": bool(r.get("writable"))} for r in roots]
        self.cwd = str(Path(cwd).expanduser().resolve()) if cwd else self.roots[0]["path"]
        self.profile = profile
        self.image = image or sandbox_image()
        # A label value may hold letters, digits, '-', '_' and '.', and at most 63 characters.
        self.label = "".join(c if c.isalnum() or c in "-_." else "-" for c in label)[:63].strip("-_.")
        self.sandbox_name = f"ow-{uuid.uuid4().hex[:12]}"
        self.sandbox_id: Optional[str] = None
        self._runner = build_runner_zipapp()
        # The session's private folder (copied credentials, the policy file). NOT under
        # /tmp: the gateway installed by NVIDIA's installer is a systemd service with
        # PrivateTmp=true, so a bind-mount source under /tmp does not exist for it and the
        # sandbox fails to create (OPE-208). NOT under the state dir's `sandbox/` either:
        # that whole folder is mounted read-only into every sandbox for the runner, and
        # copied keys must never be readable from another session's sandbox. The server's
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
        extra_hosts = self.copied.hosts if self.copied is not None else []
        policy_file = Path(self._tmp) / "policy.yaml"
        policy_file.write_text(yaml.safe_dump(policy.render(self.roots, profile=self.profile, uid=uid, gid=gid, home=home, extra_hosts=extra_hosts), sort_keys=False), encoding="utf-8")
        driver_config = json.dumps(policy.mounts(self.roots, str(self._runner.parent), home))
        command = [policy.PYTHON, "-S", f"{policy.RUNNER_MOUNT}/{self._runner.name}", "serve", "--socket", _SOCKET, "--cwd", self.cwd]
        env = {"HOME": policy.RUNTIME_DIR, **(self.copied.env if self.copied is not None else {})}
        args = [
            "sandbox", "create", "--name", self.sandbox_name, "--from", self.image,
            "--no-tty", "--detach", "--no-auto-providers",
            "--policy", str(policy_file), "--driver-config-json", driver_config,
            "--label", f"{LABEL}=1", *[x for k, v in env.items() for x in ("--env", f"{k}={v}")],
        ]  # fmt: skip
        if self.label:
            args += ["--label", f"{LABEL}-owner={self.label}"]
        try:
            _cli(*args, "--", *command, timeout=600)  # the first create pulls the image
        except RuntimeError as exc:
            log.warning("sandbox create failed for %s: %s", self.sandbox_name, exc)
            if "bind" in str(exc).lower() and "enable" in str(exc).lower():
                raise OpenShellUnavailable("The OpenShell gateway does not allow bind mounts, so it cannot give a sandbox your folders. Set `enable_bind_mounts = true` under `[openshell.drivers.docker]` in the gateway config (`openworker machine sandbox setup` does this).") from exc
            raise
        self._wait_ready()
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
        return GrpcExecTransport(self.sandbox_id, command)

    def verify(self, client: Any) -> None:
        """After connecting: every folder must really be reachable inside (spike finding F:
        a path that already exists in the image can silently block a mount)."""
        for root in self.roots:
            info = client.call("fs.list", {"path": root["path"], "limit": 1}, timeout=30)
            if "entries" not in info:
                raise RuntimeError(f"the folder {root['path']} is not reachable inside the sandbox")

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
        try:
            _cli("sandbox", "delete", self.sandbox_name, timeout=90, check=False)
        except (subprocess.TimeoutExpired, OpenShellUnavailable):
            pass

    def destroy(self) -> None:
        self._delete()
        shutil.rmtree(self._tmp, ignore_errors=True)


def list_our_sandboxes() -> list[dict[str, Any]]:
    """Every sandbox on this gateway that OpenWorker created (for the registry's clean-up)."""
    done = _cli("sandbox", "list", "--selector", f"{LABEL}=1", "-o", "json", timeout=60, check=False)
    if done.returncode != 0 or not done.stdout.strip():
        return []
    data = json.loads(done.stdout)
    return data.get("sandboxes", data) if isinstance(data, dict) else data


class GrpcExecTransport:
    """The runner's pipe: OpenShell's `ExecSandboxInteractive` stream, no terminal."""

    def __init__(self, sandbox_id: str, command: list[str]) -> None:
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
        self._call = call(self._requests(wire.encode_start(sandbox_id, command)))
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
