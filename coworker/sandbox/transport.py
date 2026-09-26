"""The pipe between the workspace client and a tool runner.

The client needs three things from a pipe: send a line, receive a line, close. A local
process pipe (this file), an OpenShell exec stream and, later, a stream over the machine
channel all fit that shape, which is what keeps the client independent of the sandbox
technology and of which machine the runner is on.
"""

from __future__ import annotations

import subprocess
from typing import Optional, Protocol


class Transport(Protocol):
    def send(self, frame: bytes) -> None: ...

    def recv_line(self) -> Optional[bytes]:
        """The next line, or None when the pipe is closed."""

    def close(self) -> None: ...


class PipeTransport:
    """A child process whose stdin and stdout are the pipe (the attach relay, locally)."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc

    def send(self, frame: bytes) -> None:
        if self.proc.stdin is None:
            raise OSError("pipe is closed")
        self.proc.stdin.write(frame)
        self.proc.stdin.flush()

    def recv_line(self) -> Optional[bytes]:
        if self.proc.stdout is None:
            return None
        line = self.proc.stdout.readline()
        return line or None

    def close(self) -> None:
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
            except OSError:
                pass
