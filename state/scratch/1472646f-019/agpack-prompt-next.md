# agpack — Builder Prompt #1: Finish the Sandbox Engine (imports.py + host.py)

## Context (what already exists)

The agpack sandbox (`src/agpack/sandbox/`) is the hard security boundary where
untrusted third-party tool code runs. Four modules make it up:

- `capabilities.py` — **POLICY** (`what` the tool may do). ✅ implemented, 25 tests green.
- `limits.py` — **RESOURCES** (`how much`: fuel / memory_pages / wall_time_ms). ✅ implemented, 25 tests green.
- `imports.py` — **SURFACE** (the only door the guest can call). ⬜ **STUB**.
- `host.py` — **ENGINE** (instantiate, dispatch, meter, record). ⬜ **STUB**.

The next work is the last two modules. This is the load-bearing Step-2 work:
everything downstream (trust's audit ledger, portability's driver, tools'
metering) only matters because it can *dispatch through the host*. Do not jump
ahead to `trust/`, `portability/`, or `tools/`.

**Contract reminder:** scope this builder session to the next step only — finish
`imports.py` and wire `host.py`. Leave `oci.py`, `trust/`, `portability/`,
`tools/metered.py` as stubs.

**CRITICAL ENV NOTE:** the workspace path is **read-only** to the shell/grep/run
tools in this environment — only `read_file` works. Therefore:
- Read `capabilities.py`, `limits.py`, `imports.py`, `host.py`, and the ADRs
  *first* to internalize the exact contracts (every module's docstring spells
  out the intended surface verbatim).
- Write code by editing files through the available file-editing tools (NOT
  shell heredocs — those will be rejected as "path escapes the workspace").
- Confirm "50 green" however your harness lets you: either get shell access to
  the agpack path, or run pytest in a copy where the path is reachable. **Do
  not claim "test suite green" without a real, reproduced run.**

## Module 1 — `imports.py` (the host-import surface)

This is the ONLY door between the guest and the host. Its real job: turn the
abstract `CapabilityPolicy` (`capabilities`) into a concrete, metered, auditable
Python namespace that the engine wires into the guest at instantiation.

Rules to enforce **at instantiation time** (not at first call):
1. The guest's declared imports are a **subset** of the policy's scopes. An
   unlisted import → hard instantiation error (`ImportNotDeclared`), never a
   "available-but-denied" at-call-time gap.
2. Parameter types/arities must match the scope contract (e.g. `net.fetch`
   takes one `str`). A signature mismatch is a hard instantiation error, not a
   policy decision.
3. Every registered import **must** carry a fuel hook and an audit hook. A host
   import without both → hard error at sandbox construction.

Deliver the documented surface:
- `IMPORTS: dict[str, Callable]` — the 8 default scopes
  (`agpack/fs.read`, `agpack/fs.write`, `agpack/net.fetch`, `agpack/clock.now`,
  `agpack/random`, `agpack/memory.get`, `agpack/memory.set`, `agpack/emit.text`),
  each a real host-side implementation.
- `build_imports(policy, budget, audit) -> dict[str, Callable]` — filters
  `IMPORTS` down to the policy's scopes and wires the fuel/audit hooks.

Then add tests in `tests/test_sandbox_imports.py` mirroring the style of
`test_sandbox_capabilities.py` — e.g. unknown import raises; scope filtered out
of the policy is absent; only allowed scopes are present; each import is
call-able and routes metering/audit. Target: **25+ tests, all green**.

## Module 2 — `host.py` (the engine; this is where `limits.check()` finally runs)

The engine is the glue that makes the other three modules act as one. It does
not own the WASM engine — it *borrows* one from the portability driver
(`portability.profiles.wasmtime`) via `RuntimeDriver`.

Call order, and what each must do:
1. **`load_tool(bundle, tool_cid) -> bytes`** — read the WASM module bytes for
   the named tool component. Read-only; no execution.
2. **`import()`** — instantiate the engine instance with: module bytes,
   `build_imports(policy, budget, audit)`, the `Budget`, the `AuditLedger`.
   Fail with `ImportNotDeclared` if the guest requested an undeclared import.
3. **`dispatch(bundle, policy, budget, driver, audit, *, tool_cid, args) -> RunResult`**
   — call the guest `run` export, meter after it returns/traps, record.
4. **Meter** — after dispatch, read engine fuel, peak memory pages, and wall
   time → build `BudgetSpent`.
5. **`limits.check(spent, budget)`** — **this is the gate.** Key the rejection
   off `BudgetSpent.died_on` (fuel → `FuelExhausted`, memory →
   `MemoryExhausted`, wall_time → `Timeout`). On the happy path, if the engine
   wrongly claims `ok` while over budget, fall back to the numbers and reject.
   A `Timeout` whose fuel was still within budget → `fuel_meter_bug_suspect=True`
   with `LIMIT_WALL_CLOCK` soft flag. Return unchanged if within every meter.
6. **Record** — `AuditLedger.append(DispatchRecord(...))` with: tool id,
   truncated arg-hash SHA-256 fingerprint, observable `emit.text` output, the
   `BudgetSpent`, and the ordered host-import call sequence.
7. **`RunResult`** — `run_id` (UUIDv5 from bundle ref + dispatch index, so the
   same bundle + index = always the same run id across hosts/time),
   `tool_cid`, `output` bytes, `budget: BudgetSpent`, `records`.

Then add tests in `tests/test_sandbox_host.py` — happy path within budget,
fuel/memory/wall rejections, the `ok`-but-over-budget fallback, the
fuel-meter-bug flag on wall-time-with-fuel-ok, deterministic `run_id` for a
given bundle+index, import-not-declared at instantiation. Target: **25+ tests,
all green**.

## Definition of Done
- `imports.py` and `host.py` are fully implemented (no `NotImplementedError`
  stubs), matching the docstrings' intended surface exactly.
- `limits.check()` is wired into `host.dispatch()` as the meter gate.
- Both modules have new test files totaling **25+ passing tests each**.
- Full sandbox suite (`test_sandbox_capabilities.py` + `test_sandbox_limits.py`
  + the two new files) passes green.
- Update `README.md` status to reflect the two modules now implemented, add a
  one-line entry to any CHANGELOG/threads as appropriate, and bump the
  `Status` line ("Scaffold only" → current reality).
- Do **not** implement `oci.py`, `trust/`, `portability/`, or `tools/metered.py`.

## Verify
Reproduce the suite run in the sandboxed environment (read-only path caveat
above) and report exact test counts + a real pass/fail. Do not assert green
without a reproduced run.
