# OpenWorker Tools SDK — development API

A small Rust contract for partner-owned tools. The SDK does not contain an agent
loop, permission system, coworker ACL, dynamic-library loader or UI framework.
It is currently a workspace/path dependency, **not a published crates.io release**.

Optional live-state hooks: `model_context(session_id)` returns model-safe JSON
facts, refreshed on every provider attempt for coworker-selected tools only
(4 KB per tool / 16 KB total). Keep it synchronous, bounded and nonblocking;
never include credentials, pixels or arbitrary private control payloads. Facts
are transient instructions context, not journaled history or action authorization.
`turn_finished(session_id, completed)` defaults to `revoke`; tools with expiring
app consent may preserve unused access through successful clarification replies.
Cancellation/session close still call `revoke` directly. Execution and image
forwarding must continue to independently recheck consent.

## The whole model

1. The partner binary registers native tools and named MCP providers at startup.
2. A trusted coworker bundle lists the fully qualified tools it needs.
3. OpenWorker resolves those names, validates schemas, presents only those tools
   to the model, and routes calls through its existing approval/execution journal.

The bundle's list is the selection. There is no second coworker allowlist in the
tool crate. An available implementation is not automatically visible to a coworker.

MCP providers can now also be registered through app-owned `--servers servers.json`,
with `stdio` launch or `streamable_http` local-service connection. See
[Windows/local service configuration](../WINDOWS.md). This does not change
build-time native crate registration or the coworker's qualified tool selection.

```json
{"version":3,"tools":["example.web_search","acer.acer_care_center_batterystatus_get"]}
```

This is `tools.json` beside `coworker/manifest.md`. The manifest still contains
the coworker identity, system instructions and optional skills. Tool declarations
are independent of whether the implementation is native or MCP.

## Implement and register

Implement `Tool` with:

- `descriptor()`: qualified name, description, JSON input schema, implementation version.
- `call(context, arguments)`: a `Send` async future returning `ToolOutput` or `ToolError`.

`ToolContext` supplies harness-owned session/turn/call IDs, workspace, cancellation
and bounded progress. Results contain structured JSON; errors have code/message.
The runtime also supplies a text representation for the current model pipeline.
Bounded image attachments use the explicit ephemeral image mechanism in
`ToolOutput`; do not put base64 screenshots into text results as a substitute for
authorization/image handling. Consent is rechecked before model delivery.

The complete compilable reference is [notification-tools](../examples/notification-tools/src/lib.rs).
The [custom host](../examples/native-tools-runtime/src/main.rs) registers native
tool crates, then invokes the shared SDK-capable OpenWorker CLI entry point.
That executable reuses the existing loop, protocol, storage and permissions.

```rust,ignore
let mut tools = openworker_tools::ToolRegistry::default();
example_tools::register(&mut tools)?; // partner-owned crate
tools.register_mcp(openworker_tools::McpServer {
    namespace: "acer".into(),
    program: "bin/acer-mcp-launcher".into(),
    args: vec![],
})?;
openworker_runtime::cli::main_with_registry(tools);
```

For direct Rust embedding instead, use `Runtime::open_with_registry`. The shipped
ExampleApp architecture can keep the harness as a separate stdio process; the partner
tool crate is linked **inside that process**, not dynamically loaded over stdio.

## Routing and identity

`example.web_search` resolves to an exact registered native descriptor.
`acer.acer_care_center_batterystatus_get` resolves to the `acer` MCP provider and
the exact discovered remote name `acer_care_center_batterystatus_get`.
MCP discovery does not expose unlisted tools. Missing tools, duplicate names,
namespace conflicts and derived-alias collisions fail, with no fallback search.

Model transport aliases replace each dot with `__`, e.g. `example__web_search`.
Use the alias in model instructions; use qualified names in the bundle. Aliases
are an encoding detail, not another namespace that needs manual registration.
Names are case-sensitive; namespace `openworker` is reserved. Selected names and
derived aliases must fit 80 ASCII bytes. Native/MCP namespaces cannot overlap.
The registry is frozen for the runtime instance. MCP catalogs are discovered at
turn preparation; catalog/schema changes cannot inherit an earlier approval.

Approvals/session execution identity pin the bundle, policies, registry selection,
native descriptors and host executable bytes. Implementation changes require a
new session. Keep descriptor versions accurate. Existing v1/v2 MCP-only bundles
and their execution identity remain unchanged.

## Trust and cancellation

Native tools are trusted **host-process code**, including when MCP runs in
OpenShell. They cannot be assigned sandbox placement. The runtime enforces
selection, argument validation, approvals and journaling at the call boundary;
it cannot kernel-confine linked Rust code inside its own process. Native code can
access process credentials and host resources; OS/service authorization must be
implemented by that trusted code. Choose sandboxed MCP for code needing isolation.

Defaults remain External classification and normal approval rules. Bypass skips
human approval, not resource restrictions that an execution boundary can enforce.
Changing classification never makes host code sandboxed.

Tool implementations must be safe for concurrent sessions, nonblocking and
cooperatively cancellable. Do not spawn detached work, swallow cancellation or
write secrets into progress/results/errors. Drop child processes with owned
lifecycle cleanup. Native calls have a 75-second bound, 64 progress-event maximum,
4 KB progress messages, 16 KB progress JSON and 256 KB normalized result limit.
Limits/panics/timeouts may occur **after effects**; the harness stops that turn
without automatically replaying the action. Cancellation is not rollback.
Expected `ToolError` results return to the agent; reserve these for recoverable
failures whose outcome your implementation can describe honestly.

macOS dialog demo: `cargo run -p notification-tools --example mac_dialog`. Run only
with the local user's consent. It bypasses the harness to exercise the platform
adapter alone; it is **not** evidence of full macOS harness support or approvals.
