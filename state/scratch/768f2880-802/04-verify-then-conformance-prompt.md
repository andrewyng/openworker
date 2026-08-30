# Prompt — Verify the Metered Web Broker, then begin Phase 0 (Conformance Harness)

## Context
The Metered Web Broker is a neutral "one-contract" service at
`/home/iconbaypark2900/dataScience/metered-web-broker`. The core product is complete:

- `packages/core` — micros money math, injectable `Clock`, error hierarchy (3 terminal
  failure classes: `blocked` / `payment-required` / `token-rejected`).
- `packages/budget` — `BudgetEngine` (ceiling / meter, tested).
- `packages/rails` — `PaymentRail` interface + `InMemoryRail` / `SelfHosted402Rail` /
  `BridgeRail` / `RailRegistry` (shims; no live x402 / Cloudflare).
- `packages/identity` — `KeyRing` (Ed25519, rotate/expire, signing) + `PACT` (tested).
- `packages/license` — `parseLicense` / `deriveObligations` / `enforce` / `resolveStoreability`
  (source exists, **no test file yet**).
- `packages/gateway` — `Broker.fetch` orchestrator (tested, covers all 4 terminal classes).
- `packages/audit` — `AuditLedger` / `summarize` / `reconcile` / `renderDashboard` (tested).
- `scripts/demo.ts` — runnable end-to-end demo (`npm run demo`), exercises all 4 terminal
  outcomes against the in-memory rail with a frozen clock + injected origin.

**State to verify before doing anything else:** `node_modules` is now installed (a
`package-lock.json` exists). But nothing has been confirmed green end-to-end in THIS
environment, and the CI's last step references a package that doesn't exist yet.

---

## STEP 1 — VERIFY (do not change anything yet). Run in the project dir.

The prompt should run each command, capture its exit code + output, and report. **Do not modify
any project file in STEP 1.**

1. `npm ci`
   - This must succeed. The lockfile is pinned; if it fails the project's install state is
     broken and that's the blocker to report.
2. `npm run typecheck`
   - Must exit 0. Watch for: `demo.ts` imports `PACT` from `@mwb/identity` but never uses it
     (dead import — report as a lint-ish finding, not a fail).
3. `npm test`
   - Currently **38/38** across audit, budget, identity, gateway. **BUT** `packages/license`
     has zero tests — so 38 is the ceiling until license tests are added. Confirm the 38 and
     note the 0 in license explicitly.
4. `npm run demo`
   - Must exit 0. Confirm it walks all four terminal outcomes (fulfilled → blocked →
     payment-required → token-rejected) and prints the final SUMMARY line
     `terminal classes on the audit surface: fulfilled=1 blocked=1 payment-required=1
     token-rejected=1`. Confirm the fulfilled receipt shows a `pactToken` (PACT-issued) — note
     its length; if it's wildly off from a small-claims token, that's a flag to investigate,
     not assume.
5. `cat .github/workflows/ci.yml` and read it.
   - It has a hard final step: `npm run conformance packages/conformance/fixtures/a2a-card.json
     a2a-card`. **That path does not exist** — `packages/conformance` is Phase 0, not built.
   - **Conclusion to report:** if CI is the "done" gate, it is **not green yet** — it would
     fail on that last step regardless of the 38 passing tests. Fixing that step = building the
     conformance harness.

Deliverable of STEP 1: a short verification report — exit code + pass/fail per command, the
license-package test-count gap, and the CI-conformance gap. **No edits made.**

---

## STEP 2 — BEGIN (Phase 0: Conformance Harness)

Once STEP 1 confirms the core is green (or the failures are understood), begin Phase 0.

### What it is
A CI tool that validates an agent's protocol artifacts against **current drafts** and flags when
a draft bumps. It's the recommended first ship: low-risk, fast, distribution into the
ecosystem, keeps the broker current on the specs it depends on. The broker's own domain packages
do **not** touch this — conformance validates *third-party* artifacts, so **no runtime
dependency on `@mwb/core` or any broker package.**

### What it validates (from the plan §5)
A tool that checks an agent's:
1. **A2A card** — `/.well-known/agent-card.json` (typed, well-known-URL discovery).
2. **AP2 mandate shape** — typed payment mandates with guardrails.
3. **UCP checkout** — typed checkout per `/.well-known/ucp`.
4. **Web Bot Auth signature** — IETF-draft identity: http-message-signatures directory + Ed25519
   key. Mirror the *pattern* of the broker's `packages/identity` directory format; don't import
   it.

