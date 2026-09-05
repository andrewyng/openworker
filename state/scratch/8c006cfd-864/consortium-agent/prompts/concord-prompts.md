# ConcordCircuit — Prompts (run right here)

All three work in **this workspace** (`/home/iconbaypark2900/openworker-tasks/8c006cfd-864/`), which is **read-write**. The three pillars are **read-only** and referenced by absolute path under `dataScience/`.

- `concord/` — where the new package is built.
- `agpack`, `sentinel-local`, `metered-web-broker` — read-only, reused, **never edited**.

Three prompts. Paste them in order: **P1** (persona/system), **P2** (Phase 0 — pure-math ERC-4626 corpus), **P3** (the drift-seed guard that makes Phase 1's reconciliation real before it exists).

---

## PROMPT 1 — Persona

You are **ConcordCircuit**, lead build agent for an open protocol that moves consortium partner value (yield to 140+ members across 5 chains) safely, by **composing** three existing codebases under a thin `concord` package.

Your workspace (read-write) is `/home/iconbaypark2900/openworker-tasks/8c006cfd-864/`. Create `concord/` under it.

**The three pillars (READ-ONLY — reuse, do not edit):**
- `agpack` → `/home/iconbaypark2900/dataScience/agpack/` — trust spine: replayable execution ledger, scoped delegation, Ed25519 signing, WASM sandbox, CycloneDX disclosure surface.
- `sentinel-local` → `/home/iconbaypark2900/dataScience/sentinel-local/` — quantum-threat posture (harvest-now-decrypt-later), multi-agent consensus, structured output, eval/ground-truth harness.
- `metered-web-broker` → `/home/iconbaypark2900/dataScience/metered-web-broker/` — settlement rails: one `PaymentRail` interface, budget engine, identity, RSL/license, finality-gated settlement.

**Core invariant (never violate):** every move of partner value is **SIGNED, SCOPED, BUDGETED, and RECORDED before it is final** — and never final until it **RECONCILES**. Settlement is proven last.

**Reality checks — verify, never assume (several headline modules are STUBS):**
- `agpack/cli.py` **raises `NotImplementedError`** — the CLI does not exist. You must WRITE the runtime that threads agpack signing + audit + sandbox directly.
- `sentinel/crypto/cbom.py`, `sentinel/crypto/risk.py`, `sentinel/agents/crypto_posture.py` are STUBS (`# TODO`) — Phase 6 writes them.
- `agpack` delegation `Scope` enum + `mem/fs/net` namespace are CLOSED and **cannot** express a payout spend — Phase 3 must add a payout scope.

**Real, importable code (reuse directly):** `agpack/trust/audit.py` (the closed 4-record contract + `.validate()`), `agpack/trust/signing.py` (Ed25519), `agpack/artifact/`, `agpack/sandbox/`; `sentinel/orchestrator/`, `sentinel/llm/structured`, `sentinel/eval/`; metered `core/budget/rails/identity` (TypeScript settlement spine).

**Work rules:** write `concord/`; never modify the pillars (Phase 3 is the one sanctioned edit to `agpack` — add a payout scope, carefully, because `delegation.verify` is a hard-fail function). Keep egress ZERO until a phase is allowed to move value. Record findings to durable memory.

---

## PROMPT 2 — Phase 0 (pure-math ERC-4626 corpus)

Build Phase 0 — the `concord` runtime, the audit storage, and a deterministic ERC-4626 corpus. **No settlement, no egress, no network.** Everything is pure generation + verification against a known-correct table.

### Step 1 — runtime (`concord/runtime.py`)
`agpack/cli.py` is a stub, so `concord` REPLACES the operator surface (`run / verify / audit`). It must: load an agent bundle, run it through `agpack`'s sandbox, append every host-import to `agpack.trust.audit.AuditLedger`, sign the manifest with `agpack.signing.sign` (Ed25519), persist to a JSONL store.

### Step 2 — the record contract (VERIFIED from `agpack/trust/audit.py`; do not re-derive)
An `AuditRecord` has fields `ordinal, run_id, kind, ts_unix, subject, detail`. `kind` is a CLOSED set of 4; a record whose `detail` misses a required key or has the wrong type raises `LedgerCorrupt`:

| kind | required `detail` keys |
|---|---|
| `dispatch`    | `tool_cid`, `args_sha256_prefix16`, `output_sha256`, `budget_spent` |
| `import_call` | `scope`, `resource`, `arg_fingerprint`, `host_return`, `fuel_delta` |
| `delegate`    | `token_id`, `parent_token_id`, `hop_depth`, `scope`, `resource` |
| `budget`      | `budget` |

Encode: `ts_unix` is a logical 1-sec clock, not wall time. The arg buffer is **redacted** — only the first 16 hex chars of its SHA-256 are recorded. `agpack.trust.audit` *produces* records and `.validate()`; **you own the durable JSONL storage**.

### Step 3 — the ERC-4626 corpus (`concord/corpus/generate.py`)
Pure function of a **fixed seed** → produces `concord/corpus/ground_truth.jsonl`. Model the reserve as **tokenized shares**, not raw balances:
- **140 partners**, each holding some `partnerShares`.
- **5 chains**, each with a vault (ERC-4626 `TokenizedVault`): `totalShares`, `totalAssets` (the reserve).
- Yield from **Aave, Lido, Yearn** over **one weekly interval**, per chain.
- **Attribution by share, not balance** (ERC-4626: `totalAssets()`, `convertToShares()`, `balanceOf()`, `totalSupply()`):
  ```
  partner_yield = totalShares(partner) / totalShares(vault) * totalAssets(vault)
  ```
- Emit per chain×partner: `chain_id`, `partner_id`, `shares`, `total_shares`, `yield_attributed`, `total_return = yield + principal`, `vault.total_assets`.
- The generator is **deterministic** — regenerating twice yields **byte-identical** output.

### Step 4 — the assertion invariant (`concord/assert.py`)
From the generated table, assert **by construction, to the wei, no rounding drift**:
```
sum(partner_yield)       == sum(vault_yield_delta)   per chain, and overall
sum(partner_total_return)== sum(vault.total_assets)  per chain, and overall
```
This is the contract Phase 1–4 all lean on.

### Step 5 — verification commands (each exits 0 only if the contract holds)
- `concord corpus check` — regenerate `ground_truth.jsonl`, diff against the committed copy; must be **byte-identical**.
- `concord assert sum` — run Step 4; exit non-zero on any drift.
- `concord verify <bundle>` — load the signed bundle, replay the 4 audit rows (one per `kind`), call `.validate()`. **Zero execution, offline.**
- `concord run` — run the corpus generator through the runtime, append `budget` + 4 dispatch rows to the ledger, then `concord verify` it. **No value moves. Egress = 0.**

### Definition of done
- `concord corpus check` → byte-identical, twice.
- `concord assert sum` → passes, `sum == delta` to the wei across all 5 chains.
- `concord verify` → 4-row ledger validates, signature checks, offline.
- No network, no settlement, no egress.

### Out of scope for Phase 0
- No chain observers / live data (Phase 4).
- No attribution against a live discrepancy set — the seeded discrepancy cases come in the next pass.
- No payment rails, no signing-key rotation (Phase 6).

### Deliverables
`concord/` with `runtime.py`, `audit_store.py`, `corpus/generate.py`, `assert.py`, `cli.py` (the `concord` command), `agents/observation.py` (ChainObserverAgent stub — read-only corpus reader), `agents/compliance.py` (ComplianceVerifierAgent stub), and `config/agents.yaml` wired with the sentinel `consensus` block as a declarative gate contract. Tests asserting the sum invariant and byte-identical regeneration.

---

## PROMPT 3 — Phase-1 drift seeding (the "then" step)

Context: Phase 0 is done — `corpus check` byte-identical, `assert sum` passes to the wei, 4-row ledger validates offline. **Now, before Phase 1's reconciliation agent exists, seed the corpus with realistic drift** so reconciliation has real something to catch. This makes Phase 0 auditable and Phase 1's check fire — a regression guard.

### Step 1 — the two seeded discrepancies (`concord/corpus/drift_seed.py`)
Produces `corpus/ground_truth_with_drift.jsonl` (a **superset** of the base; base stays pristine). Inject exactly these two, by-construction (reproducible, deterministic to fail):

1. **Chain 3 (Base) — 0.0375 USDC of Aave yield missing.** Simulate a failed yield sweep: a distribution was recorded in the on-chain event log but the vault's `totalAssets` snapshot never picked it up. So on chain 3, `sum(partner_yield) = base_yield_delta − 3.75e14` wei. The per-partner yields still sum among themselves; the gap is between the vault's `total_assets` and the sum of attributable yields.
2. **Partner 117 — attribution skew on chain 1 (Ethereum).** Credited `share + 0.0008e16` — a `convertToShares` rounding drift. If uncaught, it silently leaks value.

### Step 2 — the reconciliation check (`concord/reconcile.py`)
Phase-1 check written **now**:
```
for each chain:
    reported_yield_sum = sum(partner_yield on chain)
    vault_yield_delta  = vault.total_assets − vault.principal
    gap = vault_yield_delta − reported_yield_sum
    if abs(gap) > 1e12:            # sub-pico-USDC tolerance
        EMIT discrepancy: chain_id, partner(s), gap_wei, severity
```
A discrepancy is **never auto-corrected in Phase 0** — it's flagged and escalated; the base table is left untouched.

### Step 3 — the command
- `concord reconcile --seed` — loads `ground_truth_with_drift.jsonl`, prints a per-chain discrepancy report, exits **non-zero** (expected: the seed must fail). If it exits 0, the seed didn't produce detectable drift — that's a real bug.
- `concord reconcile` (no flag) — against the **pristine** base; must **exit 0**, "clean", zero discrepancies. Guard that Phase 1's reconciliation is catching the seed, not the base being wrong.

### Definition of done
- `concord reconcile` (base) → clean, exit 0.
- `concord reconcile --seed` → 2 distinct discrepancies (chain-3 Aave gap; partner-117 rounding), exit non-zero.
- Seeded table is a superset; base `ground_truth.jsonl` unchanged.

### Out of scope
- No payout, no correction, no settlement. Reconciliation **flags** — it does not fix. The fix path is Phase 1.
