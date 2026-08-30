# metered-web-broker — Builder Prompt: Branch B (P1–P4)

## Where the repo actually stands (verify this first — do not assume)

Branch A is considered **done** per the last session's record, but this box may
not match that record. **Prove it before writing code.** The repo at
`/home/iconbaypark2900/dataScience/metered-web-broker` is **read-only** in this
environment — you cannot run `npm` inside it. So:

1. **Copy the repo** into a read-write scratch dir (or get read-write access to
   it). The whole point of the read-only discipline is that you never edit the
   real source; you prove changes compile and pass in a *copy* that you then
   mirror into the read-only repo.
2. Install deps: `npm ci` (lockfile is the source of truth).
3. Run the **full gate stack** and save the output as evidence:
   - `npm test` — expect **38/38**.
   - `npm run typecheck` — expect **exit 0** across all 7 packages.
   - `npm run conformance` on the good and bad fixtures — expect
     **PASS (exit 0)** on the valid card and **FAIL (exit 1)** on the invalid
     one.
4. **Gate before P1:**
   - If **all three are green** → proceed to P1 (Branch B).
   - If **anything is red** → Branch A is *not* actually done. Stop building P1–
     P4, treat this as Branch A, and fix the regression first. Report what
     regressed and why before touching P1.

Do not skip step 3/4. The economics of this project hinge on "green before you
build on top of it."

## Context — what Branch A already delivered (the thing we are building on)

- The **self-402 settlement rail** (`packages/rails/src/selfsettle.ts`) now
  compiles and passes: a metered origin's `402` → settlement → `200` loop is
  wired on a spec-agnostic rail, with replay/nonce guards.
- **Conformance** (`packages/conformance/`) validates terminal-class, identity,
  license, settlement, and contract-shape — wired into CI. Positive fixtures
  PASS, negative fixtures FAIL.

The load-bearing thesis is **not** "we built a rail." It is: **we proved a real
metered round-trip settles safely — and nothing is treated as done settlement
until reconciliation is actually confirmed.** Branch B hardens exactly that.

---

## P1 — Wire ONE real 402 → settle → 200 round-trip on real bytes (the bearing thesis)

This is the thesis, end to end. Scope it as a single, fully-green test that
proves the loop on **real bytes, not an assumed shim**.

- **Verify the origin is real — do not assume.** Confirm the metered origin
  actually returns a `402` on the wire. Read the PaymentRail seam in the code and
  in the tests; if any test *assumes* a 402 without exercising a real 402
  response, that test is asserting the thesis by fiat. Replace the assumption
  with a real 402 round-trip.
- **Do not hardcode `x402` / any particular 402 spelling.** The rail must be
  spec-agnostic: it parses whatever the origin's metering contract actually
  returns, not a token the test happened to name `x402`. If the code reads a
  literal header/field to *detect* the 402, generalize it to a small,
  documented seam (a capability/`x-*` list or a parser) — and the test must
  exercise it against a 402 that is *not* pre-named `x402`.
- **Replay / nonce guards.** A captured or duplicated settlement request must
  be rejected as a replay. Confirm the guard exists on the rail; if it is
  thin or absent, close it: per-request nonce/timestamp binding, dedup against
  already-settled requests, and a clear failure (no double-settle).
- **Prove it on real bytes.** The P1 test drives a real 402 through the rail →
  settlement → a `200`, and asserts the observable terminal outcome **without**
  calling settlement "done" yet (that is P2).

Definition of Done for P1: one green test reproduces the full 402 → settle → 200
loop on real bytes, the 402 detection is spec-agnostic (no hardcoded `x402`),
and a replayed settlement request is rejected.

---

## P2 — Add the finality flag, in the *same* P1 pass