### Interface to build — `packages/conformance`
- `src/drafts.ts` — registry of supported drafts: `name`, `spec-url`, `latest` version +
  `publishedAt`. Each entry has a small `check(input)` returning findings. Drafts are versioned
  so the tool can (a) assert "conforms to latest N" and (b) warn "latest is M > N; you target an
  older draft".
- `src/validators/` — `agentCardValidator.ts`, `ap2MandateValidator.ts`,
  `ucpCheckoutValidator.ts`, `webBotAuthValidator.ts`.
- `src/report.ts` — `run(drafts, artifacts): ConformanceReport` per draft:
  `{ name, versionTarget, versionLatest, status: 'pass' | 'fail' | 'warn', findings: Finding[] }`.
- `src/cli.ts` — wired to the existing root script
  `"conformance": "tsx packages/conformance/src/cli.ts"`. Usage:
  `conformance [--json] <path|url> [--draft a2a,ap2,ucp,web-bot-auth] [--version N]`.
  Accepts a **local file** or a **URL** (fetches `/.well-known/` discovery or the raw file).

### Conformance model (three states — required)
- **pass** — conforms to the targeted draft version.
- **fail** — violates a required rule of the targeted draft.
- **warn** — valid, but targets an **older draft version** than `latest` (`versionLatest` >
  `versionTarget`), OR uses a field the latest draft deprecates. The headline feature: flag a
  draft bump.

### Required behavior
- **Version-aware.** Each validator knows the schema shape *at a target version*. Pin initial
  draft versions explicitly (e.g. Web Bot Auth `-05`; reasonable current numbers for A2A / AP2 /
  UCP) — **confirm these with the user before shipping**, they're an open decision.
- **Least-fail fast.** Collect all findings, don't stop at the first hard violation, so CI shows
  everything at once.
- **Well-known discovery.** For A2A / UCP, fetch the well-known URL if a base domain is given,
  then validate.
- **Draft-caveat discipline.** The plan explicitly calls x402 v2, UCP, A2UI, AG-UI, PACTs
  **churning**. Validate against the *pattern* (typed schema + well-known discovery + signed
  mandate), not hard-coded wire fields. Where the draft is unsettled, mark the check `warn`
  by-default (not `fail`) and record the reason.
- **Offline-capable.** `run()` works on an in-memory JSON artifact (no network); fetching lives
  only in the CLI layer.

### Conventions
- TypeScript, `type: "module"`, Node ESM like the other packages (`packages/*/package.json`).
- `src/index.ts` re-exports the public surface: `run`, the draft registry, validator
  constructors.
- **No runtime deps on `@mwb/core` / budget / rails / identity / license.** Standalone tool.
- Keep the CLI small; parse args by hand (no new deps) to match the lean style.

### Tests (`src/**/*.test.ts` — already wired by root `vitest.config.ts`, `include` is
`packages/*/src/**/*.test.ts`)
- `drafts.test.ts` — each draft reports `latest`; target < latest flips to `warn`.
- `agentCardValidator.test.ts` — valid card passes; bad shape fails; malformed JSON reports.
- `ap2MandateValidator.test.ts` — guardrail present passes; missing fails; uncertain fields
  `warn`.
- `ucpCheckoutValidator.test.ts` — typed checkout passes; missing checkout fails.
- `webBotAuthValidator.test.ts` — directory + signed headers validate against the pattern;
  expired/revoked key or missing pubkey entry fails.
- `report.test.ts` — `run()` aggregates multiple drafts; a draft bump is `warn`, not `fail`;
  `status` precedence correct.
- `cli.test.ts` — parse a fixture JSON through `run()` end-to-end; `--json` output round-trips.

### Definition of done
- `packages/conformance` installs; `npm test` green; `npm run typecheck` green.
- CLI validates a local JSON file **and** a URL (well-known discovery) for all four domains.
- Each draft is versioned; a draft bump is surfaced as `warn` (never a false `fail`) — the point
  is to alert that a spec moved.
- **`npm run conformance` from the repo root runs** — this makes `.github/workflows/ci.yml`
  actually pass (fixing the Step-1 gap).

### Open decisions to confirm with the user before coding
- **Target versions** to pin as the initial `latest` set — recommend using the ones named in the
  plan (Web Bot Auth `-05` + reasonable current A2A/AP2/UCP numbers).
- **CLI fetch tooling** — Node global `fetch` (Node 18+) vs a pinned HTTP dep.
- **Fail-vs-warn policy** for unsettled drafts — recommend `warn`-by-default for the churning
  set named in the plan.
