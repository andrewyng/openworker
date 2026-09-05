# Repo activity brief — 2026-09-02 (window: last 24 hours)

**Headline — gateway reachable, but credentials broken: 0 of 10 repos verifiable.**
Today's failure mode **changed** from the prior four runs. The GitHub MCP gateway is
**reachable again** — every `github-list_commits` call returned a response rather than
a connection-level failure. But the response is `MCP error -32603: Authentication
Failed: Bad credentials` on **all 10 repos** (including `liaison-agentSystem`, the
public repo). So the gateway came back online after the `fetch failed` outage, but with
a token that now fails completely. No commit data was obtainable, so nothing in the
last 24h can be reported from primary source — all 10 results are "unknown", not
"no activity". Shell path is unchanged (no `GITHUB_TOKEN`, no `gh` auth), so there is
no fallback to primary source on this run.

**Contrast with the last four runs (the important continuity):**
- **2026-08-27, 08-28, 08-31, 09-01** — gateway **unreachable**, `fetch failed` on all
  10 (a network/connectivity outage). This run I did not have to make 12 separate calls;
  the gateway refused every one.
- **2026-09-02 (today)** — gateway **reachable**, `Authentication Failed: Bad
  credentials` on all 10 (a credential/token failure). This is the same class as
  **2026-08-24**, but worse: on 08-24 the public `liaison-agentSystem` still returned
  commits; today even it fails, so the token is now fully dead rather than partially
  valid.

**Budget:** all 10 repos were queried and returned. The "12-call budget" was not a
truncation constraint — every repo was reached, and each failed identically at the
gateway. No per-repo truncation; this is a credential failure, so there are no repos
"I did not reach" in the budget sense.

## ON-FOCUS (carried forward, API-unverified this run)

These are the live projects named in FOCUS.md (week of 2026-08-31). The gateway is
reachable today but the token is bad, so — as on 08-27 through 09-01 — I cannot
re-verify their current commit state. They are carried, not rediscovered; nothing here
is reported as a fresh move in the last 24h.

- **agpack — the build (verifiable-agent harness)** — on-disk at
  `/home/iconbaypark2900/dataScience/agpack`. Latest verifiable commits per the
  08-30 session record: import from openworker-workspace 08-19, then
  `agpack/tools/metered.py` (Step 5 metered access) 08-30. Suite is GREEN (276
  passed, per memory). **State:** cannot confirm whether it moved in the last 24h —
  token bad. Open question unchanged: unblock P0 (confirm git remote/repo, deploy) and
  ship.
- **dcode-stack — the machine's serving brain (decode_proxy)** — 51 commits in the
  14-day window per FOCUS.md, all on 08-26/27 (vLLM vs llama.cpp prefill speedtest, KV
  pool sizing, auto-classifier, on-demand llama.cpp). **State change:** FOCUS.md moved
  this from "Went quiet" back to **Active** this week (was flagged quiet on 08-16).
  **Cannot re-verify the last-24h move** via GitHub today; recorded last activity was
  08-27, five days ago.
- **workstation-stack — the machine's deployed stack** — 10 commits in window per
  FOCUS.md, latest 2026-08-29 ("Seven LibreChat secrets existed on one disk and in no
  backup"; secrets-protection model under repair). Came back from the 08-21 quiet.
  **Cannot re-verify the last-24h move** via GitHub today; recorded last activity
  08-29, three days ago.
- **OpenEvolve / openScienceLab** — `~/openworker-workspace/opensciencelab`, not in
  the 10 tracked repos. Per FOCUS.md: repo path no longer exists on disk — only
  knowledge threads remain; work effectively parked. No GitHub path to verify.

## ADJACENT (carried forward, API-unverified this run — cap not an issue; all
are carried, not new)

