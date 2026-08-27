---
id: opensciencelab-phase-2
title: opensciencelab Phase 2
state: active
updated: '2026-08-23'
tags: []
---
**Now:** Phase 2 items 1-4 all shipped (commit 34aa088). Phase 2 closed; next open work is Phase 3.

## History
- 2026-08-23 — Phase 2 (openevolve_roadmap.md) is COMPLETE on branch phase2-drug-and-materials, commit 34aa088: item 4 preflight() optimize branch shipped — preflight now takes keyword-only evaluator_kind/charged_tier/planned_evals/planned_iterations/planned_population/planned_islands/planned_plateau_patience; budget check prices planned_evals at budget.costs[tier] and gates with Budget.remaining_for; sentinel smoke test (drug=CCO, materials=Li/Se Candidate) hard-fails on ScoreVector contract or tier mismatch vs. charged_tier; kill-criteria echoes planned_* in the report. Both example scripts call preflight() before evolve() and exit 1 on failure; cli.py preflight gained --optimize flag (0/1 exit). 45 tests pass (41 prior + 4 new). No changes to adapters, base.py, manifest.py, drugdiscovery.py, or materials.py. (source: phase2-drug-and-materials @ 34aa088)
