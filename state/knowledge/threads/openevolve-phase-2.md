---
id: openevolve-phase-2
title: OpenScienceLab / openEvolve — Phase 2
state: active
updated: '2026-08-22'
tags:
- opensciencelab
- openevolve
- optimization
- drug-discovery
---
**Now:** Phase 2 steps 1 and 2 of 4 are done and verified. Remaining: the budget ledger (predicted vs actual cost on every evolve() call) and preflight() as a first-class API. The Phase-3 Vina adapter slot is still a 'surrogate-offline' proxy.

## History
- 2026-08-22 — Phase 2 step 1 landed: OptimizeService.evolve() as the roadmap entry point, drugdiscovery.py objective factories, a runnable drug_evolve example (5 candidates, 27 evals, 4 generations) and 4 new tests — 30 passing. optimize() kept as a back-compat alias. (source: ~/openworker-workspace/opensciencelab/PHASE2.md)
- 2026-08-22 — Docking is a deterministic proxy stamped 'surrogate-offline' in every row's notes; the real Vina adapter is a Phase 3 item and the slot is left open for it. (source: ~/openworker-workspace/opensciencelab/PHASE2.md)
- 2026-08-22 — Phase 2 step 2 (materials example) finished. Four defects had to be fixed to make it real, two in the SHARED optimizer: _dedup keyed identity on (kind, smi, seq) so all 36 compositions collapsed to one seed; the default _child mutates SMILES/sequence so every material descendant was a clone; recorded rows dropped candidate metadata so evolved rows lost their composition; and e_form was stored in CHGNet's native sign against a higher-is-better ScoreVector contract, so the search optimized for INSTABILITY and ranked NaO (-0.99 eV) above MgTe (-3.71). Also extended the element table with Cd/In/Sn/Te because zero of the 18 default compositions could enter the default 0.8-2.2 eV window. 38 tests green; the front now ends at CdTe, band-gap fitness 1.000. (source: ~/openworker-workspace/opensciencelab/PHASE2.md)
- 2026-08-20 — Roadmap drafted (v0.1): two products on one platform — an AlphaFold-3-parity structure layer and a Claude-Science-parity orchestration layer, with provenance, staged compute and local-first models as the guiding principles. (source: ~/openworker-workspace/openevolve_roadmap.md)
