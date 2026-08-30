# Delegation Tokens — Format v0 (DRAFT)

*Companion spec to `agent-bundle-v0.md`. Covers the per-hop scoped
delegation tokens described in `src/agpack/trust/delegation.py`.*

Status: **draft**. The token is *not* a JWT (it may be *serialized*
as one for interop, but the canonical form is the dataclass shape
below). Nothing here is stable until v1.

---

## 1. What a token is (and is not)

A **DelegationToken** is a signed, single-scope, resource-bound,
budget-bound, time-bound assertion that:

> agent `sub`, acting within chain rooted at `iss`, may exercise
> scope `scope` on resource `resource`, within budget `budget`,
> before `expires_at_unix` — and the chain `chain` proves each hop.

A token is one scope. A multi-scope authority is a **set of tokens**,
never one token with many scopes. (This keeps every check in the
validator closed and keeps the audit ledger's `delegate` records
self-explanatory: one record per token, one record per scope.)

Not a JWT: no `header.payload.signature` envelope, no `alg`
negotiation, no `jws` parsing path. The token is a **flat structure**
with a **flat chain** and **one signature per hop** — an auditor
reads it top-to-bottom with no header parsing, no key lookup by
`alg`, no "which part is signed by which key" puzzle.

## 2. Canonical shape (JSON, for interop)

```jsonc
{
  "v": 0,
  "token_id":  "d_2d91...",          // 26-char base32, fresh per token
  "iss":       "acme/orchestrator",  // chain ROOT agent id
  "sub":       "acme/copywriter",    // current-hop agent id
  "scope":     "agpack/net.fetch",   // closed scope (agent-bundle-v0 §5.1)
  "resource":  "net.acme/copywriter.example.com",
  "budget": {
    "fuel_max": 100000,
    "memory_pages_max": 64,
    "wall_time_ms": 10000
  },
  "expires_at_unix": 1771000000,
  "parent_token_id": null,           // null iff this is the root token
  "chain": [
    {
      "hop_index": 0,
      "agent_id": "acme/orchestrator",
      "token_id": "d_9f83...",
      "chain_sig_b64": "..."         // signed over the canonical form of this hop
    }
  ]
}
```

### 2.1 Closed field set

- `v` — integer, the token-format version. Currently `0`. Unknown
  major = hard fail.
- `token_id` — base32, 26 chars, fresh per token. Used in the audit
  ledger's `delegate` records and in `parent_token_id` references.
- `iss` / `sub` — agent ids from the bundle spec (§4). `iss` is the
  chain root; a hop CANNOT name itself as `iss` unless it IS the root.
- `scope` — one of the closed scope set from agent-bundle-v0 §5.1.
  An unknown scope string = hard fail (no leniency; a token that
  names a scope that does not exist in *this validator's* closed set
  is a *future* token and a hard fail with a clear diagnostic).
- `resource` — a *namespaced* id (§3).
- `budget` — the same shape as agent-bundle-v0 §5.2 `budget_default`.
- `expires_at_unix` — 1s granularity, *logical* clock (the sandbox's
  `clock.now` scope is the only time source a caller may use).
- `parent_token_id` — null for the root; the `token_id` of the
  parent for all other hops. (NOT the parent's full token — that is
  how a replay-of-embedded-chain attack works and is forbidden.)
- `chain` — *ordered* hops from root (index 0) to the current hop
  (index `len-1`). The current hop's own signature is the LAST entry.

## 3. Resource namespaces (closed, v0)

```
mem.<agent_id>.<field_key>     # a field in the agent's memory contract
fs.<agent_id>.<virtual_path>   # a path under the bundle's virtual fs
net.<agent_id>.<origin>        # scheme+host+port (origin = pinned fetch host)
```

A token that names a resource outside these namespaces = hard fail.
A token that names a field/path/origin the *agent does not
own* (i.e. the `<agent_id>` in the resource ≠ `sub`) = hard fail:
a token can only delegate *over the agent's own* address space.
Cross-agent delegation to a *third-party's* memory field is the
**metered.call** scope (v0.1) with its own shape and its own
specification.

