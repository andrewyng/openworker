"""Compat harness — the step-4 demo: same bundle, two profiles, one verdict.

The harness is the only place in the system where two different
profiles meet. Everything above the harness (the host, the trust
layer, the CLI) is profile-agnostic; everything below it (a specific
driver, a specific engine, a specific budget normalization) is
profile-specific. The harness is the stitch.

The harness's contract — what a PASS means:

    A pass is a report with:
        side_a:  (profile_a.name, the audit slice from profile_a)
        side_b:  (profile_b.name, the audit slice from profile_b)
        observable_diff: none — the two audit slices are ordinal-aligned
                         and record-equal in (kind, subject, detail),
                         except the fuel_delta field, compared within
                         the profile's fuel_tolerance
        wall_time_ratio: (wall_a / wall_b) < wall_time_ratio_tolerance
    A fail is a report with the same fields PLUS a fail_reasons list
    naming the specific check and values, e.g.
        "side_b record ordinal 14: fuel_delta 812 exceeds
         tolerance 1.2 x side_a's 700"

The harness's run (what run(bundle, profile_a, profile_b) does, in order):

1. Instantiate two SEPARATE AuditLedger instances (one per side).
   Sharing a ledger across sides is a harness bug: the
   ordinal-alignment check would trivially pass because the ledger
   IS the same, which is a harness lie.
2. Instantiate two separate RuntimeDriver instances (via the
   profiles). Same reason: a shared instance is shared state, and
   shared state is a harness lie.
3. Run the bundle's run export once per side, with the same arg
   bytes — the harness generates the arg bytes from the bundle's
   manifest, not from the caller. The arg bytes are the bundle's
   contract; the harness is running the bundle, not the caller.
4. Read the two ledgers and ordinal-align them. A ledger with a
   different number of records is a HARD fail, not a tolerance fail:
   the observable is the sequence, and a different sequence length
   means a different observable.
5. Per-ordinal diff the records. The detail diff is field-by-field
   within the record's kind schema: import_call records compare
   fuel_delta within the profile's fuel_tolerance; every other field
   must match exactly.
6. Byte-diff the two emit.text outputs (byte diff, not line diff — the
   emit.text stream is a byte stream, and a line diff is a harness lie).
7. Read the two wall times (the host's observed wall time, not the
   driver's fuel) and compute the ratio. A ratio, not a delta: a wall
   time delta depends on the machine, not the bundle.
8. Emit the report — a PassReport or a FailReport, not a bool. A bool
   is a harness lie; the report names the specific checks and values
   so the operator and CI can read it without re-running.

What the harness does NOT do (deliberate — a harness that does these
is a harness that lies):

- No --fuzzy diff. A fuzzy diff hides the diff; hiding the diff is
  the opposite of the harness's job.
- No silent skip when a profile is missing. A missing profile is a
  hard fail with a profile_missing reason, not a silent pass by
  absence.
- No --assume-deterministic. Assuming determinism is skipping the
  determinism check, which is the harness's entire job.
- No --trust-cache. A cache hit means the bundle was not run; a
  harness that reports a pass it did not earn is a lie, full stop.
"""

# Intended surface:
#   @dataclass(frozen=True) class Side:
#       profile_name: str
#       ledger: AuditLedger
#       output: bytes
#       wall_ms: int
#   @dataclass(frozen=True) class HarnessReport:
#       bundle_ref: str
#       side_a: Side
#       side_b: Side
#       pass_flag: bool
#       reasons: tuple[str, ...]      # empty iff pass_flag
#       ordinal_aligned: bool         # diagnostic: did the ledgers match in length?
#   def run(bundle: BundleFileReader,
#           profile_a: Profile,
#           profile_b: Profile,
#           *, budget: Budget | None = None) -> HarnessReport: ...
#       # budget=None means "use the bundle's own declared budget."
#       # This IS the step-4 demo: one function call, one report.

raise NotImplementedError("Scaffold stub — see module docstring.")
