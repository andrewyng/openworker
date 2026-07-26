---
name: verification-loop
description: "A comprehensive verification system for agent sessions — build, types, lint, tests, security, and diff review in one pass."
allowed-tools: run_shell, grep, read_file
---

# Verification Loop Skill

A structured verification system to check quality gates before considering work done. Run this skill when asked "are we ready?", "ship it?", or before creating a PR/submitting deliverables.

## When to Use

- After completing a feature or significant code change
- Before creating a PR or submitting work
- When asked to verify quality before deployment
- After refactoring
- At logical task checkpoints in long sessions

## Verification Phases

### Phase 1: Build Verification

```bash
# Node.js / TypeScript
npm run build 2>&1 | tail -20

# Python (install + import check)
pip install -e . 2>&1 | tail -5
python -c "import myapp; print('OK')" 2>&1

# Go
go build ./... 2>&1 | tail -20

# Rust
cargo build 2>&1 | tail -20
```

**Decision:** If build fails, STOP and fix before continuing. Do not proceed to subsequent phases until build succeeds.

### Phase 2: Type Check

```bash
# TypeScript projects
npx tsc --noEmit 2>&1 | head -30

# Python projects
mypy . 2>&1 | head -30

# Go
go vet ./... 2>&1 | head -30

# Rust
cargo clippy 2>&1 | head -30
```

**Decision:** Report all type errors. Fix critical ones (missing imports, wrong signatures) before continuing.

### Phase 3: Lint Check

```bash
# JavaScript/TypeScript
npm run lint 2>&1 | head -30
# or: npx eslint . --max-warnings=0 2>&1 | head -30

# Python
ruff check . 2>&1 | head -30
# or: pylint . 2>&1 | head -30

# Go
golangci-lint run 2>&1 | head -30

# Rust
cargo fmt --check 2>&1 | head -10
```

**Decision:** Report all lint warnings. Fix if style consistency matters; note if the project is lenient.

### Phase 4: Test Suite

```bash
# Run tests with coverage
npm run test -- --coverage 2>&1 | tail -50

# Python
python -m pytest --cov=src --cov-report=term-missing 2>&1 | tail -40

# Go
go test -v -cover ./... 2>&1 | tail -30

# Rust
cargo test 2>&1 | tail -30
```

**Report:**
- Total tests: X
- Passed: X
- Failed: X
- Skipped: X
- Coverage: X% (lines, branches, functions)

**Decision:** Flag any failures. Flag coverage below 80% for code-critical personas.

### Phase 5: Security Scan

```bash
# Check for hardcoded secrets (common patterns)
grep -rn "sk-proj\|sk-ant\|api_key.*=.*['\"]\|password.*=.*['\"]" --include="*.ts" --include="*.js" --include="*.py" --include="*.go" --include="*.rs" . 2>/dev/null | grep -v node_modules | grep -v ".venv" | head -10

# Check for console.log / print debugging
grep -rn "console\.log\|print(.*DEBUG\|print(.*TODO" --include="*.ts" --include="*.tsx" --include="*.js" src/ 2>/dev/null | head -10

# Check for insecure patterns
grep -rn "verify=False\|eval(\|exec(\|shell=True" --include="*.py" --include="*.js" --include="*.ts" . 2>/dev/null | grep -v node_modules | grep -v ".venv" | head -10

# Check for hardcoded credentials
grep -rn "PRIVATE_KEY\|SECRET\|TOKEN.*=.*['\"]" --include="*.py" --include="*.env" . 2>/dev/null | grep -v node_modules | grep -v ".venv" | head -10
```

### Phase 6: Diff Review

```bash
# Show what changed
git diff --stat 2>/dev/null
git diff HEAD~1 --name-only 2>/dev/null

# Review changed files
git diff HEAD~1 2>/dev/null | head -100
```

Review each changed file for:
- Unintended changes
- Missing error handling
- Hardcoded secrets or API keys
- Console.log / debug print statements
- Unused imports or variables
- Proper error messages (not stack traces)

## Output Format

After running all phases, produce a verification report:

```
VERIFICATION REPORT
==================

Build:     [PASS/FAIL]
Types:     [PASS/FAIL] (X errors)
Lint:      [PASS/FAIL] (X warnings)
Tests:     [PASS/FAIL] (X/Y passed, Z% coverage)
Security:  [PASS/FAIL] (X issues)
Diff:      (X files changed)

Overall:   [READY/NOT READY]

Issues to Fix:
1. ...
2. ...
```

## Integration with Workflow

This skill complements the `tdd-workflow` skill. Use it:
- **Instead of TDD** when working on existing code that already has tests
- **After TDD** to do a full pre-submission sweep
- **At session end** to verify nothing was broken by exploration

## Quick Reference Commands

| What to check | Command |
|--------------|---------|
| Build | `npm run build` / `pip install -e .` / `go build ./...` |
| Types | `tsc --noEmit` / `mypy .` / `go vet ./...` |
| Lint | `npm run lint` / `ruff check .` / `golangci-lint run` |
| Tests | `npm test -- --coverage` / `pytest --cov=.` / `go test -cover` |
| Secrets | `grep -rn "api_key\|password\|secret" --include="*.py" .` |
| Diff | `git diff --stat` / `git diff HEAD~1` |

---

**Verification is not optional.** Never skip it before submitting work. If a phase fails, fix it before reporting "ready."
