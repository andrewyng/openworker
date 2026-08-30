# Build Prompt #1 — The Gateway (Broker.fetch)

## Context
This is the Metered Web Broker (`/home/iconbaypark2900/dataScience/metered-web-broker`), a
neutral "one-contract" service: an agent calls one endpoint and gets content + a license + a
settlement receipt. The domain packages already exist and are done/tested:

- `packages/core` — types + error hierarchy. Money is integer micros via `micros()`. Injectable
  `Clock` (`systemClock` / `frozenClock`). Error classes carry a stable `code` + `retryable`;
  use `isBrokerError(err)`.
- `packages/budget` — `BudgetEngine` (ceiling / meter). `preflight`, `recordPaid`, `recordDenied`,
  `snapshot`.
- `packages/rails` — `PaymentRail` interface + `InMemoryRail`, `SelfHosted402Rail`,
  `BridgeRail`, `RailRegistry`.
- `packages/identity` — `KeyRing` + `PACT`.
- `packages/audit` — `AuditLedger` (+ `summarize`, `reconcile`, `renderDashboard`).

**What is still missing:** the Gateway that *composes* the above into the single `fetch()` call.
There is no orchestrator today.

## Contract to build
Create `packages/gateway/src/broker.ts` exporting `Broker` (and a `createBroker` factory).

```ts
class Broker {
  fetch(params: FetchParams): Promise<FetchOutcome>;
  // plus a small query API used by tests / dashboards:
  ledger(): AuditLedger;
}
```

`FetchParams` and `FetchOutcome` already live in `packages/core/src/types.ts`. Read them before
coding. In particular `FetchOutcome` already carries everything the broker must fill in:
`status` (`fulfilled` | `denied` | `error`), the three-class `failureClass`, `price`, `quote`,
`rail`, `settlementRef`, `license`, `obligations`, `identity`, `origin`, `tenantId`, `timestamps`.

## Required flow
Implement `Broker.fetch`. For each call it must:

1. **Budget gate FIRST.** Build a `Price` from the incoming quote (micros, currency from policy).
   Call `budgetEngine.preflight(tenantId, price, url)`. If it throws a `BudgetError`, record a
   ledger row with `status: 'denied'`, `failureClass: 'blocked'`, and return it as an outcome
   (`status: 'denied'`, `reason` from the error's message, `error.code`). The budget check is the
   ONLY place a request may be denied *before* the origin is reached.
2. **Rail quote.** Pick the rail (use the `RailRegistry`, or a per-URL mapping). Call
   `rail.quote(request)`. On failure, record `denied` with `failureClass: 'blocked'`
   (`RailError` is retryable in most modes — surface `retryable` on the error).
3. **Authorize + settle.** Call `rail.authorize(request, quote)` then `rail.settle(...)`. Capture
   the returned `settlementHeaders`, `settlementRef`, `price`/`amountMicros`, `rail`, `currency`.
4. **Identity.** Sign the outgoing request. If a key is available, run `ring.sign(...)` (or
   `issuePact` where the origin wants attestation) and set `outcome.identity` from
   `IdentityPresentation`. Identity is a best-effort enhancement, not a hard gate in Phase 1 —
   a missing key should not block a cheap fetch, but the signed headers must be attached when a
   key exists, and it must always be recorded in the audit row (`keyId`).
5. **Fetch / content.** Perform the actual origin request and attach `outcome.content`
   (`body`, `contentType`, `storeable`, `attributionNotice`).
6. **License.** Run the License Engine (its output shape `License` / `LicenseObligation[]` already
   exists in `core/types.ts` even though the package is empty — consume it). Attach to
   `outcome.license` and `outcome.obligations`.
7. **Record the outcome.** Call `budgetEngine.recordPaid(...)` for a settled fetch. Append exactly
   one row to the `AuditLedger` via `ledger.record(outcome, { tenantId })`. Idempotent per
   `fetchId` — never write two rows for one call.
8. **Return the outcome.**

## The three failure classes — this is the whole point
Every terminal *denied* path must set exactly one of these, never a generic "failed":
- `'blocked'` — budget policy, missing/invalid mandate, rail could not quote/authorize.
- `'payment-required'` — origin returned 402 and no rail could or should settle it.
- `'token-rejected'` — origin 403 / bot gate / agent detection; signature/token rejected.

An `error` status (internal fault, network transport failure, parse error) is *not* one of the
three classes — keep it in `status: 'error'` with a `reason` + `error`, not a false `failureClass`.

## Conventions you MUST follow
- **All money is micros.** Use `micros()` (throws on non-finite / unsafe integer). Never store or
  compare floats in ceilings, prices, or the ledger.
- **Inject the `Clock`.** The `Broker` constructor takes `{ ledger, budget, rails|railsById,
  ring?, license?, clock?, currency? }`. The gateway's timestamps must come from the injected clock
  so replay/tests are deterministic. Default to `systemClock`.
- **Errors carry `code`.** Surface `error.code` / `error.message` on the outcome for the audit +
  agent to consume.
- **Don't fork the rail bridge.** If wiring `BridgeRail` around `ap2-x402-bridge`, only use the
  `BridgeClientShape` interface already defined in `packages/rails`.
- **Draft-caveat discipline.** The `PaymentRail` field names live inside an opaque `payload` by
  design — don't parse draft wire fields in the gateway; talk to the rail through its interface.

## Tests (`packages/gateway/src/broker.test.ts`, vitest)
Write tests that exercise every branch, using `frozenClock()`:
- Happy path: preflight OK → quote → settle → fetch → `fulfilled`, row recorded, budget debited,
  `settlementRef` present, `price` present.
- Budget denied → `denied` / `blocked` / stable `BUDGET_DENIED` code.
- Rail quote failure → `denied` / `blocked`.
- Rail settle failure → `payment-required` (402 semantics).
- Origin 402 with no able rail → `payment-required`.
- Origin 403 / bot gate → `token-rejected`.
- Idempotency: two `record` calls for the same `fetchId` → single row.
- Reconciliation passes on a full ledger (`reconcile(...).ok === true`).
- Missing key still fetches but records `keyId: undefined` (identity is best-effort, Phase 1).

## Definition of done
- `Broker.fetch` composes all four packages through their real interfaces.
- Every outcome fills the correct `status` / `failureClass` per the rules above.
- Exactly one ledger row per call; `reconcile` is green.
- `packages/gateway/package.json` depends on `@mwb/core`, `@mwb/budget`, `@mwb/rails`,
  `@mwb/identity`, `@mwb/audit` (align with the other packages' dependency style).
- `npm test` and `npm run typecheck` pass.

## Open decisions to confirm before coding
- What does the real origin-fetch transport look like (native `fetch`? injected transport
  function?) — for Phase 1, an injectable `transport(url, headers) => { ok, status, body,
  contentType }` is the cleanest shape and keeps the gateway testable against the in-memory rails.
- Does the broker route by URL→rail or tenant→rail? Recommend a `route(url, tenantId)` hook with a
  per-URL map defaulting to the first registered rail.
- Should `broker.ts` be pure-orchestration (no `import` of Node `node:fetch`)? For testability,
  prefer injecting the transport and keep `packages/gateway` Node-agnostic like the others.
