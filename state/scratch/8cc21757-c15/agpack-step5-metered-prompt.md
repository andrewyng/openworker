# agpack — Step 5: Metered Access as a Built-in Tool — Prompt

## Context
agpack is a portable, verifiable agent runtime (see README.md + spec/agent-bundle-v0.md).
Steps 1–4 are IMPLEMENTED and the suite is GREEN (231 passed). This is the LAST build step.

- Step 1 (artifact): spec/agent-bundle-v0.md + agpack/artifact/  ✓
- Step 2 (sandbox): agpack/sandbox/ — capabilities.py (Scope, CapabilityPolicy),
  imports.py, limits.py (Fuel, Budget, meters, LimitError, LimitViolationCode),
  host.py ✓
- Step 3 (trust): agpack/trust/ — signing.py, delegation.py, audit.py ✓
- Step 4 (portability): agpack/portability/ — driver.py (RuntimeDriver/Instance
  protocols, ToolCall, RunResult, sha256_prefix16, DFD# envelope, port()),
  drivers.py (PurePythonDriver + DeterministicFingerprintDriver), proof.py
  (run_portability_proof, assert_equivalent), fixture tool ✓

Suite command: `python -m pytest tests/ -q`
(pythonpath=["src"] in pyproject.toml — no PYTHONPATH env var needed.)

## Goal — Step 5: Metered Access
Deliverable (per README): "Pay-per-tool-call wrapper on top of existing broker work."
agpack/tools/metered.py — a `MeteredTool` adapter that wraps ANY tool callable and
charges it from a `Fuel`/`Budget` budget, one credit per invocation, with
accounting that is itself auditable.

Keep it faithful — no hand-waving, no stubbed accounting.

## Design constraints
- Design against PROTOCOLS, not implementations. The tool you wrap is opaque
  (duck-typed). Import agpack internals ONLY under TYPE_CHECKING if at all, so
  this module has zero hard dependency on sandbox/trust at import time.
- Runtime deps: **standard library + pydantic only.** No cryptography, no
  wasmtime, no network, no broker, no pip install. If the "broker" the README
  says "on top of existing broker work" would need it, model a minimal in-process
  ledger/settlement instead (see caveat) — do NOT pull a real broker.
- Reuse the SHAPE of sandbox/limits where natural, but only as a type
  (under TYPE_CHECKING) — reconstruct a `Fuel` by hand so the module stands
  alone. Do not import agpack.sandbox.limits at runtime.

## Build
Create `agpack/tools/metered.py` with:

1. `MeteredTool` — wraps a tool callable `fn(args)->result` (or a tool object with
   `.call()`). On each invocation:
   - checks remaining fuel in the caller's `Fuel` (or per-call Budget);
   - if fuel < cost → raise `InsufficientFuel` (NOT silently pass);
   - else deduct `cost` (default 1, or the tool's declared cost) from fuel.
2. `Fuel` — a simple decrementing balance with `remaining`, `cost(name)`,
   `has(fuel)`, and `replenish(n)`. Keep it a plain dataclass.
3. `MeteredTool.meter_call(*args, **kwargs)` — the public entry. Records to a
   pluggable `Ledger`-ish object (a plain list-backed `MeterLedger` with `.record()`
   and `.history`) the: call index, tool name, fuel_cost, fuel_before, fuel_after,
   success bool, and the result's hashable signature (or an error marker).
4. A `CostPolicy` — a mapping tool-name → integer cost, with a default. Supports
   `cost_for(name)`.
5. `MeteredBroker` — a tiny in-process settlement layer: holds a `Fuel` ledger,
   registers named tools (each with a `CostPolicy` cost + the callable), exposes
   `broker.call(name, *args, **kwargs)`. This stands in for the "external broker"
   so metering is testable without pulling in a real broker.
6. Export surface: `__init__.py` re-exports the public names.

## Faithfulness / realism
- `meter_call` must ACTUALLY deduct fuel and ACTUALLY raise when empty — prove it
  with a test that runs a tool repeatedly until fuel is exhausted, checking the
  last call raised InsufficientFuel and the ledger shows the exact number of
  successful calls == initial fuel / cost.
- The accounting must be self-consistent: sum of fuel_cost over history ==
  fuel_replenished - fuel_remaining at all times. Test this invariant.
- The wrapper must not mask the underlying tool's exceptions: if `fn` raises,
  record success=False, do NOT deduct fuel, re-raise. Test this.

## Tests
Add `tests/test_metered_access.py` — aim for 25+ cases, all GREEN:
- basic charge + remaining balance after one call
- cost_policy default + per-tool cost
- exhaustion: repeatedly call until InsufficientFuel; assert success count
- fuel NOT deducted on tool exception (and exception propagates)
- ledger invariant: sum(costs) == replenished - remaining
- MeterLedger.history ordering + record fields
- replenish refills and allows resuming
- MeteredBroker.register/call with two differently-costed tools
- broker call accounting across mixed tools
- zero-fuel start refuses immediately
- negative/zero cost policy handled deterministically (document behavior)
- tool cost larger than remaining fuel refuses without partial charge

## Verification
`python -m pytest tests/ -q` must be FULLY GREEN before reporting done. Nothing
in Steps 1–4 may break. If adding this changes any other test, stop and report.

## Report back
(a) test count + pass/fail; (b) files added/changed, one line each;
(c) a 4-line proof that metering is real: run 3–5 tool calls through MeteredTool /
MeteredBroker and show remaining-fuel after each + the ledger history;
(d) any honest caveat about what the "broker"/metering models vs. a real
pay-per-call broker (billing, rate-limits, cross-process settlement).
