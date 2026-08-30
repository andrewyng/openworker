---
id: automation-fleet
title: The scheduled fleet and its knowledge base
state: active
updated: '2026-08-28'
tags:
- automations
- qdrant
- knowledge-base
- cadence-ladder
---
**Now:** 2026-08-28: KB `default` collection holds 21 points (8 + 13 stored 08-28); 08-24/08-26 data-loss (50 pts) still unrecovered. GitHub MCP gateway now returns `fetch failed` (unreachable, 3rd run) instead of the earlier bad-credentials token error.

## History
- 2026-08-28 — KB `default` collection now holds 21 points (was 8 after the 2026-08-27 run; +13 on 2026-08-28 from papers/breakage/jobs/morning-briefing). The 50 lost 08-24/08-26 points remain unrecovered. DEDUP LESSON: with the collection small, cosine 0.72 find-first returns many topic-similar but DISTINCT findings (false "dups"); the reliable near-duplicate test for this job is identifier match (arXiv id / release tag / company+role). No real duplicate stored 08-28, nothing false lost. The GitHub MCP gateway failure mode also shifted: repo-activity 08-27 and 08-28 both return `fetch failed` (gateway unreachable, 3rd consecutive run, 0/10 repos) — distinct from the 08-24 `Authentication Failed: Bad credentials` bad-token error. (source: /home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-28.md)
- 2026-08-27 — Data-loss: KB `default` collection was at 0 points at start of the 2026-08-27 ingest run and is now at 8 (today's stores). All 50 points recorded on 08-26 and 08-24 (ledger claims 58 then, 34 at 08-24) are gone — recreate did not restore. Cause unconfirmed (possible Qdrant reset/non-persistence). 08-26 source files still on disk; 08-26 papers + breakage can be read verbatim, but 08-26 jobs/morning-briefing sources were never opened so they can't be restored verbatim by this run. Ledger at knowledge/ingest/2026-08-27.md flags the incident and recommends user decision. 8 points of 2026-08-27 content confirmed live.
- 2026-08-26 — Second ledgered run of the daily KB ingest job: 25 new points stored (5 jobs, 2 news, 7 breakage, 11 papers) from 4 automation outputs on 2026-08-25/26; 8 candidates skipped as dups/no-change (Horizon, TheRock #7051, legacy-rocm-build #6634, plus 5 carryovers). KB now at 58 points per live API check. Note: the mcp qdrant-find tool was intermittently broken all run (~6 of 14 calls failed with 'document' error); qdrant-store and Qdrant HTTP stayed healthy, so dedup leaned on prior-ledger cross-checks as a fallback. (source: /home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-26.md)
- 2026-08-24 — KB ingest daily job ran its first ledgered run: 12 candidate findings from the four 2026-08-24 automation outputs (papers, morning-briefing, breakage, repo-activity); 6 papers skipped as dups (arxiv-weekly already stored them, cosine 0.73–0.80), 5 stored new (Ollama 0.33.0-RC2, Iran sanctions 08-24 presser, ROCm/legacy-rocm-build #6634 ROCR busy-spin, liaison-agentSystem stalled 7+d, GitHub MCP token bad-credentials). Also learned: Qdrant point IDs must be uint64/UUID (string IDs 400); find-first dedup at 0.72 threshold successfully prevents arxiv-weekly/ingest double-stores. (source: /home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-24.md)
- 2026-08-22 — Ladder built: focus-derive (Mon 03:30), KB ingest (daily 06:00), weekly review (Mon 06:30), outside view (Wed 05:45), monthly, quarterly and yearly rollups. Every job reads FOCUS.md first and tags findings ON-FOCUS or ADJACENT. (source: ~/OpenWorker/knowledge/FOCUS.md)
- 2026-08-22 — Root cause found for the near-empty knowledge base: prompts named MCP tools by un-prefixed names (qdrant-store), so the corpus job fell back to shell scripts for ten runs. Registered names are doubled: mcp__qdrant__qdrant-qdrant-store. (source: ~/OpenWorker/__task__task-0db7112de6/)
- 2026-08-20 — Weekly arXiv corpus stored its first 15 papers across four topics into Qdrant collection `default`. (source: ~/OpenWorker/__task__task-0db7112de6/corpus-2026-08-20.md)
