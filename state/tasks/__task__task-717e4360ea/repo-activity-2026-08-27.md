# Repo activity brief — 2026-08-27 (window: last 24 hours)

**Headline — access regression (again, but worse):** the GitHub MCP gateway is
**completely unreachable this run**. Every `github-list_commits` call failed with
`MCP error -32603: fetch failed` (connection-level), on `liaison-agentSystem`
(retried 3×) and `dcode-stack`. This is a different failure than the
`Authentication Failed: Bad credentials` seen on **2026-08-24** and **2026-08-26**
— those were bad-token errors on a reachable gateway; today the gateway itself
returns no response at all. **Consequence: 0 of 10 repos verifiable today.** No
commit data was obtainable, so nothing in the last 24h can be reported from
primary source.

## ON-FOCUS

- **OpenEvolve / openScienceLab** — `~/openworker-workspace/opensciencelab`, not
  in this repo list, so not touched here. Per FOCUS.md (08-24): Phase 2 closed,
  Phase 3 in flight (item 1 SMILES→PDBQT conformer in question, item 2 Vina
  adapter shipped as `2529fa0`, items 3–4 PySCF/CHGNet still open). No way to
  verify via GitHub API today.
- **OpenWorker (the app)** — commits on 08-22/23 noted in FOCUS.md; latest
  commit date not re-checkable.

## ADJACENT (carried forward, all API-unverified this run)

- **liaison-agentSystem [ADJACENT]** — **stalled, unverified today** (first
  flagged 2026-08-20). Last commit was already **2026-08-17** (a bare "WIP
  snapshot" of an EVO-X2 migration) as of the 08-24 brief, so it has been quiet
  for ~10 days. Cannot confirm current state — gateway down.
- **dcode-stack [ADJACENT]** — per FOCUS.md "Went quiet," last touched
  **2026-08-16**; ~11 days as of today. Unverified (gateway down).
- **workstation-stack [ON-FOCUS]** — FOCUS.md's latest commit was **2026-08-21**
  ("static for three days"); if that still holds it is now ~6 days quiet, near
  the stalled line. Unverified today.

## Unverifiable this run (all 10 repos) — "unknown", not "no activity"
dcode-stack, workstation-stack, ragtradesystem, sigma, liaison-agentSystem,
qgg_research, materialScience, setup, polymarket_btc,
sourcelab_ai_production_scaffold — all returned `fetch failed` on the GitHub MCP
gateway. No commits in the window can be reported for any of them.

## Stalled 7+ days
- **liaison-agentSystem** — last commit 2026-08-17 (per 08-24 brief, not
  re-confirmed; gateway down).
- **dcode-stack** — last commit 2026-08-16 (per FOCUS.md, not confirmed).
- All others: unverifiable.

## WIP-only commits
- **liaison-agentSystem**: bare "WIP snapshot" commit, unchanged since 2026-08-24
  brief — still no context. (Not re-verified.)

## What I did NOT reach
The GitHub MCP gateway itself — `fetch failed` on every attempt, a gateway-level
outage distinct from the prior credential failures. No per-repo budget
truncation occurred; this is a hard access failure. No new commits can be
reported until the gateway is reachable (or a token/shell path works — shell
still has no `GITHUB_TOKEN`/`gh` auth).

**Net:** nothing verifiable this run. Last successful brief: **2026-08-26**
(same access issue, "fetch failed"). Prior state carried forward verbatim.
