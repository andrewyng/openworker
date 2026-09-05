# PHASES — executable buildouts (linked to REUSE.md)

Each phase here is a **buildout plan**: objective, concrete files, the exact
interfaces to reuse (see `REUSE.md`), the specs to research, the definition of
done, and the gate. Every file to write names what it reuses and what it
replaces (the stubs in `REUSE.md` §2).

Rule that governs all phases: **a phase that could move value does not move
value until the prior phase's audit ledger reconciles (`.validate()` + a
reconciliation assertion). Settlement is proven last.**

---

## PHASE 0 — Foundations (critical path)

**Objective.** `consortium` package that can run agents, record a valid audit
ledger, sign a disclosure bundle, and generate a deterministic synthetic corpus
that every later phase asserts against.

**Files to write.**
- `consortium/__init__.py`, `consortium/runtime.py` — **the thin runtime.**
  `agpack/cli.py` is a STUB (`NotImplementedError`), so this *replaces*
  `agpack run/verify/audit`. It threads: `agpack.signing.sign` → produce a
  manifest → `agpack.artifact` packager → `agpack.trust.audit.AuditLedger`
  → storage. (REUSE.md §1 — CLI is a stub.)
- `consortium/audit_store.py` — JSONL `AuditLedger` **storage** that produces
  `AuditRecord` objects of the exact closed shapes from `agpack.trust.audit`
  (dispatch/import_call/delegate/budget) and calls `.validate()` before any
  disclosure is accepted. `agpack.trust.audit` produces records; `consortium`
  owns durable storage (REUSE.md §1).
- `consortium/agents/{observation,attribution,compliance}.py` — agent stubs
  mirroring `sentinel/agents/*` (real).
- `consortium/corpus/generate.py` — **the deterministic synthetic corpus**
  (140 partners, OUSD on 5 chains, Aave/Lido/Yearn yield, one weekly interval).
  Produces `corpus/ground_truth.jsonl` = the **known-correct per-partner share
  table**. This is the project's `ground_truth.jsonl` in the sentinel eval
  pattern (`sentinel/eval/*`, `benchmarks/ground_truth.jsonl` — real).
- `config/agents.yaml` — the registry (already drafted in `config/agents.yaml`;
  wire `consensus.policy` as a *declarative gate contract* here, not just config).

**Specs to research (Phase 0 only): none required — but pin the corpus schema
and the ledger record contract now so Phase 1 can build to them.**

**Definition of done.**
- `consortium corpus check` regenerates `ground_truth.jsonl` **byte-identically**
  (deterministic corpus generator is a pure function of its seed).
- `consortium verify` loads a signed bundle, records 4 audit rows (one per
  `kind`), and `.validate()` passes.
- Corpus holds: `sum(partner_shares) == reserve_delta` for the synthetic
  reserves, to the wei, over all 5 chains.

**Gate.** Deterministic corpus check passes; a 4-row ledger validates; egress
zero during the run. → Phase 1.

**Reuses (REUSE.md):** agpack `signing`+`audit`+`artifact`+`sandbox`; sentinel
eval/benchmark pattern + `agents/` shape. **Newly written:** `runtime.py` (replaces
the agpack CLI stub), `audit_store.py`, `corpus/generate.py`, the 3 agent stubs.

---

## PHASE 1 — Attribution & Disclosure Agent

**Objective.** Stage 1–2 + 5: observe reserve/yield events, attribute yield
**by share** (ERC-4626), produce a **signed, CycloneDX-valid disclosure** with
ledger provenance. **No settlement. No value moves.**

**Files to write.**
- `consortium/agents/observation.py` — `ChainObserverAgent` reads events from the
  corpus/`indexer_api` (not a node). Read-only.
- `consortium/agents/attribution.py` — `AttributionAgent`: **ERC-4626 share math**
  — `share = partnerShares / totalShares * totalAssets`. Not raw balance.