- **liaison-agentSystem [ADJACENT]** — **stalled, unverified today.** First flagged
  2026-08-20; last commit was already **2026-08-17** (a bare "WIP snapshot" of an EVO-X2
  migration) as of the 08-24 brief. As of today ~16 days quiet — firmly stalled. Today
  it fails with `Bad credentials` like every other repo, so this is not new stall
  information — its silence pre-dates the current token failure.
- **Sigma (ragtradesystem / sigma branch) — pre-open market brief** — automation
  disabled; last run **2026-08-15** (per FOCUS.md). Unverified via API today.
- **qgg_research, materialScience, setup, polymarket_btc** — none verifiable via API
  today. No state recorded from prior briefs that would let me assert any is stalled
  vs. active; they remain "unknown" (not "no activity").

## Unverifiable this run (all 10 repos) — "unknown", not "no activity"
dcode-stack, workstation-stack, ragtradesystem, sigma, liaison-agentSystem,
qgg_research, materialScience, setup, polymarket_btc,
sourcelab_ai_production_scaffold — all returned `Authentication Failed: Bad
credentials` on the GitHub MCP gateway. The gateway responded today (unlike the
`fetch failed` outage of the prior four runs), so this is a credential/token failure,
not a connectivity outage. No commits in the window can be reported for any of them.

## Stalled 7+ days (carried, not re-confirmed)
- **liaison-agentSystem** — last commit 2026-08-17 (per 08-24 brief; not
  re-confirmed — token bad).
- **dcode-stack** — last *verified* commit 2026-08-27 per FOCUS.md (a return from the
  08-16 quiet); five days quiet, not yet 7+, so not stalled on this measure — but the
  most recent move cannot be re-confirmed today.
- **workstation-stack** — last verified commit 2026-08-29 per FOCUS.md; three days
  quiet, not yet 7+.

## WIP-only commits
- **liaison-agentSystem**: bare "WIP snapshot" commit — unchanged since 2026-08-24
  brief, still no context. (Not re-verified — token bad.)

## What I did NOT reach
None on a budget basis — all 10 repos were queried. The one thing not reached is a
working GitHub credential: the gateway is back online today, but its token fails
`Authentication Failed` on all 10 repos (including the public `liaison-agentSystem`).
This is the same failure class as 2026-08-24, but the token is now fully dead rather
than the partial access that let the public repo through then. No new commits can be
reported until the token is refreshed — the shell still has no `GITHUB_TOKEN`/`gh`
auth, so there is no fallback. This is a credential problem, not a research finding.

**Net:** nothing verifiable this run. Qualitatively different from the prior four runs
(08-27, 08-28, 08-31, 09-01): the connectivity outage cleared, and the gateway is
reachable again, but a fully-broken token now blocks all 10 repos (08-24 was the same
error class but let the public repo through, so at least liaison was readable then).
Last truly verifiable brief: **2026-08-24** (liaison-agentSystem only, via the
reachable-but-mistokened gateway, with all 9 private repos 404ing). On-disk state of
agpack / dcode-stack / workstation-stack (per FOCUS.md) and liaison-agentSystem
stalled carried forward verbatim.

---

## 2026-09-02 — second scheduled run (confirmation, state unchanged)

**Same as the prior 2026-09-02 brief. Nothing changed.** All 10 `github-list_commits`
calls returned `MCP error -32603: Authentication Failed: Bad credentials` — the gateway
is reachable today but its token is dead on all 10 repos, including the public
`liaison-agentSystem`. No commit data obtainable; every repo is "unknown", not "no
activity". Shell has no `GITHUB_TOKEN`/`gh` auth, so no fallback.

Net: no verifiable moves this run. The 09-02 gateway state (reachable, bad token, 0/10
readable) is unchanged, and on-disk state of agpack / dcode-stack / workstation-stack
(per FOCUS.md, week of 2026-08-31) and the stalled liaison-agentSystem carried forward
verbatim from the earlier 09-02 run and the 08-31 brief. Last truly verifiable brief:
**2026-08-24**.
