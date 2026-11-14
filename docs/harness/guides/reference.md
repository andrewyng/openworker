# SDK reference

Use the public SDK interfaces to build your application.

The guides teach one task at a time. The package references describe method shapes,
configuration and errors in more detail. Match them to your installed release;
GitHub `main` may include changes newer than the published package.

| Interface | Guide and reference |
| --- | --- |
| Harness startup and shutdown | [Desktop tutorial](desktop.md) · [TypeScript reference](../../../harness/typescript-sdk/README.md) · [Rust reference](../../../harness/harness-sdk/README.md) |
| Providers and credentials | [Models and keys](providers.md) |
| Agent definitions and tool callbacks | [Tools and MCP](tools.md) |
| Session creation, follow-ups and recovery | [Sessions](sessions.md) |
| Structured messages and files | [Attachments](attachments.md) |
| Delegation and coordinated work | [Sub-agents and teams](subagents.md) |
| Streaming, cancellation and interruptions | [Streaming and retries](streaming.md) |
| Approvals and review | [Agent alignment](alignment.md) |
| Recording and export | [Observability](observability.md) |
| Remote service and management | [Service CLI](../../../harness/service-cli/README.md) · [Deployment](deployment.md) |

## Find the exact types

The npm package includes TypeScript declarations. Use your editor's Go to Definition
on `Harness`, `Agent`, `Session` or a configuration type to read the installed
version. Generate Rust API documentation from your pinned checkout with:

```sh
cargo doc --manifest-path harness/Cargo.toml -p openworker-sdk --no-deps --open
```

For release pins and native runtime requirements, see [versions and installation](releases.md).
