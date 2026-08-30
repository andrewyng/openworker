# Task prompt — metered-web-broker: what's next (ordered)

**Repo (READ-ONLY):** `/home/iconbaypark2900/dataScience/metered-web-broker`
**Stage all files in primary scratch** (`/home/iconbaypark2900/openworker-tasks/<session-id>`) and
hand over as ready-to-paste blocks — or request write access to the broker repo first. Do **not**
`git commit`.

## Where this sits (verified against the repo on 2026-08-28, not from memory)

The "four talking points" (`metered-web-broker-talking-points.md`) describe a *finish* state.
The repo today has the **scaffolding** for all four, but **only the scaffolding**. Concretely, by
reading the actual source (not by trusting the talking points):

- **`npm ci` → green. `npm run typecheck` → exit 0. `npm test` (vitest) → 38/38.**
  These were the prior session's result; verify again before you rely on them.
- **Conformance** (`packages/conformance/`) exists with 4 validators (identity/license/
  settlement/contract-shape) + good + bad fixtures, wired into `ci.yml`. **But** it re-validates the
  broker's *own* generated card against rules the broker wrote itself. It cannot fetch a live spec,
  cannot read real field names, and cannot flag version drift. That is **Tier A** in the talking
  points, **not** the finish. (No `conformance/*.test.ts` exists — the package added test coverage.)
- **Deploy** files exist and are internally coherent: `Dockerfile` (`packages/gateway/docker/`,
  single Node process, `tsx src/server.ts` at runtime), `docker-compose.yml` (broker + Caddy/TLS),
  `Caddyfile` (reverse-proxy + auto-TLS), `DEPLOY.md`, and `.github/workflows/deploy.yml` (build+push
  to GHCR + SSH-deploy). **Nothing has been proven on a live VPS.** One defect is real: `deploy.yml`
  runs `docker compose up -d --no-deps broker`, so on a fresh host **Caddy/HTTPS (80/443) never
  starts** — it must be `docker compose up -d` (or `broker caddy`).
- **One real rail** is **not done.** `buildRail()` returns `InMemoryRail` by default. `BridgeRail`
  + `BridgeClientShape` exist in `packages/rails/src/rails.ts` as a wire shape for an external bridge,
  and `server.ts` supports a real origin via `ORIGIN_URL` (proxies the settled request to the real URL
  over HTTP) — but neither has run against any real metered origin.
- **Finality** is **not present.** There is no `finality` symbol anywhere (`grep` is empty). The
  broker mechanically treats `settle ⇒ fulfilled` — it settles, fetches, and records fulfilled. That
  bakes the unproven metered-web economics into the terminal path.

**Bottom line:** the CI gate is green and the deploy scaffold is coherent, but the *thesis* —
a real metered round-trip over the wire, and a system that won't bet real settlement on an
assumption — is unproven. The work ahead is to make those true, not to add code that looks like it.

---

## Priority 1 (do this first): wire ONE real 402 → settle → 200 round-trip

This is the load-bearing thesis. Do not skip it and go straight to finality.

**"Done" means:** an origin that truly returns `HTTP 402`, the broker arranging payment through a
real rail, the origin releasing content on proof-of-settle, and a settlement receipt that
reconciles — on **real network bytes**, not an in-memory stub.

**Tasks (local ornith-1.5-35b agent):**
1. **Verify a real metered origin first — do not assume.** Find an origin that honors x402 / HTTP
   402 (or stand up a minimal origin that returns `402 PAYMENT-REQUIRED`). Do **not** hard-code
   assumed x402 URLs or header names — confirm them by *fetching the origin* and reading what it
   demands. Record the real `PAYMENT-REQUIRED`/`PAYMENT-SIGNATURE` semantics you observe.
2. **Read the seam only.** `PaymentRail` is in `packages/core/src/types.ts` (`quote → authorize →
   settle`); the broker calls only those three. The new rail implements exactly that interface.
   Look at `packages/rails/src/rails.ts` (`InMemoryRail`, `BridgeRail`) for the shape — don't
   invent fields.
3. **Implement the rail as a thin adapter** around a real bridge (or a minimal local 402 origin for
   the first proof). Keep spec field names **opaque inside `payload`** — build against the
   interface, not a draft's field names (draft-caveat).
4. **Replay/idempotency guard.** The rail must reject a reused proof and require a per-request
   nonce/monotonic sequence. The broker already caches terminal outcomes by `fetchId`
   (`broker.ts:381-394`), so mirror that intent at the settle/proof layer.
5. **Round-trip a real 402 → settle → 200.** Exercise a URL that returns 402, watch the broker pay,
   retry, and get 200 + content + a `settlementRef` that reconciles (`reconcile()` in
   `audit/src/ledger.ts`), and assert a **replayed proof is rejected**.

**Closed by:** a **live** test (not a unit stub) asserting: origin returns 402 → broker settles →
same URL returns 200 content → `settlementRef` present and `reconcile().ok === true` → a replayed
proof returns 402/451. Record raw request/response bytes. Verify via `server.ts`'s
`ORIGIN_URL` path (`packages/gateway/src/server.ts`) with a real origin, and show the response.

---

## Priority 2 (do in the same pass, same test file): add the finality flag

