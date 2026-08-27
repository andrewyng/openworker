---
id: automation-fleet
title: The scheduled fleet and its knowledge base
state: active
updated: '2026-08-26'
tags:
- automations
- qdrant
- knowledge-base
- cadence-ladder
---
**Now:** KB ingest daily job now on its second ledgered run (2026-08-26); knowledge base at 58 points per live API check. Ledger series continues at ~/OpenWorker/knowledge/ingest/<date>.md. MCP qdrant-find tool proved intermittently broken on 2026-08-26 ('document' error) — store path stayed fine; use prior-ledger stored-list as a dedup fallback if find stays down.

## History
- 2026-08-26 — Second ledgered run of the daily KB ingest job: 25 new points stored (5 jobs, 2 news, 7 breakage, 11 papers) from 4 automation outputs on 2026-08-25/26; 8 candidates skipped as dups/no-change (Horizon, TheRock #7051, legacy-rocm-build #6634, plus 5 carryovers). KB now at 58 points per live API check. Note: the mcp qdrant-find tool was intermittently broken all run (~6 of 14 calls failed with 'document' error); qdrant-store and Qdrant HTTP stayed healthy, so dedup leaned on prior-ledger cross-checks as a fallback. (source: /home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-26.md)
- 2026-08-24 — KB ingest daily job ran its first ledgered run: 12 candidate findings from the four 2026-08-24 automation outputs (papers, morning-briefing, breakage, repo-activity); 6 papers skipped as dups (arxiv-weekly already stored them, cosine 0.73–0.80), 5 stored new (Ollama 0.33.0-RC2, Iran sanctions 08-24 presser, ROCm/legacy-rocm-build #6634 ROCR busy-spin, liaison-agentSystem stalled 7+d, GitHub MCP token bad-credentials). Also learned: Qdrant point IDs must be uint64/UUID (string IDs 400); find-first dedup at 0.72 threshold successfully prevents arxiv-weekly/ingest double-stores. (source: /home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-24.md)
- 2026-08-22 — Ladder built: focus-derive (Mon 03:30), KB ingest (daily 06:00), weekly review (Mon 06:30), outside view (Wed 05:45), monthly, quarterly and yearly rollups. Every job reads FOCUS.md first and tags findings ON-FOCUS or ADJACENT. (source: ~/OpenWorker/knowledge/FOCUS.md)
- 2026-08-22 — Root cause found for the near-empty knowledge base: prompts named MCP tools by un-prefixed names (qdrant-store), so the corpus job fell back to shell scripts for ten runs. Registered names are doubled: mcp__qdrant__qdrant-qdrant-store. (source: ~/OpenWorker/__task__task-0db7112de6/)
- 2026-08-20 — Weekly arXiv corpus stored its first 15 papers across four topics into Qdrant collection `default`. (source: ~/OpenWorker/__task__task-0db7112de6/corpus-2026-08-20.md)
