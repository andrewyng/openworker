# Finish-out prompt — metered-web-broker

**Repo:** `/home/iconbaypark2900/dataScience/metered-web-broker` (npm workspaces, `packages/*`).
**Goal:** make the CI `done` gate GREEN end-to-end, plus two small hygiene fixes.
**CI gate** (`.github/workflows/ci.yml`): `npm ci` → `npm run typecheck` → `npm test` → `npm run conformance packages/conformance/fixtures/a2a-card.json a2a-card`.
**Workspace constraint:** the broker repo is **READ-ONLY**. Write all scaffolding files into your
**primary scratch** dir (`/home/iconbaypark2900/openworker-tasks/6968e75b-227`) and hand them over
for the user to drop in (or grant write access to `.../metered-web-broker` first). Do not `git`
commit; just produce the ready-to-paste files.

---

## Context (what already exists / was done)
- Monorepo: 6 packages — `core`, `audit`, `budget`, `gateway`, `identity`, `license`.
- 38/38 vitest tests pass; `npm run demo` works (drives all 4 terminal outcomes deterministically,
  no network).
- `package.json` **already declares** two scripts:
  - `"typecheck": "npm run typecheck --workspaces"`
  - `"conformance": "tsx packages/conformance/src/cli.ts"`
- **Both currently fail** because:
  1. **typecheck:** `npm run typecheck --workspaces` re-runs `npm run typecheck` inside every package,
     but **no package declares its own `typecheck` script** → each workspace errors out.
     (Root is not empty; the script *expansion* into workspaces is what fails.)
  2. **conformance:** `packages/conformance/` **does not exist** — no `src/cli.ts`, no fixture, so
     the command has nothing to run.

## Toolchain facts
- Node **v20** runtime (CI uses Node 22). `tsx` ^4.19, `typescript` ^5.7, `vitest` ^2.1, `@types/node` ^22.
- Each package tsconfig.json: `{ "extends": "../../tsconfig.base.json", "include": ["src"] }`.
- `tsconfig.base.json`: `target ES2022`, `module ESNext`/`moduleResolution Bundler`,
  `strict`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`, `noEmit`, `types ["node"]`, `skipLibCheck`.
- Pkg-internal imports use workspace specifiers like `@mwb/core`, `@mwb/gateway`, `@mwb/identity`, etc.

---

## STEP 1 — Fix typecheck so `npm run typecheck` is GREEN
Make `npm run typecheck` actually type-check every package. Pick the cleaner of these two:

- **Option A (idiomatic, recommended):** Add a root `tsconfig.json` that project-references each
  package, and rewrite the root `typecheck` script to `tsc --build`:
  ```json
  { "files": [], "references": [
    { "path": "packages/core" }, { "path": "packages/audit" },
    { "path": "packages/budget" }, { "path": "packages/gateway" },
    { "path": "packages/identity" }, { "path": "packages/license" }
  ]}
  ```
  with root script `"typecheck": "tsc --build"`. (Keep package `tsconfig.json` files as-is.)
- **Option B (minimal, no root config):** Give every package its own `typecheck` script so the
  `--workspaces` expansion lands on something real:
  `"typecheck": "tsc -p tsconfig.json --noEmit"` in each of the 6 package.json files.

  → Note: `noUncheckedIndexedAccess` is on. When fixing downstream errors, remember
  array/index access returns `T | undefined` — handle that (guard, `??`, or non-null assertion where
  logically certain). Do NOT relax the tsconfig to paper over it.

**Verification:** run `npx tsc -p packages/<pkg>/tsconfig.json --noEmit` for each package individually
first (the current working workaround), confirm each is clean, then confirm the whole thing passes
under whichever option you implemented. Do not leave a package that only passes in isolation but
fails in the aggregate.

---

## STEP 2 — Scaffold `packages/conformance` (the Step 0 Phase-0 harness)
Create the package so `npm run conformance packages/conformance/fixtures/a2a-card.json a2a-card`
runs and exits 0. Structure:

- `packages/conformance/package.json` — name `@mwb/conformance`, `"type": "module"`,
  `"exports": "./src/index.ts"`-style (tsx runs TS directly). Provide **its own** `typecheck`
  script too (see STEP 1 — the new package must not reintroduce the same break).
- `packages/conformance/src/index.ts` — re-exports.
- `packages/conformance/src/registry.ts` — validator registry (register validator by name/contract id).
- `packages/conformance/src/validators.ts` — **4 validators**. Base them on what the broker contract
  already produces: identity (PACT/key ring), license (L1000/RSL Link-header + `rel="type:license-terms"`),
  audit/settlement (spend line + `settlementRef`), and a gateway/contract-shape validator
  (one endpoint → content + license + settlement receipt; terminal classes
  `fulfilled`/`blocked`/`payment-required`/`token-rejected`).
- `packages/conformance/src/run.ts` — `run(card, contractName)` executes the validators against the
  input card and returns a result; `report(result)` renders a PASS/FAIL summary.
- `packages/conformance/src/cli.ts` — the entry CI calls: `tsx packages/conformance/src/cli.ts
  <fixture> <contractName>`. Reads the JSON fixture, resolves the validators by name, runs the
  report, and `process.exit(0)` on full pass / `process.exit(1)` on any failure.
- `packages/conformance/fixtures/a2a-card.json` — a representative card that the 4 validators pass,
  matching the broker's real contract (include a L1000 `rel="type:license-terms"` Link reference,
  a settlement/`settlementRef`, and an identity claim) so the gate is genuinely green, not vacuous.

Keep it a **standalone** package — it reads the card as data; do not hard-couple it to broker
internals beyond the public contract surface (status/failureClass/priceMicros/settlementRef/
license/obligations/identity) that `scripts/demo.ts` already exercises.

---

## STEP 3 — hygiene (small)
- `scripts/demo.ts`: remove the **dead PACT import** — drop `PACT` from the
  `import { KeyRing, PACT } from '@mwb/identity';` line (only `KeyRing` is used).
- `packages/license`: add **at least one real test** (e.g. slugFromUrl → id `/r/73` from the
  `rel="type:license-terms"` Link href) so the `license` package is covered and the test ceiling
  of 38 is no longer the ceiling. Do not add tests purely to inflate the count — cover real
  behavior.

---

## Verification (run all, in order, show output)
1. `npm run typecheck` — exit 0.
2. `npm test` — 38+ tests, exit 0 (expect +1 once the license test lands).
3. `npm run demo` — still exit 0, renders all 4 outcomes.
4. `npm run conformance packages/conformance/fixtures/a2a-card.json a2a-card` — exit 0.
5. If you add a fixture/validator, add a deliberately **failing** card to prove the gate reports
   red and exits 1, then remove it (or keep it under a separate fixture path CI does not call).

## Constraints
- Repo is READ-ONLY — stage every new/changed file in scratch and hand off as ready-to-paste
  blocks (or get write access to `.../metered-web-broker` first).
- Don't touch unrelated code. Keep `noUncheckedIndexedAccess` + `verbatimModuleSyntax` intact.
- Do not `git commit`. Produce files only.
