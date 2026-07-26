# ECC → OpenWorker Skill Packs: Delivery Summary

> **Location:** `` directory (10 skill packs, 3,654 lines total)
> **Format:** Compatible with OpenWorker's `SkillLoader` — YAML frontmatter + markdown body
> **Source:** Adapted from ECC (`affaan-m/ECC`) patterns, rewritten for OpenWorker's architecture

---

## Quick Reference

| # | Skill | Lines | When to Use |
|---|-------|-------|-------------|
| 1 | [`security-review`](security-review/SKILL.md) | 360 | Auth, user input, secrets, API endpoints, payment features |
| 2 | [`tdd-workflow`](tdd-workflow/SKILL.md) | 393 | Writing features, fixing bugs, refactoring — test-driven development |
| 3 | [`verification-loop`](verification-loop/SKILL.md) | 180 | Pre-submission quality check — build, types, lint, tests, security |
| 4 | [`coding-standards`](coding-standards/SKILL.md) | 418 | Starting new code, reviewing quality, refactoring, onboarding |
| 5 | [`search-first`](search-first/SKILL.md) | 232 | Unfamiliar code, new integrations, onboarding — research before editing |
| 6 | [`strategic-compact`](strategic-compact/SKILL.md) | 159 | Long sessions, multi-phase tasks — compact at logical boundaries |
| 7 | [`error-handling`](error-handling/SKILL.md) | 479 | Error design, debugging, incident response, root-cause analysis |
| 8 | [`eval-harness`](eval-harness/SKILL.md) | 386 | Measuring code quality, benchmarking models, comparing implementations |
| 9 | [`git-workflow`](git-workflow/SKILL.md) | 656 | Branching, commits, PRs, conflicts, releases |
| 10 | [`docker-patterns`](docker-patterns/SKILL.md) | 391 | Container setup, networking, volumes, security, multi-service orchestration |

---

## File Structure

```
skills/
├── coding-standards/
│   └── SKILL.md          418 lines  — naming, immutability, performance, code smells
├── docker-patterns/
│   └── SKILL.md          391 lines  — Compose, Dockerfiles, networking, volumes, security
├── error-handling/
│   └── SKILL.md          479 lines  — custom errors, circuit breakers, debugging, incidents
├── eval-harness/
│   └── SKILL.md          386 lines  — correctness, completeness, performance, readability scoring
├── git-workflow/
│   └── SKILL.md          656 lines  — GitHub Flow, conventional commits, merge/rebase, conflicts
├── search-first/
│   └── SKILL.md          232 lines  — research-before-coding, map-then-search-read-plan
├── security-review/
│   └── SKILL.md          360 lines  — secrets, validation, SQL injection, XSS, CSRF, CORS
├── strategic-compact/
│   └── SKILL.md          159 lines  — when-to-compact, what-to-save, context optimization
├── tdd-workflow/
│   └── SKILL.md          393 lines  — RED→GREEN→IMPROVE, checkpoint commits, evidence reports
└── verification-loop/
    └── SKILL.md          180 lines  — build→types→lint→tests→security→diff (6-phase check)
```

Each directory contains exactly one `SKILL.md` file — compatible with OpenWorker's `SkillLoader`:

```python
# OpenWorker's parser:
md = sub / "SKILL.md"
if md.is_file():
    skill = _parse_skill(md)
    # Extracts: name, description, allowed_tools, body (instructions)
```

---

## What Was Adapted (ECC → OpenWorker)

Every skill was adapted from ECC's patterns, with these changes:

### 1. Tool References
ECC references Claude Code tools (`write_file`, `grep`, etc.). OpenWorker has similar tools but different names and APIs.

**ECC:**
```markdown
allowed-tools: write_file, run_shell, grep
```

**OpenWorker:**
```markdown
allowed-tools: read_file, grep, list_files, run_shell
```

### 2. Context Awareness
ECC assumes a Claude Code session with `CLAUDE.md`, `~/.claude/`, hooks, auto-compact. OpenWorker has no hooks, no auto-compact, no `CLAUDE.md`.

**ECC:** "Run the `suggest-compact.js` hook script on PreToolUse"

**OpenWorker:** "The agent should compact when it detects context pressure — here's when and how"

