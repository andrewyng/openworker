"""Validator — the "verify without executing" path.

This is the surface an auditor, a CI hook, or a registry mirror calls
when it wants to answer: "Is this bundle *structurally and policy-wise*
sound?" — with zero code execution, zero network calls, and no
dependency on the sandbox package.

Checks, in order (fail fast; report carries *all* failures found so
far, plus the first fatal one):

1. ``unpacked bundle is a tar.gz, and tar members match files/ + manifest.json``
   A bundle that is a tar but is missing a file referenced by the
   manifest is malformed, not merely *bad* — reject early.
2. ``manifest.json parses as AgentBundleManifest`` and ``spec_version``
   is one this validator understands. Unknown future major versions
   hard-fail with a clear "upgrade the runtime" message — never guess.
3. ``every FileRef``'s declared sha256 matches the recomputed hash of
   the bundled file bytes. (This is the tamper check for the payload.)
4. ``every tool component`` declares a host-import list, and that list
   is a *subset* of the imports actually present in the guest module
   (wasm metadata, readable without executing), and — critically — a
   *subset* of what the bundle's own permission policy allows.
   A tool that wants more than the bundle allows is a policy violation;
   that's a hard reject, not a warning.
5. ``permission_policy`` is valid: every permission it grants is in
   the platform allow-list (``max_cap_path``); no capability is
   granted to an agent that has no corresponding memory_contract or
   eval suite declaring it (cross-consistency check, see spec §6).
6. Optional ``--key <pubkey>``: verify the signature block (delegates
   to :mod:`agpack.trust.signing`). Without a key, step 5 is the last
   structural check — signature is a trust-layer concern.

Design note:
- The validator *never* executes guest code. Reading WASM metadata
  (import lists, memory page counts) is metadata parsing, not execution;
  it is allowed and required. Anything that would run is a policy
  violation in this module.

Return type:
    ``ValidationReport{ok: bool, hard: list[Violation], soft: list[Violation]}``
    ``hard``  breaks signatures / manifest integrity / policy allow-list.
    ``soft``  is advisory (e.g. "prompt is unusually long", "no evals
    declared for tool T") — the CLI can surface it as a yellow warning
    without failing the build.
"""

# Intended surface:
#   @dataclass(frozen=True) class Violation: code: str; detail: str; ref: str | None
#   @dataclass(frozen=True) class ValidationReport: ok: bool; hard: tuple[Violation, ...]; soft: tuple[Violation, ...]
#   def validate(bundle_path: Path, *, max_cap_path: Path | None, pub_key: Path | None) -> ValidationReport: ...

raise NotImplementedError("Scaffold stub — see module docstring.")
