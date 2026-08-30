# Next task prompt — metered-web-broker: make `npm run typecheck` GREEN

**Repo (READ-ONLY):** `/home/iconbaypark2900/dataScience/metered-web-broker`
**Stage files in your primary scratch** (`/home/iconbaypark2900/openworker-tasks/<session-id>`) and hand
over as ready-to-paste blocks — or request write access to the broker repo first. Do not commit.

## Why this task is "next"
The CI `done` gate (`.github/workflows/ci.yml`) runs in strict order:
`npm ci` → **`npm run typecheck`** (line 18) → `npm test` (line 19) → `npm run conformance` (line 20).
`typecheck` is the first failing gate; everything downstream is blocked until it is green.
(Conformance — `packages/conformance/` — is the SECOND outstanding gate; a separate prompt covers it.)

## Current failure (from read-only verify, 2026-08-27)
Root `package.json` declares:
```json
"typecheck": "npm run typecheck --workspaces"
```
This re-invokes `npm run typecheck` inside **every** workspace. **No package declares a
`typecheck` script**, so npm prints "Missing script" and exits 1. Workspaces:
`core, audit, budget, gateway, identity, license` (and `rails` exists but has no `.test` — confirm its
package.json too).

## Constraints (do not violate)
- `tsconfig.base.json` uses `strict`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`,
  `module ESNext` / `moduleResolution Bundler`, `noEmit`, `types ["node"]`, `skipLibCheck`, **no DOM lib**.
- Keep those flags. Do **not** paper over errors by loosening the config.
- Pkg-internal imports use `@mwb/*` workspace specifiers; each package tsconfig extends
  `../../tsconfig.base.json` and `include: ["src"]`.
- Node v20 runtime, TS ^5.7, vitest ^2.1.

## Known type errors to verify once the script is wired (the verify step flagged these; confirm
which are real vs. tooling noise — do NOT assume they're all genuine):
- `JsonWebKey` is undefined in `identity/ring.ts` and `gateway` — the base config has no DOM lib, so
  the `CryptoKey`/`JsonWebKey` references aren't known. Options: (a) add `"lib": ["ES2022","DOM"]` to
  base config, or (b) stop referencing the DOM global and use the JWK round-trip path (`crypto`
  `createPrivateKey`/`importJwk`) which already works on v20 with `@types/node`. Prefer (b) unless
  DOM is genuinely needed — it keeps the runtime footprint honest.
- `UrlString` branded type in `core` — `scripts/demo.ts` passes plain `string` where the branded
  `UrlString` is required. (Not a blocker for THIS task: demo.ts isn't typechecked by any package
  tsconfig `include: ["src"]`. But fix at some point.)
- `InMemoryRail` missing `priceMicros`/`currency`/`clock` params in `rails.ts`;
  `RslBodyLike` missing `link`/`rawSource` in `license/engine.ts`;
  `attributionNotice` on the Link-header object in `broker.ts:459`;
  `ring.test.ts:56` + `ring.ts:340` typed-prop mismatches.

## Acceptance criteria (must all pass in the repo, in this order)
1. Each package that has `src/` declares its own `typecheck` script, e.g.
   `"typecheck": "tsc -p tsconfig.json --noEmit"` — OR (recommended for fewer files) add a root
   `tsconfig.json` with `references` to all packages and set root script to `"typecheck": "tsc --build"`.
   Pick ONE approach; be consistent. (I prefer the root project-reference approach — it type-checks the
   whole graph in the right order and is one file. But add per-package scripts if that's cheaper to
   hand over.)
2. `npm run typecheck` exits 0 — every `src/` file across all packages type-checks under the current
   strict base config with no errors. Fix any genuine errors (the JsonWebKey DOM-lib issue and the
   typed-prop mismatches). Do NOT relax flags to make it pass.
3. Regression: `npm test` still 38/38 and `npm run demo` still exits 0 after your changes.
4. `scripts/demo.ts` dead `import { PACT }` (line 23) can stay for now — it's a separate minor item,
   but if it surfaces a typecheck error it MUST be fixed (remove `PACT` from the import).

## Verification command sequence (run all, paste output)
1. `npm run typecheck`            → exit 0
2. `npm test`                     → 38/38, exit 0
3. `npm run demo`                 → exit 0, all 4 terminal outcomes render
4. (Sanity) `npx tsc -p packages/<pkg>/tsconfig.json --noEmit` per package → each clean

## Output format
Produce every new/changed file as complete ready-to-paste blocks, labeled with the exact repo path
and whether it's a NEW file or an EDIT to an existing one. Group by package so the user can drop them
in with minimal ceremony. Flag anything that needs a judgment call (e.g. "add DOM lib vs. switch to
JWK path") rather than guessing silently.
