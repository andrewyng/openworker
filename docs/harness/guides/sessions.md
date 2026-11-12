# Sessions and teams

An agent is a reusable definition. A session is its durable conversation and
execution state. Sending a message starts a run; the run exposes events and a final
result or interruption. Keep the session reference in your application database.

Use `session.state()` for current activity, pending interactions and outstanding
timers. On restart, reattach to the saved session rather than creating a replacement.
Reconnect/replay does not mean replaying side effects. If an action's outcome is
unknown, inspect its status before retrying it.

## Agent identity and workspace

Agent names identify persisted immutable definitions. Changing instructions,
tools, approval mode or other definition fields requires a new name. Identical
registration is idempotent; creating another session does not replace a definition.
Keep old identities for their existing sessions. A fresh state directory is a
separate development environment, not a migration of existing conversations.

An explicit `workspace` selects the working directory. Omitting it normally
allocates a private scratch workspace. `scratch: false` requires an explicit
workspace; `scratch: true` cannot accompany one. Scratch does not promise automatic
deletion. Keep state outside files available to tools, and enforce directory scope
in app callbacks; a workspace is not an OS sandbox.

## Context and limits for each turn

Use `instructionAddendum` in TypeScript, or `TurnOptions.instruction_addendum` in
Rust, for application-owned preferences that change between turns. The addendum
accompanies every model call and retry in that turn, survives recovery, and is
included in the assembled request inspected by before-model hooks. User-input
guardrails apply to the user message, not this application-owned field. It is stored with the turn rather than
as user transcript text. It expires for the next turn. Keep user-supplied text in
the user message; an addendum does not grant tools, approvals or sandbox access.

```typescript
const run = await session.send("Suggest names for my app.", {
  submissionId: "naming-turn-42",
  instructionAddendum: "Prefer .ai domains. The application budget is $40.",
  limits: {
    maxModelCalls: 8,
    maxOutputTokens: 60000,
    maxOutputTokensPerCall: 24000,
    summaryTokens: 512,
  },
});
```

`maxOutputTokensPerCall` defaults to 8,192 and applies to each working-model call.
The remaining turn budget may reduce it. `maxOutputTokens` is the total output
budget for the turn, including the final summary reserve; increasing a per-call
limit does not increase that total. Set limits on the agent for defaults or on
`send` for one turn. The selected model must support the requested per-call limit.
Reusing a submission ID requires the same message, context, limits and safety
options; a changed addendum is a new turn with a new submission ID.

## Guide the auto-approver and compactor

**Source preview:** use the matching updated runtime. For embedded Rust and
managed-subprocess TypeScript sessions, `autoApproverContext` supplies application
facts and review guidance to the action reviewer. `compaction.additionalInstructions`
adds summary guidance. Both can be set on an agent, a new session, or one turn.

<!-- sdk-tabs -->
### TypeScript

```typescript
const session = await agent.newSession({
  autoApproverContext: "Staging workspace. Ask before publishing.",
  compaction: { additionalInstructions: "Preserve decisions and unfinished tests." },
});
await session.run("Continue.", {
  autoApproverContext: "For this turn, changes must remain inside src/.",
  compaction: { additionalInstructions: "Keep the migration checklist." },
});
```

### Rust

```rust
let session = agent.new_session_with(
    SessionOptions::default()
        .auto_approver_context("Staging workspace. Ask before publishing.")
        .compaction(CompactionOptions::additional_instructions(
            "Preserve decisions and unfinished tests."
        ))
).await?;
let run = session.send_with_options("Continue.", TurnOptions {
    auto_approver_context: Some("Changes must remain inside src/.".into()),
    compaction: Some(CompactionOptions::additional_instructions("Keep the migration checklist.")),
    ..Default::default()
}).await?;
let result = run.result().await?;
```
<!-- /sdk-tabs -->

Precedence is agent → session → turn. Omitted fields inherit; an empty string
clears inherited guidance for that scope. Turn overrides survive recovery and
apply to retries and tool follow-ups in that turn. They do not change the next
turn's defaults. Reusing a submission ID with changed guidance fails.

Each string supports up to 32,000 UTF-8 bytes and no NUL. Guidance is stored
separately from user history. Reviewer context does not enable auto-approval or
grant tool access: configure auto-approval and a reviewer model separately.
Hard policy checks still run first; an unavailable or unsure reviewer asks the
human. Compaction guidance supplements the required summary structure. These
session/turn overrides are not yet exposed by the service-cluster REST client.

