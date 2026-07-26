---
name: eval-harness
description: "Evaluation framework for measuring agent code generation quality — compare outputs against criteria, score accuracy and completeness, and benchmark model performance."
allowed-tools: run_shell, grep, read_file
---

# Eval Harness Skill

An evaluation framework for measuring code quality, agent performance, and model outputs. Use when you need to objectively assess code, compare implementations, or benchmark different approaches.

## When to Activate

- Comparing two implementations (e.g., model A vs model B output)
- Evaluating code quality before merging
- Benchmarking different approaches to the same problem
- Validating that refactoring didn't break functionality
- Assessing AI-generated code against criteria

## Evaluation Criteria

### 1. Correctness (0-10)

Does the code produce the correct output for all inputs?

```python
def evaluate_correctness(test_cases: list[dict], code_fn) -> tuple[int, list]:
    """Run code against test cases and score correctness."""
    passed = 0
    failures = []

    for i, tc in enumerate(test_cases):
        try:
            result = code_fn(tc["input"])
            if result == tc["expected"]:
                passed += 1
            else:
                failures.append({
                    "test": i,
                    "expected": tc["expected"],
                    "got": result,
                    "input": tc["input"]
                })
        except Exception as e:
            failures.append({
                "test": i,
                "error": str(e),
                "input": tc["input"]
            })

    score = (passed / len(test_cases)) * 10 if test_cases else 0
    return round(score, 1), failures
```

```typescript
function evaluateCorrectness(testCases: TestCase[], codeFn: (input: any) => any): { score: number; failures: Failure[] } {
  let passed = 0;
  const failures: Failure[] = [];

  for (let i = 0; i < testCases.length; i++) {
    const tc = testCases[i];
    try {
      const result = codeFn(tc.input);
      if (JSON.stringify(result) === JSON.stringify(tc.expected)) {
        passed++;
      } else {
        failures.push({ test: i, expected: tc.expected, got: result, input: tc.input });
      }
    } catch (e) {
      failures.push({ test: i, error: String(e), input: tc.input });
    }
  }

  return {
    score: testCases.length ? (passed / testCases.length) * 10 : 0,
    failures
  };
}
```

### 2. Completeness (0-10)

Does the code cover all required functionality?

Checklist:
- [ ] All required functions/methods implemented
- [ ] All required parameters handled
- [ ] All edge cases considered
- [ ] All error paths covered
- [ ] All documented behavior implemented

```python
def evaluate_completeness(implementation: str, requirements: list[str]) -> dict:
    """Check if all requirements are addressed in the code."""
    coverage = []
    for req in requirements:
        # Simple keyword/pattern matching (improve with AST parsing for production)
        found = req.lower() in implementation.lower()
        coverage.append({
            "requirement": req,
            "covered": found,
            "confidence": "high" if found else "low"
        })

    covered_count = sum(1 for c in coverage if c["covered"])
    score = (covered_count / len(requirements)) * 10 if requirements else 0

    return {
        "score": round(score, 1),
        "coverage": coverage,
        "missing": [c["requirement"] for c in coverage if not c["covered"]]
    }
```

### 3. Efficiency (0-10)

Is the code efficient for its use case?

```python
import time
import cProfile
import io

def evaluate_performance(code_fn, iterations: int = 1000) -> dict:
    """Measure execution time and memory usage."""
    # Time measurement
    start = time.perf_counter()
    for _ in range(iterations):
        code_fn()
    elapsed = time.perf_counter() - start

    avg_ms = (elapsed / iterations) * 1000

    # Rough score based on speed (adjust thresholds for your use case)
    if avg_ms < 1:
        efficiency_score = 10
    elif avg_ms < 10:
        efficiency_score = 8
    elif avg_ms < 100:
        efficiency_score = 6
    elif avg_ms < 1000:
        efficiency_score = 4
    else:
        efficiency_score = 2

    return {
        "avg_ms": round(avg_ms, 3),
        "total_ms": round(elapsed * 1000, 1),
        "iterations": iterations,
        "score": efficiency_score
    }
```

### 4. Readability (0-10)

Is the code easy to read and understand?

Checklist:
- [ ] Descriptive names (functions, variables, classes)
- [ ] Small functions (< 50 lines)
- [ ] Low nesting depth (< 4 levels)
- [ ] Comments explain WHY, not WHAT
- [ ] Consistent style and formatting
- [ ] No magic numbers or strings
- [ ] Follows language conventions

```python
def evaluate_readability(implementation: str) -> dict:
    """Score code readability based on common metrics."""
    lines = implementation.split('\n')
    total_lines = len([l for l in lines if l.strip() and not l.strip().startswith('#')])

    # Count average function length
    function_starts = sum(1 for l in lines if l.strip().startswith('def ') or l.strip().startswith('function '))
    avg_func_length = total_lines / max(function_starts, 1)

    # Check for deep nesting (heuristic)
    max_indent = max((len(l) - len(l.lstrip())) for l in lines if l.strip())
    indent_units = 4  # or 2, depending on language
    max_depth = max_indent // indent_units

    # Scoring
    func_length_score = max(0, 10 - (avg_func_length - 20) / 10)
    depth_score = max(0, 10 - (max_depth - 2) * 3)

    return {
        "avg_function_length": round(avg_func_length, 1),
        "max_nesting_depth": max_depth,
        "function_length_score": round(min(10, func_length_score), 1),
        "depth_score": round(min(10, depth_score), 1),
        "overall_score": round((func_length_score + depth_score) / 2, 1)
    }
```