Rule: resources are *never* free-form strings. "A token that names
a free-form string" is a *spec violation* caught at parse time, not
a "weird policy" caught at call time.

## 4. Chain rules

- The chain is a **line**. A fan-out (one hop delegates to two
  children, both of whom reach the same resource) is a **v1** spec
  bump. The line keeps the validator trivial: each hop's parent is
  the previous hop, and the chain is a *path*, not a DAG.
- A hop CANNOT insert a token it did not sign. (A captured parent
  token + a *replayed* child token fails at the *hop signature*
  check: the child's `chain_sig_b64` covers the child's own
  `token_id` and the parent's `token_id`, so a replayed child
  token from a *different* parent would fail the parent-child link,
  and a replayed *parent* token would fail the child's
  parent-child link too. Both directions are covered by the closed
  chain link check.)
- The chain's last entry MUST be the current hop's own signature.
  (A chain whose last entry is a *previous* hop is a token whose
  authority is one hop stale — a hard fail.)
- A chain entry's `chain_sig_b64` is a signature over the canonical
  JSON of the current token EXCLUDING the current hop's own
  `chain_sig_b64` (which is the field being signed). This is the
  standard "sign everything but the signature field" rule, applied
  recursively — each hop signs the *token as it appeared when the
  hop signed it*, and the *current* hop's signature covers the
  *current* token.

## 5. Verification procedure (the validator)

In order, fail-fast with the specific rule named:

1. Parse. `v` = 0. Field types closed (an integer where a string
   is expected = parse fail, not a policy fail).
2. `scope` is in the closed set (hard fail + named).
3. `resource` is in a closed namespace (hard fail + named; the
   namespace name and the offending char are in the diagnostic).
4. `<agent_id>` in `resource` == `sub` (hard fail; see §3 rule).
5. Chain is a *line*: `chain[i].token_id` == `chain[i+1].parent
   expectation` ... concretely: `chain[0].agent_id == iss`, and
   `chain[i].parent_token_id` links ... — this is the shape; the
   exact link check is the code's detail. The *audit invariant* is
   that the chain has no cycles and the last hop is the current hop.
6. Signature check per chain entry (recompute canonical bytes
   excluding `chain_sig_b64`, verify the entry's
   `chain_sig_b64` against the chain-entry agent's public key —
   keys are looked up by the *verifier's* key registry, keyed by
   agent id).
7. `expires_at_unix` > `logical_now_unix` (the *caller's* logical
   clock, not the validator's wall clock — a validator that uses
   its *own* wall clock to check a *caller's* logical clock is a
   validator that fails on a *time zone* mismatch, which is a
   validator bug, not a token bug).
8. Budget monotonicity: for each consecutive pair (parent, child),
   `child.budget.fuel_max ≤ parent.budget.fuel_max`, same for
   memory, same for wall time. A *child extends* a parent's budget
   is a hard fail. (The *root* token is the only token that can
   set a budget; a hop can only *narrow* or *hold*. This is the
   "narrowing-only" rule for tokens, matching the delegation.py
   docstring.)

## 6. What this spec is NOT deciding (open)

1. **Fan-out.** §4 defers. A v1 token shape (a DAG, not a line)
   will need a *new* `chain` field and a *new* validator; this
   spec deliberately leaves the field name and the rule open.
2. **Revocation.** Out of scope. If added, it is a *platform*
   feature (a revocation list the platform enforces against the
   `token_id`), not a *token* field. A token that embeds its own
   revocation status is a token that is always stale — that's how
   you get a "stale-but-valid" bug in the first place.
3. **Cross-agent resource delegation.** §3 defers to the
   `metered.call` scope (v0.1). A token that crosses agent
   boundaries on a *resource* (not just a *scope*) is a v0.1+
   concern with its own shape.
4. **Key rotation for a chain mid-flight.** The chain's key
   registry is a *per-verification* lookup; a key rotated mid-chain
   is a *policy* question (does the verifier accept the new key?)
   and is a *platform* decision, not a *token* field. This spec
   does not name a rotation field.
