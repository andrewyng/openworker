---
id: dependency-breakage-watch-automation-breakage-yyyy-mm-dd-md
title: Dependency & breakage watch automation (breakage-YYYY-MM-DD.md)
state: active
updated: '2026-08-31'
tags: []
---
**Now:** 2026-08-31: net-new since 08-28 = ollama v0.33.2 (macOS/Claude-proxy housekeeping, no ROCm change, not a break) + legacy-rocm-build #6522 (second independent open report of gfx1151 AsyncEventsLoop 100%-CPU spin, same class as TheRock #7051). TheRock #7051 still open/triage/adityas-amd/4-comments/last-touched-2026-08-13. transformers v5.16.1, llama.cpp v0.3.0/b10621, OpenWorker v0.2.1, unsloth v0.1.804-beta all unchanged. ROCm Core SDK 10.0.0 notes: "Since ROCm 7.14, ROCm uses TheRock" (upstream direction, no host action).

## History
- 2026-08-31 — 2026-08-31 — ollama is now at v0.33.2 (2026-08-27 20:31), first ollama release since 0.33.1; it is a macOS-app/Claude-Desktop-proxy housekeeping release (no Linux/ROCm change, ROCm wheel still shipped) — not a break vector. Net-new corroborating gfx1151 issue legacy-rocm-build #6522 opened ("HSA runtime AsyncEventsLoop livelocks 100% CPU spin on gfx1151", open/triage/amd-nicknick) — same symptom class as TheRock #7051, so the gfx1151 AsyncEventsLoop spin now has two open AMD-triage reports instead of one.
- 2026-08-28 — Tavily search backend returned `getaddrinfo EAI_AGAIN` (unreachable) on 3 consecutive scheduled runs: 2026-08-26, 08-27, 08-28. This is a recurring condition for THIS job, not a one-off. (source: /home/iconbaypark2900/OpenWorker/__task__task-f471043f3e/breakage-2026-08-28.md)
- 2026-08-28 — unsloth shipped v0.1.804-beta on 2026-08-27 13:09 UTC (adapter-additive: Qwen3.8-Flash-Next + GLM-5.3-Flash, up from v0.1.803-beta). No visible gfx1151/AMD break. Since unsloth is the wrong-torch trap package on this box, the standing guardrail still applies on any fresh `pip install -U unsloth`: reinstall torch from gfx1151 nightly index, set BNB_ROCM_VERSION=71 if bitsandbytes is pulled, set UNSLOTH_MOE_BACKEND=native_torch if an MoE-probe import runs. First unsloth release stored in the KB. Tavily (`tavily-tavily_search`) still down — `getaddrinfo EAI_AGAIN` on 3 consecutive runs (08-26/27/28); all version-of-record data verified via direct GitHub REST API, nothing reconstructed from memory. TheRock #7051 (4 comments, last touched 08-13) and legacy-rocm-build #6634 still open with zero movement. (source: /home/iconbaypark2900/OpenWorker/__task__task-f471043f3e/breakage-2026-08-28.md)
