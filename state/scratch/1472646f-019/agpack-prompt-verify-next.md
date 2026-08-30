# agpack — Builder Prompt: Verify Step 3 (trust layer) is actually green

## What this prompt does
Verify that the trust layer (Step 3) that the *prior* builder session left is
real AND actually passes — not just "files exist." Then follow whichever branch
the verification points to.

**DO NOT** add new features, touch `portability/`, `tools/`, or `artifact/` beyond
what fixing a broken test requires. The goal is a *verified green suite*, then a
clear next step.

## The workspace state you're landing in
- `src/agpack/trust/` contains three **fully implemented** modules (no stubs,
  no `NotImplementedError`): `audit.py`, `signing.py`, `delegation.py`.
- `tests/` contains three matching test files:
  `test_trust_audit.py`, `test_trust_signing.py`, `test_trust_delegation.py`.
- The `verify()` signature is `(token, *, logical_now_unix, keys)`. Do not add
  `bundle_policy` — the tests and impl already agree on this.
- The shell is **read-only** to the agpack path. You can `read_file` but not run
  shell/grep against it. You may, however, **copy** the agpack sources into the
  read-write workspace, install deps, and run `pytest` there — **that is how you
  verify**. Assert nothing about green without a reproduced run.

---

## Step 1 — Reproduce the suite (mandatory; do not skip)
1. Copy `src/agpack/`, `tests/`, `pyproject.toml`, `spec/` into a fresh
   read-write scratch dir.
2. Install the project + its dev deps (`pip install -e .` or `pip install
   cryptography pydantic pytest`).
3. Run each trust test file and record exact pass/fail counts:
   `python -m pytest tests/test_trust_*.py -v`
4. Save the full output to a file in the scratch dir as evidence.

If ANY test errors or fails, go to **Step 2A**. If **all** trust tests pass, go
to **Step 2B**.

---

## Step 2A — Something is not done / a test is broken → fix and re-verify
The most likely offender (flagged but unverified):
`test_trust_signing.test_canonical_manifest_bytes_is_stable` builds an
`AgentBundleManifest` whose `components[]` entries omit the required
`cid`, `kind`, and `file` fields. `schema.py` forbids extra keys and requires
those fields, so that constructor call raises a pydantic error → the test
*errors*.

Work the branch:
- Run the failing file, read the exact error, confirm the root cause.
- If the **module code** is wrong → fix the module, keep it spec-accurate.
- If only the **test** is broken (assertion is wrong, not the code) → fix the
  test to construct a valid manifest (add `cid`/`kind`/`file`), do not weaken the
  schema to accommodate a bad test.
- Re-run **all** trust test files until every one is green.
- Report: exact per-file counts, and a one-line note of what you changed and why.

Definition of Done for this branch: all three `test_trust_*.py` files green from
a real run; any change is the minimum needed to reach that; `portability/`,
`tools/`, and the trust modules' spec-faithful behavior are untouched.

---

## Step 2B — It IS fully done and green → what comes next
If the suite reproduces green, the trust layer (Step 3) is complete. The next
logical piece, per the project's own README build order, is **Step 4 — the
Portability proof**:

> The same bundle executes unchanged on two different runtimes. →
> `agpack/portability/` (`driver.py` runtime-driver interface, `profiles.py`
> concrete drivers like wasmtime/wasmer, `compat_harness.py` run one bundle on two
> drivers and diff behavior).

Concrete next-step scoping (scope the builder session to this):
- Wire the existing `sandbox.host.dispatch()` result through a first runtime
  driver so a real (or real-enough) `compat_harness` diff shows identical
  `RunResult` (output bytes, `BudgetSpent` fuel/memory/wall, `limit`) across two
  drivers.
- Keep `host.py`, `imports.py`, and all trust modules **frozen** — portability
  only *consumes* their output; it does not change the engine or the trust gate.
- No `tools/` work, no new `artifact/` fields.
- Deliver: a `portability/` package + `tests/test_portability_harness.py` that
  reproduces the same run on two drivers and asserts the observable outputs match;
  report exact test counts from a real run.
- Update `README.md` Status: mark Step 3 "implemented and tested" and Step 4 as
  "implemented", leaving `tools/` (Step 5) as the scaffold it still is.

---

## Reporting
Either branch, end with: (a) exact test counts per file, (b) what changed and
why, (c) which branch you landed on and the single recommended next session.
No "looks green" claims without the reproduced output pasted.
