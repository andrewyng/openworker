# OpenWorker Harness

Build your product and user experience. Let OpenWorker run the agents.

OpenWorker Harness is a Rust agent runtime with Rust and TypeScript SDKs. It
manages model requests, tools, approvals, durable sessions and agent teams.
Embed it in a desktop application or deploy the service with PostgreSQL.
The shipped Rust runtime does not require Python. The existing OpenWorker
Desktop application continues to use its Python harness.

Use a pinned checkout and build the SDK and executable from the same revision.
Source manifests are development packages; see [versions and compatibility](../docs/harness/guides/releases.md).

## Start here

- [SDK guides](../docs/harness/guides/index.md) — choose a desktop or web application.
- [Desktop app](../docs/harness/guides/desktop.md) — build Code Atlas with an Electron UI and the TypeScript SDK; a Rust agent example is included.
- [Web app](../docs/harness/guides/server.md) — upload a PDF and ask questions about it.
- [Arcade Studio](examples/arcade/README.md) — a desktop game-building application.
- [Rust SDK](harness-sdk/README.md) and [TypeScript SDK](typescript-sdk/README.md).
- [Tools and MCP](../docs/harness/guides/tools.md), [sessions and teams](../docs/harness/guides/sessions.md), and [model credentials](../docs/harness/guides/providers.md).
- [Guardrails](../docs/harness/guides/guardrails.md), [alignment](../docs/harness/guides/alignment.md), [compliance](../docs/harness/guides/compliance.md), and [observability](../docs/harness/guides/observability.md).
- [Deployment options](../docs/harness/guides/deployment.md) — local containers, Kubernetes and cloud deployments.
- [Documentation site](../docs-site/README.md) — build the HTML guides from Markdown.

## What the harness handles

- Responses, Chat Completions and Anthropic Messages APIs, cloud proxies and compatible local model servers.
- Streaming, tools, cancellation, compaction and durable session recovery.
- Human approvals, optional model-based review and session-scoped grants.
- Rust tools, application callbacks and explicitly registered MCP servers.
- Agent teams with durable inboxes and boards, on SQLite or PostgreSQL.
- Guardrails, hooks, compliance recording and optional security/SIEM adapters.
- Observability export and artifact storage for service deployments.

Your application owns its UI, authentication and product workflow. Keep model keys
and service credentials in trusted configuration, outside prompts and browser code.
Use SDK/service interfaces instead of reading runtime tables directly.

## Pick an integration

| Application | Interface |
|---|---|
| Rust desktop app | Run `openworker-sdk` in your Tokio process or use a managed child |
| TypeScript desktop app | `@openworker/harness` manages the Rust child over private stdio |
| Custom tool host | [Native host builder](partner/README.md) and [tools SDK](tools-sdk/README.md) |
| Service | [API and workers](server-reference/README.md), [CLI](service-cli/README.md), REST/SSE and PostgreSQL |

For local containers and Kubernetes, start with the [service deployment README](../deploy/service/README.md).
The Rust and TypeScript SDKs share the same engine; TypeScript does not reimplement
agent logic.

## Build from this checkout

Use the pinned Rust toolchain in `rust-toolchain.toml`, Node.js 24 and npm 11+,
plus your platform's native build prerequisites.

```sh
cd harness
cargo build --release --locked -p openworker-sdk --bin openworker-harness
npm --prefix typescript-sdk ci
npm --prefix typescript-sdk run build
```

The executable is `target/release/openworker-harness` (`.exe` on Windows).

## Execution and safety

The runtime defaults to NVIDIA OpenShell. Provision Docker/OpenShell before using
that mode. Unavailable sandboxing fails rather than silently falling back.
Desktop examples explicitly select `sandbox: "off"` for application-owned work;
that work runs with the application's privileges and has no OS isolation from
other host resources. See [OpenShell integration](../docs/harness/guides/openshell.md).

Linux is the service/OpenShell reference platform. Windows and macOS have desktop
SDK integrations; native build and packaging prerequisites apply. See
[Windows build notes](WINDOWS.md). Build and test on the OS/architecture you ship.

## Layout

```text
runtime/              Agent loop, policy, sessions and execution
harness-sdk/          Rust SDK and native executable
typescript-sdk/       Node/TypeScript client
tools-sdk/            Native Rust tool interface
store-postgres/       Service persistence and coordination
artifacts/            Filesystem and object-store artifacts
telemetry/            OpenTelemetry integration
server-reference/     Service API and workers
service-cli/          ows CLI
service-ui/           Management UI
desktop-starters/     Electron and Tauri project generators
examples/             Runnable applications and integrations
tests/                Shared contracts and test entry points
```

## Test changes

```sh
cd harness
cargo fmt --all -- --check
cargo test --locked -p openworker-sdk --features test-support --test provider_auth
npm --prefix typescript-sdk test
node --test desktop-starters/test.mjs tests/stdio-client.test.mjs
```

The workspace/storage matrix is in [CI](../.github/workflows/rust-sdk.yml).
Tests requiring PostgreSQL, OpenShell, credentials or a visible desktop must be
explicitly enabled with their prerequisites. A skipped external test is not a pass.

Licensed under the repository's [MIT license](../LICENSE). Third-party example
assets retain their own notices and licenses.
