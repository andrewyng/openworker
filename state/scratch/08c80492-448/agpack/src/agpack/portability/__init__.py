"""Step 4 — the portability proof: same bundle, two runtimes, one behavior.

This is the *moat* per the decision memo: "the same bundle executes
unchanged on two different edge/cloud runtimes. That single demo … is
the entire thesis." This package is where the demo lives.

Three modules, in the order of *concern*:

- ``driver``       — the *interface*, not an implementation. The
                     *driver* is the *only* thing the sandbox *host*
                     talks to (see sandbox.host design note). A driver
                     is what makes "a runtime" a *replaceable noun* in
                     this system, not a *verb*.
- ``profiles``     — the *concrete* drivers: ``wasmtime``, ``wasmer``,
                     and a *container-fallback* (a *later* profile, not
                     a v0 target — see the design note under
                     ``container_fallback`` in profiles.py).
- ``compat_harness`` — the *harness* that runs the same bundle on
                     two *different* drivers (or two *different*
                     machines) and *diffs the observable behavior*.
                     This is the step-4 *demo* and it lives here
                     because it is a *test*-shaped thing, not a
                     *product*-shaped one: the *output* of the harness
                     is a *diff report*, and the *consumer* of the
                     harness is the *CI* and the *operator*, not the
                     *sandbox*.

The *interface* is the product (see driver.py). The *harness* is the
*proof* (see compat_harness.py). The *profiles* are the *substitutes*
(see profiles.py) and they are *expected* to be *replaced* as the
ecosystem moves.
"""
