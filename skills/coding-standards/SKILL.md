---
name: coding-standards
description: "Baseline cross-project coding conventions for naming, readability, immutability, and code-quality review. Use framework-specific skills for detailed patterns."
allowed-tools: read_file, grep
---

# Coding Standards & Best Practices

DIRECTIVE: You MUST adhere to these baseline coding conventions across all projects. Do not deviate unless explicitly instructed by the user or project-specific configuration.

## Core Constraints

1. **Readability First:** Favor clarity over cleverness. Use clear, descriptive variable and function names.
2. **KISS & YAGNI:** Implement the simplest solution that works. Do not over-engineer or add speculative generality.
3. **DRY:** Extract common logic into reusable functions/modules.
4. **Immutability:** Strongly prefer immutable data structures and operations (e.g., returning new objects/arrays instead of mutating in place).
5. **Error Handling:** Implement comprehensive error handling with properly typed or contextual exceptions. Always catch and log specific errors rather than swallowing them.
6. **Async Operations:** Run independent asynchronous operations in parallel (e.g., `Promise.all` or `asyncio.gather`) rather than sequentially.
7. **Performance:** Strictly avoid N+1 queries by using eager loading (e.g., `prefetch_related`, `select_related`, or SQL JOINs). Prefer built-in language functions for iterations.
8. **Code Smells (Prohibited Patterns):**
   - No functions longer than 50 lines.
   - No deep nesting (return early instead of nesting `if` statements).
   - No magic numbers (use named constants).
   - No "God Objects" (separate responsibilities).

## File Organization & Comments
- Follow the existing project structure (e.g., `src/components`, `src/lib`, `tests/`).
- Match the existing file naming conventions (camelCase, PascalCase, snake_case) as observed in the project.
- Comments MUST explain the "WHY", not the "WHAT". Use docstrings for public APIs.

## Testing Standards
- Use the Arrange-Act-Assert (AAA) pattern for all tests.
- Write descriptive test names that explain exactly what behavior is being tested and the expected outcome.
- Ensure each test is fully isolated and does not depend on state from previous tests.

## Exit Condition
Apply these standards implicitly during any coding task. If specifically invoked for a code review task, the skill is complete when you have provided a structured list of violations mapped to the constraints above and suggested refactoring steps.
