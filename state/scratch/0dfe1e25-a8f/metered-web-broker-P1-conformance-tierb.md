# Task: P1 — Conformance Tier B (live-spec fetch + draft-drift detection)

## Scope
Upgrade `@mwb/conformance` from **Tier A** (re-validates the broker's OWN generated A2A card against rules the broker wrote itself — can't catch external drift) to **Tier B**: fetch the **live spec** for each protocol, validate a real artifact against it, and flag **version drift** when a draft bumps a field.

The metered-web-broker repo at `/home/iconbaypark2900/dataScience/metered-web-broker` is **read-only**. Copy it to a writable scratch location, work there, run the gate stack, report exit codes.

## The gate stack (run all four, all green, report each exit code)
1. `npm ci`
2. `npm run typecheck`  (exit 0)
3. `npm test`           (all pass — 58/58 baseline)
4. `npm run conformance <artifact> <name>`  (exit 0 on good, exit 1 on bad)

## Read first
- `packages/conformance/src/validators.ts`, `types.ts`, `run.ts`, `cli.ts` — the 4 current validators (identity / license / settlement / contract-shape) and the `FailureClass` union.
- `packages/core/src/types.ts` — artifact types + the broker's `FetchOutcome`.

## Tasks
1. **Web-search live spec field names — do not assume them.** Look up current drafts/fields for each protocol and record the exact URLs + draft/edition versions checked:
   - **A2A** `AgentCard` fields
   - **AP2** authorization / mandate shape
   - **UCP** `/.well-known/ucp` commerce object
   - **Web Bot Auth** signature-directory structure
2. **Extend `@mwb/conformance`** with validators for A2A card, AP2 mandate, UCP checkout, and Web Bot Auth. Each validator must be able to:
   - (a) fetch the **live spec** OR accept a pinned draft + version,
   - (b) validate the artifact against it,
   - (c) report **drift** vs. the pinned draft (field added/renamed/removed).
3. **Wire CI** so each step resolves the live/pinned draft per protocol and runs the harness on representative artifacts; fail the build on any drift or validation error.
4. **Prove it catches drift AND rejects a bad artifact:** generate one artifact that satisfies the current draft and one that violates one field; assert the harness **accepts the good and rejects the bad**; and assert that pinning to an older/other draft **flags drift** on the good artifact. Commit both artifacts as fixtures.

## Closed by
`npm test` green **with new coverage for each protocol validator**, PLUS `conformance <good-artifact> <name>` → exit 0 AND `conformance <bad-artifact> <name>` → exit 1, AND pinning an older draft flags drift on the good one — with the pinned draft version + live spec URLs recorded in the harness output. The gate must demonstrably reject a drifted/bad artifact, not just re-validate the broker's own card.

## Invariants to preserve
Keep the framework's six immutable invariants green — in particular: one terminal outcome per `fetchId`; the three failure classes reported separately (never collapsed); identity best-effort, never a gate; one ledger row + one meter charge per `fetchId`; settled payment provable.

## In scope vs. not
- **In:** live-spec fetch, drift detection, good/bad + drift-flag proof, CI wiring.
- **Out:** P2 (a real 402→settle→content round-trip) and P3 (finality flag) are separate multi-hour phases — do not roll into them.

## Finish
Show exit codes for the full gate stack AND the P1 proof cases (good → 0, bad → 1, drift flagged). Write results back to scratch. Give a one-line status.