### 3. Framework-Specific Examples
ECC has Next.js/Supabase-specific examples (heavily JavaScript/TypeScript). OpenWorker covers all languages.

**ECC:**
```typescript
import { z } from 'zod'
const CreateUserSchema = z.object({ email: z.string().email() })
```

**OpenWorker:**
```python
from pydantic import BaseModel
class CreateUser(BaseModel):
    email: EmailStr
```

```bash
# npm test (ECC)
python -m pytest (OpenWorker)
go test ./... (OpenWorker)
cargo test (OpenWorker)
```

### 4. Hook Dependencies
ECC's `strategic-compact` and `eval-harness` reference hook infrastructure. OpenWorker has no hooks, so these were rewritten as agent-guided checklists.

**ECC (hook-dependent):** "The `suggest-compact.js` script fires on PreToolUse"

**OpenWorker (agent-guided):** "When you detect context pressure, save your state, then compact with a summary"

### 5. Platform-Neutral
ECC is heavily oriented toward the Claude Code / Vercel / Supabase stack. OpenWorker is language-agnostic and infrastructure-agnostic.

| ECC Bias | OpenWorker → |
|----------|-------------|
| Next.js + Supabase | Python, TypeScript, Go, Rust, any stack |
| Vercel/Railway deployment | Generic deployment patterns |
| Claude Code tool API | General tool usage patterns |
| Hook-based automation | Agent-guided manual workflows |

---

## Integration with OpenWorker Personas

These skills are designed to be loaded by OpenWorker's `SkillLoader` and invoked via the `load_skill(name)` tool. Here's how they map to personas:

### Code Persona (github-style workspace)
- `search-first` — explore codebase before changing it
- `tdd-workflow` — write tests before implementation
- `verification-loop` — verify before submitting
- `git-workflow` — commits, PRs, branching
- `docker-patterns` — container setup, multi-service dev

### Cowork Persona (deliverable workspace)
- `security-review` — review deliverables for security issues
- `coding-standards` — review code quality of deliverables
- `error-handling` — review error handling in deliverables
- `eval-harness` — objectively score code deliverables
- `strategic-compact` — manage context in long analysis sessions

### Ops Persona (operations)
- `docker-patterns` — container orchestration, debugging
- `error-handling` — incident response, debugging
- `verification-loop` — pre-deployment quality gates

---

## How to Use in OpenWorker

### Option A: Drop into existing skills directory

Copy the `skills/` directory into OpenWorker's skills path:

```bash
# If OpenWorker loads skills from ~/.openworker/skills/
cp -r skills/* ~/.openworker/skills/

# Or if it loads from a project-local .cowork/skills/
cp -r skills/* .cowork/skills/
```

### Option B: Reference via SkillLoader paths

OpenWorker's `SkillLoader` takes a list of directories:

```python
loader = SkillLoader([
    "/Users/jose/OpenWorker/12428dbc-8a1/skills",
    # ... other skill directories
])

# The agent sees these in its catalog:
# - security-review: Use this skill when adding authentication...
# - tdd-workflow: Use this skill when writing new features...
# - coding-standards: Baseline cross-project coding conventions...
# etc.
```

### Option C: Load selectively

Only load the skills relevant to a specific task:

```python
# For a code review session:
loader = SkillLoader(["skills/security-review", "skills/coding-standards"])

# For a new feature implementation:
loader = SkillLoader(["skills/search-first", "skills/tdd-workflow"])

# For a pre-deployment check:
loader = SkillLoader(["skills/verification-loop", "skills/security-review"])
```

### Option D: Agent Tool Invocation

Once skills are loaded via the `SkillLoader`, the agent can dynamically invoke them using the `load_skill` tool:

```python
# The agent issues a tool call in its context:
call_tool("load_skill", {"name": "tdd-workflow"})
```

---

## Creating a Custom OpenWorker Skill

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

---

## Cross-Referencing: Which Skills Use Which

```
security-review  ──references──>  coding-standards (as baseline)
tdd-workflow   ──references──>  verification-loop (pre-submission check)
search-first   ──feeds into──>  tdd-workflow (research before RED)
error-handling ──independent── (applies to all)
eval-harness   ──feeds into──>  verification-loop (quality scoring)
git-workflow   ──feeds into──>  tdd-workflow (checkpoint commits)
docker-patterns ──feeds into──> git-workflow (Dockerfile in commits)
strategic-compact ──applies to─ all long sessions
```

