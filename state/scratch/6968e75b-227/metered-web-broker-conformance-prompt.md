# Task prompt — metered-web-broker: build `packages/conformance` (final CI gate)

**Repo (READ-ONLY):** `/home/iconbaypark2900/dataScience/metered-web-broker`
**Stage all files in primary scratch** (`/home/iconbaypark2900/openworker-tasks/<session-id>`) and hand
over as ready-to-paste blocks — or request write access to the broker repo first. Do **not** `git commit`.

## Context — why this is the last task
The CI `done` gate is `.github/workflows/ci.yml`:
`npm ci` → `npm run typecheck` (line 18) → `npm test` (line 19) → `npm run conformance .../a2a-card.json a2a-card` (line 20).

- `npm ci` — **PASS**
- `npm run typecheck` — **GREEN now** (2026-08-27, 0 errors across all 7 packages; final 22 were in `packages/gateway`)
- `npm test` — **PASS 38/38**
- `npm run conformance …` — **FAIL (only outstanding gate).**

`packages/conformance/` **does not exist at all** — no `package.json`, no `src/cli.ts`, no
`fixtures/a2a-card.json`. The root `package.json` already declares the script
`"conformance": "tsx packages/conformance/src/cli.ts"` and CI line 20 invokes
`tsx packages/conformance/src/cli.ts <fixture> <contractName>` with args
`packages/conformance/fixtures/a2a-card.json a2a-card`. That currently throws
`ERR_MODULE_NOT_FOUND`. **Building this package makes the entire gate stack green.**

## Toolchain facts
- Node v20 runtime (CI uses v22). `tsx` ^4.19 (runs TS directly), TS ^5.7, vitest ^2.1, `@types/node` ^22.
- Root script: `"conformance": "tsx packages/conformance/src/cli.ts"`.
- Pkg-internal imports use `@mwb/*` specifiers. `verbatimModuleSyntax` is ON — type-only imports must use `import type`.
- Conformance is **standalone**: read the card as data. Do not hard-couple to broker internals — only use the
  public contract surface that `scripts/demo.ts` already exercises.

## Contract surface conformance must validate
The broker is "one contract": one endpoint returns **content + license + settlement receipt**. Terminal
classes on the audit surface are exactly `{fulfilled, blocked, payment-required, token-rejected}`.
Build **4 validators**, each derived from that public contract (read `scripts/demo.ts` and `packages/gateway/src/broker.ts`
for the real shapes — don't invent fields):

1. **identity** — a PACT `keyId`/`pactToken` is present/structured consistently; matches the broker's
   rule (identity PACT issues only when a KeyRing AND `identity.subject` are both present).
2. **license** — an L1000/RSL `Link` header with `rel="type:license-terms"` resolves; obligations derive
   from it; license `id` is derived from the pathname (e.g. the href `/r/73` → `id=/r/73`).
3. **audit/settlement** — each row has `priceMicros`/`settlementRef`, the spend line sums, and terminal
   classes are one of the 4.
4. **contract-shape (gateway)** — the outcome has `content` + `license` + `settlementRef`; `status` is
   one of the 4 terminal classes; `denied` rows carry the correct `failureClass`.

## Required package structure
- `packages/conformance/package.json` — name `@mwb/conformance`, `"type": "module"`. (Do NOT add a
  duplicate `conformance`/`typecheck` script that would collide with the root; keep it minimal. If you want
  per-package `typecheck`, add `"typecheck": "tsc -p tsconfig.json --noEmit"`, consistent with the other 7.)
- `packages/conformance/src/index.ts` — re-exports.
- `packages/conformance/src/registry.ts` — register a validator by name/contract-id; look up by name.
- `packages/conformance/src/validators.ts` — the 4 validators above. Each takes a validated contract shape
  and returns `{ok: boolean, message: string}` (or throws a typed `ConformanceError`).
- `packages/conformance/src/run.ts` — `run(card, contractName)` collects all validator results and returns
  `{contract, results: {name, ok, message}[], pass: boolean}`; `report(result)` renders a green/red PASS/FAIL summary to stdout.
- `packages/conformance/src/cli.ts` — the entry CI calls. Args:
  `process.argv.slice(2)` → `[fixturePath, contractName]`. Read + JSON.parse the fixture, resolve the validators
  by name from the registry, run + report, `process.exit(0)` on full pass / `process.exit(1)` on any failure.
  `verbatimModuleSyntax` is on — use `import type` for type-only imports.
- `packages/conformance/fixtures/a2a-card.json` — a representative card the 4 validators PASS on: a
  `rel="type:license-terms"` Link reference, a `settlementRef`, and an identity claim — matching the broker's
  real contract so the gate is genuinely green, not vacuous.
- `packages/conformance/fixtures/a2a-card-bad.json` — a card that FAILS (e.g. a `denied` row with an invalid
  terminal class, or a missing settlementRef). Keep as proof the gate is real; CI must NOT call it.
- `packages/conformance/tsconfig.json` — `{ "extends": "../../tsconfig.base.json", "include": ["src"] }`.

## Constraints
- Keep `noUncheckedIndexedAccess`, `verbatimModuleSyntax`, `strict` intact — fix data with proper indexing,
  not by loosening the config.
- Standalone: validators take the parsed card object; do not import broker internals.
- The fixture `id` derivation must reflect the real behavior: `/r/73` → `/r/73` (pathname, not the full href).
- Do not touch anything outside `packages/conformance/`.

## Verification (run ALL, paste output)
1. `npm test` → 38/38, exit 0 (conformance must not disturb existing tests).
2. `npm run conformance packages/conformance/fixtures/a2a-card.json a2a-card` → exit 0, PASS rendered.
3. Negative: `npm run conformance packages/conformance/fixtures/a2a-card-bad.json a2a-card` → exit 1, FAIL
   rendered (proves the gate actually rejects).
4. `npm run typecheck` (full repo) → still exit 0; if you added a per-package typecheck script,
   `npx tsc -p packages/conformance/tsconfig.json --noEmit` is clean too.

## Output format
Every file as a complete ready-to-paste block, labeled with exact repo path and **NEW** vs **EDIT**.
Group under a `packages/conformance/` heading. Flag any judgment call (e.g. how strict the identity validator
is, or whether to add the optional per-package `typecheck` script) rather than guessing silently.
