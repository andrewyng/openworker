# Consortium Reserve Agent — Design

> An agent that runs a crypto reserve operation end-to-end: attribute yield per
> partner across chains, automate per-partner settlement, and produce audit-grade
> reserve disclosure — locally, quantum-safe, lightweight.

**Purpose (the tangible value):** a consortium splits the yield of a pooled reserve
across 140+ members. Today that split is tracked by hand, paid out inconsistently,
and disclosed infrequently. This agent closes the loop: it **attributes** yield to
each partner on every chain, **settles** their share automatically through a
metered rail, and **records** every step in a signed, replayable ledger that a
regulator could read. Tangible value = yield leakage eliminated, payout cost
dropped, disclosure cost dropped.

## 1. What this agent is (and isn't)

- **Is:** a small local agent fleet that *observes* chains, *attributes* yield,
  and *proposes/replays* settlement — all behind a signed, scoped, budgeted
  trust layer. It does analysis by default; it only moves value along a
  **finality-gated, capped, delegatable** path.
- **Isn't:** a hedge fund, a custodian, or a general-purpose trading bot. It has
  one job — reserve attribution + settlement + disclosure — and it does it
  verifiably.

## 2. The three pillars (we stand on existing repos, not greenfield)

The design reuses the three repos as **verticals of capability**, not as a
reimplementation. Each is already the highest-ceiling gap in its slice.

| Pillar | Repo | What it gives the agent |
|---|---|---|
| **Quantum-safe + lightweight reasoning** | `sentinel-local` | Local multi-agent consensus (no inference egress), CBOM/harvest-now-decrypt-later risk ranking, schema-validated structured output, `escalate_on_schema_failure`. This is how the agent stays **lightweight, fast, and PQC-aware**. |
| **Trust + payout integrity** | `agpack` | The agent *itself* is a signed bundle; every tool runs in a capability-scoped WASM sandbox; every delegation hop is scoped and signed; every action lands in a replayable audit ledger. This is why **partner payouts and disclosure are auditable**. |
| **Settlement spine** | `metered-web-broker` | One contract — `PaymentRail` (`quote→authorize→settle`) — pluggable per chain/rail, budget engine, ed25519 identity, **finality flag** (`test`/`claimed`/`settled-and-verified`). This is the **multi-chain settlement routing + reserve-budget** core. |

## 3. Top-5 anchors (chains, assets, protocols, standards)

See [`TOP5.md`](./TOP5.md) for the curated lists and rationale. Short version:

- **Blockchains:** Base, Ethereum, Solana, Arbitrum, Tempo (the OUSD consortium set).
- **Stablecoins:** USDC, USDT, DAI, PYUSD, OUSD.
- **Cryptos:** BTC, ETH, SOL, WETH, cbBTC.
- **Protocols:** OUSD/Yearn-Drop (yield), CCTP/Circle (cross-chain transfer), Aave/Lido (yield legs), Circle Paymaster / x402 (settlement), OpenPaymaster (agentic metered pay).
- **Standards:** **FIPS 203/204/205** (ML-KEM/ML-DSA/PQC — the quantum-safe anchor), **ERC-4626** (vault accounting), **ERC-1643** (RWA registry), **CycloneDX + RSL** (audit/compliance artifacts, from sentinel + metered), **x402/AP2** (metered settlement wire).

## 4. Architecture

