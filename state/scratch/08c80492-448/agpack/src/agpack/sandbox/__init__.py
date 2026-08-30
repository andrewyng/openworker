"""Step 2 — secure WASM tool sandbox.

The hard security boundary of the system. Untrusted tool code runs inside
here *and only here*; anything outside is host/trusted. The four modules:

- ``capabilities`` — the *policy* model (what the tool may do). Pure data.
- ``limits``       — the *resource* boundaries (how much, how long).
- ``imports``      — the host-import *surface* the guest can call.
- ``host``         — the glue: instantiate the module, dispatch, mediate.

Security invariants (write these as tests *before* the implementation,
because the invariants are the product):

1. **No syscall, no fs, no network.** The guest's *only* path to the
   outside world is the host-import surface in ``imports``. If a guest
   module declares an import that the bundle's permission policy does not
   allow, instantiation must fail — not at first call, *at* instantiation.
   A late-fail lets a guest probe the boundary with a crafted call; the
   invariant is that the boundary doesn't exist for that guest at all.

2. **Capability by exception, deny by default.** The permission policy
   in the bundle is the *maximum* the tool may ask for. The host never
   grants a capability the policy didn't name. An unlisted import is not
   "available but denied" — it is *not present* in the import namespace
   the guest sees. (WASM imports are declared up front; this is natural
   and should be exploited, not worked around.)

3. **Determinism is a security property, not a nicety.** Non-determinism
   (clocks, random, ordering of concurrent effects) is how a guest
   escapes a *logical* sandbox even inside a hard one. The only clocks /
   entropy the guest may touch are through the host imports — and only if
   the policy grants ``clock`` / ``random`` — and then *only through the
   host-observed version* of that capability (e.g. a fuel-monotonic
   logical clock, not wall time).

4. **Fuel is always on.** Every call to a host import consumes fuel
   (import call + memory growth + loop iterations, via wasmtime's
   ``fueled`` engine or wasmer's fuel metering). A tool that can run
   an unbounded loop inside the sandbox is a denial-of-service tool,
   even if it can't escape. Fuel is the meter, and it's also what the
   metered-access tool (step 5) bills against.

5. **Memory is bounded and measured.** Linear memory is the guest's
   only heap; the import surface exposes its page count to the fuel
   meter and to the audit ledger. A guest can't ``mmap`` the host — the
   whole sandbox is this one linear memory.

Design note on the WASM runtime:
- The primary driver (``portability.profiles.wasmtime``) is chosen for
  fuel metering + a clean no-escape import model in the same engine. wasmer is the
  *second* driver for the portability proof — its different fuel
  accounting must be normalized by ``limits`` so that "the same bundle
  on two drivers" means "the same observable side-effects and the same
  *budget consumed*, within the declared tolerance," not "identical
  raw fuel numbers." (Writers who expect a bit-exact match will be
  disappointed; the tolerance contract is what step 4 actually tests.)
"""
