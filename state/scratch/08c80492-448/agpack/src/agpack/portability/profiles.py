"""Concrete drivers + the *profile* that makes them pluggable.

The profile is the unit of substitution: a profile is a triple of
"(driver, budget-normalization, tolerance-shape)". A driver without a
profile is a raw engine; a profile without a driver is a paper target.
The harness runs profiles, not drivers — that is the load-bearing
distinction: a profile normalizes the fuel, the wall time, and the
memory pages into a comparable form (see the tolerance note under
wasmtime below), and the harness diffs the normalized form, not the raw form.

Profiles (in the order the harness runs them — the order is the
priority, not an exhaustion; the harness runs both and reports on both,
not first-then-fail):

wasmtime (primary, v0)
    - Fuel: the engine's fueled model — a monotonically-decreasing gas
      counter the engine charges per opcode. The host reads the fuel
      delta out of the audit ledger (the import_call records'
      fuel_delta fields) and sums them. (See driver.py's "the fuel
      meter is a driver-internal concern" note for why the ledger is
      the sum, not the engine's raw counter.)
    - Memory: the engine's page counter, read at call end — the PEAK,
      not the current value. A call that grew and shrank back is still
      a call that grew; the audit ledger records the peak, not the
      end state.
    - Wall time: the host's observed wall time (a host-level concern,
      not a driver-level one). The harness tolerance below is a
      wall-time tolerance, not a fuel one: fuel is budget, wall is speed.

wasmer (secondary, v0)
    - Fuel: the engine's metering — a different counter with a
      different charging model. The profile normalizes it to the same
      fuel_delta unit as wasmtime: a fuel_delta unit is a
      budget.fuel_max unit, and both engines accept the same
      budget.fuel_max number as input, so the normalization is
      identity on the input and linear on the output — a profile
      constant, not a profile algorithm.
    - Memory: same page model as wasmtime (both WASM engines share the
      linear-memory page model); the profile does not normalize memory,
      it verifies the engine's page count matches the
      budget.memory_pages_max it was given.
    - Wall time: same as wasmtime (a host-level concern).

container_fallback (a later version, not v0)
    - A container driver (gVisor / a microVM / Firecracker) is a much
      heavier driver than a WASM engine, with different failure modes:
      a container OOM is a kernel OOM, not a fuel exhaustion; a
      container network violation is a cgroup violation, not a policy
      violation. The profile that wraps a container driver is a
      different profile from the WASM profiles, and the harness runs
      the container profile separately (not in the same diff as the
      WASM profiles) because the failure modes are not comparable.
      This is a version boundary, not a design choice: a v0 harness
      that diffs a WASM run against a container run is a v0 harness
      that lies.

The tolerance (what the harness actually diffs, and why):

    The harness diffs a normalized observable:
        (1) the sequence of import_call records, in ordinal order
        (2) the final emit.text output bytes
        (3) the fuel total, within a profile-declared tolerance
    The harness does NOT diff:
        (a) the raw per-engine fuel counter — a driver-internal metric
        (b) the wall time in absolute — a host-level metric; the
            tolerance is on the ratio wall_a / wall_b, not on
            wall_a - wall_b
        (c) the driver's name — a label, not an observable

    This is what "the same bundle runs on two runtimes" means in this
    system: same observable side-effects, same output, same budget
    consumed (within tolerance), regardless of which engine executed
    it. It is not "same fuel counter" (a driver-internal lie) and not
    "same wall time" (a host-level lie).
"""

# Intended surface:
#   @dataclass(frozen=True) class Profile:
#       name: str                          # "wasmtime", "wasmer", ...
#       driver: RuntimeDriver              # the concrete engine behind the name
#       fuel_tolerance: float              # the ratio tolerance (e.g. 1.2 = +/-20%)
#       wall_time_ratio_tolerance: float   # e.g. 2.0 = one side may take up to
#                                         # 2x the other's wall time and still pass
#   PROFILES: dict[str, Profile] = {
#       "wasmtime": _wasmtime_profile(),
#       "wasmer":   _wasmer_profile(),
#       # "gvisor":   _gvisor_profile(),    # a later version (see container_fallback note)
#   }

raise NotImplementedError("Scaffold stub — see module docstring.")
