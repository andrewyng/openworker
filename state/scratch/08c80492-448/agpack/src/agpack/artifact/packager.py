"""Packager — the *one command* that produces a signed, versioned bundle.

Guarantees (these are the tests to write first):

1. **Determinism.** Same agent dir, same key -> same bundle bytes.
   Canonical JSON (sorted keys, no whitespace, NFC-normalized strings),
   stable file ordering by (path, sha256), 1-second ``created_at_unix``.
   Nondeterminism here silently breaks signature comparison in CI and
   makes the portability diff (step 4) noisy — treat it as a P0 bug.

2. **Atomicity.** Either a complete bundle is written, or nothing is.
   Write to a temp file, fsync, then rename. A half-written bundle on a
   flaky disk must be indistinguishable from no bundle.

3. **Refusal to over-sign.** Refuse to sign a bundle whose permission
   policy names a capability the platform's max-capability file does not
   allow. The max-capability file is a *platform* input, not bundle input.
   The bundle may *request* less, never more.

4. **Re-verify before signing.** The packager re-computes every
   ``FileRef.sha256`` from the bytes on disk and compares. A caller
   supplying a manifest with a tampered hash must get a hard error, not
   a signed bundle.

Inputs (an "agent dir"):
    agent.yaml            # identity + component list (mirrors schema.py)
    prompt.md             # system prompt (optional if no component refs it)
    tools/                # *.wasm files, each with a .wasm.yaml sidecar
                      # declaring the host imports the module wants
    memory.yaml           # memory_contract (optional)
    policy.yaml           # permission_policy (REQUIRED — a bundle without
                      # a policy refuses to pack; see the "capability by
                      # exception" rule in the spec)
    evals/                # *.json, one file per eval case

Output:
    <out>                 # deterministic tar (gz *without* timestamps*)
                      # containing: manifest.json, files/<path-per-ref>

* gzip with mtime=0 so that two ``tar.gz`` archives of the same content
  are byte-identical. (This is the standard trick; document it so a
  future "cleanup" doesn't delete it.)

The OCI escape hatch (``artifact.oci``) is a *separate* command — the
tar.gz format is the canonical form and what signatures cover. ``oci``
is for registries / CD pipelines that want the familiar shape.
"""

# Intended surface:
#   def pack(agent_dir: Path, *, max_cap_path: Path, sign_key: Path) -> Path: ...
#   def _canonical_json(manifest: AgentBundleManifest) -> bytes: ...
#   def _recompute_file_hashes(agent_dir: Path) -> dict[str, str]: ...

raise NotImplementedError("Scaffold stub — see module docstring.")
