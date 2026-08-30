# Build Prompt #2 — The Conformance Harness (Plan Phase 0 fast-win)

## Context
Metered Web Broker (`/home/iconbaypark2900/dataScience/metered-web-broker`), a neutral
one-contract service. The plan's **Phase 0** is a CI tool that validates an agent's
agent-protocol documents against *current drafts* and flags when a draft bumps. It's the
recommended first ship because it's low-risk, fast, distribution into the ecosystem, and it
keeps the broker current on the churn it depends on.

The broker's own domains (budget/rails/identity/audit) already exist in
`packages/*`. This package is a **separate concern**: it validates *third-party* artifacts
against external protocol drafts. It should have no runtime dependency on the broker's other
packages.

## What this validates (from the plan §5)
A tool that checks an agent's:
1. **A2A card** — discovery via `/.well-known/agent-card.json` (typed, well-known-URL discovery).
2. **AP2 mandate shape** — typed payment mandates with guardrails.
3. **UCP checkout** — typed checkout per `/.well-known/ucp`.
4. **Web Bot Auth signature** — the IETF-draft identity work (http-message-signatures directory
   + Ed25519 key). See the broker's own `packages/identity` for the *pattern* of signing /
   directory format to compare against — don't import it, mirror the shape.

## Interface to build
Create `packages/conformance` with:

- `src/drafts.ts` — a **registry of supported drafts** with: name, spec-url, and a `latest`
  version + `publishedAt` string. Each entry has a small `check(input)` that returns a list of
  findings. Drafts are versioned so the tool can (a) assert "conforms to latest N" and
  (b) warn "latest is M > N; your artifact targets an older draft".
- `src/validators/` — one validator per domain:
  - `agentCardValidator.ts` (A2A)
  - `ap2MandateValidator.ts` (AP2 mandate shape)
  - `ucpCheckoutValidator.ts` (UCP checkout)
  - `webBotAuthValidator.ts` (Web Bot Auth signature / directory)
- `src/report.ts` — `run(drafts, artifacts): ConformanceReport` where each draft yields
  `{ name, versionTarget, versionLatest, status: 'pass'|'fail'|'warn', findings: Finding[] }`.
- `src/cli.ts` — referenced by the root package.json script
  `"conformance": "tsx packages/conformance/src/cli.ts"`. Usage:
  `conformance [--json] <path|url> [--draft a2a,ap2,ucp,web-bot-auth] [--version N]`
  It accepts **either a local file** (reads the JSON) **or a URL** (fetches `/.well-known/`
  discovery or the raw file) and runs the selected validators, printing a table or JSON.

## Conformance model (the "three states" you must support)
- **pass** — artifact conforms to the targeted draft version.
- **fail** — artifact violates a required rule of the targeted draft.
- **warn** — artifact is valid but targets an **older draft version** than `latest`
  (`versionLatest` > `versionTarget`), OR uses an optional field the latest draft deprecates.
  This is the "flag when a draft bumps" behavior — the headline feature.

## Required behavior
- **Version-aware.** Each validator knows the schema shape *at a target version*. Keep the
  schema versions explicit (e.g. A2A `1.0`, AP2 `0.9`, UCP `0.4`, Web Bot Auth `-05`) so a draft
  bump is detectable.
- **Least-fail fast.** A validator should fail on the first hard violation but keep collecting
  the rest so CI shows all problems at once (findings list, not a single error).
- **Well-known discovery.** For A2A / UCP, first fetch the well-known URL
  (`/.well-known/agent-card.json`, `/.well-known/ucp`) if a base domain is given, then validate.
- **Draft-caveat discipline.** These specs are still churning (x402 v2, AP2, UCP, A2UI, AG-UI,
  PACTs). Validate against the *pattern* (typed schema + well-known discovery + signed mandate),
  not a hard-coded exact field set. Where the draft is unsettled, mark the check `warn`-by-default
  instead of `fail`, and record the reason so maintainers know it's draft-sensitive.
- **Offline-capable.** `run()` must work on a plain JSON artifact in memory (no network) so it's
  testable. Network fetching lives only in the CLI layer.

## Conventions to follow
- TypeScript, `type: "module"`, Node ESM like the other packages (`packages/*/package.json`).
- `src/index.ts` re-exports the public surface: `run`, the draft registry, validator constructors.
- **No runtime deps on `@mwb/core` / budget / rails / identity.** This is a standalone tool.
- Keep the CLI small: arg parsing can be done by hand (no deps) to match the lean style.

## Tests (`src/**/*.test.ts`, vitest — already wired by root `vitest.config.ts`)
- `drafts.test.ts` — registry: each draft reports `latest`; target < latest flips to `warn`.
- `agentCardValidator.test.ts` — valid card passes; missing/invalid `agent-card.json` shape fails;
  malformed JSON reports a finding.
- `ap2MandateValidator.test.ts` — a mandate with the right guardrails passes; one missing a
  guardrail fails; optional/uncertain fields produce `warn`.
- `ucpCheckoutValidator.test.ts` — typed checkout passes; missing checkout spec fails.
- `webBotAuthValidator.test.ts` — a directory + signed header block validates against the pattern;
  expired/revoked key, or missing public key entry, fails.
- `report.test.ts` — `run()` aggregates multiple drafts into a `ConformanceReport` and marks the
  draft-bump as `warn` not `fail`; `status` precedence is correct.
- `cli.test.ts` — parse a fixture JSON file through `run()` end-to-end; assert `--json` output
  round-trips.

## Definition of done
- `packages/conformance` installs, `npm test` green, `npm run typecheck` green.
- CLI validates a local JSON file and a URL (well-known discovery) for all four domains.
- Each draft is versioned; a draft bump is surfaced as `warn` (never a false `fail`), because
  the point is to alert you that a spec moved.
- `npm run conformance` from the repo root runs.

## Open decisions to confirm before coding
- **Target versions:** which concrete draft versions do we pin as the initial `latest`? The
  registry needs an initial set — recommend the versions named in the plan (Web Bot Auth `-05`,
  and reasonable current numbers for A2A / AP2 / UCP) but confirm before shipping.
- **Fetch tooling:** CLI uses Node's global `fetch` (available Node 18+), or a pinned HTTP dep?
- **Fail-vs-warn policy for unsettled drafts:** recommend `warn`-by-default for anything the
  plan explicitly calls churning (x402 v2, UCP, A2UI, AG-UI, PACTs). Confirm.
