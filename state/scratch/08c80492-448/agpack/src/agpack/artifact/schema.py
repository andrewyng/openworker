"""Bundle schema — the data model behind the agent artifact contract.

The whole bundle is one JSON document (``AgentBundleManifest``) plus the
payload files it references by SHA-256. Keeping the manifest self-contained
is deliberate: a verifier that only has the manifest (e.g. a registry mirror
or a CISO's offline air-gapped box) can still answer "is this bundle
*structurally* sound?" without fetching payload bytes.

Models (pydantic v2, strict mode):

- ``AgentBundleManifest`` — top level. Pins the spec version, identity
  (publisher key hint + agent id), and a manifest of components.
- ``AgentComponent``      — one of:
      * ``system_prompt``   — plain text, byte-hashed.
      * ``tool``            — WASM module + declared host imports it wants.
      * ``memory_contract`` — schema for the agent's state read/writes across
                              turns (key -> shape + write policy).
      * ``permission_policy`` — capability policy (see sandbox.capabilities);
                              the *only* policy source the sandbox trusts.
      * ``eval_suite``      — list of named eval cases: input, expected
                              observable, and the budget they may spend.
- ``FileRef``             — ``{path, sha256, bytes}``. Every payload file
  appears here exactly once.
- ``MemoryContractField`` — a single addressable slot. ``write_policy``
  is one of ``append_only`` | ``replace`` | ``sealed_after_n_writes`` —
  this is also the surface auditors care about: it names what the agent
  is *allowed to change about itself*.

Design rules encoded by these models:
- All versions in string form (semver). ``spec_version`` is what changes
  the format; it is the only field a verifier may need to branch on.
- IDs are URI-safe slugs. No free-form strings where an ID is expected.
- ``FileRef.sha256`` is computed by the packager, never trusted from
  caller inputs — a caller-supplied hash is treated as an assertion the
  packager must re-verify before signing.
- Equality is *content* equality (hash-based), ignoring file ordering.
  This is what makes signatures deterministic and the portability diff
  (step 4) meaningful.
"""

# Intended surface:
#   @dataclass(frozen=True) class FileRef: path: str; sha256: str; bytes: int
#   @dataclass(frozen=True) class MemoryContractField: key: str; shape: str; write_policy: WritePolicy; max_writes: int | None
#   @dataclass(frozen=True) class AgentComponent: cid: str; kind: ComponentKind; file: FileRef; notes: str | None
#   @dataclass(frozen=True) class AgentBundleManifest:
#       spec_version: str           # e.g. "0"
#       agent_id: str               # URI slug
#       publisher: str              # key hint for the signing key (see trust.signing)
#       components: tuple[AgentComponent, ...]
#       files: tuple[FileRef, ...]
#       created_at_unix: int        # 1-sec granularity, for determinism
#       sign: SignatureBlock | None # set by trust.signing at bundle time


class WritePolicy:  # enum: append_only, replace, sealed_after_n_writes
    raise NotImplementedError("Scaffold stub — see module docstring.")


class ComponentKind:  # enum: system_prompt, tool, memory_contract, permission_policy, eval_suite
    raise NotImplementedError("Scaffold stub — see module docstring.")
