# OpenWorker Skills Directory

> **Format:** Compatible with OpenWorker's `SkillLoader` — YAML frontmatter + markdown body
> **Contents:** A mix of Native OpenWorker skills and symlinks to Claude-native skills.

---

## 🛠 Available Skills

This directory contains skills that can be loaded by OpenWorker. Some of these are explicitly built for OpenWorker's architecture (Native), while others are symlinked directly from `~/.claude/skills` to provide extended capabilities.

### OpenWorker Native Skills
These skills were specifically built or adapted for OpenWorker's toolset (`read_file`, `grep_search`, `run_shell`):
- `coding-standards`: Baseline cross-project coding conventions, code smells, immutability
- `docker-patterns`: Container setup, networking, volumes, security
- `eval-harness`: Measuring code quality, benchmarking models, comparing implementations
- `search-first`: Research-before-coding, map-then-search-read-plan
- `strategic-compact`: When-to-compact, what-to-save, context optimization
- `verification-loop`: Pre-submission quality check (build, types, lint, tests, security)

### Symlinked Skills (From `~/.claude/skills`)
These skills are mapped from your global Claude configuration and provide extensive workflows for architecture, ideation, and general engineering:
- `api-and-interface-design`
- `browser-testing-with-devtools`
- `ci-cd-and-automation`
- `claw-control`
- `code-review-and-quality`
- `code-simplification`
- `context-engineering`
- `debugging-and-error-recovery` (Replaces old `error-handling`)
- `deprecation-and-migration`
- `documentation-and-adrs`
- `doubt-driven-development`
- `frontend-ui-engineering`
- `git-workflow-and-versioning` (Replaces old `git-workflow`)
- `idea-refine`
- `incremental-implementation`
- `interview-me`
- `observability-and-instrumentation`
- `performance-optimization`
- `planning-and-task-breakdown`
- `security-and-hardening` (Replaces old `security-review`)
- `shipping-and-launch`
- `source-driven-development`
- `spec-driven-development`
- `test-driven-development` (Replaces old `tdd-workflow`)
- `using-agent-skills`

---

## 🧩 Integration with OpenWorker Personas

These skills are designed to be loaded by OpenWorker's `SkillLoader` and invoked via the `load_skill(name)` tool. 

### Code Persona (github-style workspace)
- `search-first` — explore codebase before changing it
- `test-driven-development` — write tests before implementation
- `verification-loop` — verify before submitting
- `git-workflow-and-versioning` — commits, PRs, branching
- `docker-patterns` — container setup, multi-service dev

### Cowork Persona (deliverable workspace)
- `security-and-hardening` — review deliverables for security issues
- `coding-standards` — review code quality of deliverables
- `debugging-and-error-recovery` — review error handling in deliverables
- `eval-harness` — objectively score code deliverables
- `strategic-compact` — manage context in long analysis sessions

### Ops Persona (operations)
- `docker-patterns` — container orchestration, debugging
- `debugging-and-error-recovery` — incident response, debugging
- `verification-loop` — pre-deployment quality gates

---

## 🚀 How to Use in OpenWorker

### Option A: Reference via SkillLoader paths
OpenWorker's `SkillLoader` takes a list of directories:

```python
loader = SkillLoader([
    "/Users/jose/ExpertAIAgents/openworker/skills",
    # ... other skill directories
])
```

### Option B: Load selectively
Only load the skills relevant to a specific task:

```python
# For a code review session:
loader = SkillLoader(["skills/security-and-hardening", "skills/coding-standards"])

# For a new feature implementation:
loader = SkillLoader(["skills/search-first", "skills/test-driven-development"])
```

### Option C: Agent Tool Invocation
Once skills are loaded via the `SkillLoader`, the agent can dynamically invoke them using the `load_skill` tool:

```python
# The agent issues a tool call in its context:
call_tool("load_skill", {"name": "test-driven-development"})
```

---

## 📝 Creating a Custom OpenWorker Skill

To create a new skill native to OpenWorker, define a directory containing a `SKILL.md` file with YAML frontmatter and a Markdown body:

```yaml
---
name: my-custom-skill
description: Describe when the agent should use this skill...
allowed-tools: read_file, run_shell, grep_search
---
```
```markdown
# Instructions
1. First, do this...
2. Next, check that...
```

## Validation & Testing

To verify that OpenWorker correctly parses and adheres to these skills:
1. Load the skill in your project.
2. Run OpenWorker with `--debug` or equivalent logging enabled.
3. Observe the system prompt or tool-call injection to confirm the markdown instructions are actively appended to the agent's context window.
