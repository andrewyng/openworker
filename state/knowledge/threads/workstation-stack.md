---
id: workstation-stack
title: workstation-stack — the deployed stack on this box
state: active
updated: '2026-08-26'
tags:
- infrastructure
- mcp-gateway
- librechat
- evo-x2
---
**Now:** Ollama on this host: v0.33.0 final available (was 0.32.14 during the RC period); upgrade to 0.33.0 is now safe and is the intended fix for the broken-Linux-packaging issue; LibreChat pin bump still pending.

## History
- 2026-08-26 — Ollama on this host was tracked as "rc3, don't upgrade production yet"; as of the 2026-08-26 ingest, v0.33.0 final is confirmed published (latest-tag verified via releases API) with the broken-default-packaging-on-Linux fix and the agent-client prefill-cancel hang fix — the upgrade hold is lifted and the LibreChat image pin will need a bump when it happens. (source: /home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-26.md)
- 2026-08-25 — 2026-08-25 — Ollama v0.33.0-rc3 assets published 2026-08-25T04:00Z (github releases API). rc2 was the known item from the 08-24 briefing; rc3 is still pre-release, but signals the 0.33.0 final is imminent — the LibreChat image pin on this box will need a bump. (source: GitHub ollama releases API)
- 2026-08-21 — Hosts split: evo-x2 separated from spark so the config stops describing the wrong machine. MCP fetch and git revived, pubmed exposed. LibreChat images pinned and repointed at ollama. (source: git log workstation-stack)
- 2026-08-17 — Unattended automations granted the tools they need, minus shell; the weekly arXiv→Qdrant corpus automation added. (source: git log workstation-stack)
