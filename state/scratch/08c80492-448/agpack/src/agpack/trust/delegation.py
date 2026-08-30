"""Delegation — per-hop scoped tokens. The *authority* half of trust.

This is the answer to the decision memo's "a valid credential only
guarantees the door opens" line: the credential is the *signature* (this
agent bundle is who it claims to be); the *delegation token* is the
*authority* — what it's *allowed to do* at this depth in a call chain.

The core data object is a **``DelegationToken``** — a signed, scoped,
time-bounded assertion that "agent X, executing as part of call chain
C, may perform scope S on resource R for at most T time." It is
*not* a free-form JWT (though it *can* be serialized as one for
interop): it is a *structured* token with a *closed* set of fields, and
the validator for it is a *closed* set of checks.

Shape (v0):

    @dataclass(frozen=True) class DelegationToken:
        iss: str            # the agent id (URI slug) of the *chain root*
                            # (not the current hop — see "issuer" note)
        sub: str            # the agent id of the *current hop* (the
                            # one *presenting* the token at this call)
        scope: Scope        # the single scope this token covers — a
                            # token is one-scoped. A multi-scope
                            # authority is a *set* of tokens, not a
                            # one-token-many-scopes.
        resource: str       # a namespaced resource id (see "resource"
                            # note). Not a free-form string.
        budget: Budget      # the *remaining* budget the chain root
                            # allocated for this scope+resource pair at
                            # this hop. This is the metering link
                            # between delegation and limits.
        expires_at_unix: int
        chain: tuple[ChainHop, ...]
        # Where ChainHop = {agent_id, token_id, signed_at_unix, sig: bytes}

Two load-bearing design choices:

1. **Issuer is the chain root, not the current hop.**
   The *iss* claim names the *root* of the delegation chain. Every
   hop after the root (the agent that delegates *to* me) is a
   *chain entry*, not an issuer. This is deliberate and is the single
   most load-bearing design choice in this module:

   - It makes the token *traversable*. An auditor can walk
     ``token.chain`` from the current hop back to the root, verify
     each hop's signature against the hop's *own* public key (which
     is in the chain entry, not in the token body), and confirm the
     chain is *connected* (each hop's ``sub`` == the next hop's ``iss``
     ... or rather, each hop's ``sub`` == the parent hop's agent id,
     and the root's ``iss`` == the root's agent id).
   - It makes the token *composable*. A new token scoped to
     (scope S', resource R') *issued at hop N* can carry the *parent*
     token (the one at hop N-1) as a ``parent`` reference (a
     token-id, not the full token — full-token embedding is how
     delegation-chain *replay* attacks work and is explicitly
     disallowed). The *chain* is how you get from one scope to the
     next, not how you get one token to *contain* the previous one.
   - It makes *replay* detectable. A captured token from a *previous*
     run of the same agent has the same ``iss`` (root) and the same
     ``scope`` and ``resource`` but a *different* ``chain`` (different
     hop signatures, different nonces). The validator checks the
     chain, not the token body, so a replays-only-the-body attack is
     a hard fail.

2. **Resource is a namespaced id, and namespacing is closed.**
   v0 namespaces, one of which the bundle must name:

       mem.<bundle_agent_id>.<field_key>
           # a field in the bundle's own memory_contract

       fs.<bundle_agent_id>.<virtual_path>
           # a path under the bundle's *virtual* fs (the fs.read/write
           # scopes' address space — not the host fs)

       net.<bundle_agent_id>.<origin>
           # an origin (scheme+host+port) the net.fetch scope may hit

   A token that names a resource *outside* these namespaces is a
   validator *hard error*. This is the "capability by exception"
   invariant applied to *resources*, not scopes: a token can't grant
   more than the bundle's policy allowed the scope to name, and it
   can't name resources that don't *exist in the bundle*.

Verification (what a hop at depth N does, in order):
1. Token parses; ``iss`` / ``sub`` / ``scope`` / ``resource`` are all
   in the closed set. (A token that names an *unknown* scope is a
   hard fail — there is no "future-proof" leniency here.)
2. ``resource`` is in the *bundle's* allow-list for the scope. (This
   check is *per-bundle*, not *per-platform*; the platform max check
   is the validator's job, the *bundle* check is the delegation
   validator's job.)
3. The *chain* is connected: for i in 1..len(chain)-1,
   chain[i].parent_agent_id == chain[i-1].agent_id ... (i.e. the
   chain is a *path*, not a *DAG* — the v0 chain model is a *line*,
   not a tree. A fan-out delegation is a *later* version and a
   *deliberate* spec bump.)
4. Every hop signature verifies.
5. ``expires_at_unix`` is in the future (against the *logical* clock,
   not the host wall clock — the logical clock is the only time
   source a sandboxed caller is allowed to use, and the token's clock
   is the caller's clock, which is the sandbox's logical clock).
6. The token's ``budget`` is *not more* than the *parent* token's
   budget for the same (scope, resource) pair at the same depth. A
   hop that *extends* the budget beyond what the parent allowed is a
   hard fail — the *root* is the only thing that can raise a budget,
   and it does so *at the root*, not at a hop.

What this module does *not* do (deliberate):
- No *token issuance* for the root. The root token is *created by the
  bundle*, not by a hop. A hop can *re-issue* a scoped-down token
  (a narrower scope, a narrower resource, or a smaller budget —
  narrowing is the *only* thing a hop is allowed to do to a token),
  but it cannot *broaden* one.
- No *token* *revocation*. v0 has no revocation; a captured token
  stays *valid until expiry* if it's replayed within the chain. The
  mitigation is *short expiry* + a *nonce* (the chain entry signature
  is a *nonce* in practice — a signature over the parent chain + the
  hop's own identifier + a fresh random, so a replayed token has a
  different chain and fails step 3). If a future version needs
  *hard* revocation, it's a *platform* feature (a revocation list the
  *platform* enforces), not a *token* feature.
"""

# Intended surface:
#   @dataclass(frozen=True) class ChainHop:
#       agent_id: str
#       token_id: str                    # a stable id for *this* hop's token
#       signed_at_unix: int
#       sig: bytes                       # the hop's own signature over its
#                                        # own (scope, resource, budget,
#                                        # expires, parent_hop_id)
#   @dataclass(frozen=True) class DelegationToken:
#       iss: str; sub: str; scope: Scope; resource: str
#       budget: Budget; expires_at_unix: int
#       chain: tuple[ChainHop, ...]
#       token_id: str
#   class DelegationViolation(Exception): ...
#   def verify(token: DelegationToken, *, bundle_policy: CapabilityPolicy,
#              logical_now_unix: int, keys: dict[str, bytes]) -> DelegationToken:
#       # Returns the token (on success) so the caller can thread it
#       # forward; raises DelegationViolation on any check failure.

raise NotImplementedError("Scaffold stub — see module docstring.")
