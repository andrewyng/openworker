"""Host — the glue that makes a bundle run in the sandbox.

This is where the four other modules meet:

    capabilities -- policy (what)
    limits        -- budget (how much)
    imports       -- surface (how the guest sees it)
    host          -- *the engine* (who wires them together)

Responsibilities, in call order:

1. **Load.** Take a ``BundleFileReader`` (abstract: the tar.gz from
   the packager, an OCI layer, or a test fixture) and read the WASM
   module bytes for the named tool component. *No* execution yet —
   just bytes in memory.
2. **Instantiate.** Construct the engine instance (via the portability
   driver — see step 4; the host talks to the driver, not to
   wasmtime/wasmer directly). Feed it:
      - the module bytes,
      - the import surface (from ``imports.build_imports``),
      - the fuel/memory/wall-time budget (from ``limits``),
      - the ``AuditLedger`` (from ``trust.audit``) — every host-import
        call must append an entry before it returns.
   If the guest requested an import the sandbox's surface doesn't
   declare, instantiation fails with ``ImportNotDeclared``. *This is
   the invariant: a guest cannot ask for a capability it doesn't have,
   and the failure happens here, not at first use.*
3. **Dispatch.** Call the guest's entry function (declared by the
   tool component's manifest; v0: a single ``run`` export) with the
   argument buffer the caller passed. The caller (the CLI, the metered
   tool, or a user script) does *not* pick the entry — the bundle did.
   This is deliberate: the *tool manifest* is the contract, not the
   caller.
4. **Meter.** After the dispatch returns (or traps), read the engine's
   fuel counter, the peak memory pages, and the wall-clock time the
   host observed. Build a ``BudgetSpent`` and hand it to the audit
   ledger as a single ``DispatchRecord``.
5. **Record.** The audit ledger (``trust.audit``) appends a
   ``DispatchRecord`` with: the tool id (component id from the bundle),
   the argument hash (a *truncated* SHA-256 of the arg buffer — the
   full arg buffer is available to the tool by contract, but the
   *audit* only sees its fingerprint, so an auditor can't re-derive
   the arg buffer from the log; see trust.audit for the
   ``audit.redaction`` note), the observable output (the ``emit.text``
   stream), the ``BudgetSpent``, and the sequence of host-import
   calls in the order they happened.
6. **Return.** The host returns a ``RunResult`` to the caller:
   the observable output, the ``BudgetSpent``, and the run id (a
   UUIDv5 derived from the bundle file ref + the dispatch index, so
   the same bundle + same dispatch index is *always* the same run id,
   across hosts and across time — this is what makes the audit log
   *addressable* and *replayable*).

Design notes:

- The host does not own the WASM engine. It *borrows* one from the
  portability driver. This is what makes "same bundle, two drivers"
  (step 4) a *single* code path with a *swappable* engine underneath.
  The cost of that indirection is a small interface the host has to
  maintain and a small amount of vtable overhead in the hot path;
  for a v0 that's a correct trade, and it's the same interface the
  ``portability.profiles`` module will implement.
- The host does not decide *where* a tool's observable output goes
  (stdout, a file, a queue). It hands the ``RunResult`` to the caller.
  The caller is free to ``log`` it, ``emit`` it, or ``push`` it to a
  downstream consumer. (The sandbox's ``emit.text`` scope already
  routes through a *single* buffer the host owns, so the host is the
  *only* thing that can see the output before the caller does; this
  is the redaction surface if we ever add redaction.)
- There is no *guest* to *host* callback. The WASM import surface is
  one-way: the guest calls the host. If a future feature needs
  host-to-guest push (e.g. streaming a large payload into the guest
  without the guest pulling it), that's a ``host.push`` scope, not a
  new mechanism — and it's a scope with its own fuel hook and audit
  hook, like everything else in this system.
"""

# Intended surface:
#   @dataclass RunResult:
#       run_id: str
#       tool_cid: str
#       output: bytes            # raw emit.text stream
#       budget: BudgetSpent
#       records: tuple[AuditRecord, ...]   # the audit slice for this dispatch
#   class ImportNotDeclared(Exception): ...
#   class FuelExhausted(Exception): ...
#   def load_tool(bundle: BundleFileReader, tool_cid: str) -> bytes: ...
#   def dispatch(bundle: BundleFileReader, policy: CapabilityPolicy,
#                budget: Budget, driver: RuntimeDriver, audit: AuditLedger,
#                *, tool_cid: str, args: bytes) -> RunResult: ...

raise NotImplementedError("Scaffold stub — see module docstring.")
