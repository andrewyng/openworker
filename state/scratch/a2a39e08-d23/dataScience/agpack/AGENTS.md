# agpack — kickoff prompt

> Copy the block below into a new session to begin building out the `agpack`
> scaffold described in `README.md` and `docs/adr/0001-wasm-not-vm.md`.

---

## Prompt

```
You are the lead engineer starting the `agpack` project (open-source, portable,
verifiable agent runtime). The repo is currently at SCAFFOLD ONLY: every Python
module is a stub whose docstrings/comments define the intended surface and no
real implementation exists yet. Work inside the existing layout below and keep
every module a stub until told otherwise — we are establishing the skeleton,
conventions, and test harness first, filling in behavior next.

## The project (from README.md)
- A neutral runtime that loads an agent as ONE MOVABLE ARTIFACT: code, system
  prompt, tool manifest, memory contract, permission policy, and eval suite
  travel together.
- It runs UNTRUSTED third-party tool code inside a capability-scoped WASM
  sandbox, with signed identity, scoped per-hop delegation, and a replayable
  execution audit record.
- Thesis: the model is no longer the bottleneck — the layer *around* it
  (context, trust, portability, safe execution) is the durable engineering
  value. This repo families #4 + #5 of the "emerging-problems-2026" map.

## Build order (each step is a subpackage) — do these IN SEQUENCE
1. Artifact format — bundle spec + packager. Signs
   `{code, prompt, tool manifest, memory contract, permission policy, evals}`
   into one versioned bundle (OCI as escape hatch).
   -> `agpack/artifact/` + `spec/agent-bundle-v0.md`
2. Sandbox — WASM capability-scoped execution of untrusted tool code (no
   arbitrary syscall/fs/network).
   -> `agpack/sandbox/`
3. Trust layer — signed artifacts, per-hop scoped delegation tokens,
   verifiable audit ledger.
   -> `agpack/trust/`
4. Portability proof — the same bundle executes unchanged on two runtimes.
   -> `agpack/portability/`
5. Metered access as a built-in tool — pay-per-tool-call wrapper.
   -> `agpack/tools/`

## Hard constraint from ADR-0001 (accepted)
The v0 tool sandbox is WASM (wasmtime / wasmer), NOT microVMs (Firecracker,
gVisor). Rationale you must respect:
- WASM imports are declared at instantiation time — a guest cannot ask for a
  capability it isn't declared. This gives "deny by default, capability by
  exception" natively. (A container's door is a syscall surface that exists and
  is then denied — an inversion.)
- Deterministic fuel metering doubles as the billing hook for step 5.
- Known v0 tradeoffs: guest code must be compiled to WASM (friction is on tool
  authors, acceptable — see spec open question 1); WASM-to-WASM interop is out
  of scope for v0; engine heterogeneity (wasmtime vs wasmer fuel accounting)
  must be normalized by the `portability/profiles.py` layer.

## Repo layout to build out
```
agpack/
├── pyproject.toml          # project metadata, deps, `agpack` CLI entrypoint
├── README.md               # already present
├── Makefile                # test / lint / build-bundle stub targets
├── spec/
│   ├── agent-bundle-v0.md  # THE STANDARDS CONTRIBUTION: bundle format, signing, manifest schema
│   └── delegation-tokens.md # per-hop scoped delegation token format
├── docs/
│   ├── adr/                # ADRs (0001-wasm-not-vm.md already present)
│   │   ├── 0001-wasm-not-vm.md  (present)
│   │   ├── 0002-single-signed-bundle.md
│   │   └── 0003-oci-escape-hatch.md
│   └── portability.md      # the X-and-Y demo that is the whole thesis
├── src/agpack/
│   ├── cli.py              # `agpack bundle` / `agpack run` / `agpack verify` / `agpack audit`
│   ├── artifact/           # step 1
│   │   ├── schema.py       # dataclasses/pydantic models for the bundle manifest
│   │   ├── packager.py     # build & bundle
│   │   ├── validator.py    # structural + policy validation
│   │   └── oci.py          # OCI image escape hatch (lazy)
│   ├── sandbox/            # step 2
│   │   ├── host.py         # WASM host: instantiate module, dispatch tool calls
│   │   ├── capabilities.py # policy model: allow/deny for fs, net, clocks, random
│   │   ├── imports.py      # host-import surface exposed to guest code
│   │   └── limits.py       # fuel/step counting, timeouts, memory caps
│   ├── trust/              # step 3
│   │   ├── signing.py      # sign & verify bundle manifests (Ed25519/ES256)
│   │   ├── delegation.py   # per-hop scoped delegation tokens
│   │   └── audit.py        # append-only, replayable execution ledger (OTel-shaped)
│   ├── portability/        # step 4
│   │   ├── driver.py       # runtime driver interface (what "a runtime" means here)
│   │   ├── profiles.py     # concrete drivers: wasmtime, wasmer, container fallback
│   │   └── compat_harness.py  # run same bundle on 2 drivers, diff behavior
│   └── tools/              # step 5
│       └── metered.py      # metered-access tool adapter (pay-per-call)
├── tests/                  # mirrors src layout; portability proof tests included
├── examples/
│   └── hello-agent/        # minimal agent bundle: prompt + one WASM tool + evals
└── .github/workflows/ci.yml
```

## How to start (do this in order, don't skip)
1. **Read first.** Read `README.md`, `spec/agent-bundle-v0.md` (if present), and
   `docs/adr/0001-wasm-not-vm.md` in full, then restate in 3–5 bullet points the
   accepted scope, the WASM constraint, and the exact first step you'll build.
   Ask me to confirm scope and the build order before writing anything.
2. **Project scaffolding.** Create `pyproject.toml` with the `agpack` package,
   a `src/agpack/` SDist/wheel layout, `agpack` CLI entrypoint, and deps needed
   for the FIRST step only (don't pull in wasmtime/wasmer until step 2).
   Configure the package so tests discover `src/agpack`.
3. **Build step 1 (artifact format) as stubs-with-surface**:
   - `spec/agent-bundle-v0.md` — write the bundle format spec: manifest schema
     (the six signed components), versioning, signing statement, and open
     questions. Treat it as a *draft standards contribution*, not a stable
     format.
   - `src/agpack/artifact/schema.py` — pydantic dataclasses/models for the
     manifest describing all six components.
   - `src/agpack/artifact/packager.py` — `pack(...)`/`unpack(...)` stubs that
     assemble the six components into one versioned bundle object; leave the
     on-disk/container encoding (OCI escape hatch) for `oci.py`.
   - `src/agpack/artifact/validator.py` — structural + policy validation stubs.
   - `src/agpack/artifact/oci.py` — lazy OCI image escape hatch (minimal / no-op
     stub with a clear TODO).
4. **CLI stub.** `src/agpack/cli.py` with `agpack bundle`, `agpack run`,
   `agpack verify`, `agpack audit` subcommands wired to argparse; stub the
   bodies for steps 2–5, implement the `bundle` path against step 1.
5. **Docs/ADRs.** Write `docs/adr/0002-single-signed-bundle.md` and
   `docs/adr/0003-oci-escape-hatch.md` in the same style as `0001`, and a
   `docs/portability.md` sketching the two-runtime demo.
6. **Makefile + CI stubs.** `Makefile` targets (`test`, `lint`, `build-bundle`),
   and `.github/workflows/ci.yml` running the test suite. Keep targets working
   against the stubs.
7. **First passing test.** At minimum, a test that packs a made-up
   `hello-agent` bundle and unpacks/validates it round-trips. Add more as each
   subsequent step is implemented.

## Ground rules
- Keep stubs honest: docstrings must state the intended interface and the exact
  failure modes; no real crypto/engine logic until its step. But each stub must
  be importable and covered by at least one passing test.
- Respect the WASM-only sandbox decision; do not propose microVMs.
- Use absolute paths / existing file contents when you read them — never assume.
- Report back: (a) what you read, (b) the confirmed scope in 3–5 bullets,
  (c) files created, (d) next step and what's blocked.
```

---

Want me to save this `AGENTS.md` into `dataScience/agpack/` (or adjust the prompt — e.g. scope it to only step 1 first, or add a concrete tool like wasmtime pinned)? The workspace here looked empty, so tell me where you'd like it written and I'll put the file there.
