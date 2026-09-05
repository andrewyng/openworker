# P2 PROMPT — Phase 0: JSONL audit store (the runtime that replaces the agpack CLI stub)

## Status
P1 (Persona / Composition) is **COMPLETE and VERIFIED**. Do not re-derive it.
Start at **P2** (Phase 0 = the pure-math audit runtime). See the P1 "Notes / gotchas" in `concord/PHASES.md`.

## What P1 proved (context, not to redo)
- `concord/` composes 3 read-only pillars: **agpack** (trust/audit/signing/delegation/sandbox/artifact, Python `src/`), **sentinel-local** (orchestrator/llm.structured/eval/crypto, Python `src/`, 5 STUB symbols), **metered-web-broker** (PaymentRail/budget.engine/finality, TypeScript `packages/`).
- `concord.reality.check()` probes 33 symbols → **27 REAL / 6 STUB / 0 MISSING**, byte-identical across runs, **egress zero** (import only + local `.ts` reads; no socket/subprocess).
- The one invariant lives in `concord/invariant.py`:
  `FINAL ⇒ (agent scoped) ∧ (budgeted) ∧ (recorded) ∧ (reconciled-to-real)`.
- `finality_is_satisfied(x) == (x == "final")` — a strict gate, not "settled at some point".
- The 6 STUBs are deliberately left unwritten (scope of P0–P6); `agpack.cli` STUB is the one Phase 0 replaces.

---

## THE TASK for P2
Build the `concord.audit` runtime — the **JSONL audit store** that stands in for the
`agpack.cli` stub. This is Phase 0: pure-math, no crypto, no network, no external
dependency. Its only job is to prove the **recording** half of the invariant is
*real* (not asserted): a replayable, deterministic, self-validating append-only audit
ledger, with attribution and reconciliation expressed as exact rational arithmetic.

Grounding source for the contract is `agpack/trust/audit.py` (the `AuditRecord` /
`AuditLedger` / `LedgerCorrupt` spec). That file is out of my read scope this run, so
I'm encoding the contract I verified earlier — **you MUST read the real `audit.py`
first and reconcile any divergence in your write-up.**

### A. The 4-record LedgerCorrupt contract (verified from `agpack` audit.py)
Build records that match this shape EXACTLY. A record is a dict; detail is a nested
dict keyed per kind. **A detail whose keys don't match its kind → `LedgerCorrupt`
(hard fail, name the offending key).**

- `dispatch`  → `{ tool_cid, args_sha256_prefix16, output_sha256, budget_spent }`
- `import_call` → `{ scope, resource, arg_fingerprint, host_return, fuel_delta }`
- `delegate`  → `{ token_id, parent_token_id (None allowed), hop_depth, scope, resource }`
- `budget`    → `{ budget }`

Record fields (top level): `ordinal` (monotonic int, 1-based), `run_id` (fixed
string), `kind` (one of the 4 closed values), `ts_unix` (a **logical 1-sec clock**,
NOT wall time — starts at 0, increments by 1 per record), `subject`, `detail`.

Redaction rule: argument buffers are hashed to a **16-hex-char SHA256 prefix**
(`args_sha256_prefix16`). The ledger is a **replay ORACLE, not a replay-input** — the
raw arg is never stored, only this prefix.

### B. Deterministic corpus
Generate a corpus in code (deterministic, egress zero — no RNG with a seed, no time,
no network):
- a **140-partner** base set, and
- a **5-link delegation chain** (each `parent_token_id` points back; `hop_depth` grows
  0→4).
No external data files. The corpus is produced by a pure function so it's identical on
every machine.

### C. Attribution by exact rational math
Attribution is `totalShares / totalShares × totalAssets` — i.e. share-weighted split of
total assets, expressed as **Python `fractions.Fraction`** so it is *exact*, not float.
Requirement: **`sum(attributions) == delta` to-the-exact-ration** for every settlement.
This is the pure-math proof-of-Phase-0: a float version will drift; Fraction will not.

### D. The ledger + finality link
- `AuditLedger` is append-only; each action appends exactly one of the 4 record kinds.
- `.validate()` hard-fails (raises the equivalent of `LedgerCorrupt`) on any contract
  violation and **names the offending key/kind** — this is the honesty gate, same as
  `assert_real_reality()` in P1.
- `.replay()` returns the canonical ordered record list.
- The settlement's finality stays **`claimed`/`test`** until it reconciles; P1's
  `concord.invariant.finality_is_satisfied` is the gate Phase 3 reads. Phase 0 does not
  mark anything `final` — it only proves the **recorded** facet is real.

### E. Constraints (non-negotiable)
- **Egress zero.** Import only stdlib (`fractions`, `hashlib`, `json`, `pathlib`,
  `dataclasses`). No socket, no subprocess, no file reads of external data.
- **Deterministic.** `python -m concord.audit run` and `.replay` output must be
  byte-identical across machines and runs (no wall clock, no unseeded RNG).
- **Read-only toward the pillars.** `concord.audit` is new code, but it must never edit
  agpack / sentinel / metered. It is the `concord` runtime, not a pillar rewrite.
- Keep the P1 shape: `concord/__init__.py` re-exports (`check`, `assert_real_reality`,
  `finality_is_satisfied`, `unify`, `Report`), `concord/__main__.py` CLI stays working.

### F. Deliverables
1. `concord/audit.py` — the runtime (records, ledger, validate/replay, attribution).
2. A `concord/__main__.py` subcommand, e.g. `python -m concord audit run`, that prints the
   140-partner / 5-chain corpus, the settlement attribution table, the replayed records,
   and a `VALID` verdict — all byte-deterministic.
3. `concord/PHASES.md` update: mark P1 done, record P2 in the same "verified against
   source" style P1 used.

### G. Exit criteria
- `python -m concord check` (P1 reality gate) still passes unchanged.
- `python -m concord audit run` prints `VALID` and the attribution **sums exactly to the
  delta** (Fraction equality, not `≈`).
- Ledger `.validate()` raises on a deliberately-corrupt record, naming the key.
- Re-running yields a byte-identical report.

---

## Open P3 (skipped until P2 ships)
Phase 1's drift seed — two by-construction anomalies (chain-3 Aave 0.0375 USDC gap,
partner-117 rounding skew) fed into a reconciliation check so Phase 1 has real drift to
catch. `reconcile` exits clean on the base, non-zero on the seed. **Do not start until
P2 is in.**
