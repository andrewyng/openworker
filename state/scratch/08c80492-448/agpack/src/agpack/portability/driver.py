"""RuntimeDriver — the *interface* the sandbox host talks to.

The *load-bearing* type in the portability story. The host is
*driver-agnostic*: it hands the driver ``(module_bytes, imports,
budget, audit)`` and asks for a ``RunResult``-shaped outcome. A driver
is *not* a "WASM engine wrapper" in the sense of "a thin shim around
wasmtime" — it is a *contract* that says:

1. **Observable-equivalence.** A *conforming* driver, given the same
   bundle, the same import surface, the same *budget*, and the same
   audit ledger, produces the *same sequence of host-import calls*
   (the *audit* records are the *observable* — the *raw fuel numbers*
   are *not*, because two legitimate engines will count differently).
   This is the *only* thing the *host* is allowed to assume across
   drivers; everything else (the *raw fuel*, the *raw memory pages*,
   the *wall time*) is a *driver-internal* concern and the *harness*
   (compat_harness.py) *normalizes* it into a *tolerance* report
   rather than a *diff*-fail.

2. **Failure is observable, too.** A driver that *traps* (wasm-time
   error: stack overflow, unreachable, out-of-fuel, out-of-memory)
   must *record* the trap in the audit ledger *before* it returns,
   with a *closed* set of trap codes. A driver that *panics* (a
   *host-side* error, e.g. the driver's own OOM) must *also* record
   an audit record (a *``kinds: error``* record, not a *``dispatch``*
   record — the *harness* diffs *kinds*, not *fate*). The *point* of
   this is that the *harness* can *trust* the *ledger* as the
   *observable*, even when the *driver* *fails*, because a *conforming*
   driver *always* leaves a *ledger*.

3. **The driver does not *own* the audit ledger.** The host *owns* the
   ledger and *passes a *reference* to the driver*. A driver that
   *truncates* the passed ledger (or *reorders* it, or *replaces* it)
   is a *hard* bug and the *harness* catches it (a *harness* check,
   not a *driver* check — the *driver's* contract is "append to the
   ledger I was *given*", the *harness's* job is to *verify* the
   ledger the driver *left* matches the *expected* append-sequence).
   This is the *same* "the trust module produces, the caller stores"
   principle from trust/__init__.py, applied to the *harness*: the
   *harness* is the *caller*.

The *interface* (what ``wasmtime`` and ``wasmer`` must both implement):

    class RuntimeDriver(Protocol):
        name: str                 # "wasmtime", "wasmer", ... — the *harness*
                                  # uses this to build the *diff report*'s
                                  # *side-a* / *side-b* labels
        def instantiate(self, module_bytes: bytes,
                        imports: dict[str, Callable],
                        budget: Budget,
                        audit: AuditLedger) -> "Instance": ...
        def dispose(self, instance: "Instance") -> None: ...

    class Instance(Protocol):
        def call(self, export: str, args: bytes) -> bytes:
            # Dispatches one tool-call. The *import surface* is
            # *already* wired (the host built it), the *budget*
            # is *already* applied (the driver's engine enforces the
            # fuel/memory limits on *its own* meter), and the *audit*
            # is *already* the host's ledger (the driver *appends to*
            # it, it doesn't *replace* it). Returns the *observable*
            # byte stream (the ``emit.text`` output) or raises a
            # *closed* set of *TrapError* subclasses.

What the *interface* deliberately does *not* include:
- No ``get_fuel_used()`` on the *interface*. The *fuel* meter is a
  *driver-internal* concern; the *host* reads the *fuel delta* out of
  the *ledger* (the ``import_call`` records carry ``fuel_delta``)
  and *sums* them. This is *deliberate*: the *ledger* is the *single
  source of truth* for the *budget*, and the *driver* is *not*
  allowed to *report* fuel in a way the *ledger* can't *verify*.
  (A driver whose *fuel counter* and *audit records* *disagree* is a
  *driver bug*; the *host* trusts the *ledger*, not the *driver*,
  and the *harness* is where that *disagreement* *surfaces* as a
  *tolerance* *fail*.)
- No ``stream_output`` — the *interface* returns a *bytes* per
  *call*. *Streaming* is a *future* feature (a *later* version of the
  *interface*, and a *new* *scope*, per the sandbox module's
  *host-push* note). A *v0* driver that *tries* to *stream* is a
  *v0* *driver* that *buffers* and *returns* the *buffer* at *call*
  *end*, which is *correct* and *simpler* and *diffable* (a *stream*
  is *not* a *bytes*; the *harness* can't *diff* it).
"""

raise NotImplementedError("Scaffold stub — see module docstring.")
