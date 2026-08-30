# Build Prompt #3 — The License Engine (Plan Phase 4)

## Context
Metered Web Broker (`/home/iconbaypark2900/dataScience/metered-web-broker`), a neutral
one-contract service. The goal of **Phase 4** is "compliance, not just access": read the
license an origin attaches to its content, derive the obligations the fetch creates, and surface
those obligations in the receipt so the agent can comply.

The package skeleton exists at `packages/license` (`package.json` + `tsconfig.json`), but
`src/` is **empty**. The output shapes already exist in `packages/core/src/types.ts`:

```ts
export type RslClauseType = 'attribution' | 'no-store' | 'no-derivative-works'
  | 'no-commercial-use' | 'pay-per-inference' | 'redistribution' | (string & {});
export type RslClauseValue = 'required' | 'permitted' | 'forbidden' | number;
export interface RslClause { type: string; value: RslClauseValue; params: Record<string, string|number>; }
export interface License { draft: string; id: string; title?: string; publishedAt?: string;
  clauses: RslClause[]; rawSource: string; }
export interface LicenseObligation { clause: string; kind: 'notice' | 'store-prohibited'
  | 'inference-budget' | 'forbidden' | 'permitted'; instruction: string; applied?: string; }
```

The engine reads a parsed/`rawSource` `License` and must produce `obligations: LicenseObligation[]`
that the Gateway (Prompt #1) attaches to the `FetchOutcome`. The RSL reference is "Really Simple
Licensing (RSL Collective)" — it supports pay-per-crawl and pay-per-inference terms.

## Interface to build
Create `packages/license/src/`:

- `src/parse.ts` — `parseLicense(source: string): License`.
  - Accept a JSON (or JSON-with-comments / loose) representation of an RSL document. Because the
    spec is still being finalized, parse **tolerantly**: accept the draft's field names but keep
    the raw string around (`rawSource`) for audit/reconcile, and never throw on unknown fields.
  - Fill `License.draft`, `License.id`, `title`, `publishedAt`, `clauses`, `rawSource`.
  - On malformed input, return a `LicenseError`-typed throw (import `LicenseError` from
    `@mwb/core`) with a stable `LICENSE_MALFORMED` code — but log, don't crash the broker.
- `src/obligations.ts` — the core:
  - `deriveObligations(license: License): LicenseObligation[]`
  - `classifyClause(clause: RslClause): { kind: LicenseObligation['kind'], instruction: string }`
  - `isStoreProhibited(license): boolean` and `hasAttribution(license): boolean` — convenience
    predicates the Gateway/dashboard use.
- `src/inference.ts` — `payPerInferenceCost(license, inferredOutputs: number): Micros | null`
  where each output carries an `estimatedCostMicros` (the agent's own estimate). Compute the
  obligation's `applied` field.
- `src/index.ts` — re-export: `parseLicense`, `deriveObligations`, `classifyClause`,
  `isStoreProhibited`, `hasAttribution`, `payPerInferenceCost`, and
  `export type { License, LicenseObligation, RslClause, RslClauseType, RslClauseValue }`.

## Mapping rules (clauses → obligations)
Implement these mappings, keeping the draft-caveat that exact clause names may drift:
- **attribution** (`required`): obligation `kind: 'notice'`, instruction telling the agent to
  include attribution text. If params name a field (e.g. `source`, `notice`), include it.
- **no-store** (`required`): obligation `kind: 'store-prohibited'`, instruction "do not persist
  this content". `isStoreProhibited()` returns true.
- **no-derivative-works** (`required` or `forbidden`): obligation `kind: 'forbidden'`, instruction
  "no derivatives/derived models".
- **no-commercial-use** (`required`): obligation `kind: 'forbidden'`, instruction "no commercial use".
- **pay-per-inference**: obligation `kind: 'inference-budget'`, instruction "each inference costs
  the quoted price; keep spend ≤ the per-inference ceiling". `payPerInferenceCost` fills `applied`.
- **redistribution** (`permitted`/`required`): obligation `kind: 'notice'` or `'permitted'`
  describing the redistribution rule.
- Any **unknown clause type**: surface as a `notice` obligation with a generic instruction and
  `applied: null` (so nothing silently fails compliance), never a hard error.

## Determinism & money
- **Money is micros.** Any cost computation uses integer micros (import `micros`,
  `formatMicros` from `@mwb/core`). Never emit floats in `applied` cost fields.
- Output is pure: given the same `License`, `deriveObligations` returns an **array in stable
  order** (deterministic — audit logs depend on it). Sort by clause type, then by clause order.

## Integration with the rest
- The Gateway attaches `obligations` to `FetchOutcome.obligations` and `license` to
  `FetchOutcome.license`.
- `store-prohibited` obligations must be visible on the text dashboard (`renderDashboard` already
  lists `class`/`price`/`rail`; it does not need to change, but obligations should appear in the
  receipt the broker returns to the agent — the Gateway passes `obligations` through).
- `isStoreProhibited` / `hasAttribution` feed the `content.storeable` field the Gateway sets.

## Tests (`packages/license/src/*.test.ts`, vitest — already wired by root `vitest.config.ts`)
- `parse.test.ts`:
  - Parses a valid RSL JSON into a `License` with all clauses.
  - Unknown/extra fields are preserved via `rawSource` and do NOT throw.
  - Malformed JSON throws `LicenseError` with code `LICENSE_MALFORMED`.
  - `publishedAt` / `title` are optional and default to `undefined`.
- `obligations.test.ts`:
  - `classifyClause` maps each clause type → the expected `kind` + instruction (attribution,
    no-store, no-derivative-works, no-commercial-use, redistribution).
  - `deriveObligations` returns one obligation per clause, in **stable deterministic order**.
  - `isStoreProhibited()` true only when a `no-store`/`required` clause is present.
  - `hasAttribution()` reflects an `attribution` clause.
- `inference.test.ts`:
  - `payPerInferenceCost` returns an integer micros cost for N outputs and `null` when the
    license has no pay-per-inference clause.
  - Cost computation uses micros (assert integer, no float leakage).
- `integration.test.ts`:
  - Full `parseLicense` → `deriveObligations` pipeline on a representative license with mixed
    clauses; assert the resulting obligations and `applied` values.

## Definition of done
- `packages/license/src/` implements `parseLicense`, `deriveObligations`, `classifyClause`, the
  `store-prohibited`/`attribution` predicates, and `payPerInferenceCost`.
- `npm test` and `npm run typecheck` pass.
- Output integrates cleanly with `packages/core` types the Gateway already consumes.

## Open decisions to confirm before coding
- **Input format shape:** is the RSL document always JSON, or do we also need to accept a
  content-type header / text representation? Recommend JSON parse + tolerant extras.
- **Clause vocabulary:** the plan names a fixed set (`attribution`, `no-store`,
  `no-derivative-works`, `no-commercial-use`, `pay-per-inference`, `redistribution`) — but real
  RSL may use other names. Recommend the "unknown clause → `notice`, `applied: null`" rule above.
  Confirm the exact clause set to support initially.
- **`payPerInferenceCost` base price source:** is the per-inference price carried in the license
  (`params.price`) or supplied separately? Decide which, then implement.