## Attach a file

The SDK supports images, PDFs and UTF-8 files as ordered user-message
content. See [Attachments](attachments.md) for the convenience and interleaved
forms, storage limits and guardrail behavior.

## Import a conversation

Use `initialHistory` only when creating a new session. Ordinary recovery should
resume the existing session reference. The portable import format supports plain
user/assistant text and completed tool calls/results:

```typescript
const session = await agent.newSession({ initialHistory: [
  { type: "user", text: "Check Atlas." },
  { type: "tool_call", callId: "old-lookup", name: "app__lookup",
    arguments: { name: "Atlas" } },
  { type: "tool_result", callId: "old-lookup", output: '{"available":false}' },
  { type: "assistant", text: "Atlas is taken." },
] });
```

Imported calls are historical data and never execute. Import validates the entire
transcript before creating a session, and the history and new session commit
together. A nonempty transcript must start with a user message. Every tool call
needs exactly one matching result; duplicate IDs and unfinished calls are rejected.
Limits are 256 items, 256,000 bytes per text/arguments/result, 512,000 serialized
bytes total, and 32 levels of argument nesting. Tool arguments must be objects.

This format does not accept system messages, approvals, internal runtime notices,
images or provider-private reasoning/signatures. Translate your application's
transcript to the portable fields rather than passing raw provider JSON. If a
provider requires signed reasoning metadata to replay an old tool call, resume
the original harness session or import a text-only conversation summary instead.
An import never restores permission grants or an in-progress operation.

Import validates structure and bounds; it does not retroactively run user-input
guardrails or tool-result hooks over the old transcript. The application must
sanitize migrated content before import. Before-model hooks can inspect the
assembled request, including imported history, before the next model call.

## Approvals and questions

Render durable approval/question interruptions in your UI, then send the user's
actual response. Configure the decision path in the agent definition:

| TypeScript mode | Behavior |
| --- | --- |
| `always` (default) | Requests a human decision unless a valid session grant covers the tool. |
| `only` with a tool list | Requests approval for listed tools; ordinary unlisted tools receive application-policy approval. |
| `auto` with `reviewerModel` | An explicit LLM reviews eligible calls: allow executes, deny returns a denial, uncertain requests human input. |
| `never` | Bypasses approval review; hard security checks still apply. |

Rust exposes `Approvals::Always`, `Only`, `auto(model)` and `Never`. Auto review
can include read-only tools. Hard denials and valid session grants are checked
first. Privileged child/type-creation operations have additional requirements;
omission from an `only` list does not automatically exempt them.

Use `once` when each mutation requires a new card. A `session` grant covers the
entire tool, including different valid arguments, survives restart and does not
override hard checks. Questions are separate from approval decisions. See the
[complete streamed approval loop](streaming.md#streaming-with-tool-approvals).

The typed `auto_approved` summary currently describes application-policy approval,
not a model review verdict. Raw `interaction_required` / `interaction_resolved`
events can include `data.review.verdict`, a safe `reason`, `origin` and nested
`call.call_id`; handle absent/withheld content. A denied tool result tells the
working model not to retry or evade the denial, but currently omits the reviewer's
reason. The app can display an available safe journal reason itself.

A tool-free guide can disable optional tools, memory, planning and scheduling.
Auto-skip questions is a separate setting, not an authorization mode.

An agent with application tools can also opt out of implicit session tools:

```typescript
const agent = harness.createAgent({
  name: "naming", instructions: "Help the user choose a name.",
  tools: ["app.lookup"],
  builtinTools: { askUser: false, readHistory: false },
  defaultModel: { provider: "OpenAI", name: "YOUR_MODEL_ID" },
});
```

Omitting `builtinTools` retains the existing behavior: question/history tools
accompany a nonempty selected tool catalog, while a completely tool-free agent
gets neither. Explicit `true` enables that built-in tool, including on an otherwise
tool-free agent. Disabled tools are absent from the provider request and unexpected
calls are rejected. These settings are part of the immutable agent definition;
changing them requires a new agent identity and new sessions.

These APIs require an updated native runtime as well as the SDK package. Clients
check host capabilities before sending the new options. Omitted options retain
existing agent definitions and session schema digests across upgrades.

## Standalone sub-agents

Requires a runtime advertising `standalone_subagents`, available in native
runtime 0.1.0-beta.3. An ordinary agent can let its model delegate work:

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