## Evaluation Workflow

### Step 1: Define Test Cases

Create test cases that cover the expected behavior:

```python
test_cases = [
    # (description, input, expected_output)
    ("empty input returns empty", [], []),
    ("single item", [1], [1]),
    ("multiple items", [1, 2, 3], [1, 2, 3]),
    ("null input handled", None, []),
    ("invalid type handled", "not a list", []),
    ("large input", list(range(10000)), list(range(10000))),
]
```

### Step 2: Run Evaluation

```python
def run_evaluation(implementation, test_cases, requirements):
    """Run all evaluation dimensions and return composite score."""
    results = {}

    # Correctness
    results["correctness"] = evaluate_correctness(test_cases, implementation)

    # Completeness
    results["completeness"] = evaluate_completeness(implementation, requirements)

    # Performance
    results["performance"] = evaluate_performance(implementation)

    # Readability
    results["readability"] = evaluate_readability(implementation)

    # Composite score
    weights = {
        "correctness": 0.4,
        "completeness": 0.2,
        "performance": 0.2,
        "readability": 0.2,
    }
    composite = sum(
        results[dim]["score"] * weights[dim]
        for dim in results
    )

    results["composite_score"] = round(composite, 1)
    return results
```

### Step 3: Generate Report

```
EVALUATION REPORT
==================

Implementation: search_items_v2
Date: 2026-07-26
Requirements: 5 total

Dimension Scores:
- Correctness:  9.5/10 (19/20 tests passed)
- Completeness: 8.0/10 (4/5 requirements covered)
- Performance:  7.0/10 (avg 15ms per call)
- Readability:  8.5/10 (avg 18 lines/function, depth 2)

Composite Score: 8.5/10

Failures:
1. Test 15: expected "admin", got "admin " (whitespace mismatch)

Missing Requirements:
1. Support for async operations

Recommendation: APPROVE with minor fix for whitespace handling
```

## Model Comparison Template

When comparing two implementations (e.g., Model A vs Model B):

```
MODEL COMPARISON: search_items
==============================

Test Suite: 20 tests, 5 requirements

| Dimension       | Model A | Model B | Winner |
|----------------|---------|---------|--------|
| Correctness     | 9.0     | 10.0    | B      |
| Completeness    | 8.0     | 8.0     | Tie    |
| Performance     | 8.0     | 6.0     | A      |
| Readability     | 7.0     | 9.0     | B      |
| Composite       | 7.9     | 8.5     | B      |

Winner: Model B (higher composite, better correctness and readability)
Trade-off: Model A is 2x faster but less readable
```

## Automation

### Pre-Merge Evaluation Gate

```bash
# Before merging, run the full eval suite
python run_evals.py --implementation search_items_v2.py --test-suite test_search.py --requirements requirements.txt
```

### Continuous Evaluation

```python
# Track eval scores over time to catch regressions
import json
from datetime import datetime

def log_eval_score(score: dict, implementation: str):
    """Log evaluation results to a file for tracking."""
    record = {
        "timestamp": datetime.now().isoformat(),
        "implementation": implementation,
        "scores": {k: v for k, v in score.items() if isinstance(v, (int, float, str))}
    }
    with open("eval_history.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
```

## Evaluation Anti-Patterns

### FAIL: Subjective Scoring

```python
# BAD: "I think this looks good" — no criteria
score = 7  # arbitrary
```

### PASS: Objective Scoring

```python
# GOOD: Based on measurable criteria
score = (correct_tests / total_tests) * 10  # reproducible
```

### FAIL: Single Test Case

```python
# BAD: Tests only pass because the input happens to work
result = func([1, 2, 3])  # one lucky case
```

### PASS: Comprehensive Test Suite

```python
# GOOD: Tests cover edge cases, errors, and boundaries
test_cases = [
    ("normal", [1, 2, 3], expected),
    ("empty", [], expected_empty),
    ("null", None, expected_error),
    ("large", list(range(100000)), expected_large),
]
```

### FAIL: Ignoring Readability

```python
# BAD: Scores 10/10 on correctness but is unreadable
func = lambda x: [i for i in x if not any(i % j == 0 for j in range(2, int(i**0.5)+1)) and i > 1]
```

### PASS: Balanced Evaluation

```python
# GOOD: Scores correctnes AND readability AND performance
scores = {
    "correctness": 9.5,
    "readability": 7.0,
    "performance": 8.0,
    "composite": 8.5,
}
```

## Resources

- [Evaluating LLM Outputs](https://arxiv.org/abs/2310.08499)
- [What We Talk About When We Talk About Benchmarking](https://arxiv.org/abs/2301.13080)
- [HumanEval and MBPP: Code Generation Benchmarks](https://github.com/openai/human-eval)
- [Constitutional AI: Harmlessness from AI Feedback](https://arxiv.org/abs/2212.08073)

---

**Evaluate objectively.** Subjective opinions lead to inconsistent decisions. Measurable criteria lead to reproducible, defensible outcomes.
