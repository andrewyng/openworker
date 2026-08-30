# Agent Bundle Format — Spec v0 (DRAFT)

*The normative contract for a portable, verifiable agent artifact.
Status: **draft for feedback** — nothing here is stable until v1.
Implementation target: `src/agpack/artifact/` (the code must match this
document; on conflict, this document wins).*

---

## 0. Purpose

One signed, versioned, deterministic bundle in which **all of the
following travel together and verifiably**:

- agent code (WASM tool modules),
- the system prompt,
- the tool manifest (which modules, which imports each wants),
- the memory contract (what state the agent may read/write),
- the permission policy (the maximum capabilities the agent may use),
- the eval suite (named cases with budgets).

Design goals, in priority order:

1. **Verifiable without executing.** A verifier with only the bundle
   and a public key can decide "structurally sound, policy-valid,
   signature-valid" with zero code execution. (§4, §5)
2. **Deterministic.** Same inputs → same bundle bytes. (§3)
3. **Portable.** The bundle name no runtime, no cloud, no kernel.
   Execution is by *driver* (see `agpack/portability/`), and the
   bundle's job ends at declaring what the driver must provide. (§6)
4. **Least authority.** Capability by exception, deny by default;
   resource namespaced and closed. (§5, §7)
5. **Replayable.** The audit ledger (a *runtime* concern, not a
   bundle concern — but shaped by the bundle's eval suites and memory
   contract) must be reconstructable from the bundle + a run. (§8)

---

## 1. Terminology

- **Bundle** — the container: canonical `manifest.json` + payload files.
- **Component** — one addressed unit inside a bundle (one prompt, one
  tool module, the memory contract, the permission policy, the eval suite).
- **Scope** — one capability; a closed enum (see §5).
- **Budget** — one run's fuel / memory / wall-time caps (see §5).
- **Driver** — a concrete WASM engine binding (wasmtime, wasmer, ...)
  that a *runtime* picks when executing a bundle. Bundles do not name
  drivers.

## 2. Container layout

