"""The attach relay: copies bytes between its own stdin/stdout and the daemon's socket.
Standard library only.

One relay per client stream. It never reads frames. It ends when its stdin ends, when the
daemon closes the connection, or when the client has been silent for too long (the client
pings; a provider may leave a relay behind for a while after a stream is cut, and this is
how such a leftover goes away by itself).
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time

from .protocol import RELAY_SILENCE_SECONDS


def run(socket_path: str, silence_seconds: float = RELAY_SILENCE_SECONDS) -> int:
    # Keep the pipe clean: move the real stdout to a private descriptor and point
    # descriptor 1 at stderr, so a stray print can never land inside a frame.
    out_fd = os.dup(1)
    os.dup2(2, 1)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
    except OSError as exc:
        print(f"attach: cannot reach the runner at {socket_path}: {exc}", file=sys.stderr)
        return 2

    last_heard = [time.monotonic()]
    done = threading.Event()

    def upstream() -> None:
        try:
            while True:
                data = os.read(0, 65536)
                if not data:
                    break
                last_heard[0] = time.monotonic()
                sock.sendall(data)
        except OSError:
            pass
        done.set()

    def downstream() -> None:
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                view = memoryview(data)
                while view:
                    view = view[os.write(out_fd, view):]
        except OSError:
            pass
        done.set()

    threading.Thread(target=upstream, daemon=True).start()
    threading.Thread(target=downstream, daemon=True).start()
    while not done.wait(1.0):
        if silence_seconds and time.monotonic() - last_heard[0] > silence_seconds:
            print("attach: no word from the client; leaving", file=sys.stderr)
            break
    try:
        sock.close()
    except OSError:
        pass
    return 0