- `consortium/agents/compliance.py` — `ComplianceVerifierAgent` stub (escalation
  shape); real sanctions matching in Phase 5.
- `consortium/disclosure/cbom_emit.py` — **replaces** `sentinel/crypto/cbom.py`
  (`to_cbom`/`write` are `# TODO`). Emits CycloneDX `crypto-assets`/
  `components[].type: "crypto-asset"`. **Validate against the published schema.**
- `consortium/runner.py` — runs the multi-agent pipeline through
  `sentinel/orchestrator/` consensus and records each step to `audit_store`.

**Specs to research (this phase):**
- **CycloneDX crypto-asset schema** — version + required fields.
- **ERC-4626** — `totalAssets`, `convertToShares`, `balanceOf`, `totalSupply`.
- **Sanctions list schema** — the SDN-like fields to match (preview).

**Definition of done.**
- **Attribution correctness by construction:** `sum(partner_shares) == reserve_delta`
  per chain and overall, asserted, to the wei.
- **Consensus gate (declarative, from `config/agents.yaml`):** ≥2 agents compute
  attribution; divergence above `attribution_confidence_floor` **escalates**
  (`sentinel` consensus — real).
- **Disclosure** is a valid CycloneDX document; every row traces to an
  `audit_store` record; the bundle is **signed** via `agpack.signing`;
  `verify` passes.
- **Egress zero** during the run.

**Gate.** Sum-total assertion passes; valid CycloneDX; signed + `validate()`
passes; escalation branch fires. → Phase 2.

**Reuses (REUSE.md):** agpack `signing`+`audit`; sentinel `orchestrator/` +
structured output + non-crypto agents. **Newly written:** `cbom_emit.py`
(replaces the cbom stub), `attribution.py`, `runner.py`, disclosure schema binding.

---

## PHASE 2 — Settlement Router (simulation)

**Objective.** Stage 3–4 **in simulation only.** Decide *which chain* each
partner's share would go to, and at what cost — **do not execute.** This proves
the routing policy before any value is at risk.

**Files to write.**
- `consortium/routers/chain_selector.py` — `SettlementRouterAgent`: pulls a
  `quote` from each chain's `PaymentRail`, chooses cheapest **qualified** chain
  per partner, records the *intended* settlement with `finality: 'test'`.
- `consortium/routers/rail_registry.py` — mirrors `metered/packages/rails/src/
  rails.ts` `RailRegistry` (`register/get/optional/ids`) + one `PaymentRail`
  per chain.
- `consortium/budget/caps.py` — per-partner / per-chain caps; over-cap →
  `failureClass: 'blocked'` + `denied` ledger row.

**Specs to research:**
- **Per-chain gas/fees** — to make "cheapest" *real* (net of gas) in Phase 4.
- **Circle x402/AP2 wire** — status + header names (preview).

**Definition of done.**
- Every partner → `chosen_chain, expected_cost, within_budget`.
- Chosen chain is provably cheapest **among those passing the compliance pre-check**.
- A simulated outflow **reconciles**: `sum(payouts) ≤ reserve_delta` and
  `reconcile().ok === true` under `finality: test`.
- **Currency matches policy** (metered `BudgetEngine.preflight` throws
  `CURRENCY_MISMATCH` otherwise) — a hard gate.

**Gate.** Routes reconcile under `test`; no row marked `final`; over-cap is
recorded as `blocked`. → Phase 3.

**Reuses (REUSE.md):** metered `PaymentRail ×5`, `BudgetEngine`, `defaultPolicy`,
`makeSettlementRef`. **Newly written:** the router, `caps.py` policy.

---

## PHASE 3 — Payout (finality-gated) — **risk apex**

**Objective.** Stage 4 — the **only** stage that moves value. Proven **last**,
always under a finality + budget gate. **Prefer testnet / dead-man's switch
before mainnet.**

**Files to write.**
- `consortium/settlement/executor.py` — `quote → authorize → settle` through each
  rail; injects proof header (opaque payload, per draft-caveat).
