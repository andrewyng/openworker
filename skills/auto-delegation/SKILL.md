---
name: "auto-delegation"
description: "How to automatically break down complex tasks and delegate them to parallel subagents using the Inbox."
---

# Auto-Delegation Protocol

When you are asked to perform a complex, multi-step task, you can accelerate execution by automatically delegating subtasks to parallel subagents.

## Step 1: Breakdown and Classification
Analyze the request and break it down into parallelizable subtasks.
Classify each subtask into one of the following model tiers based on its complexity:
- `fast`: For quick reads, basic summarization, or simple file parsing.
- `balanced`: For standard code edits, data transformation, or moderate reasoning.
- `heavy`: For complex architectural changes, deep logical puzzles, or difficult algorithms.

**Resource Efficiency Rule:** 
Use models as required to guarantee a high-quality result, but do so efficiently. You should not hesitate to use the `heavy` model for complex, multi-step architectural reasoning or difficult algorithms where quality is paramount. However, do not waste `heavy` on trivial work; if a subtask is purely searching, summarizing, or standard code editing, route it to `fast` or `balanced` respectively to optimize speed and token usage.

## Step 2: Narration & Logging (Monitoring)
Instead of blocking execution to ask for human approval via the Inbox, you will use your **Narration** to provide real-time visibility. 
Before you call the `delegate` tools, you MUST write a short, clear narration string explaining your breakdown. 
- Example: *"Delegating the database refactor to the heavy model, and the log parsing to the fast model."*
- *Note: This will instantly show up in the user's terminal/GUI as a status update so they can monitor the parallel agents without having to click "Approve". If any subagent attempts a dangerous action (like writing files), the native PermissionEngine will automatically intercept it and put it in the Inbox anyway.*

## Step 3: Parallel Delegation
After your narration, proceed to use the `delegate` tool for each subtask immediately.
- Call the `delegate` tool multiple times in the same turn for parallel execution.
- Pass the appropriate `target_model` (e.g., `"fast"`, `"balanced"`, `"heavy"`) as assigned in your plan.
- Pass `allow_write=True` or `allow_shell=True` if the subagent needs to modify files or run commands.

## Example Flow
1. User: "Analyze our 3 core modules and write tests for them."
2. You determine:
   - Subtask A: Analyze/Test module 1 (balanced)
   - Subtask B: Analyze/Test module 2 (balanced)
   - Subtask C: Analyze/Test module 3 (heavy - contains complex logic)
3. You narrate: *"Delegating tests for modules 1 and 2 to the balanced model, and module 3 to the heavy model."*
4. You call `delegate(task="test module 1", target_model="balanced")`, `delegate(...)`, etc., simultaneously.
