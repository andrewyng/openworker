"""How a provider starts the tool runner on THIS machine.

From a source or pip install the runner is the zipapp, run by the same Python that runs the
server, with `-S` so no site-packages leak in. In the packaged desktop app there is no
Python to call: the frozen sidecar starts ITSELF in runner mode (`openworker-server
sandbox-runner ...`), which loads only the standard-library runner package. Providers that
run the runner inside a Linux container (OpenShell) keep using the zipapp, because the
Mac binary cannot run there.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

from .runner import winpipe


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def runner_command(runner_path: Path) -> list[str]:
    """The argv prefix that runs the runner here; `serve ...` or `attach ...` follows."""
    if frozen():
        return [sys.executable, "sandbox-runner"]
    return [sys.executable, "-S", str(runner_path)]


def read_paths() -> list[str]:
    """What the process that runs the runner must be able to read: the interpreter and its
    standard library, or the frozen app's folder (the bootloader reads `_internal`)."""
    paths = {os.path.dirname(os.path.realpath(sys.executable))}
    if frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            paths.add(os.path.realpath(meipass))
    else:
        paths.update({sys.prefix, sys.base_prefix})
    return sorted(p for p in paths if p)


def runner_dir(base: Optional[str] = None) -> tuple[str, str]:
    """A private folder for one runner and the address its daemon listens on: a socket
    file inside the folder, or on Windows a named pipe carrying the folder's name.
    A Unix socket path is limited to about 100 bytes and temp folders on macOS are long,
    so the folder is short and under `/tmp` there."""
    if sys.platform == "win32":
        folder = tempfile.mkdtemp(prefix="owr-")
        return folder, winpipe.pipe_name(os.path.basename(folder))
    folder = tempfile.mkdtemp(prefix="owr-", dir=base or ("/tmp" if os.path.isdir("/tmp") else None))
    return folder, os.path.join(folder, "r.sock")


def serve_arguments(address: str, folder: str, *, also_sids: Sequence[str] = ()) -> list[str]:
    """The `serve` options that tell the daemon where it lives and who may connect: this
    user (the server), plus `also_sids` (a sandbox's own session SID, so that a daemon
    under a write-restricted token may open further instances of its own pipe)."""
    args = ["--socket", address, "--dir", folder]
    if winpipe.is_pipe(address):
        from . import winsec

        for sid in (winsec.current_user_sid(), *also_sids):
            args += ["--allow-sid", sid]
    return args


def spawn_kwargs() -> dict:
    """How a daemon or relay is started so that it shares neither our session nor our
    console: its own session on POSIX, its own process group and no window on Windows."""
    if sys.platform == "win32":
        return {"creationflags": winpipe.default_creation_flags()}
    return {"start_new_session": True}


def wait_for_runner(address: str, daemon: subprocess.Popen, *, seconds: float = 15, said: Optional[str] = None) -> None:
    """Block until the daemon listens at `address`; raise when it exits first or is late."""
    deadline = time.monotonic() + seconds
    while not _listening(address):
        if daemon.poll() is not None:
            tail = f" {said[-400:]}" if said else ""
            raise RuntimeError(f"the tool runner exited at once (code {daemon.returncode}).{tail}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"the tool runner did not come up in {seconds:g} seconds")
        time.sleep(0.02)


def _listening(address: str) -> bool:
    if winpipe.is_pipe(address):
        return winpipe.wait_ready(address)
    return os.path.exists(address)


def maybe_run_runner(argv: list[str]) -> bool:
    """`openworker-server sandbox-runner <serve|attach> ...`: run the runner and exit.
    Returns False when argv is anything else. Imports only the runner package, so the
    daemon inside a sandbox stays small and starts fast."""
    if argv[:1] == ["sandbox-wfp"]:  # the elevated setup step on Windows (windows_setup.py)
        from . import windows_wfp

        raise SystemExit(windows_wfp.main(argv[1:]))
    if argv[:1] != ["sandbox-runner"]:
        return False
    from .runner.__main__ import main as runner_main

    raise SystemExit(runner_main(argv[1:]))
