---
id: local-model-reliability
title: Running the agent fleet on a local 27B
state: active
updated: '2026-08-22'
tags:
- openworker
- compaction
- context-window
- qwen3.8-27b
- automations
---
**Now:** Compaction now counts tool schemas and clamps a turn that cannot be compacted; the open question is whether the whole automation fleet can run unattended on a local 27B without silent failures. Two automations still report 'incomplete'.

## History
- 2026-08-22 — Job matches and Repo activity brief both ended their latest runs 'incomplete' — the failure mode this work exists to remove. (source: /v1/automations)
- 2026-08-20 — Compaction fixed twice over: the budget never counted the tools array (a turn accepted at 60,159 tokens with no tools 500'd the moment schemas were attached), and pick_boundary returned a boundary it knew did not fit. clamp_tool_results is the outbound-only last line of defence. (source: git log openworker 9c76d5e)
- 2026-08-19 — Context window sized explicitly for local models to stop silent automation failures; compaction logged so a run can be followed live. (source: git log openworker)
