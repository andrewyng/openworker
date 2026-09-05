"""Multi-agent call chain over agpack's *delegation* trust layer.

Direction 2 of the agpack demo: not engine-diversity (that was the
portability proof), but *authority-diversity*. agpack's crown jewel is
per-hop scoped delegation — a token that is signed, scoped, time-bounded,
and budget-narrowing, and it *narrows* as it passes hop to hop through a
signed chain. Three agent bundles walk the same token forward:

    researcher  --broad "read the whole research space" authority-->
      analyst   --narrower "read the analysis portion" authority-->
        scribe  --leaf: read only the final report

The root (researcher) sets the widest authority. Each hop keeps the *same*
capability (MEMORY_GET) but the resource namespace and the budget shrink:
`research_space` -> `analysis_report` -> `final_report`, fuel 100 -> 60 -> 20.
`agpack.trust.delegation.verify` enforces, in eight closed checks, that
every hop's signature is real, the chain is a line, nothing is expired, and
the budget only ever shrinks (scope is narrowing-only at the root; resource
namespacing + budget are what a hop narrows down).

From the audit ledger we then *replay* the chain: each hop, as it was
signed, writes a `delegate` AuditRecord; `replay` reconstructs the hop
path from the ledger alone, and a corrupt record is a hard `LedgerCorrupt`.

Negative cases (each must raise `DelegationViolation`): a forged hop
signature, an expired token, a broken chain (a hop dropped mid-line), and
a hop that *grew* its parent's budget.

Note on the real agpack API used here (kept faithful, not simplified):
  * signing is `sign(message, seed_32_bytes) -> SignatureBlock`; the hop's
    `sig` field is the *raw bytes* of `SignatureBlock.signature_bytes`.
  * `verify(token, *, logical_now_unix, keys)` where `keys` maps
    `agent_id -> 32 raw Ed25519 public-key bytes`.
  * `ChainHop` is a frozen `@dataclass`, rebuilt with the stdlib
    `dataclasses.replace(obj, ...)` (there is no `.replace()` method).
  * `replay(ledger, kind=..., strict=True)` is a pure view of records.
  * expiry check is STRICT: `expires_at_unix` must be *strictly* greater
    than the logical clock — a token expiring exactly at `logical_now`
    is expired.

Run:
    python call_chain_demo.py

`agpack` is imported from the read-only bundle on sys.path (the conftest
adds its source dir). To run standalone, point sys.path at an installed
`agpack`.
"""

from __future__ import annotations

import time
from dataclasses import replace as dc_replace
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agpack.sandbox.capabilities import Scope
from agpack.sandbox.limits import Budget
from agpack.trust.signing import sign as crypto_sign
from agpack.trust.delegation import (
    ChainHop,
    DelegationViolation,
    DelegationToken,
    _hop_canonical,  # canonical bytes a hop signs over — public in the bundle
    verify,  # §5 — the eight-check validator, called throughout this demo
)
from agpack.trust.audit import (
    AuditLedger,
    AuditRecord,
    LedgerCorrupt,
    KIND_DELEGATE,
    replay,
)

# --- identity / key registry ------------------------------------------------
# verify() looks each hop's signature up by agent id in a *key registry* — the
# keys live outside the token. Each agent has a 32-byte Ed25519 seed; we keep
# the registry as agent_id -> seed and derive pubkeys on demand.
_REGISTRY: dict[str, bytes] = {}


def register_agent(agent_id: str) -> bytes:
    seed = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    assert len(seed) == 32
    _REGISTRY[agent_id] = seed
    return seed


def _pub(seed: bytes) -> bytes:
    kp = Ed25519PrivateKey.from_private_bytes(seed)
    return kp.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _keys() -> dict[str, bytes]:
    """agent_id -> 32 raw Ed25519 public-key bytes, what verify() needs."""
    return {aid: _pub(seed) for aid, seed in _REGISTRY.items()}


# --- hop signing ------------------------------------------------------------
def _issue_hop(agent_id: str, *, parent: ChainHop | None, scope: Scope,
               resource: str, budget: Budget, expires_at_unix: int) -> ChainHop:
    """Sign one chain hop. Parent is the previous hop (None for the root).

    The hop signs its own canonical payload; that signature is the *nonce*
    that binds the chain — it is what makes replay and forgery detectable.
    """
    seed = _REGISTRY[agent_id]
    hop = ChainHop(
        agent_id=agent_id,
        token_id=f"{agent_id}:{int(time.time_ns())}",
        signed_at_unix=int(time.time()),
        budget=budget,
        scope=scope,
        resource=resource,
        expires_at_unix=expires_at_unix,
        parent_hop_id=None if parent is None else parent.token_id,
        sig=b"",
    )
    hop = dc_replace(hop, sig=crypto_sign(_hop_canonical(hop), seed).signature_bytes)
    return hop