---

## Comparison: ECC vs OpenWorker Skill Quality

| Dimension | ECC Original | OpenWorker Derivative |
|-----------|-------------|----------------------|
| **Breadth** | 279 skills (massive catalog) | 10 curated skills (focused) |
| **Depth** | 100-2000 lines per skill | 150-650 lines per skill |
| **Code examples** | TypeScript-heavy, Next.js/Supabase focused | Language-agnostic (Python, TS, Go, Rust) |
| **Tool references** | Claude Code tools (`write_file`, hooks) | OpenWorker tools (`read_file`, `run_shell`) |
| **Format** | YAML frontmatter + markdown (same) | YAML frontmatter + markdown (same) |
| **Philosophy** | "Always use these patterns" | "Use these when relevant, don't force" |

---

## Files Delivered

| File | Path | Size |
|------|------|------|
| security-review | `security-review/SKILL.md` | 360 lines |
| tdd-workflow | `tdd-workflow/SKILL.md` | 393 lines |
| verification-loop | `verification-loop/SKILL.md` | 180 lines |
| coding-standards | `coding-standards/SKILL.md` | 418 lines |
| search-first | `search-first/SKILL.md` | 232 lines |
| strategic-compact | `strategic-compact/SKILL.md` | 159 lines |
| error-handling | `error-handling/SKILL.md` | 479 lines |
| eval-harness | `eval-harness/SKILL.md` | 386 lines |
| git-workflow | `git-workflow/SKILL.md` | 656 lines |
| docker-patterns | `docker-patterns/SKILL.md` | 391 lines |
| **Total** | | **10 skills, 3,654 lines** |

Summary: [ECC_OpenWorker_Skill_Summary.md](ECC_OpenWorker_Skill_Summary.md)

---

## What's NOT Included (and Why)

These ECC skills were intentionally excluded because they don't fit OpenWorker's architecture:

| Skill | Why Excluded |
|-------|-------------|
| **hooks/** (suggest-compact.js, etc.) | OpenWorker has no hook system |
| **continuous-learning-v2** (instincts) | Requires persistent session memory + evolution pipeline |
| **mcp-server-patterns** | MCP management is persona-level, not skill-level |
| **skill-stocktake** | Skill quality audit — meta-tooling not needed |
| **hookify-rules** | Hook creation tool — OpenWorker has no hooks |
| **pal/ram-say** | Customer ops — too domain-specific |
| **stromboli-task-patterns** | NestJS-specific framework patterns |
| **aws-lambda-inline-database** | AWS-specific infrastructure |
| **rabbitmq** | Infrastructure-specific |
| **deployment-patterns** | Covered by docker-patterns (local dev) |

The principle: **Only skills that work with OpenWorker's actual tool surface** (read_file, grep, list_files, run_shell, load_skill). No hook-dependent, MCP-dependent, or infra-specific skills.

---

## Future Expansions

### How to Port an ECC Skill to OpenWorker
1. **Strip out hook scripts:** Remove any reliance on auto-executing scripts (e.g., `suggest-compact.js`).
2. **Map tools:** Replace Claude Code tools (like `write_file`) with OpenWorker equivalents (like `read_file`, `run_shell`).
3. **Generalize examples:** Convert highly specific framework examples (e.g., Next.js, Supabase) into language-agnostic patterns (Python, TS, Go).

If you want more skills, here are the next-best candidates from ECC that would adapt well using the steps above:

1. **`deployment-patterns`** → Adapt to generic CI/CD patterns (GitHub Actions, GitLab CI)
2. **`api-design`** → REST/GraphQL API design patterns, language-agnostic
3. **`docker-patterns`** → Already created above!
4. **`python-patterns`** → Python idioms, type hints, project structure
5. **`prisma-patterns`** → Database modeling and migrations
6. **`performance-optimization`** → Profiling, caching, N+1 query detection
7. **`frontend-patterns`** → React/Next.js component patterns
8. **`testing-patterns`** → Mocking strategies, testing databases, test doubles
9. **`documentation-lookup`** → How to find and cite docs for any library
10. **`code-tour`** → Walkthrough generation for unfamiliar codebases
