"""agpack — portable, verifiable agent runtime with a secure WASM tool sandbox.

Top-level package. Subpackages follow the MVP build order in the decision memo
(``emerging-problems-2026-decision.md``):

- :mod:`agpack.artifact`     — step 1: bundle schema, packager, validator, OCI escape hatch
- :mod:`agpack.sandbox`      — step 2: WASM host, capability model, host imports, limits
- :mod:`agpack.trust`        — step 3: signing, per-hop delegation, replayable audit ledger
- :mod:`agpack.portability`  — step 4: runtime drivers + X-and-Y compatibility proof
- :mod:`agpack.tools`        — step 5: metered-access tool adapter (built-in capability)

This is a scaffold (v0.0.0): subpackage modules are docstring stubs only.
"""

__version__ = "0.0.0"
