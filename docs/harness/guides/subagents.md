# Sub-agents and teams

Let an agent delegate work and combine the results.

A coding agent can ask a child to inspect authentication while another reviews
tests. Each child has its own conversation and a restricted tool catalog.

## Choose how agents work together

| | Standalone sub-agents | Explicit teams |
| --- | --- | --- |
| Start with | A normal agent and `subagents.enabled` | A team with a lead and agent types |
| Child instructions | The parent model supplies the prompt and context | Registered types; optional authorized dynamic types |
| Communication | Parent/child direct messages and waits | Direct messages and optional board mentions |
| Workspace | Shares the parent's sandbox/workspace | Shared workspace or separately configured child environments |
| Best fit | Ad hoc delegation during a task | A recurring builder/verifier or other coordinated workflow |

## Enable standalone sub-agents

Enable delegation on an ordinary agent. The SDK supplies the tools its model
uses to create and coordinate children:

<!-- sdk-tabs -->
### TypeScript

```typescript
const assistant = harness.createAgent({
  name: "assistant",
  instructions: "Delegate source reviews and combine the findings.",
  tools: ["coding.read_file", "coding.list_files"],
  subagents: { enabled: true },
});
```

### Rust

```rust
let assistant = Agent::new("assistant")
    .instructions("Delegate source reviews and combine the findings.")
    .tool("coding.read_file")
    .tool("coding.list_files")
    .allow_subagents(true);
```
<!-- /sdk-tabs -->

The model receives `create_sub_agent`. It supplies an instance ID, system prompt,
explicit task/context, a subset of its tools and `execution: "sync"` or `"async"`.
Each child starts with a fresh conversation. No parent history is copied.
Sync waits without occupying a worker slot; async returns a durable child identity.

Parent and child exchange messages through durable inboxes. The parent can wait,
cancel a running child or close it. A completed child can accept follow-up work;
explicitly cancelled or closed children do not automatically resume. Creation uses
normal approval policy and does not approve subsequent child tools.

Children initially share the parent's workspace and sandbox; assign separate
paths for concurrent edits. Service profiles must allow a shared environment.
Defaults allow four active children and 32 retained child identities. Shared scope
budgets count parent and child work; children cannot reset the allowance or spawn
further descendants. Existing explicit teams remain available separately.

See the [complete tool contract and configuration](../../../skills/openworker-harness-builder/references/subagents.md).

## Teams

A lead can create multiple instances of registered agent types, communicate through
durable inboxes/board mentions and wait without keeping an execution slot occupied.
Shared workspaces use separate worktrees; independent sandboxes exchange changes
through explicit handoff. Team creation does not enlarge the lead's permissions.

Continue with the [Rust SDK](../../../harness/harness-sdk/README.md) or
[TypeScript SDK](../../../harness/typescript-sdk/README.md) for session and team
APIs. See [alignment](alignment.md) for approval behavior and
[deployment options](deployment.md) for shared service storage.

## The tool the parent model receives

The application enables delegation; the model decides when to call
`create_sub_agent`. For example:

```json
{
  "agent_id": "auth-reviewer",
  "system_prompt": "Review authentication code and report evidence. Do not modify files.",
  "context": "Inspect src/auth.ts for token refresh races. Include file and line references.",
  "tools": ["coding.read_file"],
  "execution": "async"
}
```

`agent_id` identifies an instance, so the parent can create several reviewers.
`context` carries the task and relevant details; the harness does not copy the
parent's conversation. Repeating the same ID and arguments returns the same child;
changed arguments conflict. Use `send_message` for follow-up work.

`sync` waits for the child. `async` returns its identity while it works. The parent
also receives `send_message`, `wait_agent`, `cancel_agent` and `close_agent`.
Messages reach busy agents at the next model call; completed children can wake for
follow-ups. Cancellation or closure is not undone by a message.

## Handle child activity in your UI

Child events include their own session/turn identifiers and the parent relationship.
Open a child's session when the user selects it, with a breadcrumb back to the
parent. A parent's stream finishing does not imply all asynchronous children have
finished. Use durable session/event APIs to observe later activity.

Keep the harness running for background work. Show approvals for the relevant
child session: approving creation does not preapprove its subsequent actions.
See [streaming](streaming.md) and [observability](observability.md).
