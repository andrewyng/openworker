---
id: rocm-strix-halo-local-inference-baseline
title: ROCm / Strix Halo local-inference baseline
state: active
updated: '2026-09-02'
tags: []
---
**Now:** ROCm baseline for this Strix Halo box is ROCm 7.2.2; AMD has shipped ROCm 10 (GA ~Aug 28 2026), which explicitly supports Strix Halo and adds vLLM + SGLang. Not yet adopted/verified on this box.

## History
- 2026-09-02 — Freshest ROCm is now ROCm 10 (AMD jumped from ROCm 7.14 to 10, skipping 8/9), GA ~Aug 28 2026 with ROCm.AI. TheRock build system across the stack, vLLM + SGLang support on Instinct GPUs, 3.3x inference / 2.4x training vs ROCm 7 (tested on 8× MI355X with GLM-5/Kimi-K2.5/DeepSeek-R1-0528). AMD explicitly states ROCm 10 is supported by Strix Halo. This box's working baseline was ROCm 7.2.2.
