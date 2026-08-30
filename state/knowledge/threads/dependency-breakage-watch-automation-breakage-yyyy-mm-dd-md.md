---
id: dependency-breakage-watch-automation-breakage-yyyy-mm-dd-md
title: Dependency & breakage watch automation (breakage-YYYY-MM-DD.md)
state: active
updated: '2026-08-28'
tags: []
---
**Now:** 2026-08-28: unsloth v0.1.804-beta (adapter-additive) shipped — no visible gfx1151 break; standing reinstall guardrail still applies. Tavily still EAI_AGAIN (3 consecutive runs); TheRock #7051 + #6634 zero movement.

## History
- 2026-08-28 — Tavily search backend returned `getaddrinfo EAI_AGAIN` (unreachable) on 3 consecutive scheduled runs: 2026-08-26, 08-27, 08-28. This is a recurring condition for THIS job, not a one-off. (source: /home/iconbaypark2900/OpenWorker/__task__task-f471043f3e/breakage-2026-08-28.md)
- 2026-08-28 — unsloth shipped v0.1.804-beta on 2026-08-27 13:09 UTC (adapter-additive: Qwen3.8-Flash-Next + GLM-5.3-Flash, up from v0.1.803-beta). No visible gfx1151/AMD break. Since unsloth is the wrong-torch trap package on this box, the standing guardrail still applies on any fresh `pip install -U unsloth`: reinstall torch from gfx1151 nightly index, set BNB_ROCM_VERSION=71 if bitsandbytes is pulled, set UNSLOTH_MOE_BACKEND=native_torch if an MoE-probe import runs. First unsloth release stored in the KB. Tavily (`tavily-tavily_search`) still down — `getaddrinfo EAI_AGAIN` on 3 consecutive runs (08-26/27/28); all version-of-record data verified via direct GitHub REST API, nothing reconstructed from memory. TheRock #7051 (4 comments, last touched 08-13) and legacy-rocm-build #6634 still open with zero movement. (source: /home/iconbaypark2900/OpenWorker/__task__task-f471043f3e/breakage-2026-08-28.md)
