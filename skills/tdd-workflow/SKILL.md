---
name: tdd-workflow
description: "Use this skill when writing new features, fixing bugs, or refactoring code. Enforces test-driven development with 80%+ coverage including unit, integration, and E2E tests."
allowed-tools: read_file, run_shell, grep
---

# Test-Driven Development Workflow

DIRECTIVE: This skill ensures all code development follows strict TDD principles. You MUST write tests BEFORE implementing code.

## Core Directives
1. **Tests BEFORE Code:** ALWAYS write tests first, then implement code to make tests pass.
2. **Coverage Requirements:** Target minimum 80% coverage. Cover edge cases, error scenarios, and boundaries.
3. **Chain-of-Thought Marker:** Before every tool call, you MUST output a `<tdd_state>` tag indicating your current phase: `[Step 1: Write Tests]`, `[Step 2: RED Gate]`, `[Step 3: Implement Code]`, `[Step 4: GREEN Gate]`, `[Step 5: Refactor]`, `[Step 6: Coverage]`, or `[Step 7: Evidence Report]`.
4. **Git Checkpoints:** (If applicable and approved by user) Create checkpoint commits on a working branch after RED and GREEN phases. Do not pollute the main branch with broken states.

## TDD Workflow Steps

### Step 0: Detect the Test Runner
Do not assume test runners. Inspect configuration files (`package.json`, `pyproject.toml`, etc.) to resolve the actual test command.

### Step 1: Write Test Cases
Implement test cases for the feature or bug before writing the implementation logic.

### Step 2: RED Gate (Failing Tests)
Run the test runner.
**Verification:** Ensure the tests compile and FAIL for the intended reason (missing implementation). Do NOT proceed until a RED state is confirmed.

### Step 3: Implement Code
Write the minimal code required to make the failing test pass.

### Step 4: GREEN Gate (Passing Tests)
Run the test runner again.
**Verification:** Ensure the newly written tests PASS and no existing tests were broken.

### Step 5: Refactor
Improve code quality (remove duplication, improve naming, optimize) while ensuring tests remain GREEN.

### Step 6: Verify Coverage
Run the coverage command to ensure the new code is adequately covered.

### Step 7: Evidence Report
Generate a short Markdown report documenting what was verified, coverage percentages, and changes made.

## Exit Condition
The skill is complete ONLY when you have generated the Evidence Report using an Artifact creation tool or standard markdown output (do not commit it to the source tree unless explicitly requested). Once generated, summarize the report to the user and await further instructions.
