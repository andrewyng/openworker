# agpack — portable, verifiable agent runtime with a secure tool sandbox

A neutral runtime that loads an agent as a **single movable artifact** — code, system
prompt, tool manifest, memory contract, permission policy, and eval suite travel
together — and runs **untrusted third-party tool code inside a capability-scoped
WASM sandbox**, with signed identity, scoped per-hop delegation, and a replayable
execution audit record.

Thesis (from `emerging-problems-2026-decision.md`): the model is no longer the
bottleneck; the layer *around* it — context, trust, portability, safe execution —
is where the durable engineering value is. This repo is the open, highest-ceiling
gap in that map (families #4 + #5 fused) made concrete and shippable.

## MVP build order (each step is a subpackage)

| Step | What it delivers | Where |
|------|------------------|-------|
| 1. **Artifact format** | Bundle spec + packager. One command signs `{code, prompt, tool manifest, memory contract, permission policy, evals}` into one versioned bundle (OCI as escape hatch). | `agpack/artifact/` + `spec/agent-bundle-v0.md` |
| 2. **Sandbox** | WASM capability-scoped execution of untrusted tool code — no arbitrary syscall/fs/network. | `agpack/sandbox/` |
| 3. **Trust layer** | Signed artifacts, per-hop scoped delegation tokens, verifiable audit ledger. | `agpack/trust/` |
| 4. **Portability proof** | The same bundle executes unchanged on two different runtimes. | `agpack/portability/` |
| 5. **Metered access as a built-in tool** | Pay-per-tool-call wrapper on top of existing broker work. | `agpack/tools/` |

## Layout

```
agpack/
├── pyproject.toml          # project metadata, deps, `agpack` CLI entrypoint
├── README.md
├── Makefile                # test / lint / build-bundle stub targets
├── spec/
│   ├── agent-bundle-v0.md  # THE STANDARDS CONTRIBUTION: bundle format, signing, manifest schema
│   └── delegation-tokens.md# per-hop scoped delegation token format
├── docs/
│   ├── adr/                # architecture decision records
│   │   ├── 0001-wasm-not-vm.md
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

## Status

**Scaffold only.** Every Python module is a stub: docstrings and comments define
the intended surface; no implementation yet. The spec in `spec/agent-bundle-v0.md`
is a draft for feedback, not a stable format.

## Why Python

The control plane (packager, trust, portability harness, CLI) is host-side glue —
a first-class Python package keeps the spec + tooling approachable and testable.
The *guest* code runs in WASM (Rust/C/AssemblyScript tool authors, or Python
compiled with the same WASM toolchain), so the trust boundary does not depend on
the host language.
