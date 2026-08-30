"""Capability model — the *policy* half of the sandbox.

Pure data, no I/O. This is the data model that ``policy.yaml`` in the
agent dir (and later, the ``permission_policy`` component of the bundle)
deserializes into. It is the single source of truth for *what the tool
may do at all* — the fuel/clock/memory limits in ``limits.py`` are the
*how much* half, and ``imports.py`` is the *surface* the guest sees.

The two halves are deliberately separate so that:
- the *validator* (step 1) can check a policy without a sandbox engine
  in the process (policy is data; validation is pure),
- the *sandbox* (step 2) can construct its import surface from the
  policy without caring where the bytes of the policy came from,
- the *portability harness* (step 4) can assert "same policy -> same
  import surface" across drivers without executing the guest.

Concepts (in the order you should read them):

* ``Scope`` — a *single* capability. One capability = one import
  namespace + one fuel-meter hook + one audit hook. Examples:
      - ``fs.read``    read from the *virtual* filesystem the bundle
                       exposes (a named snapshot, not the host fs).
      - ``net.fetch``  a single ``GET URL`` (domain/origin pinned by
                       policy; TLS + method + status code are the
                       observable outputs; headers are stripped).
      - ``clock.now``  logical monotonic time (see sandbox invariant #3).
      - ``random``     CSPRNG, consumed from the host's entropy source
                       and *recorded* (seeded) into the audit ledger,
                       so replays are bit-exact.
      - ``memory.get`` read the agent's own memory_contract fields
                       (the *only* way a tool can read agent state).
      - ``memory.set`` write to them, respecting the field's
                       ``write_policy`` (see artifact.schema).
      - ``emit.text``  append to the tool's observable output stream.
                      This is the only way a tool can "say" anything;
                      there is no ``stdout`` in the sandbox.
  Every capability *must* be explicitly granted, or it is not in the
  import namespace the guest module can see (sandbox invariant #2).

* ``CapabilityPolicy`` — a frozenset of ``Scope``s plus per-scope
  parameters (e.g. ``net.fetch.origins: list[str]``,
  ``memory.set.fields: list[str]``, ``emit.text.max_bytes: int``).
  This is the only parameter surface the guest can *see*; everything
  else (fuel, memory ceiling, wall-clock timeout) is a sandbox-level
  guard in ``limits`` that the guest cannot parameterize.

* The platform *max-capability* file (an operator input, NOT a bundle
  input) is a ``CapabilityPolicy`` whose scopes are a superset of every
  scope the platform is willing to grant any bundle. The validator
  (step 1) checks ``bundle.policy ⊆ platform.max`` before any signing
  happens. A bundle that requests more than the platform max will not
  pass validation, full stop. (This is how the "deny by default"
  invariant gets teeth against a *future* platform that adds new
  scopes: the platform max grows with the platform, not with bundles.)

Validation rules the module must enforce:
- ``Scope`` names are ``"<area>.<verb>"`` (lower-case, dot-separated,
  no wildcards). This is a *namespace* rule, not a regex: the set
    fs.read, fs.write, net.fetch, clock.now, random, memory.get, memory.set,
    emit.text
  is closed, and is the *only* set of valid scopes as of v0.
- No capability names another capability. (There is no
  ``grant.anything`` scope; if the platform ever adds one, the
  validator changes *and* the platform max file is re-audited.)
- Parameter values are *typed and bounded at validation time* —
  unbounded lists are a DoS vector in a sandbox with a bounded fuel
  meter, so the validator rejects them.
"""

# Intended surface:
#   class Scope(str, Enum): FS_READ = "fs.read"; ... ; EMIT_TEXT = "emit.text"
#   @dataclass(frozen=True) class CapabilityParams: origins: tuple[str, ...] = (); max_bytes: int | None = None
#   @dataclass(frozen=True) class CapabilityPolicy: scopes: frozenset[Scope]; params: dict[Scope, CapabilityParams]
#   class CapabilityViolation(Exception): ...

raise NotImplementedError("Scaffold stub — see module docstring.")
