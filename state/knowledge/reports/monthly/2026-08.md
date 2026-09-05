# Monthly review — 2026-08

Written 2026-09-01 by the "Monthly — themes and drift" automation. This is the
**first** monthly report; there is no prior file in `reports/monthly/`.

## Data availability — read this before the rest

This job is supposed to summarize a month from **4–5 weekly reviews**. It could
not. The `reports/weekly/` record has **exactly one file** for the whole of
August: `2026-W36.md` (the final week, of 2026-08-31). The weekly record
itself says so — "the first weekly report… `reports/weekly/` directory did not
exist this run." There are no `2026-W31`–`W35` files, so the 30-31 of July,
early-mid-August, and the weekly cadence that would have produced them are all
absent. Every week before W36 is only knowable through the `ingest/` ledgers,
the knowledge threads, and FOCUS.md's carried state — not through a weekly
review. The sections below are honest about which each line is drawn from.

---

## What shipped

Nothing "shipped" in the sense of a finished deliverable leaving the box this
month — but the month is book-ended by two things that *did* complete, and the
middle is a long run of build-without-close. Strictly, "worked on" ≠ "shipped":

- **agpack: the 5-step verifiable-agent harness reached a finished build state**
  (ON-FOCUS). Commit `60aaaf1` (2026-08-30) landed `tools/metered.py` — the final
  Step 5 (metered / pay-per-tool-call access). The `agpack` thread reports the
  suite grew 203 → 231 → 276 across the 08-30/31 sessions; the harness is
  described as *fully built* (Steps 1–5). **What did NOT ship:** the P0
  decision this project is named for — "ship it / confirm the git remote and
  deploy" — stays open. There is no deploy or external-consumer commit. The one
  honest flag carried by Step 4's own proof: it is not a *true* cross-engine
  test because the host has no wasmtime/wasmer. A fully-built, undeployed
  harness is the month's cleanest "built, not shipped" case. (source: W36
  weekly, `agpack` thread)

