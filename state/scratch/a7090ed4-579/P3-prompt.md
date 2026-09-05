# P3 PROMPT — Phase 1: the drift seed + reconciliation check

## Status
- **P1** (Composition) — DONE and VERIFIED.
- **P2** (Phase 0 — pure-math ERC-4626 settlement + JSONL replay) — DONE. The runtime
  (`concord/runtime.py`) is complete, tested, and egress-zero. **Do not re-derive or
  rewrite it.** Phase 1 builds *on* this runtime — it is the seam you add to.
- Start at **P3** (Phase 1). Read `concord/PHASES.md` last block and `concord/runtime.py`
  before writing anything.

---

## 0. What Phase 1 is (and isn't)

Phase 0 proved the **recorded** and **reconciled** facets are *computable*:
`reconcile().ok` is exactly `delta == 0` to the wei, and a row is stamped `final` only
once it reconciled. But a system whose only non-zero balance is a **self-constructed
corpus** never has anything to reconcile *against* — drift can never arise, so it can
never be caught. That is Phase 1's job:

> **Give the runtime a real, non-zero drift between the recorded ledger and the ground
> it reconciles to, then write the check that catches it.**

This is **Phase 1 = attribution + disclosure** in the ConcordCircuit roadmap (the
highest-value / lowest-risk phase: no payout, no risk apex — just *detection*). It does
**not** touch Phase 2 (settlement routing), Phase 3 (payout), or Phase 6 (PQC). Those
stay untouched. Phase 1 reads the Phase 0 runtime, extends the corpus with two
by-construction anomalies, and writes a reconciliation *check* — a pure function whose
return value is "clean" or "drift detected."

**Constraints carry over from Phase 0 (non-negotiable):**
- **Egress zero.** stdlib only (`fractions`, `hashlib`, `json`, `pathlib`, `dataclasses`).
  No socket, no subprocess, no `random` with an unseeded RNG, no `time.time()`.
- **Deterministic.** Same code + same seed ⇒ byte-identical output. Iterate in sorted
  order.
- **Read-only toward the pillars.** `concord/runtime.py`, `__main__.py`, and P1's
  `reality.py` must keep passing `python -m concord check` unchanged. Phase 1 adds, it
  never edits the Phase 0 core.

---

## 1. The ground you reconcile against (reuse, don't invent)

Phase 0's corpus is the **ground truth**: `build_corpus()` yields 140 partners across
5 chains, each chain's `total_assets` is `total_shares × PRICE_PER_SHARE`
(`1_000_000_000`), so by-share attribution sums exactly:

```
Σ_partner(partner_out) == chain.total_assets        # to the wei, zero residual
Σ_chain(chain.total_assets) == corpus.total_assets()
```

The runtime already knows this:
- `concord/runtime.py:recorded_total_out(records)` = `Σ` over every `import_call` of
  `int(detail.host_return)` — the **out** side, read from the *ledger file*.
- `concord/runtime.py:recorded_total_in(records)` = the `total_assets` recorded in the
  single `budget` record — the **in** side, read from the *ledger file*.
- `concord/runtime.py:reconcile(total_in, total_out)` → `{"ok": delta == 0, "sum_in", "sum_out", "delta"}`.
- `concord/runtime.py:reconcile_ledger(records)` = `reconcile(recorded_total_in, recorded_total_out)`.
- `concord/invariant.py:finality_is_satisfied(x) == (x == "final")` — the gate.

The clean base: `reconcile_ledger(build_ledger().records)["ok"]` is `True` — in == out,
every chain row is `final`. That invariant is the control. **Phase 1 keeps the control
and adds a seed.**

---

## 2. The drift seed (two by-construction anomalies)

Phase 1 injects **two** anomalies into the corpus/ledger so Phase 1 has something real
to catch. They are small and structural — the kind of thing that would be a bug (or
fraud) in production:

