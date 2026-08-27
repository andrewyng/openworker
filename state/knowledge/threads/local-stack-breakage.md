---
id: local-stack-breakage
title: Local stack — dependency and breakage watch
state: active
updated: '2026-08-26'
tags:
- rocm
- llama.cpp
- ollama
- gfx1151
- strix-halo
---
**Now:** Watch list as of 2026-08-26: unchanged versions on the host (ROCm 7.1.0, llama.cpp 4df29be4f, ollama 0.32.14, torch 2.11.0+rocm7.13.0, transformers 5.5.0, kernel 7.0.0-29), but upstreams have moved: OpenWorker v0.2.1 (v0.2.0 "updating is recommended"), Ollama v0.33.0 final with the Linux packaging fix, llama.cpp v0.3.0/nightly b10621. ROCR AsyncEventsLoop (#7051 gfx1151, #6634 gfx1201) still open, zero movement. Unsloth SIGSEGV workaround now verified: UNSLOTH_MOE_BACKEND=native_torch.

## History
- 2026-08-26 — Ingest run 2026-08-26 confirms three stack state changes from the 08-26 breakage digest: OpenWorker upstream at v0.2.1 (v0.2.0, 2026-08-24, carries an explicit "Updating is recommended" security line — approval-handling/workspace-trust fixes); Ollama v0.33.0 FINAL is out (host on 0.32.14) including the broken-Linux-packaging fix; llama.cpp v0.3.0 (2026-08-25) + nightly b10621 with "Restore ROCm job for Ubuntu" #27399. TheRock #7051 and legacy-rocm-build #6634 still open with zero movement; the sleepingrobots unsloth/Strix-Halo workaround (UNSLOTH_MOE_BACKEND=native_torch, BNB_ROCM_VERSION=71) got its first verified full read (404'd on 08-24). (source: /home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-26.md)
- 2026-08-24 — New item to watch: the ROCr AsyncEventsLoop busy-spin bug (previously scoped only to TheRock #7051 on gfx1151) has a second, independent report this week: ROCm/legacy-rocm-build #6634 — "[gfx1201] ROCr AsyncEventsLoop busy-spins at 100% CPU after Wan 2.2 workload completion", state=open, created 2026-08-20, same kernel (7.0.0-29-generic) as this machine's. Reframes it as a wheel-channel / ROCR-runtime problem, not a gfx1151-only driver quirk. Profiling: 99.79% of the spinning thread in rocr::core::Runtime::AsyncEventsLoop (libhsa-runtime64.so.1); 5s strace -e ioctl shows 0 ioctl calls (pure userspace spin). TheRock #7051 itself unchanged: open, triage, adityas-amd, last updated 2026-08-13.
- 2026-08-24 — Repo-activity 2026-08-24 confirms the GitHub MCP gateway token is still broken ('Authentication Failed: Bad credentials') — 9 of the 10 iconbaypark2900 repos are UNKNOWN/404 to the automation, reverting coverage to its pre-2026-08-20 blind state for a fourth consecutive day; liaison-agentSystem's last commit remains the 2026-08-17 bare WIP snapshot (stalled 7+ days). (source: /home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-24.md)
- 2026-08-22 — Two state changes and one new release since 08-21; everything else unchanged. Ordered by likelihood of breaking this Strix Halo (gfx1151) machine. (source: ~/OpenWorker/__task__task-f471043f3e/breakage-2026-08-22.md)
- 2026-08-17 — Benchmark recorded in the vector store: Vulkan beats ROCm for token generation on gfx1151; ROCm wins prefill. (source: qdrant:default)
