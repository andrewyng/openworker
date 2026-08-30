# agpack — Builder Prompt #2: Step 3 — The Trust Layer

## Context (what already exists)

agpack is a portable, verifiable agent runtime. Step 2 (the sandbox) is
**complete: 108 tests green** across `sandbox/{capabilities,limits,imports,host}.py`.

Step 3 is the **trust layer** — the "why this is the product" half of agpack.
The sandbox says "this guest can't escape"; the trust layer says "and here's
*proof*":

- `trust/signing.py`     — the **cryptographic** guarantee that the manifest
                            bytes are what the publisher said they were.
- `trust/delegation.py`  — the **authority** guarantee that a call deep in a
                            chain still stays within what the chain's root allowed.
- `trust/audit.py`       — the **accountability** guarantee that an auditor can
                            reconstruct from the ledger alone what the agent did.

Together they turn "door + credential" into "authorized execution with a
replayable record."

**Contract reminder:** scope this builder session to **Step 3 only**. Implement
`signing.py`, `delegation.py`, and `audit.py` with real code + tests. Leave
`artifact/oci.py`, `portability/`, and `tools/metered.py` as stubs — do not
jump ahead.

**CRITICAL ENV NOTE:** the workspace path is **read-only** to the shell/grep/run
tools in this environment — only `read_file` works. Therefore:
- Read every module + spec linked below *first* to internalize the contracts.
- Write code by editing files through the available file-editing tools (NOT
  shell heredocs — those are rejected as "path escapes the workspace").
- Confirm the suite passes however your harness lets you. **Do not claim
  "green" without a real, reproduced run.**

## Contracts you MUST match (read these before writing a line)

- `spec/agent-bundle-v0.md` — §10 (signature), §5 (scopes/budget), §7
  (cross-consistency). On conflict, the spec wins over code.
- `spec/delegation-tokens.md` — the token format + validation procedure.
- `src/agpack/trust/__init__.py` — the unifying rule: **this module produces
  records, it never stores them.** No key generation, no key storage, no
  exporters, no retention.
- `src/agpack/sandbox/host.py` — what Step 3 *consumes*. It returns a
  `RunResult` and appends `budget` / `dispatch` / `import_call` / `delegate`
  `AuditRecord`-shaped triples to the ledger.
- `src/agpack/artifact/schema.py` — the `AgentBundleManifest` + `SignatureBlock`
  models, and the shared `canonical_json`.

---

## Module 1 — `audit.py` (the replayable ledger — build this FIRST)

`host.py` already appends ledger triples; this module is the **container** those
triples go into. This is the least security-sensitive, so start here and have
it green before moving to signing.

Build the documented surface:

```python
@dataclass(frozen=True)
class AuditRecord:
    ordinal: int                     # 0-based, fixed forever
    run_id: str
    kind: Literal["dispatch", "import_call", "delegate", "budget"]
    ts_unix: int                     # LOGICAL clock, not host wall time
    subject: str                     # agent/hop id that made the event
    detail: dict[str, Any]           # closed per-kind schema (below)

class AuditLedger:
    def append(self, rec: AuditRecord) -> None: ...
    def __iter__(self) -> Iterator[AuditRecord]: ...   # in ordinal order
    def __len__(self) -> int: ...
    def last_ordinal(self) -> int: ...

def replay(ledger, kind=None, ordinal_range=None) -> list[dict]: ...
    # PURE: reads the ledger, returns already-parsed per-kind detail views.
    # Does not modify the ledger, does not execute anything.

class LedgerCorrupt(Exception): ...   # a record fails its own kind's schema
```

Closed per-kind `detail` schema (a record whose `detail` doesn't match its
`kind` is a **hard** `LedgerCorrupt` failure — the ledger must be
self-describing):

- `dispatch`    → `tool_cid`, `args_sha256_prefix16`, `output_sha256`,
                  `budget_spent` (fuel_used/memory_pages_high/wall_ms_observed/died_on)
- `import_call` → `scope`, `resource`, `arg_fingerprint`, `host_return`, `fuel_delta`
- `delegate`    → `token_id`, `parent_token_id`, `hop_depth`, `scope`, `resource`
- `budget`      → `budget` (the declared per-dispatch cap, recorded once at ordinal 0)

**Load-bearing properties to test first (write these tests BEFORE
implementing):**