```
                        ┌─────────────────────────────────────────┐
                        │        CHAIN CONNECTORS (read)            │
                        │  Base · Ethereum · Solana · Arbitrum · Tempo│
                        └───────────────────┬───────────────────────┘
                                            │ on-chain events, balances, yield
                                    ┌───────▼────────┐
   ┌───────┐   ┌───────────────┐   │  │ 4. ATTRIBUTION │  ┌───────┐
   │Agent  │→→ │ agpack runtime │   │  │   AGENT        │  │Prompt │
   │ fleet │   │ (signed bundle, │   │  │  - attribution  │  │ engine│
   │sentinel│  │ WASM tools,      │◄─┼──│  - reconciliation│  │       │
   │  LLMs │   │ scoped delegate, │   │  │  - disclosure     │  │       │
   └───────┘   │ audit ledger)    │   └───────┬─────────┘   └───────┘
              └───────┬─────────┘        ┌─────▼───────────┐
                    identity ed25519     │ 5. DISCLOSURE    │
                    + PQC posture        │  reserve report  │
                                        │  (CycloneDX/RSL)  │
                                        └─────┬───────────┘
                         ┌────────────────────▼───────────────────────┐
   ┌───────┐   ┌───────┐    metered-web-broker (settlement spine)       │
   │Budget │◄──│Trust  │  - PaymentRail ×5 (one per chain)             │
   │engine │   │ policy │  - BudgetEngine (reserve caps)               │
   └───────┘   └───────┘  - finality flag (test/claimed/final)         │
                                        └───────────────┬───────────────┘
                         ┌─────────────────────────────▼──────────────────┐
                         │ 6. SETTLEMENT ROUTER → cheapest/qualified chain   │
                         │  per-partner share, signed, replayed under budget │
                         └───────────────────────────┬────────────────────┘
                                                      │ settle → settlementRef
                                                chains + facilitator
```

The agent is a **pipeline of six stages**, only four of which are "agents":
Observation → Attribution → (report) → Disclosure → Settlement-Routing → Payout.
Stages 1–2 and 5–6 are *computation the agents propose and the trust layer enforces*.

### 4.1 Why this shape is quantum-safe by construction
- **Identity is PQC-ready.** `metered` uses ed25519 PACTs today; the migration
  path is **hybrid ML-DSA-87 + ed25519** signing (FIPS 204) for agent identity
  and delegation tokens — no code-change at the trust boundary, only a signer
  swap. Sentinel's crypto-posture agent already **inventory-ranks PQC adoption**
  chain-by-chain, so the agent can detect where hybrid KEM (ML-KEM-768) is live.
- **"Harvest now, decrypt later" is a first-class risk.** Just as sentinel ranks
  encrypted telemetry by confidentiality-lifetime, the agent ranks **reserve
  keys and settlement secrets** by how long they must stay secret past ~2035.
  Any settlement path that signs only with pure-NIST/ECC keys is flagged as a
  decryption-risk exposure, not a cosmetic "use PQC" checkbox.

### 4.2 Why lightweight + fast
- Local models only (sentinel pattern): **zero inference egress**, median
  attribution latency bounded by chain latency, not cloud queue time.
- Tool code (on-chain parsing, attribution math) runs inside agpack's
  **capability-scoped WASM sandbox** — untrusted third-party chain adapters can't
  reach network/fs/clocks beyond an explicit allow-policy.
- No PaaS cold starts (metered `DEPLOY.md` shape: one Node process + Caddy). The
  agent runs as a plain local/edge process.

## 5. Mapping the four use-case verticals

### 5.1 Partner Incentive Platforms
*Tracking/yield distribution to 140+ consortium members.*
- **Attribution agent** computes each partner's **real-time yield attribution**
  from on-chain reserve + yield-leg events (OUSD deposit → Aave/Lido → share).
- **agpack audit ledger** is the source of truth: one signed row per
  attribution event, per-partner auditable, replayable. This *is* the "partner
  dashboard data" — partners query the ledger, not a black box.
- **Automated payouts** = settlement router calling each chain's `PaymentRail`
  under the **BudgetEngine** reserve cap. Every payout is a **scoped delegation
  hop** (agpack `trust/delegation.py`) — the router can authorize *this partner,
  this share, this chain, up to this cap* without a master-key spend.

### 5.2 Regulatory Compliance Layer
*Monthly reserve disclosure + seizure capability.*
- **Reserve disclosure** = a generated **CycloneDX + RSL report** (sentinel
  CBOM shape) enumerating reserve composition, per-chain, per asset, with
  provenance links into the audit ledger. Regulators read the ledger.
