# OpenWorker Harness SDK

## Build your app. OpenWorker handles the agents.

Give your desktop or web app agents that can use tools, work together and keep
conversations across restarts. Keep your interface and product workflow while
the harness manages the agent loop, model calls, approvals and session storage.

[Get started](#choose-your-starting-point) · [Documentation](https://openworker.com/docs/sdk/) · [Builder Skill](../skills/README.md) · [Examples](#try-an-example)

![OpenWorker architecture: your application uses the TypeScript or Rust SDK to control one Rust harness, which connects to cloud/local models and app-owned or OpenShell-isolated tools.](../docs/harness/assets/architecture.svg)

One Rust engine powers both SDKs. TypeScript runs it as a managed child process;
Rust can embed it in-process. The current OpenWorker Desktop application uses
its separate Python engine.

> **Under active development.** Please try the SDK and [open issues](https://github.com/andrewyng/openworker/issues) for bugs and feature requests. Please hold off on SDK pull requests while the API is evolving.

## Choose your starting point

| You are building… | Start here |
| --- | --- |
| A Windows or Mac desktop app | [Desktop walkthrough](../docs/harness/guides/desktop.md) · [Electron/Tauri starters](desktop-starters/README.md) |
| A web app with its own backend | [Web walkthrough](../docs/harness/guides/server.md) |
| A local script or command-line utility | [Runnable file-organizer agent](../skills/openworker-harness-builder/references/local-script.md) |
| A Rust application | [Rust SDK quickstart](harness-sdk/README.md) |
| A shared agent service | [Deployment choices](../docs/harness/guides/deployment.md) · [Local service setup](../deploy/service/README.md) |

### TypeScript: install from npm

```sh
npm install --save-exact @openworkerai/harness-sdk@0.1.0-beta.4
```

npm includes the matching native runtime automatically. No Rust compiler or
Python installation is needed. This client documentation release uses native
runtime **0.1.0-beta.3**. Run the SDK in Node.js 20+, Electron main or your server;
the browser talks to your app's API. See the [TypeScript quickstart](typescript-sdk/README.md).

### Rust: embed from a pinned checkout

Use `openworker-sdk` from a pinned checkout with the repository's Rust toolchain.
The [Rust quickstart](harness-sdk/README.md) includes dependencies and a complete
program. npm availability does not imply Cargo registry publication.

### Build with Codex or Claude Code

```sh
npx @openworkerai/openworker-harness-builder --agent claude --project .
```

Use `--agent codex` for Codex or `--global` for user-wide installation. Existing
installs ask before replacing and save a backup; scripts can use
`--replace-existing`. The [Skill](../skills/README.md) includes version-matched
instructions and runnable examples without requiring a repository checkout.

## What your application gains

| Outcome | What the harness provides |
| --- | --- |
| Ship your product experience sooner | One agent loop for provider requests, tools, streaming and compaction. |
| Keep conversations across restarts | Durable sessions and explicit recovery, without blindly replaying uncertain actions. |
| Keep people in control | Human approvals, session grants and optional LLM-assisted review. |
| Choose where work happens | App-owned tools or isolated execution through NVIDIA OpenShell. |
| Divide larger tasks | Standalone sub-agents and agent teams with durable messages and boards. |
| Use the model that fits | OpenAI, Anthropic and compatible cloud/local endpoints, with app-owned credentials. |
| Understand what happened | Opt-in turn traces, usage accounting and telemetry export to tools such as Langfuse. |

## Your app and the harness

Your app owns the UI, authentication, user/session ownership and product workflow.
It chooses the tools, models and policies. The harness enforces its configured
controls; an application callback still has the application's privileges.

OpenShell isolates configured shell/file execution while the agent loop stays
in the trusted harness. A workspace path alone is not an OS sandbox. See
[execution boundaries](../docs/harness/guides/openshell.md),
[guardrails and hooks](../docs/harness/guides/guardrails.md),
[alignment](../docs/harness/guides/alignment.md) and
[compliance](../docs/harness/guides/compliance.md).

## Try an example

| Example | What it demonstrates |
| --- | --- |
| [File organizer](../skills/openworker-harness-builder/references/local-script.md) | Custom tools, confined directory operations, activity and per-operation approvals. |
| [Code Atlas](examples/quickstart/README.md) | A desktop UI that explains a selected code directory. |
| [PDF Notes](../docs/harness/guides/server.md) | A web app that accepts a PDF and answers questions about it. |
| [Arcade Studio](examples/arcade/README.md) | A desktop application that builds and previews games. |

## Deployment and operations

Start in one application process or container. Use the service when you need
API/worker separation, durable shared storage and separately managed execution.
The service uses PostgreSQL; local apps can use SQLite.

- [Local containers and Kubernetes](../deploy/service/README.md)
- [AWS](../docs/harness/guides/aws.md), [Azure](../docs/harness/guides/azure.md), [GCP](../docs/harness/guides/gcp.md) and [private Kubernetes](../docs/harness/guides/private-kubernetes.md)
- [Management CLI](service-cli/README.md), [management UI](service-ui/README.md) and [observability](../docs/harness/guides/observability.md)

Cloud deployment paths are experimental references. Qualify your configuration
in the target environment before using it for production. Read
[versions and compatibility](../docs/harness/guides/releases.md) before upgrading.

## For developers and coding agents

Start with the matching package README: [TypeScript](typescript-sdk/README.md) or
[Rust](harness-sdk/README.md). Use the builder Skill's `installation.json` to find
its exact SDK pin. Source release manifests and npm client/native versions can
differ; do not infer registry availability from a source version string.

- Preserve immutable agent identities; changing instructions/tools/approvals requires a new name.
- Keep credentials and durable state outside model-readable files.
- Use real user decisions for approvals; observation is independent from execution.
- Reconcile unknown tool outcomes before retrying.
- Prefer public SDK/service interfaces over runtime tables or private transport methods.

Need to change the engine or register native tools? See the [native host guide](partner/README.md),
[Tools SDK](tools-sdk/README.md) and [workspace CI](../.github/workflows/rust-sdk.yml).

<details>
<summary>Build and test this checkout</summary>

Use the [pinned toolchain](rust-toolchain.toml), Node.js and your platform's native
prerequisites. See [Windows build notes](WINDOWS.md) when applicable.

```sh
cd harness
cargo build --release --locked -p openworker-sdk --bin openworker-harness
npm --prefix typescript-sdk ci
npm --prefix typescript-sdk run build
```

The host is `target/release/openworker-harness` (`.exe` on Windows). Build a custom
SDK/helper pair from the same revision; npm consumers normally use the packaged runtime.

```sh
cargo fmt --all -- --check
cargo test --locked -p openworker-sdk --features test-support --test provider_auth
npm --prefix typescript-sdk test
```

OpenShell, PostgreSQL and live-provider checks require their documented setup.
The [documentation site](../docs-site/README.md) is built from repository Markdown.

</details>

[MIT license](../LICENSE).