1. **Append-only + fixed ordinal.** Once a record is appended, its `ordinal`
   is immutable. A replay of the same ledger returns the same view in the same
   order.
2. **Redaction.** The argument buffer is **never** stored in full — only the
   first 16 hex chars of its SHA-256 (`args_sha256_prefix16`). An auditor who
   has the output + budget + import-call sequence can reconstruct *what the
   agent did* but never *what the agent was handed* (this is `audit.redaction`).
3. **Logical time.** `ts_unix` is the logical clock. The module must never
   read the host wall clock to stamp a record (test: a fake caller injects the
   logical ts; there is no `time.time()` call in the record path).
4. **`replay` is pure and schema-checking.** `replay(ledger, "dispatch")`
   returns only dispatch records with parsed details; a corrupt detail (wrong
   key set / wrong type) raises `LedgerCorrupt`.
5. **Self-consistency with `host.py`.** A run that produces a `budget` record,
   N `import_call` records, and one `dispatch` record is exactly recoverable
   by `replay`.

Target: **25+ tests, all green.**

## Module 2 — `signing.py` (Ed25519 — the cryptographic guarantee)

The shared function between packager and validator is the load-bearing piece:
**if `canonical_manifest_bytes` ever changes shape, verification silently
breaks** — that's a P0, because it breaks nothing *else*.

Build the documented surface:

```python
@dataclass(frozen=True)
class SignatureBlock:
    scheme: str               # v0: "ed25519"
    public_key_bytes: bytes
    signature_bytes: bytes
    signed_at_unix: int       # 1s granularity, for determinism

def sign(canonical_manifest_bytes: bytes, private_key: bytes) -> SignatureBlock: ...
def verify(canonical_manifest_bytes: bytes, block: SignatureBlock) -> None: ...
    # raises SignatureVerificationError on mismatch
def canonical_manifest_bytes(manifest: AgentBundleManifest) -> bytes: ...
    # THE shared function between packager and validator.
```

Rules to honor:

- **Message signed = canonical manifest bytes with `sign: null`.** Reuse
  `schema.canonical_json` over the manifest with `sign` nulled — the
  `canonical_json_without_sign` helper already exists. Do **not** sign the whole
  tar.gz (that would couple the signature to compression — §2 R4).
- **Key is an input, never a product.** No key generation, no key storage, no
  key rotation in v0. The `publisher` field is a *key hint* (`keyhint:<name>`),
  not a key — the public key bytes live *inside* the block, so verification is
  self-contained.
- **Determinism.** Ed25519 signatures are deterministic — two signatures of the
  same message with the same key are byte-identical. Pin this in a test (same
  bytes + same key → identical signature) because it makes the "did this file
  drift?" check trivial.
- **Key scheme.** Ed25519 (v0). The block is scheme-agnostic (stores the name),
  so a later ES256 migration is a *validator change*, not a *format change*.
- **The crypto choice is explicit about what it does NOT do.** Key generation,
  storage, rotation, and export/OTel integration are out of scope (see
  `trust/__init__.py`).

**Dependency:** `signing.py` uses Ed25519. Decide *how* — either a pinned
crypto dependency (e.g. `cryptography` or `pycryptodome`) or a tiny pure-Python
Ed25519 implementation. If you add a dependency, note it in `pyproject.toml` and
guard the import. If you vendor pure-Python, keep it minimal, correct, and
**clearly documented** — a hand-rolled crypto primitive is a review hotspot;
prefer the audited dependency and say so.

Target: **25+ tests, all green.** Test vectors: sign a known manifest, verify
accepts it, verify rejects a one-byte-manifest mutation, verify rejects a tampered
signature, verify determinism, verify the block round-trips through
`canonical_manifest_bytes`.

## Module 3 — `delegation.py` (per-hop scoped tokens — the authority half)

The most security-loaded module. The token is **not** a JWT — it's a flat,
closed-field dataclass with a flat line-chain and one signature per hop.

Build the documented surface:

