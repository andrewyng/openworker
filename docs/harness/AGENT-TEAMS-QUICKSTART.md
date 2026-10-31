# Named agent teams

A lead can create several instances of a registered agent type, delegate work,
wait without occupying a worker slot, and continue when a child replies or finishes.
Each instance has its own session and durable inbox. A shared board provides history;
only explicit `@instance` and `@all` mentions notify participants.

Register `lead`, `builder` and `verifier` as ordinary agent definitions with their
own instructions, models, tools and permission settings. Use the new named-team
API below; `.subagent()` and the existing `Team` work-item API retain their earlier
assignment/review behavior.

## Rust: embedded or managed subprocess

```rust,no_run
use openworker_sdk::{Harness, TeamOptions};
// `harness` already has the three registered agent definitions.
# async fn example(harness: &Harness) -> openworker_sdk::Result<()> {
let team = harness.new_team(
    "lead", "/workspace/project", TeamOptions::new(["builder", "verifier"]),
).await?;

let run = team.lead().send(
    "Build a small counter game. Delegate to builder-ui, then ask verifier-ui \
     to check it. Use different working directories when needed.",
).await?;
let result = run.result().await?;
println!("{:?}", result.status);

// Save this reference to reattach; no replacement sessions are created.
let reference = team.reference();
let team = harness.resume_team(reference).await?;
let state = team.snapshot().await?;
# let _ = state;
# Ok(())
# }
```

`run.events()` streams `RunEvent::TeamActivity` with the instance ID, agent type,
team ID, canonical journal event and summary. Questions and approvals from newly
spawned children appear in the lead's interruptions; reply through the lead handle.
Use `Harness::attach_team` when creating the lead separately, for example to choose
its model at session creation.

## TypeScript: managed subprocess

```typescript
// `harness` already has registered lead, builder and verifier definitions.
const team = await harness.newTeam("lead", {
  workspace: "/workspace/project",
  agentTypes: ["builder", "verifier"],
  communication: { board: true, directMessages: false }, // default
  limits: { maxModelCalls: 100, maxOutputTokens: 256_000 },
});
const run = await team.lead.send("Build a counter game, then verify it.");
for await (const event of run.events()) {
  updateUI(event.agentId, event.summary); // instance, e.g. builder-ui
}
const result = await run.result();
const saved = team.reference();
const resumed = await harness.resumeTeam(saved);
```

The SDKs do not implement a second agent loop. Both process modes use the same Rust
storage, dispatch, budget and scheduler decisions. Local SDK tests use an explicit
`sandbox: "off"` profile. Shared OpenShell execution uses the service below; the
local host rejects a configuration that would silently allocate one sandbox per
child. There is no isolation fallback.

## Service: one shared OpenShell environment

Deploy activated releases and a shared RuntimeProfile first. Create the lead session
through `Harness.connect()` using that profile, then attach its approved child types:

```typescript
const lead = await (await service.agent("lead@1")).newSession({
  runtimeProfile: "shared-openshell@1",
  idempotencyKey: "project-session",
});
const team = await lead.createTeam({
  agentTypes: { builder: "builder@1", verifier: "verifier@1" },
});
const run = await lead.send("Build the game, delegate verification, and report.", {
  idempotencyKey: "build-request",
});
const result = await run.result();
```

Rust service sessions expose `create_team(AgentTeamOptions)` and `resume_team()`.
Type-to-release mappings are pinned deployment configuration. The controller checks
activation, principal permissions and profile compatibility. Child creation, initial
task admission and reservation completion commit together. All instances retain the
original environment ID; provisioning a child never creates another environment.

## Communication and control

| Model tool | Purpose |
| --- | --- |
| `team_spawn` | `{agent_type, agent_id, message}` reserves one canonical child session. Identical retries return it; changed tasks/types conflict. Only the lead can spawn. |
| `team_wait_agent` | `{agent_ids, timeout_ms}` parks until a selected child finishes, a message arrives, or timeout. It persists the wait and releases server capacity. |
| `team_post_board` | `{message}` appends shared history. `@builder-ui` or `@all` triggers inbox delivery; passive posts do not notify. |
| `team_read_board` | `{message_id, direction, limit}` reads chronological history with an exclusive anchor, default 20 and maximum 50. |
| `team_send_message` | `{agent_id, message}` delivers privately when enabled. It never writes to the board. |

Applications can enable either, both or neither channel, equally for leads and
workers. Disabled tools are absent from discovery and rejected by dispatch. Spawn's
initial task and explicitly waited results still work with both channels disabled.

Application `post`, `sendMessage`/`send_message`, and `spawn` methods take a command
ID for durable deduplication. `stop` persists the stop before cancelling members;
service controllers reconcile cancellation through the existing control path.
A team stop does **not** delete a shared environment. Close sessions and explicitly
release/delete the owned environment when appropriate.

Working-model limits aggregate across instances, in addition to each agent's turn
limits. Defaults are 100 work calls and 256,000 output tokens, including one bounded
team summary allowance. Unknown usage retains its reservation. A wait consumes no
worker slot or model calls. Cancelled, failed and approval-paused sessions are not
silently restarted by incoming messages.

The shared allowance counts working-generation and final-summary requests.
Auxiliary compaction and auto-approver requests retain their existing per-turn
limits; this is not a combined billing cap for every model purpose.

Agent messages and delegated tasks are collaborator input, never new human approval.
Inbox receipts commit with canonical transcript input, so restart cannot acknowledge
and lose a message. A lost external tool result stays unknown; it is not automatically
replayed. Team members must coordinate files, worktrees and ports themselves.

Threads, external activity subscriptions and model-requested child environments are
outside this version. See [the decisions](AGENT-TEAMS-DECISIONS.md),
[implementation ledger](AGENT-TEAMS-IMPLEMENTATION.md) and
[test case guide](AGENT-TEAMS-TEST-CASES.md).
