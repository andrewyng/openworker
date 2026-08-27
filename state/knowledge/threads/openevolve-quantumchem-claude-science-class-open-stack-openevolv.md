---
id: openevolve-quantumchem-claude-science-class-open-stack-openevolv
title: 'OpenEvolve+quantumchem: Claude-Science-class open stack (openevolve_roadmap)'
state: active
updated: '2026-08-26'
tags: []
---
**Now:** Phase 2 item 3 (budget ledger) implemented, 41/41 tests green; only item 4 (preflight optimize branch) remains open.

## History
- 2026-08-26 — 2026-08-26 outside-view argued against free-form LLM SMILES mutation as the core operator: ToolMol (arXiv:2605.12784) shows tool-backed editing beats LLM direct mutation; Vina ranking weak (clawRxiv 2604.01170: best R²=0.31; Liganx r=0.5-0.7); proposed replacing _child operator with RDKit tool set and using CArBO-style cost cooling for tier allocation. A/B experiment (validity+duplicate+rank parity with deterministic operators) sketched as the falsification test.
- 2026-08-23 — Phase 2 item 3 (budget ledger) is DONE and committed (2 commits, branch phase2-drug-and-materials, head 32bf113). Every evolve() call now: (1) quotes max_evals x costs[tier] up front via Adapter.predict_cost, (2) is admitted/refused against remaining budget BEFORE any eval (BudgetExceeded), (3) charges actual per eval's real ScoreVector tier, (4) records cost/predicted_cost/delta_cost per Bundle and per manifest call. New Budget.remaining_for() gate + Bundle.predicted_cost/delta_cost fields. 41/41 tests pass (3 new). Both examples run; drug manifest shows actual 0.53 vs quote 0.80, delta -0.27. Item 4 (preflight optimize branch) still not started. (source: opensciencelab: PHASE2.md, base.py, manifest.py, adapters/optimize/adapter.py, tests.py)
