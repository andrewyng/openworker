"""The allow-list proxy: the only way out for a sandbox that cannot filter by host itself.

Seatbelt can say "this process may connect to one local port and nowhere else", but it
cannot say "only to github.com". So the sandbox is given this port, and this proxy decides.
It runs in the server, outside every sandbox, and does one thing: `CONNECT host:443` for a
host on the profile's list is tunnelled; anything else gets a 403 that says why. The
traffic inside the tunnel is end-to-end TLS; the proxy never sees it.

Standard library only, one thread per tunnel. One proxy per network profile per process.
"""

from __future__ import annotations

import select
import socket
import threading
from collections import deque
from typing import Optional, Sequence

from . import network_profiles

_IDLE_SECONDS = 300.0
_CONNECT_SECONDS = 15.0
_MAX_HEAD = 16 * 1024


class AllowListProxy:
    def __init__(self, profile: str = network_profiles.DEFAULT_PROFILE, extra_hosts: Sequence[str] = ()) -> None:
        """`extra_hosts`: "host:port" entries beyond the profile (credential grants, section
        11b); `*.example.com` matches any subdomain. A port of 22 is an SSH tunnel."""
        self.profile = network_profiles.check(profile)
        self._hosts = {h.lower() for h in network_profiles.hosts(profile)}
        self._extra: set[tuple[str, int]] = set()
        for item in extra_hosts:
            host, _, port = str(item).rpartition(":")
            if host and port.isdigit():
                self._extra.add((host.lower().rstrip("."), int(port)))
        self.denied: deque[str] = deque(maxlen=50)  # recent refusals, newest last
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))  # this machine only
        self._server.listen(64)
        self.port: int = self._server.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept, name=f"sandbox-proxy-{profile}", daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def allows(self, host: str, port: int) -> bool:
        host = host.lower().rstrip(".")
        if port == 443 and host in self._hosts:
            return True
        for allowed, allowed_port in self._extra:
            if allowed_port != port:
                continue
            if allowed.startswith("*.") and host.endswith(allowed[1:]) and host != allowed[2:]:
                return True
            if host == allowed:
                return True
        return False

    def close(self) -> None:
        self._closed = True
        try:
            self._server.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while not self._closed:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        upstream: Optional[socket.socket] = None
        try:
            client.settimeout(_CONNECT_SECONDS)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk or len(head) > _MAX_HEAD:
                    return
                head += chunk
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = request_line.split()
            if len(parts) != 3:
                return self._refuse(client, 400, "not an HTTP request")
            method, target = parts[0].upper(), parts[1]
            if method != "CONNECT":
                return self._refuse(client, 403, f"only tunnelled connections are allowed out of this sandbox (got {method} {target[:120]})", target)
            host, _, port_text = target.rpartition(":")
            host = host.strip("[]")
            if not host or not port_text.isdigit():
                return self._refuse(client, 400, "bad CONNECT target")
            if not self.allows(host, int(port_text)):
                return self._refuse(
                    client,
                    403,
                    f"{host}:{port_text} is not on this sandbox's allow list (network profile '{self.profile}')",
                    f"{host}:{port_text}",
                )
            try:
                upstream = socket.create_connection((host, int(port_text)), timeout=_CONNECT_SECONDS)
            except OSError as exc:
                return self._refuse(client, 502, f"could not reach {host}: {exc}")
            # HTTP/1.0 on purpose: BSD nc (the ssh ProxyCommand on macOS) accepts only that
            # line for a tunnel, and every HTTP client accepts it too.
            client.sendall(b"HTTP/1.0 200 Connection established\r\n\r\n")
            rest = head.split(b"\r\n\r\n", 1)[1]
            if rest:
                upstream.sendall(rest)
            self._pump(client, upstream)
        except OSError:
            pass
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    def _refuse(self, client: socket.socket, status: int, why: str, target: str = "") -> None:
        if status == 403 and target:
            self.denied.append(target)
        body = f"Blocked by the OpenWorker sandbox: {why}.\n".encode()
        reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}[status]
        head = f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        client.sendall(head.encode() + body)

    @staticmethod
    def _pump(a: socket.socket, b: socket.socket) -> None:
        a.settimeout(None)
        b.settimeout(None)
        while True:
            ready, _, _ = select.select([a, b], [], [], _IDLE_SECONDS)
            if not ready:
                return
            for src in ready:
                data = src.recv(65536)
                if not data:
                    return
                (b if src is a else a).sendall(data)


_lock = threading.Lock()
_proxies: dict[str, AllowListProxy] = {}


def shared(profile: str = network_profiles.DEFAULT_PROFILE) -> AllowListProxy:
    """This process's proxy for a profile, started on first use."""
    with _lock:
        proxy = _proxies.get(profile)
        if proxy is None or proxy._closed:
            proxy = _proxies[profile] = AllowListProxy(profile)
        return proxy


def environment(proxy: AllowListProxy) -> dict[str, str]:
    """What a sandboxed process needs in its environment to find the proxy. Programs that
    ignore these variables get no network at all, which is the safe way to fail."""
    names = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
    env = {name: proxy.url for name in names}
    env.update({"NO_PROXY": "", "no_proxy": "", "NODE_USE_ENV_PROXY": "1"})
    return env