### A. Aave 0.0375 USDC gap (a small, persistent under-distribution)
- **Chain 3 = `aave`** (chain-3 in the roadmap, sorted-ordered: `aave, compound, curve, lido, yearn`).
- Across *every* Aave partner, the recorded `host_return` is short by **0.0375 USDC**
  per partner. In wei that is `375_000_000` wei per Aave partner (USDC = 1e8 wei).
- Model it as a `price_slippage` / `basis_points` field on the Aave chain:
  `0.0375 / 1.0 = 0.00375` = **37.5 basis points**. So Aave's recorded payout to each
  partner is `share × price × (1 − 0.00375)` instead of `share × price`.
- **Net effect:** `Σ Aave out < Σ Aave declared pool` by `37.5_bps × aave_total_assets`,
  i.e. the Aave row leaves a tiny residual unaccounted for. In the *base* corpus this
  is exactly the kind of residual Phase 0's "last partner absorbs" scheme would *hide* —
  here it must be **exposed**, because it is drift, not rounding.

### B. Partner-117 rounding skew (a single-partner rounding anomaly)
- **`p117`** (0-indexed; partner-id string `p117`). Its recorded `host_return` is
  off by **one fractional wei** relative to the exact by-share value — a classic
  integer-division remainder that Phase 0's attribution "assigns to the last partner"
  scheme is *supposed* to absorb. Here it is **not** absorbed: it lands in `p117`'s row
  only, and the row's recorded value differs from the exact share value by exactly
  ±1 wei.
- Net effect: the **global** `Σ out` and `Σ in` still *might* balance if the Aave gap
  were absent — but with *both* anomalies present, `Σ in − Σ out` equals
  `(37.5_bps × aave_total_assets) + 1 wei`, a non-zero, attributable residual.

**State explicitly in your write-up** which is rounding (B, the kind Phase 0 legitimately
absorbs on the base corpus) and which is real drift (A, a per-partner structural loss).
Phase 1's check must distinguish "residual I am allowed to absorb" from "residual I am
not."

---

## 3. What you build

### 3.1 A drift-augmented corpus builder (additive)
Add `concord/runtime.py:build_corpus(seed="phase1")` — or a new `build_corpus_drift()` —
that returns a corpus where:
- Aave partners carry the `price_slippage=0.00375` (37.5 bps) reduction,
- `p117` carries the ±1-wei rounding skew,
- every *other* chain/partner is identical to the Phase 0 base.

Keep the **base** `build_corpus()` untouched (it is the clean control, and P2's
`verify invariant` depends on it). The drift corpus must be a deterministic
superset-modification of the base, so `diff(base_corpus, drift_corpus)` is exactly
{aave chain: slippage, p117: rounding}.

### 3.2 A drift-aware reconciliation check (the heart of Phase 1)
Add `concord/runtime.py:reconcile_drift(records, allowed_absorb: dict | None)` that
returns:

```python
{
  "ok": bool,                                  # True iff no unabsorbable drift
  "residual": int,                             # Σ in − Σ out, to the wei
  "by_chain": {chain: {"declared": int, "recorded": int, "gap": int}},
  "culprits": [
      {"kind": "slippage", "chain": "aave", "bps": 37.5, "residual": int},
      {"kind": "rounding", "partner": "p117", "wei": int},
  ],
  "absorbable": int, "unabsorbable": int,
}
```

Semantics:
- **`absorbable`** = residual the Phase 0 remainder scheme is *designed* to swallow
  (the single-wei rounding on a partner that the base scheme would have dumped on the
  last Aave partner). **`unabsorbable`** = everything else.
- **`ok` is `True` iff `unabsorbable == 0`** — i.e. the only residual is one the runtime
  is contractually allowed to absorb, and even that only on the *clean* base corpus.
- The check must **name the culprits** (which chain / which partner / how much) so
  Phase 1's *disclosure* facet produces an auditable trail, not a single boolean.
- On the **base** corpus: `ok == True`, `residual == 0`, `culprits == []`.
- On the **seed** corpus: `ok == False`, `residual == (37.5_bps × aave_total) + 1`.

