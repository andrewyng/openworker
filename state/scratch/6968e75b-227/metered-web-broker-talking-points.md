# Metered Web Broker — From Skeleton to Real Project

*Prepared 2026-08-27. One file, four talking points. Each point has: (1) what "completely done" means, (2) the actions/tasks an agent (running the local **ornith-1.5-35b** in this read-only-workspace workflow) would execute, (3) the verification that closes it.*

## Standing context (needed to read everything below)

- **Repo (read-only):** `/home/iconbaypark2900/dataScience/metered-web-broker` — a npm workspaces monorepo, 6 packages (`core`, `audit`, `budget`, `gateway`, `identity`, `license`), vitest + tsx + typescript.
- **Work style:** the broker repo is **read-only** to the agent. New/changed files are written to **primary scratch** (`/home/iconbaypark2900/openworker-tasks/<session-id>`) and handed over as ready-to-paste blocks, or the agent requests write access to the repo first. No `git commit` unless explicitly permitted.
- **Current state:** orchestrator done, 38/38 tests green, `npm run typecheck` green. The only open CI gate is `packages/conformance/` (doesn't exist yet). Outward-facing pieces (real rail, real identity, real RSL fetch, deployment) are **in-memory simulations** — nothing external is wired.

The model that will execute the tasks below is the local **ornith-1.5-35b**. Because it runs locally and is a mid-size frontier, treat every task as **write-then-verify**, scoped tightly, and avoid assumptions about spec field names until checked against a live source.

---

## Talking Point 1 — Conformance ("when is it truly done?")

### What "completely done" means
Two tiers. Green CI is only Tier A. Tier B is the real finish.

- **Tier A (mechanical — not "done"):** `packages/conformance/src/cli.ts` exists, runs against a valid card (`exit 0`) and a broken card (`exit 1`), and is wired into `.github/workflows/ci.yml` line 20. **This is not complete** — a fixture that always passes is vacuous.
- **Tier B (actually done):** the harness validates a **real upstream artifact** against **current draft field names**, and **detects version drift** — when a spec bumps its `draft`/version number, the harness flags it red. Then:

  **done ⟺ (gate green) AND (it catches a real version drift) AND (it rejects a genuinely broken artifact).**

- Validators must check **4 real artifacts**, not just the broker's internal shapes:
  1. **A2A** — `/.well-known/agent-card.json` shape.
  2. **AP2** — payment mandate shape (guardrails, settlement scheme, flow types).
  3. **UCP** — commerce checkout shape.
  4. **Web Bot Auth** — the IETF-draft signature (`http-message-signatures-directory`).

### Actions / tasks for the agent (ornith-1.5-35b)
1. **Explore (read-only).** Read the broker contract surface to anchor validators: `packages/gateway/src/broker.ts` (the 4 terminal classes + outcome shape), `scripts/demo.ts` (the canonical contract in action), `packages/core/src/types.ts` (`FetchOutcome`, `LedgerEntry`, `PaymentRail`, `License`), `packages/audit/src/dashboard.ts` (what the receipt must show).
2. **Investigate upstream (check live sources — do NOT assert field names from memory).** Confirm what each artifact currently looks like:
   - Search web for the current `agent-card.json` fields (A2A spec, latest draft).
   - Search web for the current AP2 mandate/checkout field set.
   - Search web for the current Web Bot Auth signature draft (`draft-meunier-web-bot-auth`, field names, `iat`/`expires` constraints).
   - Record findings to memory so a future session doesn't re-derive them.
3. **Scaffold the package (write to scratch, hand off).** Create:
   - `packages/conformance/package.json` (`@mwb/conformance`, `"type": "module"`, no script that collides with the root).
   - `src/index.ts` — re-exports.
   - `src/registry.ts` — register a validator by name; look up by name.
   - `src/validators.ts` — 4 validators, each `{ok, message}` or throws `ConformanceError`.
   - `src/run.ts` — `run(card, name)` collects results + `pass`; `report(result)` renders green/red.
   - `src/cli.ts` — reads `process.argv.slice(2)` = `[fixturePath, contractName]`, resolves validators by name, runs, `process.exit(0)` on pass / `process.exit(1)` on failure.
   - `fixtures/a2a-card.json` — a card the 4 validators PASS on (real-ish shape matching a spec's current form).
   - `fixtures/a2a-card-bad.json` — a card that FAILS (invalid terminal class, or missing settlementRef).
   - `tsconfig.json` — `{ "extends": "../../tsconfig.base.json", "include": ["src"] }`.
4. **Wire into CI (hand off the diff).** Add the `conformance` step to `.github/workflows/ci.yml` line 20: `npm run conformance packages/conformance/fixtures/a2a-card.json a2a-card`.
5. **Prove Tier B (verify, read-only run if permitted; else hand the commands to the user).**
   - `tsx packages/conformance/src/cli.ts packages/conformance/fixtures/a2a-card.json a2a-card` → exit 0.
   - `tsx packages/conformance/src/cli.ts packages/conformance/fixtures/a2a-card-bad.json a2a-card` → exit 1.
   - **Version-drift proof:** edit the fixture to the wrong `draft` version (e.g. bump a spec number) and confirm it goes red. This is what makes "done" real — a drift-detector, not a static list.
6. **Clean up.** Ensure existing `npm test` still 38/38, `npm run typecheck` still 0. Keep `verbatimModuleSyntax` intact (`import type` for type-only imports).

### Closed by
`npm test` 38/38 **AND** typecheck 0 **AND** good-fixture exit 0 **AND** bad-fixture exit 1 **AND** a version-drift edit flips the result red. Record the drift-detection result to memory.

---

## Talking Point 2 — Deploy Somewhere Open-Source and Cheaper than Vercel

### What "completely done" means
The broker is **one long-running HTTP endpoint** (`fetch(url, budget)` as a tool). It does not need Vercel's edge/auto-scale/serverless model — it needs a **plain Node process behind HTTPS**, fully open source. "Done" = the repo has a one-command deploy path to a low-cost host, fully reproducible, no proprietary platform lock-in.

- **Deploy target:** a small VPS (Hetzner ~€4/mo, DO droplet ~$5/mo, or Oracle Cloud "Always Free") running `docker compose` behind **Caddy** (free Let's Encrypt, one-line TLS).
- **Reproducible:** `Dockerfile` + `docker-compose.yml` + a deploy workflow. A fresh box should bring the whole stack up from `git clone`.

### Actions / tasks for the agent (ornith-1.5-35b)
1. **Read the current package layout (read-only).** Confirm the build/test commands that must work inside the container: `npm ci`, `npm run typecheck`, `npm test`, `npm run demo`, `npm run conformance …`. Note the Node version (CI uses v22; the local runtime is v20 — pick one and pin it in the Dockerfile).
2. **Write the `Dockerfile` (to scratch, hand off).** Multi-stage: build stage (`npm ci --include=dev` + `npm run typecheck`), runtime stage (`node:22-alpine` or debian-slim), `npm ci --omit=dev`, copy built workspace, expose the port, `CMD ["npm", "run", "serve"]` (the server entrypoint from Talking Point 5, or `npm run demo` as a smoke placeholder until the entrypoint exists).
3. **Write `docker-compose.yml` (to scratch, hand off).** Service: broker on the Node image. Proxy: **Caddy** with auto-HTTPS via `caddy_upstream` or a `Caddyfile` template. Healthcheck hitting the `/health` endpoint. Volumes only for logs/state, never code.
4. **Write a deploy workflow (to scratch, hand off).** GitHub Actions: on push to main → build Docker image, push to a registry, SSH `docker pull` + restart on the VPS. This keeps the whole pipeline open source (no Vercel/Netlify gate).
5. **Add the server entrypoint (see Talking Point 3 & 5).** A tiny `packages/gateway/src/server.ts` that exposes `POST /fetch` — this makes the container actually serve the broker, not just smoke-test with `demo`.
6. **Document the runbook.** A `DEPLOY.md` describing: VPS provisioning, `git pull`, `docker compose up -d`, domain + TLS, the one deploy command, and how to roll back.

### Closed by
`docker compose up -d` brings the broker up behind Caddy on the VPS; a curl to the HTTPS `/fetch` endpoint returns a terminal outcome. Documented so a fresh machine can repeat it.

---

## Talking Point 3 — Wire One Real Rail (agent → real metered fetch)

### What "completely done" means
The broker already has a **spec-agnostic** `PaymentRail` interface — wiring a rail is 100% an adapter-implementation task, not an orchestrator change. "Done" = an agent calls `Broker.fetch(realUrl)` and gets **content from a real origin that genuinely demands payment**, a **real settlement**, and **one ledger row**, with the 4-terminal-class invariant intact.

- **Pick the simplest real scheme first: self-hosted 402** (no blockchain, no external facilitator — just a gate + your own ledger). x402 facilitator = second (needs an external `/verify`+`/settle` + nonce/replay window). Cloudflare Pay Per Crawl = third (vendor-gated).
- The `OriginClient.fetch` already returns `{httpStatus, headers, body}` — wire it to a **genuine `fetch(url)`** against a real metered origin. **Do NOT assert specific x402 test URLs from memory** — verify the scheme live first (see Tasks).

### Actions / tasks for the agent (ornith-1.5-35b)
1. **Verify the scheme is live (web search — do not assume).**
   - Search: "which origins currently honor x402 / HTTP 402 metered requests" and "Cloudflare Pay Per Crawl test origin". Record which public origins return a real `402`/`PAYMENT-REQUIRED` today. This is the single most important pre-flight — the whole step depends on a real metered target.
2. **Read the seam (read-only).** Read `packages/core/src/types.ts` (`PaymentRail`, `PaymentRequest`, `RailAuthorization`, `RailSettlement`, `Quote`), `packages/gateway/src/broker.ts` (how it calls `rail.quote` → `rail.authorize` → `rail.settle` → `origin.fetch`), and `packages/rails/src/rails.ts` (the existing `InMemoryRail` — the reference implementation of the interface).
3. **Scaffold the adapter to scratch (hand off).** Create `packages/rails/src/x402SelfHosted.ts` implementing `PaymentRail`:
   - `quote(req)` → returns a real `Quote`.
   - `authorize(req, quote)` → returns `RailAuthorization` (the real settlement payload).
   - `settle(req, auth)` → performs the real settlement (self-402: charge the local ledger and return a `settlementRef`; x402: build the `PAYMENT-SIGNATURE`, call the facilitator `/verify`/`/settle`).
4. **Add the protocol flow for the chosen scheme.**
   - **x402 path:** origin returns `402` + `PAYMENT-REQUIRED` → build the `PAYMENT-SIGNATURE` from the quote → call facilitator `/verify`+`/settle` → on success, **retry the origin with settlement headers**. That loop is the whole thing.
   - **Self-402 path:** on `fulfilled`, charge the local ledger → return `settlementRef` + `amountMicros`.
5. **Add guardrails that only matter against real rails** — this is the part that separates a simulation from production:
   - **Replay protection:** nonce + `iat` replay window (see core `time.ts`).
   - **Idempotency key** on settlement so a retry can't double-charge.
   - **Nonces** embedded in the signature.
6. **End-to-end test (verify with a real target).** Agent → `Broker.fetch(realUrl)` → real `402` → real settlement → content + license + receipt + one ledger row. The 4-terminal-class invariant must still hold. Add a real-rail test to `packages/rails/*.test.ts` (or a new `packages/gateway/src/rails.test.ts`).
7. **Budget still gates first.** Keep the in-memory budget gate while bootstrapping; swap the wallet in later.

### Closed by
`npm test` 38/38 **plus** a new test that drives a **real** `402`→settlement→content round-trip through a real (or locally-emulated real) origin, with replay/idempotency guards asserted. Typecheck still 0.

---

## Talking Point 4 — A Universal Framework with First-Principle Elements

### What "completely done" means
Because metered-web economics and settlement finality are unproven, and the specs (x402/AP2/UCP) churn, the framework must **separate invariants (that never change) from variables (that are each a pluggable adapter)**. "Done" = an agent can change **any** spec version or **any** settlement substrate by writing an adapter, with zero changes to the immutable core — because the core is the 6 first-principle invariants, the variables are each behind a stable interface, policy is data, and conformance is a drift detector.

- **Do not hardcode "settle ⇒ fulfilled."** Finality is unproven, so model it as a flag (`finality: 'pending' | 'settled'`) and let the **budget gate decide** whether `fulfilled` requires `final`. You can then run the economics experiment regardless of whether on-chain finality is instant — and the audit surface shows the gap.

### The 6 first-principle invariants (immutable core — never change)
1. A resource has a **price**.
2. Access requires **consent-to-pay, then settlement**.
3. The payer is an **attested, non-replayable entity** (identity).
4. The permitted actions form a **license** (what you may do with the content).
5. Every transaction produces an **auditable receipt**.
6. The payer has a **budget cap** (guardrail).

### The 4 variables (each behind a stable interface — swap freely)
- **Payment protocol**: x402 header vs Cloudflare PPC vs self-402 → `PaymentRail`.
- **Settlement substrate**: USDC/Base, stablecoin, off-chain → adapter field (and the `finality` flag, Talking Point 4.1).
- **Identity token**: PACT vs Web Bot Auth vs self-issued JWT → `KeyRing`/adapter.
- **License format**: RSL draft versions, JSON-LD shapes → `parseLicense`/adapter.

### Actions / tasks for the agent (ornith-1.5-35b)
1. **Audit the current core for invariants (read-only).** Map each of the 6 invariants to the code that encodes it — `FetchOutcome` (receipt, 4 terminal classes, failure classes), the `budget.preflight` gate, `attributionNotice`/license obligations, the `settlementRef`/`priceMicros` fields, the `identity` claim. Produce a short "invariant → code location" table so you know what is safe to touch.
2. **Add the `finality` invariant as a flag (write to scratch, hand off).** Extend `FetchOutcome`/`RailSettlement` with a `finality: 'pending' | 'settled'` (or `final: boolean`) field, and make the **budget policy decide** whether a `pending` settlement may still count as `fulfilled`. This is the single change that lets you *test unproven economics safely* — record the gap instead of assuming finality.
3. **Make policy declarative (data, not code).** Convert the in-code budget/policy choices (`packages/budget/src/policy.ts`) into a **policy DSL** — a data file (JSON/YAML) describing ceilings, allow-listed hosts, required license clauses, and identity requirements — with a validator + serializer. Then policy changes survive spec churn without code changes.
4. **Verify each variable is already a pure adapter (read-only + web check).**
   - `PaymentRail` (Talking Point 3), `KeyRing` (identity adapter), `parseLicense` (RSL adapter) — each takes the external thing as input.
   - For the **license adapter**, re-verify current RSL field names via web search before trusting `parseLicense` — because the format churns.
5. **Make conformance the drift detector (Talking Point 1).** When the upstream spec moves a field name, the conformance harness goes red — so you *know* before you build against a dead field. The "unproven + churning" problem is handled by making drift detectable, not by hardcoding drafts.
6. **Write the framework contract doc.** A short `DOCS/framework.md` stating the 6 invariants, the 4 variables, the declarative policy layer, and the drift-detector — i.e. the one-sentence design so a future session (or a different agent) can extend without breaking the core.

### Closed by
- Changing a settlement substrate or spec version requires **only** a new adapter, with **zero** changes to the immutable core (`FetchOutcome`, the 4 terminal classes, the audit row, the budget gate).
- The `finality` flag demonstrably lets a `pending` settlement be recorded and the audit surface shows it as non-final (an experiment, not an assumption).
- A declarative policy file governs ceilings/allow-list/required-clauses and is validated before use.
- Conformance drift detection flips red on a spec-version change.

---

## The whole path, one line each
1. **Conformance:** scaffold `packages/conformance`, wire it into CI, and prove it catches a version drift **and** rejects a bad artifact.
2. **Deploy:** one-command `docker compose` behind Caddy on a small VPS; documented so a fresh box brings the whole stack up.
3. **One real rail:** implement a real `PaymentRail` (self-402 first, then x402), wire a genuine `fetch(url)`, add replay/idempotency guards, and round-trip a real `402`→settlement→content.
4. **Universal framework:** split the 6 invariants (immutable) from the 4 variables (pluggable), add a `finality` flag, make policy declarative, and let conformance be the drift detector.

All four are *extensions* of the existing, spec-agnostic orchestrator — none require rewriting the contract. The broker already proves the *design*; the work ahead is making the outside world talk to it.

*Verification checklist is per talking point; the master gate is: `npm ci` → `npm run typecheck` → `npm test` (38/38+) → `npm run conformance …/a2a-card.json a2a-card` (exit 0), plus a live `/fetch` round-trip and a VPS bring-up in `DEPLOY.md`.*
