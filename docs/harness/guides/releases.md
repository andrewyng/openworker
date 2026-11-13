# Versions and installation

npm installs the SDK and the native runtime it requires.

## Published packages

Verified on November 13, 2026:

| Package | Version | Purpose |
| --- | --- | --- |
| `@openworkerai/harness-sdk` | **0.1.0-beta.4** | TypeScript/Node client |
| Matching platform runtime | **0.1.0-beta.3** | Rust harness executable; selected automatically by npm |
| `@openworkerai/openworker-harness-builder` | **0.1.0-beta.7** | App-building skill installer |

Install with `npm install @openworkerai/harness-sdk`, or append
`@0.1.0-beta.4` to pin this release. Keep npm optional dependencies enabled and
commit your lockfile. Node.js 20+ is supported; the complete Desktop/Web examples
require **22.12+**. Native packages cover macOS, Windows and glibc Linux on ARM64
and x64. Alpine/musl is not one of these targets.

Client beta.4 updates documentation; executable code and declarations match
beta.3. Its native dependency remains beta.3. Do not replace that binary based on
the client version alone. Applications that set `OPENWORKER_EXECUTABLE` must
supply the runtime required by their installed client.

Builder beta.7 targets SDK beta.4. The installer records its tested SDK version in
`references/installation.json`. Skill versions advance independently of the SDK.
The previous `build-with-sdk-skills` package and `openworker-apps` skill name are
legacy. Use the [current installer](../../../skills/README.md).

## What this release includes

The current runtime includes native user attachments, standalone sub-agents,
attempt-aware streaming, provider usage/caching support and optional recording.
Use [attachments](attachments.md), [delegation](subagents.md),
[streaming](streaming.md) and [observability](observability.md) for their APIs.
A remote service must advertise the corresponding capabilities too.

Application-tool images and per-session reviewer guidance have their own capability
checks. These guides identify limits; a feature in GitHub `main` is not automatically
available in every older package or custom host.

## Rust from source

The npm release is not a crates.io publication. Use a pinned Git dependency for
Rust. This docs revision uses the following source revision:

```toml
[dependencies]
openworker-sdk = { git = "https://github.com/andrewyng/openworker.git", rev = "4af48474" }
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }
futures-util = "0.3"
anyhow = "1"
```

A text-only in-process agent needs no separate helper. If your selected tools or
execution mode require a helper, build it from that same revision:

```sh
cargo build --manifest-path harness/Cargo.toml --locked --release \
  -p openworker-sdk --bin openworker-harness
```

Run that command from the pinned checkout. Supply the resulting absolute path
through the harness builder when needed. The repository Rust examples use local
path dependencies; keep the SDK and helper source revision aligned.

## Examples and source links

The website source drawer contains the example snapshot built with this guide.
The complete TypeScript examples pin the published npm package and resolve its
runtime automatically. GitHub links generally show current development source;
use a pinned checkout or the packaged skill when reproducing a released example.

For TypeScript desktop packaging, place the runtime in application resources
outside ASAR. Build and test on the target OS, including its signing requirements.
See [Desktop](desktop.md) or [Web](server.md) for the runnable track.

## Upgrade an application

1. Back up durable state.
2. Update the SDK and use its selected runtime; update explicit/custom executables too.
3. Follow [storage upgrades](upgrades.md) before opening existing state.
4. For services, drain and coordinate API/worker upgrades.
5. Resume a saved conversation and check the application's providers, tools and files.

Protocol negotiation does not make arbitrary SDK/host releases interchangeable.
Rust users with exhaustive event matches may need to handle new variants;
`RunEvent::TextDelta` carries assistant output. See [streaming](streaming.md).
