---
id: concord
title: concord
state: active
updated: '2026-08-31'
tags: []
---
**Now:** Phase 0 (P2) COMPLETE

## History
- 2026-08-31 — concord Phase 0 runtime is settled and verified. `concord/runtime.py` builds a deterministic 140-partner / 5-chain ERC-4626 corpus (price_per_share=1e8 wei → exact attribution) and writes a 286-record JSONL ledger (budget×1 / dispatch×5 / delegate×140 / import_call×140) via the real agpack AuditLedger. `__main__.py` added 4 verbs: build, verify invariant (replay self-validate + sum==delta to the wei), verify audit (schema + ordinals + prefixes), verify reconcile (reconcile().ok + finality gate). P1 check kept byte-identical. Egress-zero; agpack suite 276 passed; determinism + LedgerCorrupt-on-corruption both confirmed. (source: concord/PHASES.md)
