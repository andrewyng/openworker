"""`runner-local`: the tool runner as a plain local process, with NO confinement.

For developers and tests only. It exists to exercise the protocol end to end and to
measure what the runner path costs on its own. It has the same shape as a real provider:
a daemon that outlives any one connection, and an attach relay per pipe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from ..bundle import build_runner_zipapp
from ..launch import runner_command
from ..transport import PipeTransport, Transport


class RunnerLocalProvider:
    name = "runner-local"

    def __init__(self, *, cwd: str | Path, runner_path: Optional[Path] = None, relay_silence_seconds: Optional[float] = None) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        self._runner = Path(runner_path) if runner_path is not None else build_runner_zipapp()
        self._relay_silence = relay_silence_seconds
        # A Unix socket path is limited to about 100 bytes, and temp folders on macOS are
        # long, so the socket gets a short folder of its own.
        self._dir = tempfile.mkdtemp(prefix="owr-", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.socket_path = os.path.join(self._dir, "r.sock")
        self._daemon: Optional[subprocess.Popen] = None

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "enforcement": "none", "reason": "a local process without any sandbox (developer mode)"}

    def create(self) -> None:
        self._daemon = subprocess.Popen(
            [*runner_command(self._runner), "serve", "--socket", self.socket_path, "--cwd", self.cwd, "--exit-with-parent"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 15
        while not os.path.exists(self.socket_path):
            if self._daemon.poll() is not None:
                raise RuntimeError(f"the tool runner exited at once (code {self._daemon.returncode})")
            if time.monotonic() > deadline:
                raise RuntimeError("the tool runner did not come up in 15 seconds")
            time.sleep(0.02)

    def open_runner(self) -> Transport:
        argv = [*runner_command(self._runner), "attach", "--socket", self.socket_path]
        if self._relay_silence is not None:
            argv += ["--silence-seconds", str(self._relay_silence)]
        return PipeTransport(
            subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        )

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
        shutil.rmtree(self._dir, ignore_errors=True)
