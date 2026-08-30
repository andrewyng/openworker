---
id: agpack-trust-layer-step-3
title: agpack trust layer (Step 3)
state: active
updated: '2026-08-28'
tags: []
---
**Now:** FULL suite green: 203 passed (was 10 failed / 72 on import). Blocker fixed = missing `import pydantic` in artifact/validator.py + pyyaml dep. Trust-suite test fixes in delegation + audit. Commit still PENDING.

## History
- 2026-08-28 — Reviewed agpack Step 3 trust-layer work. All three modules fully implemented (not stubs): trust/{audit.py, signing.py, delegation.py}, tests/{test_trust_*.py} (29 audit, 29 delegation, 24 signing). impl and tests are internally consistent (verify() signature matches). One likely broken test spotted: test_trust_signing.test_canonical_manifest_bytes_is_stable builds an AgentBundleManifest component WITHOUT required cid/kind/file fields -> pydantic ValidationError at construction. So Step 3 suite is NOT fully green; previous run claim unverified.
- 2026-08-28 — agpack trust layer is done AND the FULL suite is now green. Last builder session fixed a blocker (src/agpack/artifact/validator.py referenced pydantic.ValidationError without `import pydantic` → NameError) + installed missing pyyaml, so import broke the whole suite. Trust suites also fixed: delegation removed redundant `isinstance(token.scope,Scope)` check in the Scope gate (bare string scope now refused as intended) + 3 chain test-setup bugs; audit `validate()` now names the offending field+kind via `_detail_bad`, corrected `unknown_kind` test. Result: **203 passed** (was 10 failed / 72 on import). Only production change = the pydantic import; rest are test-side fixes. Nothing weakened — schema/guardrails intact. Reproduce output at .scratch/reproduce_full.txt. COMMIT still PENDING — full green state not yet committed to git.
