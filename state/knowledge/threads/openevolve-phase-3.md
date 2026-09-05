---
id: openevolve-phase-3
title: openevolve-phase-3
state: resolved
updated: '2026-08-31'
tags: []
---
**Now:** Phase 3 closed at sign-off 2026-08-25 (commit 3ef8030, 102/102 tests). Phase 4 scope decisions locked in: drug target = HCV NS3/4A protease (replace placeholder receptor.pdbqt), materials front = perovskite solar absorbers (CHGNet E_form + band-gap window, substitution search).

## History
- 2026-08-31 — 2026-08-31 derive run: the live build moved. `agpack` (verifiable-agent harness, /home/iconbaypark2900/dataScience/agpack) is now the top project — 8 sessions 08-30 of 189–330 msgs ("Continue the agpack build", P1 Conformance Tier B, Direction A external-facing, Step5 metered access, P0 unblock deployment/ship it); 2 commits (import 08-19, `tools/metered.py` 08-30). The old openEvolve path `~/openworker-workspace/opensciencelab` no longer exists on disk — only knowledge threads remain — so Phase 3 work has effectively parked. dcode-stack's decode_proxy came back from quiet (51 commits 08-26/27, vLLM+llama.cpp shared proxy). openEvolve first flagged active 2026-08-24. (source: /home/iconbaypark2900/OpenWorker/knowledge/FOCUS.md)
- 2026-08-26 — Phase 3 CLOSED 2026-08-25 on branch phase2-drug-and-materials (commit 3ef8030): 4 adapters (descriptors/Vina/DFT/CHGNet) + live-kernel Docker verify image (vina 1.2.5, all libs pinned: pymatgen 2024.9.10 floor for chgnet 0.4.2, meeko 0.7.1 + gemmi 0.7.5, curl in slim base) + 5 kill-criteria tests (KILL 1 stamp/value consistency for dft/docking/chgnet; KILL 2 per-instance monotonic top-N ceiling for dft/chgnet). 102/102 tests green. PHASE3.md has the 5-section closure report. Sign-off given.