- **openworker: "carry the failed mornings" landed** (ON-FOCUS). `281990f`
  (2026-08-30) commits state that carries four days *including the mornings that
  failed* to the local-27B fleet — plus `641992f` (a place that knows where the
  user's screen is), `f2f77e3` (automations catch up one at a time),
  `e51280a` ("a folder dialog that cannot open says so" — an honest stop-marker).
  This is a shipped behavior, though not a *proved* one: the unattended-without-
  silent-failure question it answers is still open. (source: W36 weekly, openworker
  `git log`)

- **dcode-stack came back from quiet and shipped a fix for its own open question**
  (ON-FOCUS). 51 commits 08-26/27 (all iconbaypark2900), moving from "Went quiet"
  to Active, and ending the month by fixing the very bug FOCUS had posed: the
  "dead CUDA engine exits 0 so on-failure never restarts the brain" problem
  (`b7fa880`, `9bc3b30`, 08-27). That's a real outcome, not just a fix attempt.
  (source: W36 weekly, `dcode-stack` thread)

- **workstation-stack shipped a real secrets-protection repair** (ON-FOCUS).
  `639fd2d` (2026-08-29) and `468eae4` (08-28) begin actually protecting the
  seven LibreChat secrets that "existed on one disk and in no backup" — the
  mirror "never protected the secrets." A flag that became a fix. (source: W36
  weekly, `workstation-stack` thread)

- **Horizon 2027 Fellowship closed** — but only as a lost deadline, not a
  deliverable. The 2027 funding window (deadline 2026-08-30) lapsed with the
  2026-08-26/27/28 drafts never submitted; the 2027 window is gone. (source: W36
  weekly, `job-search` thread)

## Themes

- **Build-without-ship is the month's dominant pattern (ON-FOCUS).** Three of the
  four active "build" projects — agpack (harness fully built, P0 "ship" open),
  dcode-stack (came back, fixed its open question, then went quiet 08-27), and
  the agpack Conformance Tier B live-spec fetch — finished the *engineering* and
  stopped short of the *outcome*. openworker's "carry the failed mornings" is
  also shipped-but-not-proven. The pattern that recurs across the projects is
  "close the technical loop, leave the decision that would close the *project*"
  — the opposite of a month that shipped.

- **The automation that is supposed to see everything is blind (ON-FOCUS /
  infra).** Every project's "did it move in the last 24h" is reported as
  *unknown, not no-activity*: the GitHub MCP gateway has been unreachable since
  08-27 (`MCP error -32603: fetch failed` on all 10 repos), preceded by
  `Authentication Failed: Bad credentials` on 08-24. That's 4–5 consecutive
  runs of the repo-activity automation seeing 0 of 10 repos. This is not a
  project theme so much as the reason the record is thin — it is *why* August has
  only one weekly review, and why so many state claims rest on threads/ledgers
  instead of live verification. (source: W36 weekly,
  `github-github-list_commits` thread)

- **The job/grant pipeline is pure mass with no output (ON-FOCUS).** 10 daily
  `jobs-*.md` (08-16→08-31) all report drafts; ~106 "draft" mentions across the
  set; zero applications sent. The cost is now concrete: Horizon 2027 closed
  unmoved, and the freshly-open MATS Residency window (best live fit, closes
  31 Oct) is at risk of the same. A month of accumulation and no send. (source:
  W36 weekly, `job-search`/`funding` threads, `ingest` ledgers)

## Drift

FOCUS.md's state of 2026-08-31 (a rewritten snapshot) tells a story of drift that
runs *through* August:

- **Expected focus vs. what actually moved.** The 08-31 FOCUS (which is the
  state *into* this month) already flags the "center of gravity" shifting to
  agpack. What actually happened: agpack's *build* shipped in the final days
  (30th–31st), but its P0 decision did not. dcode-stack and workstation-stack —
  both listed as "went quiet" in the 08-24 lineage — came **back** mid-month
  (dcode 08-26/27, workstation 08-28/29) and each produced a real fix. So the
  month drifted *toward* re-igniting two quiet projects, not just toward agpack.

- **Where drift cost something.** The real divergence is the opposite direction
  from activity: the schedule kept running (ingest, breakage, jobs, corpus,
  repo-activity) yet its *output* never turned into a decision. repo-activity
  is blind (see Theme 2), the jobs automation only ever drafts, and the KB corpus
  job stores papers that are never queried. That is the "drift is sometimes the
  reason nothing shipped" case — high automation volume, low decision yield.

- **openEvolve / openScienceLab.** Parked before August even starts (last
  touch 2026-08-24, repo path gone — only knowledge threads remain). It did not
  participate in August at all; it's a theme of *absence*, not drift.

## Stop doing

1. **The job/grant pipeline, as it currently runs.** A month of 10 daily drafts
   and zero sends; Horizon 2027 is the standing warning of what that costs. This
   automation's output never appears in a decision. Until the loop is "draft →
   send," every daily run is a recurring cost with a 0% conversion rate.

2. **Letting the GitHub gateway stay down.** repo-activity has seen 0 of 10
   repos for 4–5 straight runs and the "did it move last 24h" answer for all
   projects is "unknown." One token/infra fix restores 10/10 coverage. An
   automation whose output is systematically unavailable is a cost; flagging it
   every run instead of fixing it is the worse option.

3. **Reading the weekly ladder that isn't there.** This job's budget is the
   weekly reviews, and there is only one for the whole month. Either the weekly
   "derive/roll-up" automation that should be producing `2026-W31`…`W35` needs
   to run, or this monthly job must accept it is summarizing the *tail* of the
   month from ledgers and threads, not a full weekly series. Right now it is the
   former pretending to be the latter.

## Still unexamined

*Method: an item qualifies only if it has appeared in 2+ of the records I read
(this run's W36 weekly, plus the threads/ingest it cites) and still shows no
state change. The W36 weekly already ran this grepping on 3+ appearances; I carry
its three forward.*

1. **TheRock #7051 — gfx1151 AsyncEventsLoop 100%-CPU spin (ADJACENT / dependency
   watch).** Present, unchanged (`state=open`), in 2026-W36 weekly *and* the
   `local-stack-breakage`/`dependency-breakage-watch` threads across 8+ breakage
   briefs (08-18→08-31). A second independent report, legacy-rocm-build #6522,
   corroborates it. First flagged 2026-08-18 — a full month of ignoring it.
   Meaning: it's a tracked upstream issue, so inaction is defensible, but a
   month-long "still open" on a symptom that spins a GPU to 100% deserves one
   decision — act on the adjacent #5284 fixed-wheel note, or formally drop it.

2. **The "ship agpack" decision (ON-FOCUS, borderline "still open" → "still
   unexamined").** It's been named P0 for a month and the only thing that has
   happened is the build completing. It has survived being ignored across the
   08-31 FOCUS, W36 weekly, and the agpack thread without a decision. Meaning:
   either it was never actually a "must ship this month" decision, or it's a
   decision being avoided under the label of "P0." Either way it is the single
   clearest closed-item this monthly report can name.

3. **dcode-stack re-quiet (ON-FOCUS).** It came back hard mid-month then went
   quiet again after 08-27 (four days by W36). It reappeared as Active only to
   become "still open, last touched 08-27." Meaning: its July-quiet pattern is
   recurring, and each revival is real work followed by abandonment — worth a
   note that its "came back" has not yet produced a shipped, *sustained* outcome.

---

*Note to next run (2026-09): this file is the seed of the monthly record. If the
weekly ladder is producing W37+ by then, September's monthly should read from
those — the August figure is unusable as a template because it has one week, not
four. If the weekly automation is still not producing files, that automation is
the real monthly story, not any project.*
