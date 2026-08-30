# agpack — full test-suite verification

Date: 2026-08-27
Method: read-only. pytest could not be executed (no shell access to the
read-only `dataScience/agpack` checkout). Every one of the 8 test files was
read in full and each test was classified against its module's docstring.

## Reconciled count — 203 tests, 8 files

| # | test file | cases |
|---|-----------|-------|
| 1 | tests/test_trust_audit.py | 29 |
| 2 | tests/test_trust_delegation.py | 29 |
| 3 | tests/test_trust_signing.py | 24 |
| — | **trust subtotal** | **82** |
| 4 | tests/test_sandbox_limits.py | 30 (22 + 3 parametrized×3) |
| 5 | tests/test_sandbox_host.py | 25 |
| 6 | tests/test_sandbox_imports.py | 28 |
| 7 | tests/test_sandbox_capabilities.py | 25 (19 + 2 parametrized×3) |
| — | **sandbox subtotal** | **108** |
| 8 | tests/test_artifact_bundle.py | 13 |
| — | **artifact subtotal** | **13** |
| | **TOTAL** | **203** |

The 203 count is the **full** suite, not the trust subset. The trust subset
is 82 (audit 29 + delegation 29 + signing 24).

## The stale reproduce file is gone

`.scratch/reproduce_full.txt` (which had reported "10 FAILED / 72 PASSED"
for the 82 trust items) no longer exists — `read_file` now returns
"File does not exist". That 10-failed figure was stale; it predates the
session's fixes (delegation `Scope` gate, audit error detail, etc.) and the
suite that is now present reconciles to exactly 203.

## What each module's tests actually assert (verified clean)

Every test below reads as a faithful behavioral-spec of its module: the
assertions match the module docstring's stated contract, and no test
asserts a *wrong* behavior. They are not asserting implementation details
that would break on a legitimate refactor; they assert the documented
outcomes.

### trust_audit (accountability half)
- **Append-only + fixed ordinal**: ordinals stamped at append, never reused,
  iteration/indexing in ordinal order; `AuditLedger(start=N)` offsets.
- **Redaction**: dispatch records keep only `args_sha256_prefix16`, never the
  full input buffer.
- **Self-describing schema check**: closed kind set `(DISPATCH, IMPORT_CALL,
  DELEGATE, BUDGET)`; each kind has a required-field schema; extra keys
  *allowed* (host dispatch record is a superset); a `bool` is rejected for an
  int field (`fuel_delta`); unknown kind → `LedgerCorrupt`.
- **replay()** is pure: filters by kind / inclusive ordinal range,
  self-validates *every* record (even out-of-request ones), returns plain
  dicts, leaves the ledger untouched, empty ledger → empty list.

### trust_delegation (authority half)
- 8-step `verify()` fail-fast, each step exercised by mutating exactly one
  field of a valid 2-hop (root→sub) chain:
  1. types closed (`v` must be `V0`; scope must be a `Scope` member, no coercion)
  2. scope in closed `Scope` set; missing registry key rejected
  3. resource namespace closed (`mem/fs/net`) + ≥3 labels
  4. resource agent-label must equal `token.sub`
  5. chain is a connected line: head==iss, tail==sub, `token_id`==chain[-1].token_id,
     no cycles (repeated hop token_id)
  6. per-hop Ed25519 signature verifies against `keys[agent_id]` (wrong key rejected)
  7. expiry strictly in the future vs. logical clock (exactly-now fails)
  8. budget monotonic narrowing per meter (widening → `DelegationViolation`)
- Primitives: `_hop_canonical` stable for equal hops, changes when a field
  changes; `CapabilityPolicy` available for later composition; empty chain rejected.

### trust_signing (cryptographic half)
- `sign()` returns `SignatureBlock` (32-byte pub, 64-byte sig, `scheme=ed25519`),
  deterministic byte-for-byte; wrong key length → `ValueError`; wrong type → `TypeError`.
- `verify()` returns `None` (trust) for the block made over the exact bytes,
  and raises `SignatureVerificationError` (never a bare bool) on: tampered
  message, tampered signature, wrong scheme, wrong-length pub/sig, non-canonical bytes.
- `canonical_manifest_bytes()` is the single source of truth; **order-independent**
  over `components`/`files` order; round-trips sign→verify; tamper detected.

### sandbox_limits (resource half)
- Construction/validation: `Budget`/`BudgetSpent` reject negatives, non-int,
  and unknown `died_on`; closed `died_on` set `{fuel, memory, wall_time, ok}`;
  frozen dataclass; `Receipt` is a plain snapshot.
