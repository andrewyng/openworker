"""Signing — the cryptographic guarantee that the bundle is what it says.

Algorithms and key shapes (v0):

- **Signature scheme:** Ed25519 for v0 (small key, small signature,
  one-shot, no ECDH complexity). Ed25519 is the right *default* for a
  v0 that doesn't need to interoperate with an existing PKI yet; the
  signature *block* in the manifest is algorithm-agnostic (it stores
  the scheme name, the public key bytes, and the signature bytes), so
  migrating to ES256 / ECDSA-P-256 later is a *validator change*, not a
  *format change*.
- **What gets signed:** the *canonical bytes* of the manifest with the
  ``sign`` field zeroed. The packager computes this, then the signer
  signs it, then the packager writes the ``sign`` field into the
  manifest, then tar.gz's the result. The *entire tar.gz is not
  signed* — that would couple the signature to the *compression*, and
  compression is a host-level concern (a different tar version on a
  different machine could produce different bytes for the same
  content). Signing the *canonical manifest* (a pure function of the
  bundle content) is what makes the signature *portable* in the same
  sense the bundle must be.
- **Key hint:** the ``publisher`` field in the manifest is a *key
  hint*, not a key. It's the *address* of the key (a URI or a stable ID)
  that the *verifier* looks up in its own key directory. The public
  key bytes are *in the signature block* (self-contained verification),
  but a *trusted* verifier should cross-check the key hint against
  its own registry of allowed publishers; a *self-verifying* verifier
  (no registry) trusts the key bytes in the block. This two-mode
  behavior is the same as cosign / sigstore's "key" vs. "identity"
  modes and is *deliberate* — it's the only way to make the verify
  command work both offline (key mode) and online (identity mode)
  without a runtime config.

Verification (what ``agpack verify`` calls, in order):
1. Check the signature *scheme* is one the verifier supports.
2. Recover the public key from the ``sign`` block (key mode) OR the
   verifier's key registry (identity mode).
3. Re-derive the canonical bytes (the *same* function the packager
   used — this is the **cryptographic** link between packager and
   validator; a drift here is a P0 because it silently breaks
   verification without failing any other check).
4. Recover the *message* from the signature and compare to the
   re-derived bytes. (Ed25519 is deterministic; two signatures of the
   same message with the same key are *byte-identical*, which makes the
   comparison trivial and testable.)
5. Return the verified ``AgentBundleManifest``. The caller is now
   *allowed* to trust the content of the manifest; what it is *not*
   allowed to trust is the content of the *files* the manifest
   references (that's the validator's job, step 1 of the artifact
   package).

What this module does *not* do (deliberate):
- No key *generation* in this module. Key generation is an *operator*
  concern; the trust module signs with a key it's *given*. A key in
  the trust module's memory is an *input*, never a *product*.
- No key *storage*. Same reason. The trust module is a *function*,
  not a *service*; an operator who wants key management wants a
  KMS, not a library.
- No key *rotation* in v0. A rotated key is a *new publisher identity*;
  the old key's signatures remain verifiable against the old key hint,
  and the new key's signatures against the new hint. Rotation is a
  *policy* decision (which publisher to trust), not a *crypto* one —
  and the trust module is crypto, not policy.
"""

# Intended surface:
#   @dataclass(frozen=True) class SignatureBlock:
#       scheme: str                      # v0: "ed25519"
#       public_key_bytes: bytes
#       signature_bytes: bytes
#       signed_at_unix: int              # 1-sec granularity, for determinism
#   def sign(canonical_manifest_bytes: bytes, private_key: bytes) -> SignatureBlock: ...
#   def verify(canonical_manifest_bytes: bytes, block: SignatureBlock) -> None: ...
#       # raises SignatureVerificationError on mismatch
#   def canonical_manifest_bytes(manifest: AgentBundleManifest) -> bytes: ...
#       # THE shared function between packager and validator. If this
#       # function ever changes shape, it's a *major* spec bump.

raise NotImplementedError("Scaffold stub — see module docstring.")
