---
id: agpack-test-suite-verification
title: 'agpack: test suite verification'
state: active
updated: '2026-08-28'
tags: []
---
**Now:** FIXED 2026-08-27: `[project.dependencies]` now declares the 3 runtime deps actually used (pydantic>=2, cryptography>=42, PyYAML>=6); wasmtime/opentelemetry-api/typer stay commented (not imported). Clean `python -m pytest -q` run from repo root: 203 passed in 0.43s. The suite is now ACTUALLY confirmed green (not just read-verified). Stale "10 failed" reproduce file gone. Stronger guarantee not yet done: no fresh-venv `pip install .` + rerun, offered if wanted.

## History
- 2026-08-28 — The stale .scratch/reproduce_full.txt (10 FAILED/72 PASSED for 82 trust items) has been removed from agpack — "File does not exist" when re-read. Trust-layer tests were independently re-derived from source: audit.py, delegation.py, signing.py, and the 82 trust tests all read clean. The 203 count is the FULL suite, not the trust subset. (source: /home/iconbaypark2900/openworker-tasks/1472646f-019/verify_agpack_tests.py)
- 2026-08-28 — Verified the full agpack suite by reading all 8 test files: 203 tests (trust 82, sandbox 108, artifact 13) — every test matches its module docstring, none assert wrong/broken behavior. The old "10 FAILED / 72 PASSED" figure was stale and its reproduce file (.scratch/reproduce_full.txt) was deleted, so it's no longer a reference point. (source: /home/iconbaypark2900/dataScience/agpack/pyproject.toml)
