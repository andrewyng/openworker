---
id: dcode-stack
title: dcode-stack
state: quiet
updated: '2026-08-31'
tags:
- langfuse
- eval
- tracing
- prometheus
---
**Now:** dcode-stack returned to Active (from quiet) as of 2026-08-27/31 with 51 commits; the "dead CUDA engine exits 0" open question is answered by commit b7fa880.

## History
- 2026-08-31 — 2026-08-31 weekly review: dcode-stack moved from "Went quiet" (last activity 08-16) back to Active — 51 commits all 08-26/27 by iconbaypark2900. The live arc: vLLM on :5100 + on-demand llama.cpp via decode_proxy (f19c6a8 08-26), auto-classifier is the main model (granite failed 5/6 destructive batches, e24cce7), "Size the serving profile for four consumers" (94ade62), and the FOCUS open question "why does a dead CUDA engine exit 0 so on-failure never restarts it?" is answered by commit b7fa880 (08-27): the exit-0-on-failure IS the bug, now recorded; 9bc3b30 (08-27) reports the serving engine + proxy. (source: /home/iconbaypark2900/OpenWorker/knowledge/reports/weekly/2026-W36.md) (source: /home/iconbaypark2900/OpenWorker/knowledge/reports/weekly/2026-W36.md)
- 2026-08-16 — Last commits: repo scaffolding, `eval` scoring the agent against Langfuse datasets, and `project` scoping a repo to dcode with its own trace lane. Nothing since. (source: git log dcode-stack)
- 2026-08-15 — Wired the LLM stack into the Grafana/Prometheus already running on the box; gave dcode its own Langfuse project and traced it over OTLP. (source: git log dcode-stack)
