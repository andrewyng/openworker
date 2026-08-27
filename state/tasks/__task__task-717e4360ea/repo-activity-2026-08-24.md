# Repo activity brief — 2026-08-24 (window: last 24 hours)

**Headline — access regression:** the GitHub MCP gateway token that this job
verified as reaching the 9 private iconbaypark2900 repos on **2026-08-20** now
fails with `Authentication Failed: Bad credentials`. The shell path is
unchanged (no `GITHUB_TOKEN`, no `gh` auth). Consequence: **9 of 10 repos are
UNKNOWN again** (HTTP 404), reverting this automation to its pre-2026-08-20
blind state. This is a token/credential fix, not a research problem.

## iconbaypark2900/liaison-agentSystem — verifiable [ADJACENT]
- **0 commits in the last 24 hours.**
- Last commit: `WIP snapshot taken during migration to EVO-X2`, **2026-08-17
  02:56 UTC** — exactly 7 days old, so this repo has **now crossed into the
  stalled (7+ days) case**. First flagged by this automation on **2026-08-20**
  as "not yet stalled (3 days)"; it is now stalled.
- 🚩 **WIP-only message** (carried, unchanged): the last commit's entire
  content is a bare "WIP" snapshot of an EVO-X2 migration with no context.
  The EVO-X2 migration thread in this repo is silent for a week.

## Unverifiable this run (9 repos) — UNKNOWN, not "no activity"
dcode-stack, workstation-stack, ragtradesystem, sigma, qgg_research,
materialScience, setup, polymarket_btc, sourcelab_ai_production_scaffold all
returned 404 on the GitHub API. No commits in the window can be reported for
any of them. From FOCUS.md (rewritten 2026-08-24) for context only:

- **workstation-stack [ON-FOCUS]** — FOCUS.md reports its latest commit as
  **2026-08-21** and notes the repo "static for three days" while OpenWorker
  and opensciencelab moved fast. If that holds, it has had **no commits in the
  last 24h** and is on the edge of the stalled line (3 days as of today).
  Marking as **unverified** — could not query it.
- **dcode-stack [ADJACENT]** — FOCUS.md "Went quiet": last touched
  **2026-08-16**, carried from 08-22 as "still no commits". That makes it
  **~8 days untouched** today. Stalled, unverified via API.

## Stalled 7+ days (as of today)
- **liaison-agentSystem** — verified: last commit 2026-08-17.
- **dcode-stack** — per FOCUS.md: last commit 2026-08-16 (API-unverified).
- No other assessments possible this run.

## WIP-only commits
- **liaison-agentSystem**: latest commit is a bare "WIP snapshot…" (unchanged
  from 2026-08-20 report).

## What I did NOT reach
All 9 private repos — failed on credentials (MCP token bad, shell has no
token), not on budget. liaison-agentSystem was fully covered.