- `consortium/settlement/finality.py` — two-step: `claimed` → `final` only on
  `reconcile().ok`.
- `consortium/settlement/replay_guard.py` — nonce/monotonic proof; replayed
  proof returns `402/451`.
- `consortium/settlement/freeze.py` — revoke one partner's delegation scope to
  pause payout (the "seizure" surface).

**Specs to research:**
- **Circle x402/AP2** — the `PAYMENT-SIGNATURE`/`X-PAYMENT` header names +
  `/verify`→`/settle` flow (pin from the current draft).
- **CCTP** — attest message format + contract set.

**Definition of done.**
- A live payout `claimed` → `final` fires **only** on `reconcile().ok`; a
  failed/uncfinalized settlement stays `claimed`.
- A **replayed proof is rejected** (replay guard).
- Max outflow across the window ≤ reserve cap; breach → escalate.
- **Scoped delegation:** every spend is authorized `per-partner, per-chain,
  up-to-this-cap` — no master-key spend.

**Gate.** Simulated live-run where every `final` row reconciles, replay blocked,
no outflow exceeds budget. Prefer testnet. → Phase 4.

**Reuses (REUSE.md):** metered `settle` loop + finality + `reconcile`. **Newly
written:** the executor, finality, replay guard, freeze. **New to agpack:**
a payout **`Scope` / resource namespace** (delegation has `mem`/`fs`/`net`
only) — see below.

> **Agpack addition required (do not assume it exists).** The closed
> `Scope` enum + `mem`/`fs`/`net` resource namespace cannot express a payout
> spend. Add, to `agpack/trust/delegation.py`: a new scope (e.g.
> `settle` or `payout`) **and** register a `settle.<chain>.<partner>`
> resource namespace, then make `verify()`'s §3 namespace check + §8 budget
> monotonicity accept it. `verify` is a *hard-fail* function, so the new scope
> must be **registered before any payout token validates**. Budgets remain
> narrowing-only (root is the only raiser) — the payout cap is a
> `budget.fuel_max`-style narrowing, not a widening.

---

## PHASE 4 — Multi-Chain Reality (network integration)

**Objective.** Replace the synthetic corpus with **real on-chain data** and make
the router speak **real settlement rails**.

**Files to write.**
- `consortium/observers/*.py` — live indexer/IPC integrations per chain.
- `consortium/settlement/backends/*.py` — one `PaymentRail` backend per chain
  that actually returns/accepts `402`.
- `consortium/routers/routing.py` — gas/fees-aware routing (cheapest = net
  quote − gas − cross-chain cost).

**Specs to research (this phase):**
- **CCTP** message format + contract set (the bridging leg).
- **Circle x402** — full flow.
- **Per-chain gas/fees** — real "cheapest" routing.
- **CycloneDX** — full schema (final disclosure).

**Definition of done.**
- Real reserve delta from live chains reconciles with the Phase 1–3 math
  (parity on the same window).
- A real `quote → settle` round-trip lands and reconciles; `reconcile().ok`.

**Gate.** Live data reconciles to the same attribution math; a real
`quote→settle` reconciles. → Phase 5.

**Reuses (REUSE.md):** metered `BridgeRail`/live-402 task; sentinel
exported-data principle; agpack sandboxed adapters. **Newly written:** the
network observers, per-chain backends, gas-aware routing.

---

## PHASE 5 — Compliance & Disclosure (regulatory)

**Objective.** Stage 5–6: the **regulatory disclosure** + **compliance gate**
that makes the operation a **third-party-auditable** record.

**Files to write.**
- `consortium/agents/compliance.py` — real `ComplianceVerifierAgent`:
  sentinel-style consensus + schema validation; matches against the sanctions
  list; **proposes** blocks, never auto-spends.
- `consortium/disclosure/packet.py` — dated, signed **monthly disclosure** with
  full ledger provenance; ERC-1643 RWA-registry hook for the asset view.