- **AML/KYC + sanctions screening** = a **sentinel-style verification agent**
  (consensus + schema validation + `escalate_on_schema_failure`) that scores
  partner addresses against a sanctions list and *proposes* settlement blocks.
  It never auto-spends; it gates.
- **Seizure capability** = the **delegation scope + finality flag** already
  there. Because every spend is a scoped, signed, reversible-in-review hop, the
  operator can freeze a partner's payout by revoking that one delegation scope —
  a compliance action with an auditable trail.

### 5.3 Multi-Chain Distribution Networks
*OUSD across Base/Ethereum/Solana/Tempo — fragmentation as opportunity.*
- **`PaymentRail ×5`** (one per chain) is exactly metered's protocol-agnostic
  seam: the agent routes a settlement to **the cheapest qualified chain per
  partner**, choosing via a `quote` from each rail. This *is* the settlement
  protocol the use-case describes — no bespoke bridge code.
- **CCTP/Circle** handles cross-asset movement; the **budget engine** enforces
  per-partner and per-chain caps. Settlement finality flag means a partner can
  test a route (`test`/`claimed`) before real value moves.

### 5.4 Treasury Management
*Reserve optimization, redemption-pressure risk, compliance monitoring.*
- **BudgetEngine reserve caps** = the "reserve optimization" guardrail: the
  agent proposes allocation that maximizes attributed yield **subject to**
  chain/cap/risk policy, never beyond it.
- **Redemption-pressure dashboard** = a sentinel-style **analysis agent** that
  models outflow scenarios against on-chain reserve + pending settlement claims
  and flags a breach threshold (with `escalate_on_schema_failure` to a human).
- **Compliance monitoring** (incl. tariff/asset-restriction checks) = the
  sanctions/AML verification agent + a policy file the agent reads (declarative,
  like metered's `BudgetPolicy`), not hard-coded rules.

## 6. The one invariant that ties it all together

> **Every move of partner value is signed, scoped, budgeted, and recorded before
> it is final — and never final until it reconciles.**

This is metered's `settled ⇒ reconcile().ok` finality rule elevated to the whole
system. It's what makes 140+ partners trust an agent with their payout:
- **Signed** — agpack bundle identity + delegation token signatures.
- **Scoped** — per-partner, per-chain, per-cap delegation (agpack).
- **Budgeted** — reserve caps (metered BudgetEngine).
- **Recorded** — replayable ledger (agpack audit / metered AuditLedger).
- **Reconciled** — no payout is "done" until it reconciles against chain state.

## 7. What to build first (spike)

See [`SPIKE.md`](./SPIKE.md). The highest-tangibility, lowest-risk spike is:
**an Attribution + Disclosure agent** — read-only, fully local, no settlement risk.
It turns on-chain reserve events into a per-partner yield table + a signed,
replayable disclosure. That stage (1–5 of the pipeline) carries almost all the
demonstrable value (yield leakage, disclosure cost) with **zero settlement
exposure**, and it directly reuses sentinel + agpack today. Settlement routing is
the *next* stage and only after the ledger is proven.

## 8. Open risks

- **R1 On-chain data cost.** Reading per-partner yields across 5 chains is
  indexing-heavy. Mitigate with an existing indexer/API, not a full node, for the
  spike. (Sentinel reads exported telemetry, not live streams — same principle.)
- **R2 Settlement is the hard part.** The settlement spine is the only stage that
  moves real value; keep it finality-gated and budgeted, and prove it *after*
  attribution is proven.
- **R3 PQC is migration, not magic.** Hybrid signing reduces but doesn't nullify
  stored ciphertext risk on a timeline the consortium outlives. Rank and disclose,
  don't overclaim.
- **R4 Regulatory surface.** "Seizure capability" and sanctions screening carry
  legal exposure. The agent *proposes and gates*; a human approves any block.
  Locked read-only-by-default, like sentinel's scope.
