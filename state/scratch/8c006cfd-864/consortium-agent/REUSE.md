# REUSE & SPEC PINS — what's real vs. stub, and what to research

> **Read this before any phase.** It is the honest inventory of the three
> repos: what actually imports and runs today, what is a documented *stub*
> (TODO), and which external specs must be pinned before the agent writes code.
> Every phase's buildout links to the exact rows here.

The design docs treat "reuse sentinel/agpack/metered" as nearly free. It is **not
fully** — several headline modules are *stubs*. The reusable *core* is real and
tested; the *domain* pieces (PQC risk scoring, CycloneDX emission, the agpack CLI)
must be **written** in the consortium package. This file names each precisely so
no phase silently re-implements or mis-imports.

---

## 1. The trust spine — REAL and tested (agpack)

These import and run. **Reuse directly.** Do not re-implement.

| Item | Path (Python) | Signature / API | Reuse in |
|---|---|---|---|
| Signing | `agpack/trust/signing.py` | `sign(canonical_manifest_bytes: bytes, private_key: bytes) -> SignatureBlock(scheme, public_key_bytes, signature_bytes, signed_at_unix)`; `verify(canonical_manifest_bytes, block) -> None | None` | Phase 1 (bundle), Phase 6 (PQC swap) |
| Delegation | `agpack/trust/delegation.py` | `verify(token, *, logical_now_unix, keys: dict[str,32bytes]) -> token` (8-step) | Phase 3 (scoped payout) |
| Audit ledger | `agpack/trust/audit.py` | `AuditLedger().append(record)`, `.validate()`, `replay(ledger, kind, range)`, `.validate()` | All phases (record + verify) |
| Sandbox | `agpack/sandbox/host.py`, `capabilities.py`, `imports.py`, `limits.py` | `Budget(fuel_max, memory_pages_max, wall_time_ms)`; `Scope` enum; `CapabilityPolicy` | Phase 1 (run agents) |
| Artifact | `agpack/artifact/*` | `AgentBundleManifest.canonical_json_without_sign()`, packager, validator | Phase 1 |

### Footnotes that are real engineering (not optional)
- **CLI is a STUB.** `agpack/cli.py` raises `NotImplementedError`. You **cannot**
  call `agpack run`/`verify`/`audit`. The `consortium` package must build its own
  thin runtime that threads `audit` + `signing` + `delegation` + `sandbox.host`
  directly. See Phase 0.
- **Signing is ed25519-only today.** `SCHEME_ED25519` is the only recognized
  scheme. The signature *block* is algorithm-agnostic (`scheme` field is a
  string), so PQC migration (Phase 6) is a **drop-in add** — add `SCHEME_MLDSA87`
  and a hybrid-sign path; the *manifest format does not change*. But `verify()`
  must be extended to accept the new scheme before it is usable.
- **Delegation has no `settle`/`money` scope.** The closed `Scope` enum is the
  sandbox's capability set, and the resource namespace is only
  `mem`/`fs`/`net` (`<ns>.<agent_id>.<rest>`). A payout spend cannot be delegated
  with the existing token as-is. **Phase 3 must add a payout scope** — either a
  new `Scope` member or a new resource namespace (e.g. `settle.<chain>.<partner>`).
  This is a real addition to `agpack`, not a free reuse. (Consequence: the 8-step
  `verify()` in `delegation.py` must accept it — budget monotonicity §8 and the
  namespace check §3 both have to allow the new shape.)
- **`verify` fails on unknown scope/namespace (hard fail, no leniency).** So any
  new scope/namespace must be *registered* in the closed sets before a payout
  token validates.

### Ledger record shape (must match, audit.py is authoritative)
`AuditRecord` fields: `ordinal` (assigned by ledger at append), `run_id`,
`kind ∈ {dispatch, import_call, delegate, budget}`, `ts_unix` (logical clock),
`subject`, `detail` (closed per-kind schema). Detail schemas:

- `dispatch`: `tool_cid, args_sha256_prefix16, output_sha256, budget_spent(dict)`
- `import_call`: `scope, resource, arg_fingerprint, host_return, fuel_delta(int)`
- `delegate`: `token_id, parent_token_id(str|None), hop_depth(int), scope, resource`
- `budget`: `budget(dict)`

A record whose `detail` misses a required key or has the wrong type → `LedgerCorrupt`.
**The consortium writes its own `AuditLedger` storage (JSONL) that produces these
records and calls `.validate()` before a disclosure is accepted.** The audit module
produces records; it does not store them — `consortium` owns durable storage.

