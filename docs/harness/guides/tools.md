# Tools and MCP

Expose only the tools an agent needs. The agent definition lists registered names,
such as `coding.read_file` or `app.show_preview`.

| Tool implementation | Registration | Execution |
|---|---|---|
| App callback in TypeScript/Rust | `registerTools` / `register_tools` | Application process; private callback channel when managed |
| Native Rust tool crate | Tools SDK registry | Compiled into your chosen host/runner |
| External MCP server | Explicit server registration | Stdio-launched service or configured HTTP endpoint |

Tools declare JSON input schemas and return structured results/errors. Cancellation
and progress are part of the interface. A callback's process placement is distinct
from its permission classification: registering a tool does not grant unrestricted
file, network or screen access.

Use the bundled coding tools for workspace files, search, patches, processes and
Git. Use the [tools SDK](../../../harness/tools-sdk/README.md) for native tools
and the [TypeScript SDK](../../../harness/typescript-sdk/README.md) for app callbacks.

For external services, keep endpoints and credentials in trusted registration
configuration. Guardrails may inspect selected arguments/results, and authorization
still runs before execution. See [Guardrails guide](guardrails.md).

## Correlate tool activity in your UI

Each application callback receives a call ID: `context.callId` in TypeScript or
`context.call_id` in Rust. The tool-start and tool-finish event summaries carry
the same ID (`summary.callId` in TypeScript; `RunEvent::ToolStarted` and
`RunEvent::ToolFinished` have `call_id` in Rust). The raw journal retains
`event.data.call_id`. Providers can reuse call IDs in later turns: include the
turn ID (`context.turnId` / `context.turn_id`, and `event.turn_id` or the Rust
tool event's `turn_id`) when correlating activity.

The harness records the start before running a tool. Your application may receive
the callback before it consumes that journal event, however, because the two
channels are independent. Create or update the same UI entry using the session ID,
turn ID and call ID from either channel. Do not wait inside a callback for the UI to read
the start event, and do not let a late start reset a completed entry. This also
works when reading saved events after the run has finished.

Chat Completions history currently rejects reused provider call IDs before the
next model request. A tool may already have completed when this happens; a failed
turn does not mean its tool actions were undone. The correlation fields help your
UI preserve that distinction.
