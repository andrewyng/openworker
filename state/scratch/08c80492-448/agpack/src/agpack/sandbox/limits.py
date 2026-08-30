"""Limits — the *resource* half of the sandbox.

What the guest may do *in how much* of the host's time/memories.
Three independent meters, one budget object:

- ``fuel``            — a monotonically-increasing per-run token that the
                        WASM engine charges for every *observable* step
                        (import call, memory growth, loop iteration via
                        the fueled engine). A hard cap per run. This is
                        the meter the audit ledger reads *after* each
                        tool dispatch (not per step — that's too fine;
                        the fuel counter is the host's truth).
- ``memory_pages``    — the WASM linear-memory page ceiling (64 KiB per
                        page in the canonical profile). Growth above
                        this is a *fuel exhaustion* (guest sees
                        "out of fuel"), not a crash — the guest is
                        *supposed* to be able to observe this as a
                        budget failure, not a process death.
- ``wall_time_ms``    — a host-side wall-clock timeout. Belt-and-suspenders
                        on fuel: if the fuel meter itself has a bug and
                        the guest can loop "for free," the wall-clock
                        timeout still kills the run. This is the
                        *last* line of defense and the only one that
                        involves the host wall clock — everything above
                        it is guest-observable.

Design notes:

- All three are *per tool dispatch*, not per bundle run. A bundle run
  may dispatch many tools; each dispatch gets its own fuel/memory budget
  from the *bundle-level* budget the policy declared. The sum of tool
  budgets is what the audit ledger reports as "total fuel" for the run.
  This is the *metering* hook step 5 (the metered-access tool) bills
  against: one dispatch = one billable unit, and the "unit price" is
  the fuel consumed, not the wall time.
- The fuel meter is *the* source of truth for what the guest did.
  The wall-clock timeout is *not* a source of truth — it's a hard
  kill-switch. If a run dies on wall-clock but not on fuel, the audit
  ledger flags it as a **possible fuel-meter bug** (`soft` violation,
  code ``LIMIT_WALL_CLOCK``) rather than silently passing.
- Budget objects are *frozen* after construction; the sandbox engine
  mutates a *copy* it owns (the *spent fuel* counter), and the budget
  itself is what the validator / audit ledger compare against. Two
  different objects, one contract.
"""

# Intended surface:
#   @dataclass(frozen=True) class Budget:
#       fuel_max: int
#       memory_pages_max: int
#       wall_time_ms: int
#   @dataclass(frozen=True) class BudgetSpent:
#       fuel_used: int
#       memory_pages_high: int
#       wall_ms_observed: int
#       died_on: Literal["fuel", "memory", "wall_time", "ok"]
#   def check(spent: BudgetSpent, budget: Budget) -> BudgetSpent: ...
#       # raises FuelExhausted / MemoryExhausted / Timeout as appropriate

raise NotImplementedError("Scaffold stub — see module docstring.")
