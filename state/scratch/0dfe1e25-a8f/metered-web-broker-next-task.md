# Next task — metered-web-broker: from local-green to actually-shipped

## Context (verified 2026-08-28, memory-based; RE-VERIFY before trusting anything below)
The repo is at `/home/iconbaypark2900/dataScience/metered-web-broker` and is **read-only to you**. Do NOT edit files there. Copy the repo to a writable scratch location, work there, run the gate stack, and show exit codes.

Current state:
- Code + gates are GREEN: `npm ci` ok, `npm run typecheck` exit 0, `npm test` green (~58 passed per the repo's own commit log — the `metered-web-broker-talking-points.md` doc still says 38/38, treat that as stale), `npm run conformance <good>` exit 0 and `<bad>` exit 1.
- There is a local `master` branch, a single commit. **No git remote is configured** (`git remote -v` was empty on the last check). CI and `deploy.yml` both fire on `main`, so nothing has been pushed or deployed.
- The rails are in-memory / bridge-wrapped. There is NO live 402 → settle → content loop; `scripts/demo.ts` only renders outcomes locally.
- Conformance is still "Tier A": it re-validates the broker's OWN generated card against rules the broker wrote itself. It can't catch draft drift.
- `deploy.yml` line ~75 runs `docker compose -f docker-compose.yml up -d --no-deps broker`, which on a fresh host never starts Caddy (TLS on :80/:443).
- The broker assumes `settle ⇒ fulfilled`; no `finality` mode exists.

## The gate stack (run all four, all must be green, report each exit code)
1. `npm ci`
2. `npm run typecheck`   (exit 0)
3. `npm test`            (all pass)
4. `npm run conformance packages/conformance/fixtures/a2a-card.json a2a-card`  (exit 0), and the bad fixture must exit 1.

## Next steps — do in this order, each self-contained and gated
### P0 — unblock deployment (the actual blocker)
- Confirm `git remote -v`. If empty, the project has never been pushed. Decide branch strategy (push `master` as `main`, or make `main` the default) and get the remote URL from the user before pushing. **Do not push without the user confirming the origin.**
- Fix the `deploy.yml` `--no-deps` bug (line ~75): change `docker compose -f docker-compose.yml up -d --no-deps broker` to `docker compose -f docker-compose.yml up -d` (or `up -d broker caddy`).
- Re-run the gate stack (P0 is done only when the stack is still green after the edit).

### P1 — conformance Tier B (draft-drift detection)
- Make the harness fetch the LIVE spec (A2A `AgentCard` fields, AP2 mandate shape, UCP `/.well-known/ucp`, Web Bot Auth signature-directory) instead of relying only on a checked-in copy.
- Prove: one good artifact → exit 0, one bad artifact → exit 1, and pinning to an older draft flags drift on the good one.
- Closed by: the stack still green PLUS a documented `conformance <good>` → 0 / `<bad>` → 1 / drift-flag case.

### P2 — one REAL metered round-trip (the hardest, highest-value)
- Stand up a minimal origin that actually returns `HTTP 402 PAYMENT-REQUIRED` (or find one that honors x402). Do NOT assume field/header names — confirm by fetching the origin.
- Route it through the existing `PaymentRail` seam (`core/src/types.ts: quote → authorize → settle`) behind one of the rails; add replay/idempotency + a per-request nonce.
- Prove on real bytes: 402 → settle → same URL returns 200 content + `settlementRef` with `reconcile().ok === true`, AND a replayed proof is rejected (402/451).

### P3 — finality flag (unproven-economics safety)
- Before treating a settle as authoritative, honor `finality: 'settled-and-verified' | 'settled-claimed' | 'test'`. In `test`/`claimed`, fulfillment is marked conditional; only `settled-and-verified` (+ `reconcile().ok === true`) is true `fulfilled`.
- Closed by: a `test`-mode run producing a conditional (not auto-fulfilled) outcome, while `settled-and-verified` yields `fulfilled` only when `reconcile().ok === true`.

## Invariants the framework must hold across all of the above (keep these green)
1. One endpoint returns content + license + settlement receipt.
2. One terminal outcome per `fetchId` ∈ {fulfilled, blocked, payment-required, token-rejected} — never a generic error.
3. One ledger row + one meter charge per `fetchId` (idempotent).
4. The three failure classes reported separately, never collapsed.
5. Identity is best-effort, never a gate.
6. Settled payment is provable (`reconcile` needs a `settlementRef` + positive price on every fulfilled row).

## How to finish
Run the full gate stack (all four) AND prove P0's deploy fix + whichever of P1/P2/P3 you land, with exit codes shown. Write results back to scratch (not the read-only repo) and give a one-line status. Ask the user before pushing to any git remote.
