---
name: skill-creator
description: Create, improve, test, compare, and safely install Worker skills when a user asks to build or refine a reusable workflow.
source: worker
---

# Skill Creator

Help the user create or improve a Worker skill. Reuse relevant facts already stated in the conversation, then ask only for missing decisions: intended outcome, trigger situations, inputs, expected outputs, constraints, and how success will be judged.

Draft concise instructions with a clear `name` and `description`. Keep detailed or conditional material in `references/`, deterministic repeatable work in `scripts/`, and output assets in `assets/` only when they provide real value. Do not copy third-party skill text; write original instructions for Worker.

Before installation, use `stage_skill_draft` to store the candidate and its two or three realistic test prompts. Explain the tests in plain language. Use `run_skill_evals` only after the user approves the model usage. Review the comparison, objective checks, and user feedback with `read_skill_eval`; revise the draft when feedback supports it.

Evaluation runs are isolated. Do not promise access to connectors, network, browser, personal memory, shell commands, or folders outside the supplied test inputs. If the proposed skill requires those capabilities, mark the case for manual verification.

When the user accepts a final version, call `save_skill`. It remains the required approval step for adding or updating the installed skill. Do not install drafts directly.
