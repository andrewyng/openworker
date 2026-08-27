---
id: openevolve-phase-3
title: openevolve-phase-3
state: resolved
updated: '2026-08-26'
tags: []
---
**Now:** Phase 3 closed at sign-off 2026-08-25 (commit 3ef8030, 102/102 tests). Phase 4 scope decisions locked in: drug target = HCV NS3/4A protease (replace placeholder receptor.pdbqt), materials front = perovskite solar absorbers (CHGNet E_form + band-gap window, substitution search).

## History
- 2026-08-26 — Phase 3 CLOSED 2026-08-25 on branch phase2-drug-and-materials (commit 3ef8030): 4 adapters (descriptors/Vina/DFT/CHGNet) + live-kernel Docker verify image (vina 1.2.5, all libs pinned: pymatgen 2024.9.10 floor for chgnet 0.4.2, meeko 0.7.1 + gemmi 0.7.5, curl in slim base) + 5 kill-criteria tests (KILL 1 stamp/value consistency for dft/docking/chgnet; KILL 2 per-instance monotonic top-N ceiling for dft/chgnet). 102/102 tests green. PHASE3.md has the 5-section closure report. Sign-off given.
