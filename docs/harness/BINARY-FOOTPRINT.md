# Binary footprint baseline

Measured October 30, 2026, at commit `51d9ff22`, using Rust 1.96.0 on
**macOS ARM64 (`aarch64-apple-darwin`)**. These are individual executable file
sizes, not application bundles, memory usage or estimates for embedded Linux.

| Executable | Package | Bytes | MiB |
|---|---|---:|---:|
| `openworker` — core runtime | `openworker-runtime` | 14,340,128 | 13.68 |
| `openworker-harness` — SDK subprocess host | `openworker-sdk` | 20,961,504 | 19.99 |

The SDK host is 6.31 MiB larger in this build. The two are alternatives for
different entry points; their sizes do not need to be added together. In-process
SDK users link into their application, whose final size depends on what it uses.

## Reproduce

Build each package separately so Cargo feature unification with other workspace
packages does not inflate the core measurement:

```sh
cargo build --offline --locked --release --manifest-path harness/Cargo.toml \
  -p openworker-runtime --bin openworker
# Record this artifact before building the SDK host.
cargo build --offline --locked --release --manifest-path harness/Cargo.toml \
  -p openworker-sdk --bin openworker-harness
```

Both use the checked-in release profile: thin LTO, `strip = true`, Cargo's default
release optimization level 3 and default unwind behavior. Both are ARM64 Mach-O
executables, have no DWARF sections and passed a `--help` smoke check. They link
only Apple system libraries/frameworks in this measurement; SQLite is bundled.
No compression or additional size tuning was applied.

The deployment CLI (`ows`), service binary, management UI, model weights and
external sandbox software are excluded. GCP deployment code is in the service
CLI crate and is not a dependency of either measured binary. The SDK currently
includes PostgreSQL support and enables OTLP dependencies; the runtime has no
fine-grained embedded feature profile yet. This baseline changes no features.

Before choosing optional components, repeat on the customer's CPU/OS and measure
idle and active RSS with a representative model/tool workload. Startup-only RSS
and compressed download size should be reported separately from executable size.

[Machine-readable evidence and artifact hashes](evidence/binary-size-baseline-20261030.json).


## Guardrails/compliance SDK comparison — November 3, 2026

At implementation commit `a0264edc`, Rust 1.96.0, macOS ARM64, with the same
checked-in stripped/thin-LTO release profile:

| SDK host configuration | Bytes | MiB |
|---|---:|---:|
| Minimal (`--no-default-features`) | 26,688,672 | 25.452 |
| Full (`--features security-adapters,compliance-export`, defaults enabled) | 27,401,024 | 26.132 |
| Difference | 712,352 | 0.679 |

This comparison isolates optional features on the **same source revision**. The
increase since October 30 also includes intervening team/coding/runtime work and
must not all be attributed to guardrails. Minimal builds reject required missing
capabilities; an actual release-host TypeScript test verifies that behavior.
Both artifacts passed their `--help` smoke check. These are uncompressed executable
sizes, not memory use or application bundles.

Reproduce the SDK comparison (record/copy the first artifact before the second):

```sh
cargo build --offline --locked --release --manifest-path harness/Cargo.toml \
  -p openworker-sdk --bin openworker-harness --no-default-features
cargo build --offline --locked --release --manifest-path harness/Cargo.toml \
  -p openworker-sdk --bin openworker-harness --features security-adapters,compliance-export
```

The standalone core runtime was not remeasured for this comparison. Continue to
build it separately from the SDK when updating its baseline, to avoid Cargo
feature unification changing what is included.

[SDK measurement evidence and hashes](evidence/guardrails-sdk-size-20261103.json).
