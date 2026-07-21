# UI Refresh — Implementation Kickoff (start here)

Entry point for the agent(s) implementing the GUI/messaging refresh. Read this first, then the specs.

## Read in this order
1. [`UX-DECISIONS.md`](UX-DECISIONS.md) — the *why* + the binding UX decisions. **Do not change
   documented UX without the owner's (Rohit's) sign-off.**
2. [`UI-REFRESH-SPEC.md`](UI-REFRESH-SPEC.md) — the *what/how* (full-stack), with build phases 1–5.
3. [`FAKE-SLACK-SPEC.md`](FAKE-SLACK-SPEC.md) — **build this first** (a separate sub-agent); the
   integration tests depend on it.
4. [`UI-REFRESH-VERIFICATION.md`](UI-REFRESH-VERIFICATION.md) — the test cases + acceptance gate.
5. Visual reference: [`../ui-mocks/redesign.html`](../ui-mocks/redesign.html) (open in a browser; top
   switcher: session / integrations / persona).

## Environment & conventions
- **Worktree:** `/Users/rohit/fleet/ro4d/aisuite-personas` · **branch:** `platform/personas`
  (~64 commits ahead of `origin/main`). **Local-only — do NOT push; Rohit pushes.**
- **Commits:** author **and** committer `Devika <dr.drp8226@gmail.com>`, **no co-author trailers**
  (project's explicit choice for this work). Commit per phase; keep PR/commit descriptions short
  (one paragraph; detail in the body only if mechanical).
- **Tests:** `./.runtests.sh platform/tests/<file> -q` (uses the agent-platform venv). Baseline is
  green **except 3 pre-existing SDK-import failures** (`test_anthropic_provider`,
  `test_gemini_provider`, `test_provider_router`) — leave them; don't "fix" by mocking SDKs.
- **Run the server** (manual/visual): `SLACK_API_URL` (FakeSlack) +
  `PYTHONPATH=platform /Users/rohit/fleet/ro4d/aisuite/platform/.venv/bin/python -m coworker.server.run --port 8765`.
  Dev GUI: `cd platform/surfaces/gui && npm run dev` (serves on :1420, defaults to backend 8765).
  Frontend gate: `npx tsc --noEmit` clean + `npm run build`.
- **No backward compatibility** — pre-launch, no users. Migrate on-disk state by deletion if needed.
- **Visual review before PR:** show GUI changes in the running dev app for Rohit's review before
  raising a PR (don't open a PR off green tests alone for UI work).
- **Progress/handoff:** append a one-line entry per working session to
  [`IMPLEMENTATION-LEDGER.md`](IMPLEMENTATION-LEDGER.md) and commit it (that commit is the handoff).
  That ledger is the **shared running log for the whole `platform/personas` branch** — it already
  tracks the (completed) Personas and Messaging streams; the UI Refresh is the current stream. Label
  your entries **"UI-Refresh Phase N"** — the bare "Phase N" in older entries refers to the
  *Personas* effort (its Phases 0–3 are done) and is unrelated to this spec's Phases 1–5.

## Suggested agent topology
- **Agent A (first):** FakeSlack (`FAKE-SLACK-SPEC.md`) + its self-tests + the one adapter
  `SLACK_API_URL` override. Self-contained; unblocks everything else.
- **Then per phase (SPEC §9):** one focused agent per phase (1 registry/contract → 2 structured
  messages → 3 connection hierarchy → 4 persona/session surfaces → 5 frontend polish). Each: code +
  the phase's VERIFICATION tests green, `tsc`/build clean, commit, ledger line. Phase 3 (hierarchy) is
  the load-bearing data-model change — review it before building 4–5 on top.

## Open decisions baked into the spec (change before building if you disagree)
- Structured messages keep *framed* text for the model + a display-only `source` sidecar (stripped
  before the provider).
- Placeholder `available:false` descriptors for not-yet-shipped recommended connectors (GitHub,
  Datadog, …) so the UI renders their badge + Connect.
- Persona default connections seed from manifest `recommends` where `tier:core` AND connected.
- One production change for FakeSlack: a `SLACK_API_URL` base-URL override on the Slack adapter.
