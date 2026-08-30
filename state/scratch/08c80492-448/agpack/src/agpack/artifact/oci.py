"""OCI escape hatch — optional, lazy, *not* the canonical form.

Reasons it exists (see ADR-0003):
- CD pipelines, Air-gapped registries, and some CISOs want the
  familiar shape: ``docker pull``, image digests, registry signing
  (cosign). Those ecosystems are already hardened for this.
- OCI image layers give us *free* content-addressed deduping across
  bundles that share tool modules (the big win: the agent code and
  prompt may differ per tenant, but the WASM tool modules often don't).

Reasons it's lazy / optional (and why the tar.gz stays canonical):
- The tar.gz format is small, deterministic, and the signature target.
  Pulling it into an OCI image *behind* the same signature means the
  signature covers the *contents*, and the OCI digest covers the *shape*
  (which can vary by platform / packager version). Both, together, are
  a superset of trust — and the cost of maintaining two formats is
  non-trivial, so we don't do it until a real need lands.

Intended behavior (when we do build it):
- ``agpack bundle --oci <ref>`` pushes a tar.gz as the *single* layer
  of an image annotated with the manifest JSON + signature. The
  image digest is a *derived* value (registry-specific), not a trust
  input: the bundle signature is still the source of truth.
- ``agpack pull <ref>`` returns the tar.gz bytes, not an image. The
  caller (the packager / sandbox) is responsible for unpacking.
- No ``docker build``-era tooling in the runtime hot path. The OCI
  path is for *distribution*, not *execution*.

Out of scope (deliberate):
- Multi-platform images. A WASM module is already portable; an OCI
  multi-platform image adds nothing here that the tar.gz doesn't give.
- Layer signing with cosign *in addition to* our signature. That's a
  registry policy concern, not a bundle format concern.
"""

# Intended surface (when implemented):
#   def push_oci(bundle_path: Path, registry_ref: str, *, creds: RegistryCreds) -> str: ...
#   def pull_oci(registry_ref: str, *, creds: RegistryCreds) -> bytes: ...

raise NotImplementedError("Scaffold stub — see module docstring and ADR-0003.")
