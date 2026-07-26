---
name: strategic-compact
description: "Suggests manual context compaction at logical intervals to preserve context through task phases rather than arbitrary auto-compaction."
allowed-tools: read_file
---

# Strategic Compact Skill

Suggests manual context compaction at strategic points in your workflow rather than relying on arbitrary auto-compaction.

## When to Activate

- Running long sessions that approach context limits
- Working on multi-phase tasks (research → plan → implement → test)
- Switching between unrelated tasks within the same session
- After completing a major milestone and starting new work
- When responses slow down or become less coherent (context pressure)

## Why Strategic Compaction?

Auto-compaction triggers at arbitrary points:
- Often mid-task, losing important context
- No awareness of logical task boundaries
- Can interrupt complex multi-step operations

Strategic compaction at logical boundaries:
- **After exploration, before execution** — Compact research context, keep implementation plan
- **After completing a milestone** — Fresh start for next phase
- **Before major context shifts** — Clear exploration context before different task

## When to Compact

Use this table to decide:

| Phase Transition | Compact? | Why |
|-----------------|----------|-----|
| Research → Planning | Yes | Research context is bulky; plan is the distilled output |
| Planning → Implementation | Yes | Plan is in a file or task list; free up context for code |
| Implementation → Testing | Maybe | Keep if tests reference recent code; compact if switching focus |
| Debugging → Next feature | Yes | Debug traces pollute context for unrelated work |
| Mid-implementation | No | Losing variable names, file paths, and partial state is costly |
| After a failed approach | Yes | Clear the dead-end reasoning before trying a new approach |

## What to Save Before Compacting

Before compacting, ensure important context is preserved somewhere durable:

**Save to files:**
- Implementation plans (write to a `.plan.md` file)
- Task lists (use the todo tool)
- Design decisions (document in `docs/design/` or `docs/decisions/`)
- Test specifications (write test plan before compacting)

**Save via memory:**
- Key architectural decisions
- Project-specific patterns discovered during research
- User preferences or constraints

## Compaction Decision Framework

**Compact if:**
- You've finished reading a large codebase section and the plan is documented
- You've completed a feature and are starting a new, unrelated one
- You've exhausted debugging a bug and have documented the root cause
- You've researched a topic thoroughly and summarized findings

**Don't compact if:**
- You're mid-implementation (losing variable names, file paths, partial state)
- You're in the middle of a complex debugging session
- You're actively discussing architecture decisions with the user
- Tests are failing and you're iterating on the fix

## Compact with Purpose

When you compact, include a summary:

```
Compact: Switching from research to implementation.
Summary: Mapped 15 files in the auth module. Key findings:
- Auth uses JWT stored in httpOnly cookies
- Validation via Zod schemas in src/lib/validators.ts
- Tests in tests/auth/ follow AAA pattern
- Next: implement the /api/auth/login endpoint
```

This summary becomes the context for the post-compaction session.

## Context Optimization Tips

### File Over Memory

Save important context to files rather than keeping it in conversation:
```bash
# Write research summary
write_file("docs/research/auth-module.md", <summary of findings>)

# Write implementation plan
write_file("docs/plan/auth-login.md", <plan with milestones>)

# Write test specification
write_file("docs/spec/auth-login-tests.md", <test cases>)
```

### Task List Over Conversation

Use the todo tool to track progress:
```
todo_write: [{content: "Implement login endpoint", status: "in_progress"},
             {content: "Add auth middleware", status: "pending"},
             {content: "Write unit tests", status: "pending"}]
```

### Modular Files Over Monolithic

Break large files into modules. This reduces the context needed for any single change:
```
src/
├── auth/
│   ├── login.ts        # Login logic
│   ├── middleware.ts   # Auth middleware
│   └── validators.ts   # Input validation
└── ...
```

## When Context Pressure Happens

Signs you're hitting context limits:
- The agent starts repeating itself
- Responses become shorter or more generic
- The agent forgets earlier context (variable names, file paths)
- The agent suggests reading files it already has in context
- Response quality noticeably degrades

If you see these signs:
1. Save current state to files (plan, task list, key findings)
2. Compact the session
3. Resume with the saved files as context

## Integration with Other Skills

- **Coding standards**: Before compacting, ensure coding standards were applied
- **TDD workflow**: Compact after GREEN/coverage, before starting next feature
- **Search-first**: Compact after research, before implementation
- **Verification loop**: Run verification before compacting to ensure nothing was broken

## Quick Reference

| Situation | Action |
|-----------|--------|
| Finished reading a large file | Write summary, compact |
| Switched from debugging to new feature | Save debug notes, compact |
| Mid-TDD cycle (RED → GREEN) | Don't compact — keep context |
| Completed a milestone | Write milestone notes, compact |
| Response quality declining | Save state, compact |
| About to start research | Fresh context is better — compact early |

---

**Strategic compaction preserves what matters and discards what doesn't.** Auto-compaction preserves nothing intentionally — strategic compaction at task boundaries ensures important context survives.
