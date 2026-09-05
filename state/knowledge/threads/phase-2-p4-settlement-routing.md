---
id: phase-2-p4-settlement-routing
title: Phase 2 (P4) settlement routing
state: active
updated: '2026-09-03'
tags: []
---
**Now:** Ground-truth metered sources for Phase 2 located and read via shell cat; Phase 2 prompt file being reproduced into the repo.

## History
- 2026-09-03 — Phase 2 (P4, "settlement routing") ground-truth sources for ConcordCircuit (read-only, derived via shell cat — read tools are workspace-rooted to concordCircuit): metered core types `/home/iconbaypark2900/jabCreative/dataScience/metered-web-broker/packages/core/src/types.ts` (finality?:'claimed'|'final' line 105, PaymentRail interface line 155, BudgetError line ~22); metered policy `/home/iconbaypark2900/jabCreative/dataScience/metered-web-broker/packages/budget/src/policy.ts` (defaultPolicy: perCallCeilingMicros 10000, hourlyTenantCeilingMicros 100000, dailyTenantCeilingMicros 1000000, currency 'USD', lineItems []); metered budget engine `/home/iconbaypark2900/jabCreative/dataScience/metered-web-broker/packages/budget/src/engine.ts` (preflight() only pre-step that throws BudgetError; CURRENCY_MISMATCH if price.currency != policy.currency; BAD_QUOTE if price.unitsMicros not safe-integer or <=0; checkBucket codes CEILING_EXCEEDED/PRICE_EXCEEDS_REMAINING). Runtime surface lives at `/home/iconbaypark2900/jabCreative/dataScience/concordCircuit/concord/` — pillars.py real_stub_table at line 57, AGPACK_SRC_ROOT line 22; __main__.py dispatches on sys.argv[1] (append, don't replace). The prompt for P4 is NOT findable by name in runner sources; the contract is encoded in consortium-agent/PHASES.md + metered specs. P1 Persona done, P2 Phase0 runtime done (concord/runtime.py, 140 partners/5 chains/560e9 wei/286 records), P3 Phase1 drift done (concord/drift.py).