- `consortium/settlement/freeze.py` — real, scoped freeze with auditable trail.

**Specs to research:**
- **Sanctions list schema** (SDN JSON).
- **ERC-1643** RWA registry interface.
- **CycloneDX** — final, validated.

**Definition of done.**
- A sanctioned-address input is **caught and escalated**.
- A disclosure packet is valid CycloneDX + RSL, **signed**, and every row traces
  to a ledger entry; an **external reader can reproduce the numbers**.
- Freeze is a real, scoped action with an auditable record.

**Gate.** Sanctions escalation; external-reader reproducibility; auditable freeze.
→ Phase 6.

**Reuses (REUSE.md):** sentinel CBOM (now `cbom_emit.py`) + consensus +
`escalate_on_schema_failure`; metered RSL/license; agpack signed disclosure +
scoped freeze. **Newly written:** the real `ComplianceVerifierAgent`,
`packet.py`, freeze.

---

## PHASE 6 — Quantum-Safety Migration (hardening)

**Objective.** Turn "quantum-safe" from a label into an **auditable migration**.

**Files to write.**
- `consortium/crypto/posture_agent.py` — **replaces** the
  `crypto_posture.py` stub (`NotImplementedError`). Deterministic scoring over
  known PQC tables; LLM only for migration *narrative* (verbatim from the stub
  docstring).
- `consortium/crypto/risk_model.py` — **replaces** `crypto/risk.py` `score_finding`/
  `rank` (`# TODO`). The **Mosca inequality**: `exposure iff (shelf_life +
  migration_time) > time_to_CRQC`.
- `consortium/settlement/keys.py` — hybrid `ML-DSA-87 ‖ ed25519` signing for
  identity + delegation tokens.

**Specs to research:**
- **FIPS 203 / 204 / 205** — ML-KEM-768, ML-DSA-87, SLH-DSA (sizes, KEM vs
  signature roles).
- **time_to_CRQC range + citation** — do **not** predict a single number.

**Definition of done.**
- A PQC posture report is emitted per chain/rail; **non-hybrid signing is flagged
  as a decryption-risk exposure** in the disclosure (not silently accepted).
- **Hybrid signing is the default**; a pure-ECC downgrade is **visible**.

**Gate.** PQC posture report generated + wired into disclosure; hybrid is the
default; downgrade is visible. → Done.

**Reuses (REUSE.md):** sentinel PQC model (Mosca, harvest-now-decrypt-later),
agpack `signing` (algorithm-agnostic block → drop-in PQC swap), metered identity
shape. **Newly written:** `posture_agent.py` (replaces stub), `risk_model.py`
(replaces stub), `keys.py` (hybrid signing).

---

## Critical path & sequencing (restated, now with reuse facts)

```
Phase 0  (runtime that replaces agpack CLI + synthetic corpus)   ← CRITICAL PATH
   │
   ├─► Phase 1  (attribution by share + signed disclosure)         ← highest value,
   │            no money moves                                    zero risk
   │
   ├─► Phase 2  (router, simulation only)
   │         │
   │         ▼
   │       Phase 3  (payout — risk apex; add agpack payout Scope;
   │               prefer testnet)
   │         │
   │         ▼
   └───────────── Phase 4  (live chains + live rails)
                    │
                    ▼
                  Phase 5  (regulatory disclosure + compliance)
                    │
                    ▼
                  Phase 6  (PQC migration — stubs + hybrid signing)
```

Phase 0 is the critical path (nothing asserts without its corpus; the agpack CLI
is a stub so the runtime must be built). Phase 1 is the highest-value entry.
Phase 3 adds a real **agpack** change (payout scope/namespace) — the only phase
that edits a reused repo; it must be done carefully because `delegation.verify`
is a hard-fail function. Phase 6 replaces the sentinel stubs.

## Rough total
~18–28 agent-days, dominated by Phase 4 (network integration) and Phase 3
(finality). Stacked so the most valuable, lowest-risk stages ship before the
highest-risk stage.
