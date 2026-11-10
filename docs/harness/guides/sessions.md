# Sessions and teams

An agent is a reusable definition. A session is its durable conversation and
execution state. Sending a message starts a run; the run exposes events and a final
result or interruption. Keep the session reference in your application database.

Use `session.state()` for current activity, pending interactions and outstanding
timers. On restart, reattach to the saved session rather than creating a replacement.
Reconnect/replay does not mean replaying side effects. If an action's outcome is
unknown, inspect its status before retrying it.

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
actual response. “Allow always” applies to the session. Auto-approval uses a
reviewer; an inconclusive decision still asks the human. Bypass skips human approval
but does not remove required guardrails or sandbox restrictions.

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

## Teams

A lead can spawn multiple child instances of registered agent types using
`team_spawn`, send direct messages with `team_send_message`, and wait with
`team_wait_agent` without keeping an execution slot occupied. Children have their
own sessions. The SDK exposes these capabilities through `AgentTeam`; the board
is optional, and `communication: { board: false, directMessages: true }` selects
direct communication only.

This differs from the Python-powered Desktop app's standalone `spawn_agent`
interface. A plain SDK agent does not automatically receive that tool family;
configure a team when your SDK application needs model-driven child spawning.
Shared workspaces use separate worktrees; independent sandboxes exchange changes
through explicit handoff. Team creation does not enlarge the lead's permissions.

Continue with the [Rust SDK](../../../harness/harness-sdk/README.md) or
[TypeScript SDK](../../../harness/typescript-sdk/README.md) for session and team
APIs. See [alignment](alignment.md) for approval behavior and
[deployment options](deployment.md) for shared service storage.
