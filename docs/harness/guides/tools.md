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
and the app callback examples below.

## Register an application callback

Register tools before creating an agent, then select their qualified names in
its definition. These helpers register one status tool; your application owns
the UI. Add `app.show_status` to the agent's selected tools and use the
[streamed approval loop](streaming.md#streaming-with-tool-approvals) for decisions.

<!-- sdk-tabs -->
### TypeScript

```typescript
import type { Harness } from '@openworkerai/harness-sdk';

export async function registerStatusTool(harness: Harness, showStatus: (text: string) => void) {
  await harness.registerTools({ namespace: 'app', tools: {
    show_status: {
      description: 'Display a status update in this app.',
      inputSchema: { type: 'object', properties: { text: { type: 'string' } },
        required: ['text'], additionalProperties: false },
      execute: async (args, context) => {
        context.signal.throwIfAborted();
        await context.progress('Updating app');
        if (typeof args.text !== 'string') throw new Error('Expected text');
        showStatus(args.text); // Render as escaped text in a graphical UI.
        return { displayed: true };
      },
    },
  } });
}
```

### Rust

```rust
use openworker_sdk::{Harness, Tool};
use serde_json::json;

pub async fn register_status_tool(harness: &Harness) -> openworker_sdk::Result<()> {
    harness.register_tools("app", [Tool::new("show_status")
        .description("Display a status update in this app.")
        .input_schema(json!({"type":"object","properties":{"text":{"type":"string"}},
            "required":["text"],"additionalProperties":false}))
        .handler(|args, context| async move {
            context.progress("Updating app", json!({})).await?;
            println!("{}", args["text"].as_str().unwrap_or_default());
            Ok(json!({"displayed":true}))
        })]).await
}
```

<!-- /sdk-tabs -->

The Rust callback example uses `serde_json = "1"` in the app's dependencies.
Callbacks use the application's privileges. For scoped filesystem operations,
see the complete [file-organizer tools](../../../skills/openworker-harness-builder/assets/local-script/file-tools.mjs)
and [Node example](../../../skills/openworker-harness-builder/references/local-script.md).

## Tool names and directory scope

Register/select `files.move_file`; the model-facing alias is `files__move_file`.
Current events and approval requests can contain that alias. Use your registered
mapping for UI labels and preserve original IDs in saved event data.

`workspace` supplies a working directory and scope for supported built-in path
checks/backend mounts. Arbitrary app callbacks can access everything their app can
access. Each file handler must constrain both source and destination, reject
traversal/absolute paths and handle symlinks. The bundled demo does this for an
app-owned directory without concurrent untrusted writers. Path checks are not
kernel isolation; use an isolated execution backend for hostile code. Configuring
OpenShell does not move arbitrary Node callbacks into its sandbox.

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

A provider response that reuses a call ID from retained model context is rejected
before dispatching that new call. Earlier completed actions remain completed; a
failed later turn does not undo them. The correlation fields help your UI
preserve that distinction.

Typed start/finish summaries omit arguments. They are supplied directly to the
callback and available in raw `tool_started` data; retain them for the finished
UI row. Support missing/withheld content and display only appropriate fields.

```typescript
// Inside an application's run event loop:
if (item.event.type === 'tool_started') {
  const data = item.event.data as Record<string, unknown>;
  updateActivity({ sessionId: item.sessionId, turnId: item.event.turn_id,
    callId: data.call_id, tool: data.name, arguments: data.arguments });
}
```

`updateActivity` is application UI code. `context.progress(message, data)` supplies
intermediate updates without introducing new polling.

## Images from application callbacks

An application callback can explicitly return one image alongside JSON metadata:

```typescript
import { ToolImageResult } from '@openworkerai/harness-sdk';
// Inside execute(): capture only the app-authorized surface.
return new ToolImageResult(
  { width: 960, height: 600, scope: 'game-preview' },
  { mimeType: 'image/jpeg', base64: screenshotBytes.toString('base64') },
);
```

Requires a host advertising `application_tool_images` and a vision-capable
model. JPEG/PNG only, one image, at most 768,000 base64 characters; ordinary metadata
retains its 128,000-character bound. The host validates image signatures. Only the
latest image per session is kept ephemerally for up to five minutes; durable events
and history contain a reference, not pixels. Older images must be recaptured when
needed. The provider receives pixels and applies its own retention rules. This API
does not grant capture permission or make callbacks sandboxed: applications must
authorize and constrain what they capture. Plain JSON return values are unchanged.
