# FOCUS — week of 2026-08-24

Derived from evidence: commits in the last 14 days, live sessions, and what the
automations keep producing. Rewritten Monday 2026-08-24 by the
"Focus — derive from the week's work" automation.

**State change since last run (2026-08-22):** openEvolve has moved. It was Phase 2
in flight; Phase 2 is now **closed** (4/4 items, 71 tests, per `PHASE2.md`), and
work is in **Phase 3** — scored today (builder session of 231 messages,
"Phase 3, item 1 — RDKit descriptor bank + the SMILES->PDBQT…"). The open
question "what replaces the surrogate-offline docking proxy" is **resolved**:
the AutoDock Vina adapter shipped as `2529fa0` (session of 322 messages,
"Phase 3 item 2 (the Vina adapter, 2529fa0) made a known Phase [issue]…").

## Active

1. **OpenScienceLab / openEvolve — Phase 3 (Scoring & QM layer)** —
   `~/openworker-workspace/opensciencelab` (status: `PHASE2.md` closed, `PHASE3.md`
   "item 2 done").
   *Open question:* item 1 is in flight — can the SMILES→PDBQT conformer path even
   work on this host, where meeko, OpenBabel and RDKit are not installed (that
   "deliberately out of scope… none are installed on this host" is written into
   `PHASE3.md`)? And then: PySCF DFT (item 3) and CHGNet (item 4) at their
   documented tiered costs.
   *Evidence:* 5 builder sessions of 231, 322, 367, 274 and 54 messages on
   08-22/23/24 all working Phase 2→Phase 3 items; PHASE3.md item 2 shipped
   (`2529fa0`); Phase 2 closed with 71 tests.

2. **OpenWorker — the agent app itself** — `~/openworker`.
   *Open question:* the fleet's silent-failure problem is being closed from the
   engine side — with scheduled runs now claiming their persona's MCP tools and the
   engine recording stopped/killed runs honestly, can the local-27B fleet run
   unattended with no loss?
   *Evidence:* 20+ commits on 08-22/23, all authored by iconbaypark2900
   (persona MCP grants for scheduled runs, honest run-stop markers, brain recall,
   rail redesign, cross-machine MCP over SSH).

3. **Job search and funding pipeline** — automations only, no repo
   (drafts in `~/OpenWorker/__task__task-c6cf366b90/drafts/`).
   *Open question:* unchanged from last week — is this pipeline producing anything
   *sent*, or only accumulating drafts? Still no evidence of a single application
   going out.
   *Evidence:* 37 draft .md files accumulated; daily `jobs-YYYY-MM-DD.md` through
   08-21 and running (sessions "Job matches + tailored drafts" 08-22 and 08-23);
   weekly grant digests.

4. **workstation-stack — the machine's deployed stack** — `~/workstation-stack`.
   *Open question:* does the committed stack still describe what is running on this
   box — it has been static for three days while the app around it moved fast.
   *Evidence:* 19 commits in the window, all iconbaypark2900, but latest
   **2026-08-21** (evo-x2 split from spark, MCP gateway revival, image pinning).
   Going quiet — still in the 14-day window, on the edge.

5. **Research corpus — quantum, ML, recursive self-improvement** — automation-
   produced, landing in `~/OpenWorker/__task__task-1b2c4d3f13/` (papers-*.md) and
   the Qdrant `default` collection via the weekly arXiv→Qdrant corpus job.
   *Open question:* none named — this is a standing watch, but it is the strongest
   ADJACENT thread feeding the other entries.
   *Evidence:* papers-2026-08-22 and papers-2026-08-23 both produced this week;
   research sessions of 82 and 42 messages (08-22/23).

## Open questions

- Can the SMILES→PDBQT step be done at all on this host (meeko/OpenBabel/RDKit none
  installed) so Phase 3 item 1 finishes?  *(from the 2026-08-24 session title and
  PHASE3.md)*
- Will the PySCF DFT (item 3) and CHGNet (item 4) adapters land at the costs the
  tiered-scoring contract says they should cost?  *(from PHASE3.md "items 1, 3, 4
  still open")*
- With scheduled runs now granted their persona's MCP tools and honest stop-markers
  in the engine, does the fleet survive unattended on the local 27B with no silent
  failures?  *(from openworker commits 08-23: "automations: give a scheduled run the
  MCP tools its persona declares", "engine: tell the truth about why a run stopped")*
- Is the job/grant pipeline converting to sent applications, or only to drafts?
  *(carried forward — still open, 37 drafts and counting)*

## Went quiet

- **dcode-stack** — last touched **2026-08-16** ("Scaffold this repo… stop `project`
  from mislabelling foreign projects"). Carried from last week's file; still no
  commits since 2026-08-22 either. Eight days untouched and counting.
- **Pre-open market brief — Sigma system** — automation disabled; last run
  **2026-08-15** (unchanged from last week's file).

## Parked

*(This section is yours. The weekly job carries it forward verbatim and never adds to it.)*

---
*Note for the derive job: `~/llama.cpp` and `~/LibreChat` show commits in the window
but every author is upstream (Gerganov, Avila, et al.). Both are tracked clones, not
work in progress — count authorship, not commits.*
