# FOCUS — week of 2026-08-31

Derived from evidence: commits in the last 14 days, live sessions, and what the
automations keep producing. Rewritten 2026-08-31 by the
"Focus — derive from the week's work" automation.

**State change since last run (2026-08-24):** the live center of gravity has
moved. `agpack` — the "build" that the 08-30 sessions are almost entirely about
(8 sessions, 189–330 messages each) — is now the top project. `dcode-stack`
(the serving "brain" proxy, decode_proxy) has come back from quiet with 51
commits 08-26/27. openEvolve/openScienceLab has the opposite move: its repo path
is gone, only knowledge threads remain.

## Active

1. **agpack — the "build" (verifiable-agent harness)** —
   `/home/iconbaypark2900/dataScience/agpack`
   *Open question:* P0 "unblock deployment" is "the real blocker" — can we confirm
   the git remote/repo and ship it? (from 08-30 sessions). Downstream: does
   Conformance Tier B (live-spec fetch + draft-drift) hold, and how legible does
   it need to get (Direction A — "external-facing")?
   *Evidence:* 8 sessions 08-30 of 189–330 messages titled "Continue the agpack
   build", "P1 — Conformance Tier B", "Direction A — External-facing", "Step 5
   (metered access) prompt", "P0 — unblock deployment", "the immediate next step:
   ship it"; 2 commits authored by iconbaypark2900 — import from openworker-
   workspace 08-19, then `agpack/tools/metered.py` step-5 metered access 08-30.

2. **OpenWorker — the agent app itself** — `/home/iconbaypark2900/openworker`
   *Open question (carried):* with scheduled runs claiming their persona's MCP
   tools and honest stop-markers, does the local-27B fleet run unattended with no
   silent failure? *New:* state now carries four days including failed mornings;
   one place knows the user's screen.
   *Evidence:* 40 commits in the 14-day window, all iconbaypark2900, latest
   2026-08-30 ("state: carry four days, including the mornings that failed").

3. **dcode-stack — the machine's serving brain (decode_proxy)** —
   `/home/iconbaypark2900/dcode-stack`
   *Open question:* how do two engines (vLLM on :5100 + on-demand llama.cpp) share
   one proxy endpoint routed by model name, and why does a dead CUDA engine exit 0
   so on-failure never restarts it?
   *Evidence:* 51 commits in the 14-day window, all iconbaypark2900, all on
   08-26/27 — this was "went quiet (08-16)" in the 08-24 file; it has clearly come
   back. vLLM vs llama.cpp prefill speedtest, KV pool sizing, auto-classifier,
   on-demand llama.cpp.

4. **workstation-stack — the machine's deployed stack** —
   `/home/iconbaypark2900/workstation-stack`
   *Open question (carried):* does the committed stack still describe what is
   running on this box? *New:* a 08-29 commit flags that seven LibreChat secrets
   existed on one disk and in no backup — the secrets-protection model is under
   repair.
   *Evidence:* 10 commits in window, all iconbaypark2900, latest 2026-08-29
   ("Seven LibreChat secrets existed on one disk and in no backup"); came back
   from the 08-21 quiet.

5. **Job search & funding pipeline** — automations only, no repo (drafts in
   `~/OpenWorker/__task__task-c6cf366b90/drafts/`)
   *Open question (carried):* unchanged since 08-24 — is this producing anything
   *sent*, or only accumulating drafts? No evidence of a single application going
   out.
   *Evidence:* daily `jobs-YYYY-MM-DD.md` through 2026-08-28 and running; weekly
   grant digests.

## Open questions

- Can we unblock P0 (confirm git remote/repo, deployment) and ship the agpack
  build? *(from 08-30 session titles — "P0 — unblock deployment" / "the immediate next step: ship it")*
- Does Conformance Tier B (live-spec fetch + draft-drift) hold, and how legible
  does the harness need to get for others? *(agpack sessions 08-30)*
- How do vLLM and llama.cpp share one proxy routed by model name, and why does a
  dead CUDA engine exit 0 so on-failure never restarts it? *(dcode-stack commits 08-26/27)*
- With MCP grants + honest stop-markers in place, does the local-27B fleet
  survive unattended with no silent failures? *(carried — 08-23 openworker commits)*
- Is the job/grant pipeline converting to sent applications, or only to drafts?
  *(carried — still 37+ drafts, last draft 2026-08-28)*

## Went quiet

- **openEvolve / openScienceLab** — last touched **2026-08-24** (`PHASE3.md`
  "item 2 done"). State change: the repo path `~/openworker-workspace/opensciencelab`
  no longer exists anywhere on disk — only knowledge threads under
  `~/OpenWorker/knowledge/threads/` remain. Carried from 08-24's Active #1; the
  work has effectively parked with the codebase.
- **dcode-stack** — *moved to Active this run* (was "went quiet" last week at
  08-16 — see Active #3).
- **liaison-agentSystem** — stalled, last commit **2026-08-17** (a bare "WIP
  snapshot"); ~14 days. Carried from the 08-24 brief; GitHub API was unavailable
  to re-confirm this run (no `.git` reachable at maxdepth 2 via shell).
- **Pre-open market brief — Sigma system** — automation disabled; last run
  **2026-08-15**. Carried unchanged.

## Parked

*(This section is yours. The weekly job carries it forward verbatim and never adds to it.)*

---
*Note for the derive job: `~/llama.cpp` (and the `*master`/`*gb10`/`-qwen4exp`
clones) show commits in the window but every author is upstream (Gerganov,
Avila, etc.). Tracked clones, not work in progress — count authorship, not commits.*