```python
@dataclass(frozen=True)
class ChainHop:
    agent_id: str
    token_id: str                    # stable id for THIS hop's token
    signed_at_unix: int
    sig: bytes                       # over this hop's own (scope, resource,
                                     # budget, expires, parent_hop_id)

@dataclass(frozen=True)
class DelegationToken:
    token_id: str
    iss: str            # chain ROOT agent id (not the current hop)
    sub: str            # the current hop presenting the token
    scope: Scope        # ONE scope — a token is one-scoped
    resource: str       # namespaced, closed (§3 of the spec)
    budget: Budget
    expires_at_unix: int
    chain: tuple[ChainHop, ...]

class DelegationViolation(Exception): ...

def verify(token: DelegationToken, *, bundle_policy: CapabilityPolicy,
           logical_now_unix: int, keys: dict[str, bytes] | None = None
           ) -> DelegationToken:
    # Returns the token on success (caller threads it forward).
    # Raises DelegationViolation on any check failure.
```

Follow **§5 of `spec/delegation-tokens.md`** as the verification procedure —
fail-fast, each step names the rule:

1. `v` = 0; field types closed.
2. `scope` ∈ closed set (named hard fail if not).
3. `resource` ∈ closed namespace: `mem.<agent_id>.<field_key>`,
   `fs.<agent_id>.<virtual_path>`, `net.<agent_id>.<origin>` (name the
   offending namespace + char in the diagnostic).
4. `<agent_id>` inside `resource` == `sub` (a token can only delegate over its
   owner's address space — cross-agent = hard fail).
5. Chain is a **line** (not a DAG): `chain[0].agent_id == iss`, each hop's
   parent_id links, no cycles, last hop == current hop.
6. Per-hop signature verifies against the entry's own public key (`keys`
   keyed by agent id), recomputing canonical bytes **excluding** that hop's own
   `chain_sig_b64`.
7. `expires_at_unix` > `logical_now_unix` (the *logical* clock, not the
   validator's wall clock).
8. **Budget monotonicity:** for each (parent, child), `child.budget ≤
   parent.budget` on all three meters (fuel, memory, wall). A child *extends* a
   parent's budget = hard fail — only the root may set a budget; a hop may only
   narrow or hold.

**Design constraints (also test):**

- **Narrowing-only.** A hop may re-issue a *narrower* token (smaller scope,
  narrower resource, smaller budget) but may never *broaden* one.
- **No embedded full parent token** (replay-of-embedded-chain attack) — the
  `chain` links by `token_id`, not by embedding.
- **Replay detectable.** A captured token from a previous run has the same
  `iss`/`scope`/`resource` but a *different* `chain` — verification checks the
  chain, not the body, so a body-only replay is a hard fail.
- **No revocation** in v0. A captured token stays valid until expiry; the
  mitigation is short expiry + a fresh nonce per hop.

`verify` consumes `budget: Budget` from `sandbox.limits` (the metering link
between delegation and limits) and `scope: Scope` / `CapabilityPolicy` from
`sandbox.capabilities`.

Target: **25+ tests, all green.** Include: happy-path root token, narrowing
re-issue, budget-extension rejection, unknown-scope rejection, out-of-namespace
resource, cross-agent resource, chain-cycle / non-line rejection, expired-token,
bad-hop-signature, and the "replay same body, different chain" fail.

---

## Dependency note — reconcile the ledger import

`src/agpack/sandbox/host.py` currently imports `AuditLedger` / `AuditRecord`
from `sandbox.imports`. The audit layer is meant to live in `trust.audit`.
Decide one of:

(a) move/own the `AuditLedger` + `AuditRecord` definitions in `trust.audit`, and
    update `host.py` to import them from `trust.audit`; **or**
(b) keep the fakes where they are but ensure `trust.audit` is the source of
    truth the tests exercise.

Pick one, do it consistently, and update the `host.py` import accordingly. Flag
your choice — the two must agree on the record shape or Step 3 and Step 2 cannot
interoperate.

## Definition of Done
- `audit.py`, `signing.py`, `delegation.py` are **fully implemented** (no
  `NotImplementedError` stubs), matching the documented surfaces exactly.
- Each has a test file (`tests/test_trust_audit.py`, `tests/test_trust_signing.py`,
  `tests/test_trust_delegation.py`) totaling **25+ passing tests each**.
- The full sandbox + trust suites pass green (report exact counts).
- No out-of-scope modules touched: leave `oci.py`, `portability/`,
  `tools/metered.py` as stubs.
- Update `README.md` Status to reflect Step 3 implemented + tested, and the
  `Status`/`Why Python` lines accordingly.

## Verify
Reproduce the suite run in the read-only-sandboxed environment (see the env
note above) and report exact test counts + a real pass/fail per file. Do not
assert green without a reproduced run.