Finality is the move that lets you *exercise the unproven economics safely instead of betting real
settlement on an assumption*. Do it in the same commit as P1 so a single test proves both.

**"Done" means:** `settled ⇒ fulfilled` is **no longer assumed**. A `test`/`claimed` run produces a
**conditional** outcome even though settlement happened; only `settled-and-verified`
(`reconcile().ok === true`) is treated as the true terminal `fulfilled`.

**Tasks:**
1. **Lock the six immutable invariants as *enforced* behavior, not prose** — the four terminal
   classes (`fulfilled/blocked/payment-required/token-rejected`, never generic error), one row + one
   charge per `fetchId` (idempotent — see the `outcomes`/`recordPaid once` in `broker.ts`), the
   three failure classes reported separately, identity **best-effort, never a gate**, settled payment
   **provable** (`reconcile` needs a `settlementRef` + positive price on every fulfilled row).
2. **Add the flag** — a `finality` mode on the broker/budget path, e.g.
   `finality: 'settled-and-verified' | 'settled-claimed' | 'test'`. In `test`/`claimed`, the
   fulfillment is recorded and marked **conditional** (content retrieved but settlement not yet
   provably final), so unproven economics run without real charge. Only `settled-and-verified`
   (`reconcile().ok === true`) is the true `fulfilled`.
3. **Wire it from policy, not code.** `BudgetPolicy` (`packages/budget/src/policy.ts`) is already
   data-driven — confirm/extend it so the finality mode is config, so the engine *reads* it.
4. **Prove agnostism** — run the six invariants against at least two distinct rails/price models and
   show they hold while variables change; add a `test`-mode finality case yielding a **conditional**
   outcome (proving settle no longer auto-implies fulfilled).

**Closed by:** `npm test` **38/38+ including** the finality cases where a `test`/`claimed` run yields
a conditional outcome (NOT auto-`fulfilled`) and a `settled-and-verified` run yields `fulfilled`
**only when `reconcile().ok === true`**.

---

## Priority 3 (after P1–P2 are proven live): upgrade conformance to Tier B

Right now the gate is Tier A: green CI, but it only re-validates the broker's own card against the
broker's own rules. It can't catch *drift* when a spec draft bumps a field.

**"Done" means:** the harness fetches the **live spec** (not a checked-in copy), reads real field
names, validates an artifact against them, and flags version drift.

**Tasks:**
1. Read the contract surface (`packages/conformance/src/validators.ts`, `types.ts`, `run.ts`,
   `cli.ts`) and the artifact types in `packages/core/src/types.ts` + `broker.ts`'s `FetchOutcome`.
2. **Web-search live spec field names — do not assume them.** A2A `AgentCard` fields, AP2
   authorization/mandate shape, UCP `/.well-known/ucp` commerce object, Web Bot Auth
   signature-directory structure. Record exact URLs + draft versions.
3. **Extend the package** so a validator can (a) fetch the live spec or accept a pinned draft +
   version, (b) validate the artifact, (c) report drift vs. the pinned draft.
4. **Prove it catches drift** — one artifact that satisfies the current draft (harness **accepts**),
   one that violates a field (harness **rejects**), and pinning to an older draft **flags drift** on
   the good artifact. Commit both as fixtures.

**Closed by:** a *new* `conformance <good-artifact> <name>` → exit 0 alongside `conformance
<bad-artifact> <name>` → exit 1, with the pinned draft version and live-spec URLs recorded in the
harness output.

---

## Priority 4 (last): deploy on a real VPS + fix the deploy bug

Deploy scaffold exists; the finish is "a bare VM, freshly provisioned, serves `/fetch` after
`git pull && ./deploy.sh`."

**Tasks:**
1. **Fix `deploy.yml`** — change `docker compose up -d --no-deps broker` to `docker compose up -d`
   (or `broker caddy`) so HTTPS starts on a fresh host. (Small, real, do it first.)
2. **Bring it up on a real small VPS** (a $4/mo box). Show container logs + `GET /health` → 200 + a
   **real `/fetch` round-trip** (which needs P1). Reproducible by `./deploy.sh` or the workflow.
3. Document the health + rollback path in `DEPLOY.md` if it's missing; keep every secret out of the
   image (`IDENTITY_*`/origin creds at runtime only).

**Closed by:** a fresh host, after bring-up, answers `GET /health` with 200 and serves a real
`/fetch` (P1 + P2). Show the command + response.

---

## Verification discipline (applies to every priority)
- The repo is **read-only** to the agent and there's **no shell on it**. Produce the artifact in
  scratch, wire it in, **run the gate, show the exit code**. Don't hand-wave "should be green."
- Every delivered file is a complete ready-to-paste block, labeled with exact repo path and
  **NEW** vs **EDIT**, grouped by package.
- Don't add code that *looks* done. A deploy that never started, a rail that never hit a real 402,
  a conformance step that only re-validates its own card — flag those as scaffolding, not finish.

## Recommended order
**P1 → P2 together (one test file) → P3 → P4.** P1 and P2 close the actual thesis (a real metered
fetch + a system that won't bet real settlement on an assumption) in a single pass. P3 hardens the
drift detection. P4 is the production proof. If you only have time for one thing, it's P1.