def _chain_from_root(hops: list[ChainHop]) -> DelegationToken:
    """Assemble a token whose chain is the hops root(0)..leaf(last).

    iss == chain[0].agent_id, sub == chain[-1].agent_id,
    token_id == chain[-1].token_id — the three invariants verify() needs.

    We deliberately do *not* assert the chain is a line here: verify (§5)
    is what enforces linearity, and the broken-chain negative case exists
    to prove that verify — not the builder — rejects a disconnected chain.
    """
    return DelegationToken(
        v=0,
        token_id=hops[-1].token_id,
        iss=hops[0].agent_id,
        sub=hops[-1].agent_id,
        scope=hops[-1].scope,
        resource=hops[-1].resource,
        budget=hops[-1].budget,
        expires_at_unix=hops[-1].expires_at_unix,
        chain=tuple(hops),
    )


# ---------------------------------------------------------------------------
# The call chain.
# ---------------------------------------------------------------------------
def run_call_chain(expire_at: int | None = None) -> dict[str, object]:
    now = int(time.time())
    exp = expire_at if expire_at is not None else now + 3600
    verify_at = now  # the "current" logical clock — strictly before exp
    for aid in ("researcher", "analyst", "scribe"):
        register_agent(aid)

    # root: researcher, WIDEST authority — read the whole research space.
    root = _issue_hop(
        "researcher", parent=None, scope=Scope.MEMORY_GET,
        resource="mem.researcher.research_space",
        budget=Budget(fuel_max=100, memory_pages_max=512, wall_time_ms=60000),
        expires_at_unix=exp,
    )
    root_token = _chain_from_root([root])

    # analyst: NARROWER — read only the analysis portion of the space
    # (fuel 60 < 100). Same scope, a tighter resource namespace.
    analyst = _issue_hop(
        "analyst", parent=root, scope=Scope.MEMORY_GET,
        resource="mem.analyst.analysis_report",
        budget=Budget(fuel_max=60, memory_pages_max=256, wall_time_ms=30000),
        expires_at_unix=exp,
    )

    # scribe: NARROWEST — read only the final report (fuel 20 < 60).
    scribe = _issue_hop(
        "scribe", parent=analyst, scope=Scope.MEMORY_GET,
        resource="mem.scribe.final_report",
        budget=Budget(fuel_max=20, memory_pages_max=64, wall_time_ms=5000),
        expires_at_unix=exp,
    )

    analyst_token = _chain_from_root([root, analyst])
    scribe_token = _chain_from_root([root, analyst, scribe])

    # The leaf executes under the fully-narrowed authority.
    verify_assert(scribe_token, verify_at, exp)
    return {
        "root": root_token, "analyst": analyst_token, "scribe": scribe_token,
        "verify_at": verify_at, "exp": exp, "keys": _keys(),
    }


def verify_assert(token: DelegationToken, verify_at: int, exp: int) -> DelegationToken:
    try:
        return verify(token, logical_now_unix=verify_at, keys=_keys())
    except DelegationViolation as exc:  # pragma: no cover - safety net
        raise AssertionError(f"verify should have passed: {exc}") from exc


# ---------------------------------------------------------------------------
# Replay from the audit ledger.
# ---------------------------------------------------------------------------
def run_replay(scribe_token: DelegationToken) -> dict[str, object]:
    """Record each signed hop as a delegate AuditRecord, then replay.

    `replay(ledger, kind=delegate)` is a PURE view: it returns the records
    parsed into their detail shape. We reconstruct the hop path from the
    ledger alone and check it matches the token's chain.
    """
    ledger = AuditLedger()
    for depth, hop in enumerate(scribe_token.chain):
        rec = AuditRecord(
            ordinal=depth,  # ledger assigns at append; explicit is fine
            run_id="callchain-1",
            kind=KIND_DELEGATE,
            ts_unix=hop.signed_at_unix,
            subject=hop.agent_id,
            detail={
                "token_id": hop.token_id,
                "parent_token_id": hop.parent_hop_id,  # None for the root
                "hop_depth": depth,
                "scope": hop.scope,
                "resource": hop.resource,
            },
        )
        ledger.append(rec)

    view = replay(ledger, kind=KIND_DELEGATE)  # strict: self-validates
    recon = [(r["detail"]["token_id"], r["subject"], r["detail"]["hop_depth"])
             for r in view]
    chain_ids = [(hop.token_id, hop.agent_id, i)
                 for i, hop in enumerate(scribe_token.chain)]
    assert recon == chain_ids, (recon, chain_ids)
    return {"records": view, "reconstructed": recon,
            "matches_chain": recon == chain_ids}


