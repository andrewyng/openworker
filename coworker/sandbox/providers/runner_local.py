"""`runner-local`: the tool runner as a plain local process, with NO confinement.

For developers and tests only. It exists to exercise the protocol end to end and to
measure what the runner path costs on its own. It has the same shape as a real provider:
a daemon that outlives any one connection, and an attach relay per pipe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..bundle import build_runner_zipapp
from ..launch import runner_command, runner_dir, serve_arguments, spawn_kwargs, wait_for_runner
from ..runner import winpipe
from ..transport import PipeTransport, Transport


class RunnerLocalProvider:
    name = "runner-local"

    def __init__(self, *, cwd: str | Path, runner_path: Optional[Path] = None, relay_silence_seconds: Optional[float] = None) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        self._runner = Path(runner_path) if runner_path is not None else build_runner_zipapp()
        self._relay_silence = relay_silence_seconds
        self._dir, self.socket_path = runner_dir()
        self._daemon: Optional[subprocess.Popen] = None

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "enforcement": "none", "reason": "a local process without any sandbox (developer mode)"}

    def create(self) -> None:
        self._daemon = subprocess.Popen(
            [*runner_command(self._runner), "serve", *serve_arguments(self.socket_path, self._dir), "--cwd", self.cwd, "--exit-with-parent"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **spawn_kwargs(),
        )
        wait_for_runner(self.socket_path, self._daemon)

    def open_runner(self) -> Transport:
        argv = [*runner_command(self._runner), "attach", "--socket", self.socket_path]
        if self._relay_silence is not None:
            argv += ["--silence-seconds", str(self._relay_silence)]
        return PipeTransport(
            subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, **spawn_kwargs())
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
        if not winpipe.is_pipe(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    def destroy(self) -> None:
        self._stop_daemon()
        shutil.rmtree(self._dir, ignore_errors=True)