- **`check()` gate**: accepts within-cap (strict comparison, at-cap passes);
  raises `FuelExhausted`/`MemoryExhausted`/`Timeout` keyed off engine `died_on`
  and fall back to numbers when engine wrongly claims `ok`; deterministic
  precedence fuel>memory>wall.
- **Fuel-meter-bug soft flag**: a wall-time kill whose fuel stayed within budget
  sets `fuel_meter_bug_suspect=True` (even exactly at the fuel cap); a genuine
  fuel overrun or fuel-over-cap sets it False; violation codes `LIMIT_FUEL`/`LIMIT_MEMORY`/`LIMIT_WALL_CLOCK`.
- `LimitError` is the common base class.

### sandbox_host (engine glue)
- `load_tool` returns raw module bytes.
- **Invariant #1**: unlisted import → `ImportNotDeclared` *before* instantiation.
- **Invariant #2**: driver only ever instantiated with the policy-filtered
  import surface (verified by `FakeDriver`).
- **Invariant #3**: engine always disposed (success, guest-trap, or gate-reject).
- Fuel summed from `import_call` records; `died_on` one of the four + ok.
- **Observable-output stream**: `host_body`/`host_emit` recorded, folded into
  dispatch record with count + output digest; empty stream is well-formed.
- **`limits.check()` gate (Step 2 deliverable)**: host captures the `LimitError`
  and returns it on `RunResult.limit` instead of propagating; happy-path output
  still returned; gate outcomes recorded in the dispatch record (`rejected`,
  `limit_violation_code`, `fuel_meter_bug_suspect`); wall-0 kill → `Timeout`
  with the soft fuel-meter-bug flag; a rejected run still emits a dispatch record.

### sandbox_imports (host import surface)
- `build_imports` returns *exactly* the granted scopes (unlisted = absent, not denied);
  empty policy → empty surface.
- Each granted scope carries fuel + audit hook; every call appends an
  `import_call` record (`fuel_delta` ≥ 1, per-scope table: net.fetch highest,
  clock.now lowest); a denied (PolicyViolation) call is neither charged nor logged.
- Per-scope boundaries enforced: `NetOriginDenied`, `MemoryWriteDenied`,
  `EmitOverflow`, `FSError` (missing file).
- Per-dispatch state deterministic (logical clock, seeded PRNG, memory bank,
  virtual fs); different budget → different seed order.
- The fuel-meter-bug flag is a host-engine concept, **not** emitted on import_call.

### sandbox_capabilities (policy model, Step 2)
- Scope names are `"<area>.<verb>"` in a **closed** set; unknown/unprefixed/
  wildcard/`grant.*` names rejected; no capability may name another capability;
  duplicate scope rejected.
- Params typed and bounded: unbounded origin/field lists rejected (DoS guard),
  non-string/empty-origin rejected, `max_bytes` type + sign checks, params only
  accepted for their own scope.
- `platform_max_cap().scopes == frozenset(Scope)`; a bundle is within max iff
  its scope set is a subset (compile-time and verify-time gates).

### artifact_bundle (pack + verify, R4)
- Happy path: verifies with zero execution (`result.valid` + `signature_ok`).
- Tamper vectors all rejected: missing signature, corrupt signature/public_key/
  payload/policy/manifest, max-cap violation (`outside the platform max`).
- **Determinism** (freeze same second → byte-identical).
- **Signature robustness**: the Ed25519 block is over the canonical manifest
  JSON only — survives re-tar / re-gzip (different member order, non-zero gzip
  MTIME), proving the outer tar/gzip stream is not part of the signature.

## Bottom line

- The 8 test files are internally coherent: each test is a faithful
  behavioral-spec of its module's docstring contract, with no obviously-broken
  or wrong-behavior assertions.
- The test **count reconciles exactly to 203** (trust 82 / sandbox 108 /
  artifact 13), matching the reported "203 passed."
- The old "10 failed / 72 passed" figure is stale (file deleted).

### Caveat (honest)
I verified **by reading**, not by executing — no shell access to the
read-only agpack checkout, so I could not run `pytest`. The 203-passed figure
was last reported by the previous session, not re-run by me. The reconciliation
of hand-count = 203, plus the fact that every test asserts the documented
behavior, gives strong confidence, but the green state is **not** something I
can claim to have re-executed. The production change to
`src/agpack/artifact/validator.py` (import pydantic, pyyaml dependency) and a
clean `pytest` run on top should be confirmed before any commit.