P1 and P2 together close the actual thesis ("real metered fetch" + "safe
economics") in a single green test. Do not ship P1 and defer P2.

- **`test/claimed` is a *conditional* outcome, not a *fulfilled* one.** A test
  that has merely *claimed* settlement is not done.
- **Only `reconcile().ok === true` ⇒ `fulfilled` is true.** The terminal
  outcome of `fulfilled` must be gated strictly on reconciliation reporting
  `ok === true`. Any other value (`false`, missing, error) ⇒ not fulfilled.
- **"Don't bet real settlement on an assumption."** If the P1 test marks
  fulfillment on anything other than an explicit `reconcile().ok === true`, it is
  betting settlement on an assumption. Fix it: the fulfilled branch fires only
  when reconciliation positively confirms.

Do not add a separate test that weakens P1. Fold P2 into the P1 round-trip: the
same real 402 → settle → reconcile → fulfilled flow, where fulfilled is
conditional on the reconcile flag.

Definition of Done for P2: the terminal `fulfilled` result is true *iff*
`reconcile().ok === true`; `claimed` alone never yields `fulfilled`. This lands
inside the P1 test — P1 + P2 = one green test proving the thesis.

---

## P3 — Upgrade conformance to Tier B (live-spec fetch + drift detection)

P1/P2 are the economics proof; P3 hardens that the conformance layer can't drift
silently against the living spec it is supposed to enforce.

- **Live-spec fetch.** Conformance should validate against the *current*
  published spec, not a frozen local copy it silently falls back to. Wire the
  live-spec fetch (with a bounded, cached fallback) so drift is observable.
- **Drift detection.** When the live spec diverges from the pinned conformance
  expectations, conformance must *report the drift* (and fail closed if
  configured to), not pass against a stale baseline.
- Keep conformance **standalone** (it must not import broker internals — the
  CI contract). `verbatimModuleSyntax` + `noUncheckedIndexedAccess` strict
  config stays intact.

Do not touch P1/P2 economics for P3. P3 is the conformance layer hardening.

Definition of Done for P3: conformance resolves the live spec, detects and
reports drift, and fails closed on unconfgurable drift — all while remaining
standalone and strictly typed.

---

## P4 — Deploy on a real VPS + fix the deploy.yml bug

P4 is the production proof. It is last because it depends on P1/P2 economics and
P3 conformance being solid, and it is the highest-risk step.

- **Fix the `deploy.yml` bug.** Inspect `.github/workflows/deploy.yml` and any
  deploy manifest; fix whatever is broken (a bad step order, an unset secret,
  a wrong path/branch, a missing env) so the workflow is *actually* deployable,
  not just syntactically valid.
- **Deploy on a real VPS.** Stand the broker up on a real host (not a local
  dev server) and prove the live 402 → settle → 200 loop end-to-end over the
  network. Capture the run as evidence.
- Keep the deployment reproducible: the VPS run should be driven by the same
  gate stack (test + typecheck + conformance) that passed in CI, so "deployed"
  means "green and reachable," not "up but unverified."

Definition of Done for P4: `deploy.yml` is fixed and the workflow runs; the
broker is live on a VPS; the real 402 → settle → 200 loop is proven end-to-end
over the wire; and the deployed build passed the full gate stack.

---

## Scoping guardrails

- **P1→P2 are one test, one pass.** Do not split them into two files or two
  "half-thesis" results.
- **Freeze the branch-A contracts** while building P1–P3: the self-402 rail, the
  PaymentRail seam, the conformance validators, and the 7-package typecheck. If
  P1/P2/P3 *require* touching a contract, do it deliberately and say so.
- **No new terminal outcomes, no new rails, no economics changes** outside
  P1/P2. P3 is conformance-only. P4 is deploy-only.

## Definition of Done (whole prompt)

- P1: real 402 → settle → 200 loop proven on real bytes, spec-agnostic detection,
  replay/nonce guards.
- P2: `fulfilled` true **iff** `reconcile().ok === true`, folded into the P1 test.
- P3: live-spec fetch + drift detection, fail-closed, still standalone/strict.
- P4: `deploy.yml` fixed, broker live on a VPS, loop proven over the wire, full
  gate stack green on the deploy.
- Every change is proven in a writable copy, mirrored into the read-only repo,
  and re-verified — no unverified "looks green" claims.

## Reporting (end with this)

- Exact per-package test counts + full gate-stack output (test/typecheck/
  conformance), each line green or red with the offending line pasted.
- For P1/P2: which test proves the thesis, and that the 402 was real (not
  pre-named), replay was rejected, and fulfilled is conditional on the reconcile
  flag.
- The single recommended next session and the one riskiest open item.
- If you regressed or reopened a branch-A gate, say so plainly and why.