---

## 2. Sentinel — REAL core, STUB domain

### REAL (import and run)
- **Orchestrator:** `sentinel/orchestrator/{supervisor,router,consensus,state}.py`
  — multi-agent consensus is real. Use it verbatim for Phase 1/5 gating.
- **Structured output + repair:** `sentinel/llm/{client,structured}.py` — the
  schema-validated structured output with retry/repair. Use verbatim.
- **Agents:** `agents/{enrichment,correlation,triage,detection_engineering,reporting}.py`
  — real. Shape the consortium's *reasoning* agents after them.
- **Eval harness + benchmark:** `eval/{harness,metrics,ablations}.py`,
  `tests/` — real. The `ground_truth.jsonl` pattern is the exact template for
  Phase 0's synthetic corpus.
- **Consensus config shape:** `config/agents.yaml` — `consensus.policy` block
  (`verdict, severity_tolerance, confidence_floor, escalate_on_schema_failure`).
  This is the declarative gate contract (see Phase 1).

### STUBS — MUST BE WRITTEN in `consortium`
- **`crypto/risk.py`** — `score_finding` / `rank` are `# TODO`. The *model* is
  documented (Mosca inequality), the *code* is not. **Phase 6 writes it.**
- **`crypto/cbom.py`** — `to_cbom` / `write` are `# TODO`. **Phase 5 writes the
  CycloneDX emitter.**
- **`agents/crypto_posture.py`** — `class CryptoPostureAgent` is a **stub
  (`NotImplementedError`)**. The *role* is defined (involve scanners → inventory
  → risk-rank → LLM narrative), the class is not. **Phase 6 writes it.**
- **`crypto/scan_*.py`** — TLS/SSH/dep scanners exist but read fixed targets; the
  consortium must add **on-chain target scanning** (not present).

**Domain-research discipline baked into these stubs (verbatim from the source):**
> "PQC is classical math designed to resist quantum attack. No quantum
> computation occurs anywhere in this project. Detection itself is *deterministic
> code* — never ask a model whether a cipher is quantum-vulnerable."

So Phase 6's PQC ranking is **deterministic scoring over known tables**, with the
LLM only drafting the migration *narrative*.

### The Mosca inequality the Phase-6 risk model implements (from risk.py docstring)
```
exposure exists  iff  (shelf_life + migration_time) > time_to_CRQC
```
- `shelf_life` = required confidentiality lifetime of the asset (operator-supplied,
  per asset class, e.g. reserve key: ~10 yrs).
- `migration_time` = how long to stand up the replacement (configurable).
- `time_to_CRQC` = time to a cryptographically relevant quantum computer — **a
  documented configurable range with a citation, not a single number** (the stub
  refuses to predict it).

---

## 3. Metered Web Broker — REAL core (TypeScript), the settlement spine

These are TS packages, so `consortium` either re-implements the *shapes* in Python
or (cleaner) hosts the broker as a service and speaks its JSON. Both are options
in Phase 0; the **interfaces below are the contract either way.**

| Item | Path | Contract | Reuse in |
|---|---|---|---|
| `PaymentRail` | `packages/core/src/types.ts` | `readonly id; quote(req) → Quote; authorize(req, quote) → RailAuthorization; settle(req, authorization) → RailSettlement` | Phase 2/3/4 |
| `RailRegistry` | `packages/rails/src/rails.ts` | `register/get/optional/ids()` — pick a rail per tenant/chain | Phase 2 |
| `BudgetEngine` | `packages/budget/src/engine.ts` | `preflight(tenantId, price, url?)` throws `BudgetError`; `recordPaid(tenantId, price, url)`; `snapshot(tenantId)` | Phase 2/3 |
| `defaultPolicy` | `packages/budget/src/policy.ts` | `perCallCeilingMicros`, hourly/daily tenant ceilings, `lineItems[]` (daily+lifetime) | Phase 2 |
| `FetchOutcome` | `packages/core/src/types.ts` | `finality ∈ {claimed, final}`; `final` iff `reconcile().ok` | Phase 3 |
| `IdentityPresentation` | `packages/core/src/types.ts` | `keyId, algorithm='ed25519', signedHeaders, timestamp, pactToken?` | Phase 1/6 |
| `@mwb/identity KeyRing` | `packages/identity/src/ring.ts` | ed25519 key ring | Phase 1/6 |
| `makeSettlementRef` | `packages/rails/src/rails.ts` | deterministic settlementRef | Phase 3 |