### 3.3 A CLI verb
Add `concord verify drift` to `concord/__main__.py` (append; don't disturb the existing
`build` / `verify invariant` / `verify audit` / `verify reconcile` / `check`). It:
1. builds the **base** ledger and asserts `reconcile_ledger(...)["ok"] is True`
   (the control),
2. builds the **seed** ledger and asserts `reconcile_drift(...)["ok"] is False`
   with the two named culprits,
3. prints a one-line verdict and returns `0` on the expected outcome, `1` otherwise.

Keep it egress-zero and deterministic. If you want, also expose `concord verify
drift --base-only` that asserts only the clean control (`ok`), useful as a regression
that the *absence* of drift reads clean.

### 3.4 A test (committed evidence — Phase 0 never got one)
Add `concord/tests/test_phase1_drift.py` (or `tests/test_drift.py`) covering:
- base corpus reconciles clean (`reconcile_ledger` ok, `reconcile_drift` ok, no culprits),
- seed corpus is **detected** (`ok` is False, residual == 37.5_bps×aave+1 wei, culprits
  name `aave`/37.5bps and `p117`/1wei),
- **the distinguisher**: a *pure 1-wei rounding* anomaly (no Aave slippage) reads
  `ok==True` because it's absorbable, while the Aave slippage reads `ok==False`
  because it's not — this is what proves the check isn't just "any nonzero delta,"
- determinism: `reconcile_drift` is stable across two calls,
- egress: the module imports no `socket`/`subprocess`/`requests`.

Use whichever test runner is already wired (the agpack suite lives in `agpack/`;
if ConcordCircuit has no test harness yet, a plain `pytest` file run with `python -m
pytest` is fine — no new runner to install).

### 3.5 Document it
Append a `## P3 (Phase 1) — DONE` block to `concord/PHASES.md` in the same style P1/P2
used: what changed, the verdict, the residual number, the distinguisher, determinism,
egress, and P1-intact note.

---

## 4. Exit criteria (do not call done until all hold)

1. `python -m concord build` / `verify invariant` / `verify audit` / `verify reconcile`
   — **unchanged, still pass** (P2 intact).
2. `python -m concord check` — P1 reality table intact, 27 REAL / 6 STUB / 0 MISSING.
3. `python -m concord verify drift` — prints OK: base clean, seed detected with both
   culprits named.
4. `pytest` on the Phase 1 test — all green (control clean, seed caught, distinguisher
   proves absorbable≠unabsorbable).
5. `reconcile()` on the base returns `ok==True`; on the seed returns `ok==False` with the
   exact residual; `finality_is_satisfied('final')` gate still applies — a seeded row
   that does *not* reconcile stays `claimed`, never `final`.
6. Determinism: two runs yield byte-identical ledger + report. Egress-zero: only stdlib.
7. **No pillar is modified.** `concord/runtime.py`'s Phase 0 core is only *extended*
   (new `build_corpus` variant / new functions / additive corpus fields), never rewritten.

---

## 5. Stop here
When the four verify commands pass, the Phase 1 test passes, the distinguisher proves
absorbable≠unabsorbable, determinism and egress-zero hold, and P1/P2 are intact — **add
the P3 block to `concord/PHASES.md` and stop.** The next phase (Phase 2, settlement
routing) starts from a clean window with detection working.

**Deliverable:** drift-augmented corpus builder + `reconcile_drift` check + `concord
verify drift` verb + committed Phase 1 test, all egress-zero and deterministic, layered
on top of the untouched Phase 0 runtime.

## Context / gotchas (carry over from Phase 0)
- `agpack` is only importable when its `src/` root is on `sys.path`;
  `concord/__main__.py` injects it before importing the real audit contract.
- Two-word verbs arrive as `argv` split (`verify invariant` → `["verify","invariant"]`);
  `__main__.py` dispatch re-joins `"verify"` + a known subcommand.
- `Path` + `str` raises `TypeError` — use `Path.joinpath()` / `str(rel)`.
- `real_stub_table` is a module-level **tuple** in `concord/pillars.py`.
- `concord/settlement.jsonl` is a generated artifact; regenerate with `concord build`.
