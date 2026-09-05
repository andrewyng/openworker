# SPIKE — Stage 1: Attribution + Disclosure Agent

> Highest-tangibility, zero-settlement-risk. Reads reserve/yield events, produces
> a per-partner yield table + a signed, replayable disclosure. Directly reuses
> **sentinel** (local multi-agent, consensus, schema validation) + **agpack**
> (signed bundle, audit ledger). No money moves.

## Why this spike
The full pipeline has 6 stages. Stages 1–2 (observe → attribute) and 5 (disclose)
carry nearly all the demonstrable value — **yield leakage, per-partner
transparency, disclosure cost** — with **no settlement exposure**. Settlement
routing (stage 3–4) is the only stage that touches value, so it's proven *last*.

## Definition of "done" (all must hold, then a verdict)
1. **Attribution is correct by construction.** A synthetic reserve of `N` OUSD
   split across Aave/Lido at known APYs, over a known interval, produces a
   **per-partner share table** whose totals exactly equal the on-chain reserve
   delta (no leakage, no phantom yield). Sum-of-shares = total, asserted.
2. **ERC-4626 share math.** Yield is attributed by *share* (depositors share),
   not by raw balance — the correct model for 140+ partners.
3. **Consensus gate.** Attribution is computed by ≥2 agents; divergence above
   the `attribution_confidence_floor` escalates (sentinel `consensus` block).
4. **Disclosure is an artifact.** Output is a **CycloneDX + RSL report**
   (sentinel CBOM shape) listing per-chain reserve, per-asset, per-partner
   attribution, each row linked into an **agpack-audit-ledger** row.
5. **Signed + replayable.** The disclosure bundle is **signed** by an
   **ed25519** (PCT/PACT) identity; the ledger is append-only and replayable.
   PQC posture: the report also flags the chain's hybrid-KEM (FIPS 203/204/205)
   readiness — the "harvest-now-decrypt-later" reserve-key ranking.
6. **Local + lightweight.** Zero inference egress; runs offline against the
   synthetic corpus; latency bound by computation, not cloud.

## Build (≈2–3h, single session)
1. **Reuse sentinel.** Port the `consensus`/`supervisor`/structured-output
   pattern into a small `consortium` package (mirror `sentinel`'s
   `agents/{observation,attribution,disclosure}.py`).
2. **Reuse agpack.** Wrap the disclosure bundle in agpack's `artifact` (signed
   bundle) and log each attribution/disclosure action to agpack's `audit`
   ledger. Identity: ed25519 PACT (metered `@mwb/identity` shape).
3. **Generate synthetic inputs.** A deterministic corpus: 140 synthetic
   partners, OUSD reserves on Base/Ethereum/Solana, Aave/Lido yield, one
   weekly interval. Deterministic so the "sum = total" assertion is stable.
4. **Write the assertions** (sum-of-shares, reconciliation, escalation), not
   prose. "Done" = assertions pass.

## Verification
- Run attribution; assert `sum(partner_shares) == reserve_delta` to the last wei.
- Run disclosure; assert it's a **valid CycloneDX document** + every ledger row
  is present in it.
- Sign with agpack; assert **verify** passes.
- Turn off consensus divergence and assert the **escalation branch fires**.
- Confirm **zero network calls** during the run (local egress gate, sentinel
  `NFR-1`).

## Acceptance
If the synthetic-attribution assertions pass and the disclosure is a
signed, CycloneDX-valid, replayable artifact, the design is **proven at stage 1**.
Then — and only then — implement Stage 3–4 (settlement router) against the proven
ledger, with `finality: test` first, budget caps, and per-partner scoped
delegation.

## What this does NOT do (by design)
- No settlement, no custody, no on-chain writes. Zero value moves.
- No live indexing (R1): synthetic corpus only. A live indexer API is the next
  integration, after attribution is proven.
- No real sanctions/AML. The compliance verifier's escalation shape is the
  spike's analog; real lists are swapped in under `compliance_verifier.params`.