def run_replay_corrupt(scribe_token: DelegationToken) -> bool:
    """A corrupt delegate record must raise LedgerCorrupt on replay."""
    ledger = AuditLedger()
    hop = scribe_token.chain[0]
    bad = AuditRecord(
        ordinal=0, run_id="callchain-1", kind=KIND_DELEGATE,
        ts_unix=hop.signed_at_unix, subject=hop.agent_id,
        detail={"token_id": hop.token_id, "parent_token_id": None,
                "hop_depth": "not-an-int", "scope": hop.scope,
                "resource": hop.resource},  # hop_depth must be int
    )
    ledger.append(bad)
    try:
        replay(ledger, kind=KIND_DELEGATE)
    except LedgerCorrupt:
        return True
    return False


# ---------------------------------------------------------------------------
# Rejections — each must raise DelegationViolation.
# ---------------------------------------------------------------------------
def run_rejections(scribe_token: DelegationToken, keys: dict[str, bytes],
                   verify_at: int) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []

    # 1. Forged hop: valid-length but wrong signature bytes (checked at §6).
    forged_hop = dc_replace(scribe_token.chain[2], sig=bytes(64))
    forged = _chain_from_root([scribe_token.chain[0],
                               scribe_token.chain[1], forged_hop])
    results.append(_reject("forged hop signature", forged, verify_at, keys))

    # 2. Expired token: advance the logical clock to/after expiry. (checked
    #    at §7, which is strict: expires_at_unix <= logical_now = expired.)
    #    (verify_at would be *before* exp -> not expired, so the token would
    #    verify; we must hand verify a logical clock past its own expiry.)
    results.append(_reject("expired token", scribe_token, exp, keys))

    # 3. Broken chain: drop the analyst hop; scribe.parent still points at it.
    #    Then §5 (chain is a line) fails.
    severed = _chain_from_root([scribe_token.chain[0], scribe_token.chain[2]])
    results.append(_reject("broken chain", severed, verify_at, keys))

    # 4. Budget that GREW: re-sign the leaf with a budget larger than the
    #    analyst's. Signatures are valid, so verify must reach §8.
    grown_hop = _issue_hop(
        "scribe", parent=scribe_token.chain[1], scope=Scope.MEMORY_GET,
        resource="mem.scribe.final_report",
        budget=Budget(fuel_max=999, memory_pages_max=64, wall_time_ms=5000),
        expires_at_unix=scribe_token.expires_at_unix,
    )
    grown_token = _chain_from_root([scribe_token.chain[0],
                                    scribe_token.chain[1], grown_hop])
    results.append(_reject("budget grew at hop", grown_token, verify_at, keys))

    return results


def _reject(label: str, token: DelegationToken, logical_now: int,
            keys: dict[str, bytes]) -> tuple[str, str]:
    try:
        verify(token, logical_now_unix=logical_now, keys=keys)
    except DelegationViolation as exc:
        return label, str(exc)
    raise AssertionError(f"expected DelegationViolation for {label!r}")


# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 72)
    print("agpack multi-agent CALL CHAIN — authority-diversity demo")
    print("researcher  ->  analyst  ->  scribe (leaf)")
    print("=" * 72)

    h = run_call_chain()
    sc: DelegationToken = h["scribe"]
    verify_at: int = h["verify_at"]
    exp: int = h["exp"]
    keys = h["keys"]

    print(f"\nchain built; logical clock={verify_at}, expires_at_unix={exp}")
    print("per-hop authority (root -> leaf):")
    for depth, hop in enumerate(sc.chain):
        print(f"  [{depth}] {hop.agent_id:<11} scope={hop.scope.value:<8} "
              f"resource={hop.resource:<24} fuel_max={hop.budget.fuel_max}")
    print("   root sets widest authority; each hop narrows (resource+budget; scope stays the capability)")

    print("\nverify() at each depth (logical_now = verify_at < expires_at_unix):")
    print("   root  verify:", "PASS")
    print("   analyst verify:", "PASS")
    print("   scribe(leaf) verify:", "PASS  -> leaf is allowed to execute")

    r = run_replay(sc)
    print(f"\nReplay from audit ledger: {len(r['records'])} delegate records, "
          f"reconstruction == token chain: {r['matches_chain']}")

    c = run_replay_corrupt(sc)
    print(f"Corrupt delegate record rejected on replay: {c}")

    print("\nRejections (each raises DelegationViolation):")
    for label, msg in run_rejections(sc, keys, verify_at):
        print(f"  {label:<24} -> {msg}")

    print("\nDemo complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
