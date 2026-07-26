---
name: search-first
description: "Research-before-coding workflow: understand the codebase, find relevant patterns, and validate assumptions before writing any code. Prevents guesswork and blind edits."
allowed-tools: grep, list_files, read_file
---

# Search-First Skill (Agentic)

DIRECTIVE: This skill enforces a research-first approach. You MUST understand before you change. NEVER write code based on assumptions about how the codebase works.

## When to Activate
- Making changes to unfamiliar code
- Adding features that touch existing modules
- Fixing bugs with unknown root causes
- Integrating with existing APIs or services
- Onboarding to a new project
- Before any non-trivial refactoring

## The Search-First Workflow (Strict Directives)

Before taking action, you MUST output a `<search_phase>` tag indicating your current step.

### Step 1: Map the Territory
- Read `README.md` and package/configuration files (e.g., `package.json`, `pyproject.toml`) using `read_file`.
- Identify the project structure and main entry points using `list_files` or `run_shell`.

### Step 2: Find Relevant Code
- Do not guess module or function names.
- Use `grep` to search by feature name, keywords, or import patterns to find the specific files you need to modify.
- Locate the test files associated with the target modules.

### Step 3: Read Before Editing
- You MUST read the target files and their corresponding test files BEFORE writing any changes.
- Identify the existing function signatures, error handling mechanisms, and coding patterns.

### Step 4: Verify Understanding
- Run existing tests to confirm the baseline behavior. Ensure the tests pass before you make changes.

### Step 5: Plan Before Writing
- Based on your research, identify what files need to change, what existing patterns you will follow, and what tests need to be added or updated.

## Exit Condition
The skill is complete ONLY when you have fully mapped the required changes, verified existing tests, and formulated a concrete plan. Once complete, summarize your findings to the user and ask if you should proceed to implementation.
