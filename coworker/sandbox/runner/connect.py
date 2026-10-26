"""`connect`: an HTTP CONNECT client for ssh's ProxyCommand where there is no `nc`.

`runner connect <proxy host> <proxy port> <host> <port>` opens a tunnel to host:port
through the allow-list proxy and copies bytes between it and stdin/stdout. Windows ships
no netcat, and ssh on every platform can run a ProxyCommand; this is that command.
Standard library only, like the rest of the runner.
"""

from __future__ import annotations

import os
import socket
import sys
import threading


def run(proxy_host: str, proxy_port: int, host: str, port: int) -> int:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(0, os.O_BINARY)
        msvcrt.setmode(1, os.O_BINARY)
    try:
        sock = socket.create_connection((proxy_host, proxy_port), timeout=20)
    except OSError as exc:
        print(f"connect: cannot reach the proxy at {proxy_host}:{proxy_port}: {exc}", file=sys.stderr)
        return 2
    sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    reply = b""
    while b"\r\n\r\n" not in reply:
        chunk = sock.recv(4096)
        if not chunk:
            break
        reply += chunk
    status = reply.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 200" not in status:
        print(f"connect: the proxy refused {host}:{port}: {status}", file=sys.stderr)
        sock.close()
        return 3
    sock.settimeout(None)
    done = threading.Event()

    def upstream() -> None:
        try:
            while True:
                data = os.read(0, 65536)
                if not data:
                    break
                sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
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
                    view = view[os.write(1, view):]
        except OSError:
            pass
        done.set()

    threading.Thread(target=upstream, daemon=True).start()
    threading.Thread(target=downstream, daemon=True).start()
    done.wait()
    try:
        sock.close()
    except OSError:
        pass
    return 0
