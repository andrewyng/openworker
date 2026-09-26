"""Provisioning: every machine provenance converges on the ONE join token.

BYO machine, our managed sandbox, an operator in a customer VPC — each is a
`Provisioner` whose job is (a) mint a single-use join token and (b) optionally
launch something that will dial in with it (remote-home-design.md §Provenances).
Enrollment itself never varies: the box runs the existing join handshake.

Desktop ships `LocalProvisioner`: minting IS arming, and there is nothing to
launch — the user runs `openworker join` on their own hardware. Cloud adds
Fly/operator implementations behind the same interface.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class Provisioner(Protocol):
    def mint_token(self, spec: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Mint one single-use join token → {"token", "expires_at"}. A spec
        with a "fingerprint" binds the token to that machine identity — the
        acceptor refuses any other pubkey presenting it. A spec with an
        "org_id" makes the enrollment land in that tenant; a "provenance"
        {"kind", "ref"} is stamped on the enrolled row (managed sandboxes)."""
        ...

    async def launch(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Launch a machine that will join on boot (managed sandboxes and VPC
        operators). BYO provenances raise NotImplementedError."""
        ...


class LocalProvisioner:
    """Desktop: bring-your-own machines only."""

    def __init__(self, acceptor) -> None:
        self._acceptor = acceptor

    def mint_token(self, spec: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        spec = spec or {}
        token, expiry = self._acceptor.arm(
            bound_fingerprint=str(spec.get("fingerprint") or ""),
            org_id=str(spec.get("org_id") or ""),
            actor=str(spec.get("actor") or ""),
            provenance=spec.get("provenance") or None,
        )
        return {"token": token, "expires_at": expiry}

    async def launch(self, spec: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "no managed sandboxes on desktop — run `openworker join` on the machine"
        )
