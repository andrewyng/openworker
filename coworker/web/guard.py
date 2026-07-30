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

Not covered: DNS rebinding. The name is resolved here and resolved again by the client when
it connects, so a record with a ~0 TTL can change between the two. Closing that needs
connection-level IP pinning; the hop check is the cheap 90% and is stated as such.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlsplit

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


def check_url(url: str) -> Optional[str]:
    """None if the URL may be fetched, else a human-readable refusal reason.

    Resolves the host and rejects when *any* answer lands in a blocked range, so a name
    with both a public and a private A record cannot be used to slip through.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "url must start with http:// or https://"
    host = parts.hostname
    if not host:
        return "url has no host"

    # A literal address needs no lookup.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        reason = _blocked_reason(literal)
        return f"refusing to fetch {host}: {reason}" if reason else None

    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return f"could not resolve {host}: {exc}"

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
            return f"refusing to fetch {host} ({ip}): {reason}"
    return None


def get_checked(client, url: str, *, max_redirects: int = MAX_REDIRECTS):
    """GET `url`, validating the address before every hop.

    `client` must be built with `follow_redirects=False`; redirects are walked here so each
    Location is checked. Returns the final response. Raises `PermissionError` when a hop is
    refused, `RuntimeError` when the redirect budget is exhausted.
    """
    seen = url
    for _ in range(max_redirects + 1):
        reason = check_url(seen)
        if reason:
            raise PermissionError(reason)
        resp = client.get(seen)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        seen = str(resp.url.join(location))
    raise RuntimeError(f"too many redirects (>{max_redirects})")
