"""Deterministic destination policy for Browser Use network egress.

This module makes the security decision that a fail-closed loopback proxy must
enforce.  For every HTTP(S), WS, or WSS request:

1. ``evaluate`` canonicalizes the origin and resolves the hostname once.
2. Every returned address must be acceptable; mixed public/private answers fail.
3. The decision selects one validated address that the proxy must connect to
   directly while preserving the original hostname for Host/SNI.
4. ``verify_peer`` checks that the socket did not silently resolve elsewhere.

Redirects, popups, subresources, CONNECT, and WebSockets each require a fresh
decision.  A Playwright request route is not a substitute for this policy.
"""

from __future__ import annotations

import ipaddress
import socket
import unicodedata
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit


_ALLOWED_SCHEMES = frozenset({"http", "https", "ws", "wss"})
_DEFAULT_PORTS = {"http": 80, "ws": 80, "https": 443, "wss": 443}
_METADATA_HOSTNAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("192.0.0.192"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
_IPV6_TRANSITION_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),  # well-known NAT64
    ipaddress.ip_network("64:ff9b:1::/48"),  # local-use NAT64
)
_EXPLICIT_LOCAL_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


class DestinationPolicyError(ValueError):
    """A URL cannot be safely authorized."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, order=True)
class CanonicalOrigin:
    scheme: str
    host: str
    port: int

    @property
    def value(self) -> str:
        display_host = f"[{self.host}]" if ":" in self.host else self.host
        if self.port == _DEFAULT_PORTS[self.scheme]:
            return f"{self.scheme}://{display_host}"
        return f"{self.scheme}://{display_host}:{self.port}"


@dataclass(frozen=True)
class DestinationDecision:
    """An allow decision and the exact address a proxy must pin for this socket."""

    canonical_url: str
    origin: CanonicalOrigin
    resolved_addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]
    pinned_address: ipaddress.IPv4Address | ipaddress.IPv6Address
    local_grant_used: bool

    @property
    def connect_host(self) -> str:
        return str(self.pinned_address)

    @property
    def connect_port(self) -> int:
        return self.origin.port

    @property
    def server_name(self) -> str:
        """Original canonical hostname to use for Host and TLS SNI."""

        return self.origin.host

    def verify_peer(self, peer_address: str) -> None:
        """Fail if the connected socket is not the address selected above."""

        try:
            peer = _normalize_ip(ipaddress.ip_address(peer_address.split("%", 1)[0]))
        except ValueError as exc:
            raise DestinationPolicyError(
                "PEER_ADDRESS_INVALID", "Connected peer address is invalid"
            ) from exc
        if peer != self.pinned_address:
            raise DestinationPolicyError(
                "DNS_PIN_MISMATCH",
                "Connected peer differs from the validated DNS decision",
            )


Resolver = Callable[[str, int], Sequence[str]]


class DestinationPolicy:
    """Canonical origin and resolved-address allow policy.

    ``local_origin_grants`` are exact scheme/host/port grants.  A grant allows
    otherwise non-global addresses for only that origin.  It never overrides the
    cloud-metadata denylist.
    """

    def __init__(
        self,
        *,
        local_origin_grants: Iterable[str | CanonicalOrigin] = (),
        resolver: Optional[Resolver] = None,
    ) -> None:
        self._resolver = resolver or _system_resolver
        grants: set[CanonicalOrigin] = set()
        for value in local_origin_grants:
            grants.add(
                value if isinstance(value, CanonicalOrigin) else canonical_origin(value)
            )
        self._local_grants = frozenset(grants)

    @property
    def local_origin_grants(self) -> frozenset[CanonicalOrigin]:
        return self._local_grants

    def with_local_origin_grant(
        self, value: str | CanonicalOrigin
    ) -> "DestinationPolicy":
        """Return an equivalent policy with one additional exact local origin.

        Browser site permission is granted after the user approves or directly
        enters a URL. Returning a new immutable policy keeps in-flight connection
        decisions stable while later requests see the new exact-origin grant.
        """

        grant = value if isinstance(value, CanonicalOrigin) else canonical_origin(value)
        return DestinationPolicy(
            local_origin_grants=(*self._local_grants, grant),
            resolver=self._resolver,
        )

    def evaluate(self, url: str) -> DestinationDecision:
        canonical_url, origin = canonicalize_url(url)
        if origin.host in _METADATA_HOSTNAMES:
            raise DestinationPolicyError(
                "METADATA_DESTINATION_BLOCKED",
                "Cloud metadata destinations are never available to Browser Use",
            )
        local_grant = origin in self._local_grants
        literal = _parse_ip_literal(origin.host)
        if literal is not None:
            addresses = (literal,)
        else:
            try:
                raw_addresses = self._resolver(origin.host, origin.port)
            except Exception as exc:
                raise DestinationPolicyError(
                    "DNS_RESOLUTION_FAILED", "Destination DNS resolution failed"
                ) from exc
            addresses = _validated_dns_answers(raw_addresses)
        if not addresses:
            raise DestinationPolicyError(
                "DNS_NO_ADDRESSES", "Destination resolved to no usable addresses"
            )
        for address in addresses:
            reason = _blocked_address_reason(address)
            if reason == "metadata":
                raise DestinationPolicyError(
                    "METADATA_DESTINATION_BLOCKED",
                    "Cloud metadata destinations are never available to Browser Use",
                )
            if reason is not None and not local_grant:
                raise DestinationPolicyError(
                    "NON_PUBLIC_DESTINATION_BLOCKED",
                    "Private, local, or special-use destinations require an exact local-origin grant",
                )
        pinned = min(addresses, key=_address_sort_key)
        return DestinationDecision(
            canonical_url=canonical_url,
            origin=origin,
            resolved_addresses=addresses,
            pinned_address=pinned,
            local_grant_used=local_grant and any(
                _blocked_address_reason(address) is not None for address in addresses
            ),
        )


def canonical_origin(url: str) -> CanonicalOrigin:
    """Return an exact normalized origin or raise ``DestinationPolicyError``."""

    return canonicalize_url(url)[1]


def is_explicit_local_origin(url: str) -> bool:
    """Whether a user-entered origin is unambiguously local by spelling.

    Public DNS names are deliberately excluded even if they currently resolve to
    a private address. Granting those names would turn a later DNS-rebinding
    response into local-network access. The MVP supports localhost names and
    literal loopback/RFC1918/ULA addresses; other intranet hostnames remain
    fail-closed.
    """

    origin = canonical_origin(url)
    host = origin.host.rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    address = _parse_ip_literal(host)
    if address is None or address in _METADATA_IPS:
        return False
    return any(address in network for network in _EXPLICIT_LOCAL_NETWORKS)


def canonicalize_url(url: str) -> tuple[str, CanonicalOrigin]:
    if not isinstance(url, str) or not url.strip():
        raise DestinationPolicyError("URL_INVALID", "Destination URL is empty")
    if "\\" in url or any(
        unicodedata.category(char).startswith("C") for char in url
    ):
        raise DestinationPolicyError(
            "URL_INVALID", "Destination URL contains control characters"
        )
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise DestinationPolicyError("URL_INVALID", "Destination URL is malformed") from exc
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise DestinationPolicyError(
            "SCHEME_BLOCKED", "Only HTTP(S) and WebSocket destinations are allowed"
        )
    if parsed.username is not None or parsed.password is not None:
        raise DestinationPolicyError(
            "URL_CREDENTIALS_BLOCKED", "Credentials are not allowed in destination URLs"
        )
    raw_host = parsed.hostname
    if raw_host is None:
        raise DestinationPolicyError("URL_INVALID", "Destination URL has no hostname")
    host = _canonical_host(raw_host)
    try:
        port = parsed.port or _DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise DestinationPolicyError("PORT_INVALID", "Destination port is invalid") from exc
    if not 1 <= port <= 65535:
        raise DestinationPolicyError("PORT_INVALID", "Destination port is invalid")
    origin = CanonicalOrigin(scheme, host, port)
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host
    if port != _DEFAULT_PORTS[scheme]:
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    # Fragment identifiers never reach the server and are deliberately omitted.
    canonical = urlunsplit(SplitResult(scheme, netloc, path, parsed.query, ""))
    return canonical, origin


def _canonical_host(host: str) -> str:
    value = unicodedata.normalize("NFC", host).rstrip(".").lower()
    if not value or "%" in value:
        # Scoped IPv6 addresses are interface-relative and must never leave the proxy.
        raise DestinationPolicyError("HOST_INVALID", "Destination hostname is invalid")
    literal = _parse_ip_literal(value)
    if literal is not None:
        return str(literal)
    try:
        ascii_host = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise DestinationPolicyError(
            "HOST_INVALID", "Destination hostname cannot be canonicalized"
        ) from exc
    if (
        len(ascii_host) > 253
        or any(not label or len(label) > 63 for label in ascii_host.split("."))
    ):
        raise DestinationPolicyError("HOST_INVALID", "Destination hostname is invalid")
    return ascii_host


def _parse_ip_literal(
    host: str,
) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return _normalize_ip(ipaddress.ip_address(host))
    except ValueError:
        return None


def _normalize_ip(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _validated_dns_answers(
    answers: Sequence[str],
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    unique: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for raw in answers:
        if not isinstance(raw, str) or "%" in raw:
            raise DestinationPolicyError(
                "DNS_ANSWER_INVALID", "Destination DNS returned an invalid address"
            )
        try:
            unique.add(_normalize_ip(ipaddress.ip_address(raw)))
        except ValueError as exc:
            raise DestinationPolicyError(
                "DNS_ANSWER_INVALID", "Destination DNS returned an invalid address"
            ) from exc
    return tuple(sorted(unique, key=_address_sort_key))


def _blocked_address_reason(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> Optional[str]:
    address = _normalize_ip(address)
    if address in _METADATA_IPS:
        return "metadata"
    if isinstance(address, ipaddress.IPv6Address):
        # Do not allow transition mechanisms to smuggle an address class that differs
        # from the outer IPv6 address seen by the policy.
        if address.ipv4_mapped is not None or address.sixtofour is not None:
            return "transition"
        try:
            if address.teredo is not None:
                return "transition"
        except ValueError:
            return "transition"
        if any(address in network for network in _IPV6_TRANSITION_NETWORKS):
            return "transition"
    if not address.is_global:
        return "non-global"
    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return "non-global"
    return None


def _address_sort_key(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> tuple[int, bytes]:
    return address.version, address.packed


def _system_resolver(host: str, port: int) -> Sequence[str]:
    results = socket.getaddrinfo(
        host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
    )
    return [str(result[4][0]) for result in results]