The canonical container is a `tar.gz` with **mtime=0 on every member**
and this exact member set (relative order in the archive is
significance-free; the manifest's `files[]` is authoritative):

```
manifest.json
files/<path, slash-joined, e.g. files/tools/fetch.wasm>
```

Rules:

- R1. No absolute paths, no `..`, no symlinks, no hardlinks.
- R2. Every file in the archive appears in `files[]`, and every entry
  in `files[]` has a file. (A mismatch is a malformed bundle: the
  validator rejects in §4 step 1.)
- R3. `gz` must be written with *no* filename, *no* mtime field set
  (mtime=0). A verifier that re-compresses must use the same flags;
  the *trust unit* is content, not compression.
- R4. The bundle is the **trust unit**. Registries (OCI, §9) are
  *distribution* units and carry the bundle plus annotations; the
  signature covers bundle content, not the registry shape.

## 3. Determinism requirements

The packager **must**:

- D1. Serialize the manifest as canonical JSON: UTF-8, no BOM,
  sorted object keys (codepoint order), no insignificant whitespace,
  numbers serialized with the shortest exact representation, strings
  in NFC form.
- D2. Order `components` by `cid` and `files` by `(path, sha256)`.
- D3. Set `created_at_unix` to `floor(now / 1s) * 1s` (1-second
  granularity) — the packager's wall clock is the **only** input
  clock allowed, and it is truncated so that two packagings within
  the same second are byte-identical.
- D4. Archive with the tar flags in R3; file *payload* order in the
  archive is sorted by `files[].path`.
- D5. **Property tests must pin these**: pack the same agent dir twice
  (same key, same 1-second window) → byte-identical output. Pack with
  different key → different output (signature block differs).

## 4. `manifest.json` (schema)

```jsonc
{
  "spec_version": "0",              // string; the ONLY field whose value
                                    // changes validator behavior
  "agent_id": "acme/copywriter",    // URI slug: [a-z0-9_-]+(/[a-z0-9_-]+)*
  "publisher": "keyhint:acme-root-1",  // key hint (§10); NOT a key
  "created_at_unix": 0,             // per D3
  "components": [
    {
      "cid": "prompt/main",         // slug; unique within bundle
      "kind": "system_prompt",      // closed enum (§4.1)
      "file": {"path": "prompt.md", "sha256": "...", "bytes": 1234},
      "notes": null                 // free text, ≤ 4 KiB, display-only
    },
    {
      "cid": "tool/fetch",
      "kind": "tool",
      "file": {"path": "tools/fetch.wasm", "sha256": "...", "bytes": 20480},
      "declared_imports": ["agpack/net.fetch", "agpack/emit.text"],
      "entry_export": "run"
    },
    { "cid": "memory/contract", "kind": "memory_contract",
      "file": {"path": "memory.yaml", "sha256": "...", "bytes": 640} },
    { "cid": "policy/default", "kind": "permission_policy",
      "file": {"path": "policy.yaml", "sha256": "...", "bytes": 512} },
    { "cid": "evals/basic", "kind": "eval_suite",
      "file": {"path": "evals/basic.json", "sha256": "...", "bytes": 921} }
  ],
  "files": [
    {"path": "prompt.md",          "sha256": "...", "bytes": 1234},
    {"path": "policy.yaml",        "sha256": "...", "bytes": 512},
    {"path": "evals/basic.json",   "sha256": "...", "bytes": 921},
    {"path": "memory.yaml",        "sha256": "...", "bytes": 640},
    {"path": "tools/fetch.wasm",   "sha256": "...", "bytes": 20480}
  ],
  "sign": {                         // set at bundle time; see §10
    "scheme": "ed25519",
    "public_key": "<base64>",
    "signature": "<base64>",
    "signed_at_unix": 0
  }
}
```

### 4.1 Component kinds (closed enum, v0)

| kind | at most | required |
|---|---|---|
| `system_prompt` | N | 0 |
| `tool` | N | 0 |
| `memory_contract` | 1 | 0* |
| `permission_policy` | 1 | **1** (a bundle without a policy refuses to pack — §7) |
| `eval_suite` | N | 0 (but a tool without an eval suite is a `soft` validator note) |

\* a bundle that uses `memory.get`/`memory.set` scopes **must**
declare exactly one `memory_contract`; cross-consistency, §7.

### 4.2 Manifest invariants (validator, in order)

1. Container rules R1–R4 hold; every referenced file exists.
2. `spec_version` is one the validator supports; unknown major → hard
   fail with "upgrade your runtime".
3. JSON parses into the schema above with all types/lengths valid.
4. Every `files[].sha256` matches the recomputed hash of the
   corresponding archive member. (Tamper check — content-level.)
5. Every `components[].file` references a `files[]` entry whose
   `sha256` matches. (Dangling-ref check.)
6. Every `tool` component: `declared_imports` ⊆ {imports actually in
   the WASM module's import section (metadata read, no execution)}
   AND ⊆ the scopes of the bundle's `permission_policy`.
7. `permission_policy` is valid and within the platform max
   (§5.3 + §7); cross-consistency checks pass.
8. `sign` verifies against the presented/hinted key (§10).

## 5. Permission policy

### 5.1 Scope set (closed, v0)

```
agpack/fs.read      virtual-fs reads      (address space: the bundle's own files/)
agpack/fs.write     virtual-fs writes     (same address space; per-path allowlist)
agpack/net.fetch    pinned-origin GET     (only allowed in v0; method+origin pinned)
agpack/clock.now    logical monotonic time (sandbox-provided logical clock)
agpack/random       host CSPRNG bytes     (seeded; recorded for replay)
agpack/memory.get   read memory contract
agpack/memory.set   write memory contract (respecting field write_policy)
agpack/emit.text    the ONLY output channel
```

`agpack/metered.call` is **v0.1** (see `agpack/tools/metered.py`):
not in the v0 closed set.

### 5.2 `policy.yaml` shape

```yaml
# REQUIRED for every bundle.
scopes:
  - name: agpack/net.fetch
    params: { origins: ["https://example.com"] }   # closed, bounded (≤ 16)
  - name: agpack/memory.set
    params: { fields: ["mem.acme/copywriter/draft"] }
budget_default:                  # per-dispatch caps; evals may lower, never raise
  fuel_max: 500000
  memory_pages_max: 256          # 64 KiB pages
  wall_time_ms: 30000
```

### 5.3 Platform max-capability file

An **operator input** (never stored in a bundle) with the same shape
whose scope set is a superset of every scope any bundle may name.
Validator rule: `bundle.policy.scopes ⊆ platform_max.scopes`, else
**hard fail before signing**. The bundle may always ask for *less*;
never *more*. When the platform adds scopes, the max file changes and
existing bundles remain valid or fail cleanly — the bundle format
does not move.

## 6. Portability rule

A bundle **must not** contain: an absolute path, a hostname, a
driver name, an image reference, a kernel/OS hint, or any
machine-specific byte sequence. The *driver* is a runtime-level
choice (see `agpack/portability/driver.py`); two conforming drivers
given the same bundle, same import surface, same budget, and same
audit ledger must produce the same observable sequence of
host-import calls, the same `emit.text` output, and the same budget
consumption within the profile's declared tolerances
(`agpack/portability/compat_harness.py` — the step-4 proof).

## 7. Cross-consistency (all hard fails unless noted)

- C1. `permission_policy` present (exactly one).
- C2. `memory.get`/`memory.set` used ⇒ `memory_contract` present.
- C3. `memory.set` `fields[]` ⊆ the contract's field keys; each
  referenced field's `write_policy` permits the write.
- C4. `net.fetch` `origins[]` non-empty, closed (scheme+host+port).
- C5. A tool that declares an import the policy does not grant ⇒
  hard fail (this is also the sandbox instantiation rule — the
  bundle is *declared* unrunnable, so it should fail *before* the
  sandbox ever sees it).
- C6. Every eval case names a tool `cid` and a budget, and its
  budget ≤ `budget_default` (soft note if a tool has no evals).
- C7. `system_prompt` (if present) ≤ 256 KiB (soft note above 64 KiB).

## 8. Memory contract

```yaml
fields:
  - key: draft           # relative name; the FULL address is
    shape: "str"        # mem.<agent_id>.<key> and is a delegation
    write_policy: append_only   # append_only | replace | sealed_after_n_writes
    max_writes: null          # required iff sealed_after_n_writes
```

The memory contract is the agent's *self-modification* surface: it
names (with closed write policies) exactly what the agent is allowed
to change about itself across turns. Everything else about the
bundle — prompt, policy, tools — is immutable for a given signed
version; a different policy is a *new bundle*, not a runtime
mutation.

## 9. Eval suite shape

```json
{
  "cases": [
    {
      "name": "fetch-echo",
      "tool_cid": "tool/fetch",
      "args": {"url": "https://example.com/x"},
      "expected": {"output_prefix": "OK", "max_fuel": 100000},
      "budget_override": {"fuel_max": 200000}
    }
  ]
}
```

Evals are *data*, not code: they are inputs + budgets + expected
observables for the harness to check. A bundle with no evals is
valid but noted (§7 C6); a bundle *whose tools* have no evals is
the case a deployer should not ship.

## 10. Signature

- Scheme v0: **Ed25519**. The `sign` block stores scheme name,
  public key bytes, signature bytes, 1s-granularity timestamp.
- Signed message: canonical manifest bytes with `sign: null`
  (D1-serialized), i.e. the exact `manifest.json` content minus the
  `sign` field, minus the outer object's whitespace.
- Verification modes: **key mode** (verifier trusts the key bytes in
  the block) and **identity mode** (verifier additionally checks
  `publisher` against its registry of allowed roots). `agpack verify`
  runs key mode by default; `--identity <root-hint>` enables both.
- The *archive compression* is **not** part of the signed message.
  Signing covers the manifest content; integrity of files is covered
  by their SHA-256 entries in `files[]`; that composition is the
  trust chain — and it is what makes the signature robust to
  re-compression in transit (R4).

## 11. Versioning

- `spec_version` is a **string** (currently `"0"`). A validator may
  understand multiple versions; it must **never guess** across a
  major jump. Unknown major = hard fail with an explicit "runtime
  too old" diagnostic naming the version.
- Additive changes to a *known* version (e.g. a new `notes` field
  that verifiers ignore) do **not** bump the version.
- New scope additions (`agpack/metered.call`) are **v0.1**: the
  closed set grows by a spec amendment, and the platform max file is
  the operator's gate for whether to allow it.
- Breaking anything above is **v1**, a new `spec_version`, and old
  bundles remain valid until a runtime chooses to stop understanding
  them.

## 12. Open questions (the parts this draft is *not* deciding)

1. **Multi-language tool authoring.** v0 assumes the WASM modules
   are pre-built (Rust/C/AssemblyScript, or Pyodide-compiled Python).
   Should we ship a *tool build* step (like a Bazel for tools) or
   stay toolchain-agnostic and only name the import contract?
   Lean: stay agnostic; the *import contract* is already the
   standard.
2. **Streaming.** `emit.text` is append-and-read at dispatch end.
   A `metered.stream` scope would change the *budget* meaning (a
   stream costs more than a sum of chunks) and is a **v0.2** shape.
   Not now.
3. **Fan-out delegation.** The delegation chain (trust/delegation.py)
   is a *line*, not a tree. A fan-out (one root delegates to two
   children that both reach the same resource) is a **v1** spec
   bump and a **new** trust module; it's *not* a v0 concern.
4. **Revocation.** There is none in v0. If a future version adds
   it, it is a *platform* feature (a revocation list the *platform*
   enforces), not a *token* or *bundle* feature — this module
   deliberately leaves that door locked and unmarked.
