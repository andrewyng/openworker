# OpenWorker Harness

Build your product and user experience. Let OpenWorker run the agents.

OpenWorker Harness is a Rust agent runtime with Rust and TypeScript SDKs. It
manages model requests, tools, approvals, durable sessions and agent teams.
Embed it in a desktop application or deploy the server reference with PostgreSQL.
The shipped Rust runtime does not require Python.

This branch is a **release candidate for integration testing**, not a stable SDK
release announcement. Use a pinned checkout and build the SDK and executable from
the same revision. The existing OpenWorker Desktop application continues to use
its Python harness; this directory is additive.

## Start here

- **[Desktop QuickStart](../docs/harness/QUICKSTART.md)** — build a Windows or Mac
  app with TypeScript/Electron or Rust/Tauri.
- **[Visual QuickStart](../docs/harness/DESKTOP-QUICKSTART.html)** — open the local
  HTML file in a browser for tabbed Rust/TypeScript examples.
- **[Arcade Studio](examples/arcade/README.md)** — the flagship integration:
  a game library, cloud/local model settings, guided creation, streamed activity,
  isolated previews, approval-based publishing and revision recovery.
- **[Server getting started](../docs/harness/DEVELOPER-GETTING-STARTED.md)** —
  REST/SSE, PostgreSQL, workers and deployment references.
- **[Coding tools and unattended sessions](../docs/harness/CODING-TOOLS.md)** —
  file editing/search, managed preview processes and auto-skip questions.
- **[Memory and scheduling](../docs/harness/ASSISTANT-TOOLS.md)** — opt-in durable
  learner memory and scheduled work on SQLite or PostgreSQL.
- **[Python/Rust tool survey](../docs/harness/TOOL-PARITY.md)** — remaining parity gaps.

## What the harness handles

- Responses, Chat Completions and Anthropic Messages APIs; configurable cloud
  proxies and compatible local servers such as Ollama or vLLM.
- Streaming output, tool execution, cancellation, context compaction and session
  recovery.
- Human approvals and questions, session-scoped grants and optional model-based
  approval review. Approval modes do not replace execution isolation.
- Native Rust tools, app-owned callbacks and explicitly registered MCP servers.
- Agent teams with durable task/message boards; SQLite or PostgreSQL storage.
- Optional OpenTelemetry export and artifact storage for server deployments.
- App-owned authentication callbacks and proactive rotation of short-lived bearer
  tokens, without recreating sessions.

Your application owns its UI, user/device authentication, project catalog and
product-specific tools. The harness owns the execution record. Keep these storage
boundaries separate; use SDK/protocol APIs instead of reading runtime tables.

## Pick an integration

For authenticated remote SDK access, release/RuntimeProfile deployment and the
`ows` CLI, see the [service CLI quickstart](../docs/harness/SERVICE-M3-QUICKSTART.md)
and [local Docker deployment](../deploy/service/README.md). The
[service specification](../docs/harness/OPENWORKER-SERVICE.md) also describes later
management and cloud milestones; see the ledger for qualified scope.

| Application | Interface |
|---|---|
| Rust desktop app | Embed `openworker-sdk` in your Tokio process, or use its managed child |
| TypeScript desktop app | `@openworker/harness` manages the Rust child over private stdio |
| Custom process client | [Versioned SDK protocol](../docs/harness/AGENT-RUN-PROTOCOL.md) |
| Multi-node service | [Server reference](server-reference/README.md), REST/SSE and PostgreSQL |

Rust SDK and TypeScript SDK share the same engine. The TypeScript SDK does not
reimplement agent logic. Native tool crates are separate from the Harness SDK.

## Build from this checkout

Use the pinned Rust toolchain in `rust-toolchain.toml`, Node.js 24 and npm 11+ for
the JavaScript packages, plus your platform's native build prerequisites.

```sh
cd harness
cargo build --release --locked -p openworker-sdk --bin openworker-harness
npm --prefix typescript-sdk ci
npm --prefix typescript-sdk run build
```

The executable is `target/release/openworker-harness` (`.exe` on Windows).
Crate/npm manifests are currently source-distribution packages: use local paths
or a pinned Git revision, not an assumed published registry package.

## Execution and safety

The runtime defaults to NVIDIA OpenShell. Provision Docker/OpenShell before using
that execution mode. Unsupported/unavailable sandboxing fails rather than silently
falling back. Desktop starters explicitly use `sandbox: "off"` for app-owned
callbacks; those callbacks have the application's privileges. Their generated-game
preview is separately isolated from the native bridge and network.

Linux is the server/OpenShell reference platform. Windows and macOS have desktop
SDK integrations; native build and packaging prerequisites apply. Do not infer
that every architecture, installer, local model or sandbox combination is qualified.

- [Windows build notes](WINDOWS.md)
- [Tool and resource policy](../docs/harness/APP-POLICY-CONTRACT.md)
- [Session approvals](../docs/harness/SESSION-GRANTS.md)
- [Provider authentication and token rotation](../docs/harness/PROVIDER-AUTH.md)
- [Custom native tool packaging](../docs/harness/PARTNER-SDK-BUILD.md)
- [Deployment and operations](../deploy/reference/README.md)

## Layout

```text
runtime/              Agent loop, policies, sessions, execution and protocol
harness-sdk/          Rust library and SDK-capable executable
typescript-sdk/       Node/TypeScript client
tools-sdk/            Native Rust tool interface
store-postgres/       PostgreSQL storage and coordination
artifacts/            Artifact persistence
telemetry/            OpenTelemetry integration
server-reference/     API, worker and administrative server roles
desktop-starters/     Small Electron and Tauri application generators
examples/arcade/      Flagship desktop app
examples/             Focused coworker, tool and custom-host examples
tests/                Shared contract fixtures and acceptance tests
partner/              Generic custom-host builder and protocol reference
console/              Optional observability console
task-desk/            Small server-backed reference app
```

## Verify changes

```sh
cd harness
cargo fmt --all -- --check
cargo test --locked -p openworker-sdk --features test-support --test provider_auth
cargo test --locked -p notification-tools -p device-tools -p computer-tools --lib
npm --prefix typescript-sdk test
node --test desktop-starters/test.mjs tests/stdio-client.test.mjs
```

The full Linux workspace/storage matrix is in
[CI](../.github/workflows/rust-sdk.yml). Tests that need PostgreSQL, OpenShell,
live credentials or a visible desktop must be enabled and qualified explicitly.
See the [release checklist](../docs/harness/RELEASE-CHECKLIST.md) for this snapshot.

Licensed under the repository's [MIT license](../LICENSE). Third-party example
assets retain their own notices and licenses.
