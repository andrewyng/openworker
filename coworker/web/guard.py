"""Address guard for URLs the model chooses.

`web_fetch` and `browser_read_url` take a URL straight from the model, and the model's
input is untrusted by design — it reads web pages, email and Slack messages, all of which
are documented as "data, not instructions". A page that talks the agent into fetching
`http://169.254.169.254/` or `http://127.0.0.1:11434/` turns a read-only research tool into
a probe of the machine's own network position, and `web_fetch` is `requires_approval=False`,
so no prompt ever appears.

This blocks the ranges that are only reachable *because* OpenWorker runs on the user's
machine: loopback, RFC1918 and other private space, link-local (which covers the cloud
metadata endpoint at 169.254.169.254), and the reserved/multicast blocks.

Every hop is checked, not just the first: `follow_redirects=True` otherwise lets a public
URL 302 straight to loopback, which is the standard way this filter is bypassed.

DNS rebinding is closed by connection-level pinning: `get_checked` rewrites each hop so the
client connects to the exact address that passed the check (name in Host and SNI, so virtual
hosting and certificate verification still see the name). A record with a ~0 TTL that flips
to 127.0.0.1 between the check and the connect therefore changes nothing — the client never
resolves the name itself. `check_url` alone (browser_open_url's pre-check) still carries the
resolve-twice gap, because the browser owns its own connections and cannot be pinned from here.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_REDIRECTS = 5

# RFC 6598 shared address space. Python's is_private misses it, but it is carrier grade
# NAT space and Tailscale hands out internal hosts here (100.64.0.0/10), so a fetch to it
# is the same "reach the machine's network position" class as RFC1918.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _blocked_reason(ip: ipaddress._BaseAddress) -> Optional[str]:
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local (includes the cloud metadata endpoint)"
    if ip.is_private:
        return "a private network"
    if ip.version == 4 and ip in _CGNAT:
        return "shared address space (CGNAT / RFC 6598)"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved or ip.is_unspecified:
        return "a reserved range"
    return None


def _vet(url: str) -> tuple[Optional[str], Optional[str]]:
    """(refusal reason, address to pin the connection to).

    The reason is None when the URL may be fetched. The address is None for literal-IP
    URLs (the URL already names the connection target) and the first resolved answer
    otherwise — valid to pin because a refusal is returned when *any* answer lands in a
    blocked range, so a name with both a public and a private A record cannot slip through.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "url must start with http:// or https://", None
    host = parts.hostname
    if not host:
        return "url has no host", None

    # A literal address needs no lookup.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        reason = _blocked_reason(literal)
        return (f"refusing to fetch {host}: {reason}" if reason else None), None

    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return f"could not resolve {host}: {exc}", None

    pin: Optional[str] = None
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        # ::ffff:127.0.0.1 and friends must be judged as the v4 address they carry.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        reason = _blocked_reason(ip)
        if reason:
            return f"refusing to fetch {host} ({ip}): {reason}", None
        if pin is None:
            pin = raw
    return None, pin


def check_url(url: str) -> Optional[str]:
    """None if the URL may be fetched, else a human-readable refusal reason.

    Resolves the host and rejects when *any* answer lands in a blocked range, so a name
    with both a public and a private A record cannot be used to slip through.
    """
    return _vet(url)[0]


def _pinned(url: str, ip: str) -> tuple[str, dict, dict]:
    """Rewrite `url` so the client connects to `ip` while presenting the original name.

    Returns (request_url, headers, extensions): the URL carries the vetted address so the
    client never resolves the name itself, Host carries the name (and any explicit port)
    for virtual hosting, and `sni_hostname` keeps the TLS handshake — including certificate
    verification — against the name rather than the address.
    """
    parts = urlsplit(url)
    host = parts.hostname
    addr = f"[{ip}]" if ":" in ip else ip
    userinfo, _, _ = parts.netloc.rpartition("@")
    netloc = (f"{userinfo}@" if userinfo else "") + addr
    host_header = host
    if parts.port is not None:
        netloc += f":{parts.port}"
        host_header += f":{parts.port}"
    request_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return request_url, {"Host": host_header}, extensions


def get_checked(client, url: str, *, max_redirects: int = MAX_REDIRECTS):
    """GET `url`, validating and pinning the address before every hop.

    `client` must be built with `follow_redirects=False`; redirects are walked here so each
    Location is checked. Every hop connects to the exact address that passed its check (see
    `_pinned`), so a rebinding name cannot swap targets between check and connect. Returns
    the final response, with the final *logical* URL — the name, not the pinned address —
    stashed as `resp.extensions["logical_url"]` for callers that display it. Raises
    `PermissionError` when a hop is refused, `RuntimeError` when the budget is exhausted.
    """
    seen = url
    for _ in range(max_redirects + 1):
        reason, pin = _vet(seen)
        if reason:
            raise PermissionError(reason)
        if pin is None:
            resp = client.get(seen)
        else:
            request_url, headers, extensions = _pinned(seen, pin)
            resp = client.get(request_url, headers=headers, extensions=extensions)
        if resp.status_code not in (301, 302, 303, 307, 308):
            ext = getattr(resp, "extensions", None)
            if isinstance(ext, dict):
                ext["logical_url"] = seen
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        # Resolved against the logical URL, not resp.url — the latter names the pinned
        # address, and a relative Location must stay on the original host.
        seen = urljoin(seen, location)
    raise RuntimeError(f"too many redirects (>{max_redirects})")
