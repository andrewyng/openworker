from __future__ import annotations

import ipaddress

import pytest

from coworker.browser_security.destination import (
    DestinationPolicy,
    DestinationPolicyError,
    canonical_origin,
    canonicalize_url,
    is_explicit_local_origin,
)


def _resolver(addresses):
    calls = []

    def resolve(host, port):
        calls.append((host, port))
        return addresses

    resolve.calls = calls
    return resolve


def test_origin_and_url_canonicalization():
    url, origin = canonicalize_url("HTTPS://BÜCHER.Example.:443/a?q=secret#frag")
    assert origin.value == "https://xn--bcher-kva.example"
    assert url == "https://xn--bcher-kva.example/a?q=secret"
    assert canonical_origin("http://example.com:80").value == "http://example.com"
    assert canonical_origin("ws://example.com:81").value == "ws://example.com:81"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:4173", True),
        ("https://app.localhost", True),
        ("http://127.0.0.1:8000", True),
        ("http://10.2.3.4", True),
        ("http://172.16.4.5", True),
        ("http://192.168.1.9", True),
        ("http://[::1]:9000", True),
        ("http://[fd00::1234]", True),
        ("https://example.com", False),
        ("https://public.example", False),
        ("http://8.8.8.8", False),
        ("http://169.254.169.254", False),
    ],
)
def test_only_unambiguous_local_spellings_receive_local_grants(url, expected):
    assert is_explicit_local_origin(url) is expected


def test_public_dns_answer_is_pinned_without_reresolving():
    resolver = _resolver(["8.8.8.8", "1.1.1.1", "8.8.8.8"])
    decision = DestinationPolicy(resolver=resolver).evaluate("https://EXAMPLE.com/path")
    assert decision.origin.value == "https://example.com"
    assert decision.resolved_addresses == (
        ipaddress.ip_address("1.1.1.1"),
        ipaddress.ip_address("8.8.8.8"),
    )
    assert decision.connect_host == "1.1.1.1"
    assert resolver.calls == [("example.com", 443)]
    decision.verify_peer("1.1.1.1")
    with pytest.raises(DestinationPolicyError, match="differs"):
        decision.verify_peer("8.8.8.8")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.1.1",
        "192.168.1.1",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::",
        "::ffff:127.0.0.1",
        "2002:7f00:1::",
    ],
)
def test_non_public_addresses_are_blocked(address):
    with pytest.raises(DestinationPolicyError) as error:
        DestinationPolicy(resolver=_resolver([address])).evaluate(
            "https://internal.example"
        )
    assert error.value.code == "NON_PUBLIC_DESTINATION_BLOCKED"


def test_mixed_public_private_dns_fails_whole_decision():
    with pytest.raises(DestinationPolicyError) as error:
        DestinationPolicy(resolver=_resolver(["1.1.1.1", "127.0.0.1"])).evaluate(
            "https://mixed.example"
        )
    assert error.value.code == "NON_PUBLIC_DESTINATION_BLOCKED"


def test_exact_local_origin_grant_does_not_expand_to_alias_port_or_scheme():
    policy = DestinationPolicy(
        local_origin_grants=["http://localhost:3000"],
        resolver=_resolver(["127.0.0.1"]),
    )
    allowed = policy.evaluate("http://localhost:3000/app")
    assert allowed.local_grant_used
    for url in (
        "http://localhost:3001",
        "https://localhost:3000",
        "http://127.0.0.1:3000",
    ):
        with pytest.raises(DestinationPolicyError):
            policy.evaluate(url)


@pytest.mark.parametrize(
    "host,address",
    [
        ("metadata.google.internal", "8.8.8.8"),
        ("public.example", "169.254.169.254"),
        ("public.example", "169.254.170.2"),
        ("public.example", "100.100.100.200"),
        ("public.example", "fd00:ec2::254"),
    ],
)
def test_metadata_is_blocked_even_with_exact_local_grant(host, address):
    policy = DestinationPolicy(
        local_origin_grants=[f"http://{host}:8080"],
        resolver=_resolver([address]),
    )
    with pytest.raises(DestinationPolicyError) as error:
        policy.evaluate(f"http://{host}:8080/")
    assert error.value.code == "METADATA_DESTINATION_BLOCKED"


def test_literal_ip_skips_dns_and_invalid_dns_fails_closed():
    resolver = _resolver(["1.1.1.1"])
    decision = DestinationPolicy(resolver=resolver).evaluate("https://8.8.8.8/")
    assert decision.connect_host == "8.8.8.8"
    assert resolver.calls == []

    with pytest.raises(DestinationPolicyError) as error:
        DestinationPolicy(resolver=_resolver(["not-an-ip"])).evaluate(
            "https://example.com"
        )
    assert error.value.code == "DNS_ANSWER_INVALID"


@pytest.mark.parametrize(
    "url,code",
    [
        ("file:///etc/passwd", "SCHEME_BLOCKED"),
        ("https://user:password@example.com", "URL_CREDENTIALS_BLOCKED"),
        ("https://example.com:99999", "PORT_INVALID"),
        ("https://[fe80::1%25en0]/", "HOST_INVALID"),
        ("https://example.com/\nHost: attacker", "URL_INVALID"),
    ],
)
def test_ambiguous_or_dangerous_urls_are_rejected(url, code):
    with pytest.raises(DestinationPolicyError) as error:
        DestinationPolicy(resolver=_resolver(["1.1.1.1"])).evaluate(url)
    assert error.value.code == code


def test_numeric_hostname_cannot_bypass_ip_validation():
    # Some OS resolvers accept a one-integer form of 127.0.0.1.  The final answer,
    # not the input spelling, is what decides access.
    with pytest.raises(DestinationPolicyError):
        DestinationPolicy(resolver=_resolver(["127.0.0.1"])).evaluate(
            "http://2130706433/"
        )
