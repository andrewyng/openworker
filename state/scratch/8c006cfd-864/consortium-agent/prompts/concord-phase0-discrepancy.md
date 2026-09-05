# PROMPT 3 — Phase-1 discrepancy seeding (the "then" step, after Phase 0)

**Context.** Phase 0 is done: `concord corpus check` is byte-identical, `concord assert sum` passes to the wei, and the 4-row ledger validates offline. **Now, before Phase 1's reconciliation agent exists, seed the corpus with realistic drift so reconciliation has something real to catch.** This proves Phase 0's record is *auditable* and Phase 1's check will fire — a regression guard.

**Step 1 — the two seeded discrepancies.** Write `concord/corpus/drift_seed.py` that produces `corpus/ground_truth_with_drift.jsonl` (a **superset** of the base table; the base stays pristine). Inject exactly these two realistic anomalies:

1. **Chain 3 (Base) — missing 0.0375 USDC of Aave yield.** Simulate a failed yield sweep: one protocol's distribution was recorded in the on-chain event log but the vault's `totalAssets` snapshot never picked it up. So on chain 3, `sum(partner_yield) = base_yield_delta − 0.0375e16` (0.0375 USDC, i.e. `3.75e14` wei). The reserve_delta and the *reported* per-partner yields still sum among themselves — the gap is *between* the vault's `total_assets` and the sum of attributable yields.
2. **A 4-partner attribution skew.** One partner (partner 117) is credited `share + 0.0008e16` on chain 1 (Ethereum) — a distribution-rounding drift in `convertToShares`. This is the kind of rounding error that, if uncaught, silently leaks value. The 5 chains' per-partner yields no longer reconcile against the vault totals.

Both anomalies are **by-construction** (they change the generator's inputs, not a random fuzz), so they're fully reproducible and their reconciliation-failure is deterministic.

**Step 2 — the reconciliation check (`concord/reconcile.py`).** This is the **Phase-1 check, written now** so the seed has something to trip against:
```
for each chain:
    reported_yield_sum = sum(partner_yield for partners on chain)
    vault_yield_delta  = vault.total_assets − vault.principal      # from the ledger
    gap = vault_yield_delta − reported_yield_sum
    if abs(gap) > tolerance(1e12, i.e. sub-pico-USDC):
        EMIT a discrepancy: chain_id, partner(s), gap_wei, severity
```
A discrepancy is **never auto-corrected in Phase 0** — it's flagged, escalated, and the base table is left **untouched** (the reconciliation *action* is Phase 1).

**Step 3 — the command.**
- `concord reconcile --seed` — loads `ground_truth_with_drift.jsonl`, runs the check, prints a **per-chain discrepancy report**, exits **non-zero** (a reconciliation *failure* is the expected result: the seed must fail). If it exits 0, the seed didn't produce detectable drift — that's the real bug.
- `concord reconcile` (no flag) — against the **pristine** base table; must **exit 0** with "clean" and zero discrepancies. This is the guard that Phase 1's reconciliation is actually catching the seed, not the base being wrong.

**Definition of done:**
- `concord reconcile` (base, no flag) → clean, exit 0.
- `concord reconcile --seed` → 2 distinct discrepancies reported (chain 3 Aave gap; partner 117 rounding), exit non-zero.
- The seeded table is a **superset** of the base; the base `ground_truth.jsonl` is unchanged.

**Do NOT do:** no payout, no correction, no settlement. Reconciliation flags — it does not fix. The fix path is Phase 1.
