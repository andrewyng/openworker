"""Step 1 — the agent bundle: format, packager, validator.

This is *the standards contribution*. The rest of the system (sandbox, trust,
portability, tools) is only as durable as this contract, so the module
discipline here matters:

- ``schema``     — the data model. Plain dataclasses + pydantic validation.
- ``packager``   — deterministic bundling (canonical JSON, stable file hashes).
- ``validator``  — the "verify without executing" path the CLI exposes.
- ``oci``        — lazy OCI-image escape hatch (see ADR-0003).

The normative spec lives in ``spec/agent-bundle-v0.md``. This package must
stay byte-for-byte compatible with that spec; when they disagree, the spec
wins and the package is a bug.
"""