### Real constraints (non-negotiable footnotes)
- **Currency must match policy.** `BudgetEngine.preflight` **throws**
  `CURRENCY_MISMATCH` if `price.currency != policy.currency`. The consortium fixes
  one policy currency (likely a stablecoin/USD) — settlement quotes must be in it.
  This is a hard gate in Phase 2.
- **preflight throws → surfaced as `failureClass: 'blocked'`.** A route that
  exceeds a cap is a *policy denial*, recorded as a `denied` ledger row. Phase 2
  must treat "over budget" as a first-class outcome, not a crash.
- **Replay is a real requirement.** `settle` proofs must be nonced/monotonic so a
  replayed proof returns `402/451`. Phase 3.
- **Finality is a flag, not an assumption.** `settled ⇒ fulfilled` is *unproven* by
  design; `final` only holds when `reconcile().ok === true`. Phase 3 wires
  `finality: test | claimed | settled-and-verified`.
- **Draft-caveat:** `PaymentRequest`/`RailAuthorization`/`RailSettlement` field
  names inside `payload` are **opaque** — build against the interface, not a
  specific 402/x402 draft's fields. The router must not hard-code wire field names.

---

## 4. External spec pins — research these BEFORE writing the related phase

The agent must **look these up live**, not assume field names. Record the exact URLs
and version numbers in a `SPECS.md` the run keeps. The pins below are *what to find*:

| Phase | Spec | What to pin (do not assume — look it up) |
|---|---|---|
| 1, 5 | **CycloneDX cryptographic-asset schema** | Exact version (v1.5?), the `crypto-assets` array vs `components[].type: "crypto-asset"`, required fields (`algorithm`, `primitive`, `keySize`, `nistPublication`). Validate against the published JSON schema. |
| 1 | **ERC-4626** (tokenized vault) | Interface: `totalAssets() → uint256`, `convertToShares(uint256 assets) → uint256`, `balanceOf(addr) → uint256`, `totalSupply()`. Attribution = `partnerShares / totalShares * totalAssets`. |
| 6 | **FIPS 203 / 204 / 205** | ML-KEM-768 (KEM), ML-DSA-87 (sign, ~3.6 kB sig), SLH-DSA (hash-based). Confirm key/sig sizes and that ML-DSA-87 is the PQC signature counterpart to ed25519. |
| 3, 4 | **Circle x402 / AP2 (metered wire)** | Status `402 PAYMENT-REQUIRED`, the `PAYMENT-SIGNATURE`/`X-PAYMENT` header names, the `/verify`→`/settle` flow. Pin from the current draft (Cloudflare `x402`-draft), not memory. |
| 4 | **Circle CCTP (cross-chain transfer)** | Contract set + `MessageTransmitter` / `MessageReceipt` messages + the "attested transfer" claim. This is the "bridge" leg; pin the message format so `consortium` speaks it. |
| 5 | **ERC-1643** (RWA registry) | The registry interface the disclosure asset-registry view can query; pin field names. |
| 3 | **Per-chain gas/fees** | Base/Ethereum/Solana/Arbitrum/Tempo current gas + bridging cost for a ~$X USDC transfer — to make "cheapest chain" *real* in Phase 4 routing (not just the raw quote). |
| 5 | **OFAC / EU sanctions list format** | The list shape `ComplianceVerifierAgent` matches against (SDN JSON, name + DOB + address fields). Pin the source + schema. |

---

## 5. Reuse summary (the honest one-line version)

- **Reusable as-is (import):** agpack trust (audit/delegation/signing/sandbox/artifact),
  sentinel orchestrator + structured output + non-crypto agents + eval, metered
  core/budget/rails/identity/license.
- **Must be written in `consortium`:** the thin runtime (no CLI), the JSONL audit
  storage, the PQC risk model (`crypto/risk.py`), the CycloneDX emitter (`crypto/cbom.py`),
  the crypto-posture agent, the on-chain chain-observer, the settlement router,
  and — crucially — **a payout `Scope`/namespace on the delegation token**.
- **Must be researched (external):** CycloneDX, ERC-4626, FIPS 203/204/205, x402/AP2,
  CCTP, ERC-1643, sanctions-list schema, per-chain gas.
