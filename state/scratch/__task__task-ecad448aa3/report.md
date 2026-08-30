# KB Ingest Run — 2026-08-28

**Job:** Knowledge base — ingest yesterday's findings
**Status:** Completed. 13 new points stored; collection now at **21**. 0 left behind budget.

## What was done
- Located 5 fresh automation outputs from the last 24h (today is 2026-08-28): papers, breakage,
  jobs, morning-briefing, repo-activity. Read all in full.
- FOCUS.md (week of 2026-08-24) carried forward: ON-FOCUS on OpenEvolve Phase 3 QM/chemistry,
  the unattended-fleet question, the job/grant pipeline, and the workstation stack; ADJACENT capped at 3.
- Dedup via Qdrant HTTP `POST /collections/default/points/query` (cosine 0.72) followed by
  **identifier-aware re-check** — see the important caveat below.
- fastembed 0.8.0 (`BAAI/bge-small-en-v1.5`, 384-dim) for embedding. MCP qdrant tools not in this
  session's toolset, so the direct HTTP path was used.

## ⚠️ Important dedup caveat (this run's real lesson)
My first pass ran all 13 candidates through the mandated find-first dedup at cosine 0.72 and
reported 10 as "duplicates." They were **false positives**, not real dups. The collection was
only 8 points (from 08-27), so each candidate top-matched a *different* stored point on 0.72–0.84
cosine — OSTRE paper vs the stored 2608.25896 Trotterization paper, the 4 job candidates vs the
stored UnitedHealthcare job, unsloth vs the stored ollama release. **None shares an identifier
with a stored point.** I re-ran with identifier-aware dedup (skip only if a live hit contains my
candidate's arXiv id / release tag / company+role) and **stored all 10**.

Net: on a small collection cosine 0.72 is too loose — for THIS job, identifier match is the
reliable near-duplicate test. No real duplicate was ever stored, and nothing false was lost.

## Stores this run (13; collection 8 → 21)

ON-FOCUS:
- OSTRE — Find Rows & Decode for quantum expander codes (arXiv:2608.27211) — QEC *decode*-cost
  algorithmic result, the load-bearing half of the OpenEvolve Phase 3 QM-budget question.
- Evolution Strategies beat GRPO via broader reasoning coverage (arXiv:2608.27351) — ES ≠ cheaper
  GRPO; sparse, forgetting-free, lower-memory reasoning post-training.
- Circuit Condensation (arXiv:2608.27254) — post-train to concentrate a behavior's circuit
  8.1× smaller (up to 316×); the weight update, not the search, does the shrinking.
- AgentFold (arXiv:2608.26747) — real-world analogue of OpenEvolve's self-improving-agent loop
  for molecular model design; structured memory (successes AND failures) is the loop precondition.
- HarnessLens (arXiv:2608.27311) — budget-aware harness-evolution loop; structure around the
  model is the load-bearing lever.
- unsloth v0.1.804-beta shipped (adapter-additive, no new gfx1151 break) — first new unsloth
  release stored this job; re-affirmed standing reinstall guardrail.
- UnitedHealthcare Sr AI/ML Engineer (NEW top match, 08-28 run 1).
- Laurel Applied AI Engineer (strong reach, 7+yr gate).
- CHEQ AI Engineer (near-1:1 GCP stack, almost certainly in-office Tel Aviv).
- Horizon AI-for-Science Fellowship — 2026 cycle closed, watch next cycle (best match for Phase 3).

ADJACENT (cap 3):
- LLMs design near-optimal OR algorithms (arXiv:2608.27296).
- US weighing 7.5% tariff on Chinese goods over "overcapacity" (morning briefing).
- Cambridge ERA:AI Fellowship Winter 2027 reopened.

## State changes carried forward
- **Horizon 2027 Fellowship** — still OPEN, deadline **Aug 30 2026, 11:59pm AoE** (~1 day out from
  08-28). Time-critical — closes tomorrow. Still drafts-only, zero applications sent.
- **Ollama** — 0.33.1 was stored 08-27; 08-28 confirms prerelease phase is *over* (0.33.1 final +
  0.33.2-rc0). Affects LibreChat. No new store.
- **unsloth** — new release first stored this run (v0.1.804-beta).
- **repo-activity gateway** — GitHub MCP gateway unreachable **3rd consecutive run**; 0/10 repos.
- **workstation-stack** — FOCUS.md's latest commit still 2026-08-21 (~8 days, on the stalled line).
- **dcode-stack** (~12 days) and **liaison-agentSystem** (~11 days) — both stalled, API-unverifiable.

## Sources / non-findings
- `repo-activity-2026-08-28.md` → 0 (gateway down, 0/10 verifiable).
- `morning-briefing-2026-08-28.md` → 2 net-new (tariff, Cambridge); ollama + Chinese open-weights
  recorded as carried baseline, not re-reported.
- The 8 pre-existing points are from 08-27 only — 08-24/08-26 (58 pts) remain unrecovered, the
  data-loss incident flagged 08-27 is still open.

## Running total
`curl -s http://127.0.0.1:6333/collections/default` → `points_count: 21` (was 8, +13).

## Deliverables
- **Ledger:** `/home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-28.md`
- **Run data / scripts:** `ingest_kb_2026-08-28.json`, `ingest_kb_2026-08-28.py`,
  `ingest_kb_append_2026-08-28.py` in this task workspace.
