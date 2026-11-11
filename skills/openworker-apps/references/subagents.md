# Standalone sub-agents

Source-feature preview: this option is not in the published 0.1.0-beta.2 runtime.
Use this reference only with a matching candidate SDK/runtime whose handshake has
`standalone_subagents: true` (service: `standaloneSubagents`). Otherwise use the
installed version's existing explicit team API or explain the version requirement.
Do not silently substitute a team or upgrade an application.

Enable delegation on an ordinary agent. The application does not spawn children
or configure a board. The main model creates them using `create_sub_agent`.

```typescript
import type { Harness } from "@openworkerai/harness-sdk";
export function assistant(harness: Harness) {
  return harness.createAgent({
    name: "assistant",
    instructions: "Delegate independent inspection tasks; integrate the reports.",
    tools: ["coding.read_file", "coding.list_files"],
    subagents: {
      enabled: true,
      maxConcurrent: 4,
      maxChildren: 32,
      limits: { maxModelCalls: 40, maxOutputTokens: 64000 },
    },
  });
}
```

Default approvals remain enabled. Render the creation approval with its exact
prompt/tool subset and route later child approvals using their session IDs.
A creation approval is not a blanket approval of the child's tool actions.

The model's creation call is:

```json
{
  "agent_id": "reader",
  "system_prompt": "Inspect source and report evidence. Do not modify files.",
  "context": "Read src/auth.ts and describe token refresh. The suspected issue is concurrent requests.",
  "tools": ["coding.read_file"],
  "execution": "async"
}
```

- `tools` must be an explicit subset of the parent's catalog; `[]` is valid.
  The child still has the coordination tool `send_message` for messaging `parent`.
- There is no history inheritance. The model writes the task and relevant context.
- `sync` parks the creation tool until the child finishes; `async` returns its durable
  identity promptly. Keep the harness running for async work. Parent turn completion
  does not close its children. Harness shutdown stops execution until recovery.
- Parent tools: `send_message`, `wait_agent`, `cancel_agent`, `close_agent`.
  `send_message` takes `agent_id` and `message`; `wait_agent` takes `agent_ids`
  and `timeout_ms`. Wait can return on a message or one child's completion, not
  necessarily completion of every child. Sync creation continues to await its child.
- Busy inboxes deliver at the next model call. Completed agents wake on follow-up;
  explicit cancellation, failure, provider safety block and closure are not undone
  by inbox delivery. `cancel_agent` targets a running turn; use `close_agent` to
  prevent queued/unstarted work or permanently retire the child.
- Repeating a child ID with identical creation arguments returns that child.
  Different arguments conflict. Send follow-up work using a message.
- Children share the parent's workspace and sandbox. Assign distinct paths for
  concurrent edits. A restricted tool set is not a separate filesystem sandbox.
  Service RuntimeProfiles must allow a shared environment.
- Scope limits count parent and child model calls, including charged auxiliary
  calls, across the parent session. Defaults use the parent's configured limits.
  Each turn also retains its own limits. Limits are not reset by child creation.
- Children cannot spawn descendants, message siblings or access a board. An
  explicit/legacy team and standalone delegation cannot share one session in v1.
- Streamed child events carry `agentId`, `parentSessionId` and the original event
  session/turn IDs. Count each model-usage event once; do not add a separate group
  total to individual totals. A finished parent stream does not wait for later
  async work; use durable session/event APIs for subsequent background activity.

Do not broaden tools or disable approvals to fix a child failure. Preserve the
reported unknown outcome on interruption; reconcile effects before retrying.
