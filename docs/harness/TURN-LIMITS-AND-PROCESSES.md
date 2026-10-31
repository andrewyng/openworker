# Turn limits and coding process ownership

Agent definitions set defaults. Each new message turn can supply a complete limits
object that overrides those defaults. Once admitted, limits and counters remain
pinned to that turn across approval waits and recovery. Resuming a turn does not
reset its budget or repeat tool actions.

```typescript
const coder = harness.createAgent({
  name: "coder",
  instructions: "Build and verify the requested app.",
  defaultModel: { provider: "OpenAI", name: modelId },
  tools: ["coding.read_file", "coding.write_file", "coding.run_shell",
          "coding.process_start", "coding.process_status",
          "coding.process_logs", "coding.process_stop"],
  limits: { maxModelCalls: 80, maxOutputTokens: 64000 },
});
const session = await coder.newSession({ workspace });
const run = await session.send("Build my app.", {
  limits: { maxModelCalls: 40, maxOutputTokens: 32000, summaryTokens: 512 },
});
for await (const event of run.events()) render(event);
```

```rust,ignore
use openworker_sdk::{Agent, TurnLimits};
let coder = Agent::new("coder")
    .instructions("Build and verify the requested app.")
    .limits(TurnLimits {
        max_model_calls: 80,
        max_output_tokens: 64000,
        ..Default::default()
    });
// Register/create the agent and its session through the normal Harness API.
let run = session.send_with_limits("Build my app.", TurnLimits {
    max_model_calls: 40,
    max_output_tokens: 32000,
    ..Default::default()
}).await?;
```

For service deployments the immutable agent release also accepts `limits`. The
TypeScript service session accepts `session.send(text, {limits, idempotencyKey})`.
Rust service sessions expose `send_with_limits(text, Some(limits), key)`. A combined
service submission can supply limits for its initial message. Existing calls that
omit limits keep the agent defaults. Limits on a combined submission affect only
its initial turn; they never replace the agent default for subsequent messages.
Create an empty session without a turn limit, then pass it with the message.
The new SDK refuses explicit local turn limits on an older host that does not advertise `turn_limits`.

## What is counted

- `maxModelCalls` counts working model requests/retries and in-turn compaction and
  auto-review requests. A call can propose multiple tools; it is not a tool count.
- `maxOutputTokens` counts output across those requests, including provider-reported
  reasoning tokens when included in output. Input tokens are separate.
- Defaults: 100 work model calls, 256,000 output tokens and a 512-token summary
  reserve. Supported ranges are validated by the runtime before execution.
- One **additional tool-free summary request** is allowed after work stops. Its
  reserve (`summaryTokens`, default 512) is **inside** the output-token total.
  It does not receive another work budget and is never retried automatically.
- Each request receives a bounded provider output cap. The allowance is reserved
  durably before dispatch. Known usage replaces the reservation; absent/unreliable
  usage retains that conservative charge. Unknown usage is never called zero.
  A provider that violates its requested output cap can report an overrun; the
  harness then stops further work rather than claiming it can undo billed tokens.

When a work limit is reached, queued tools receive a structured refusal with
`executed:false` and code `turn_limit_exceeded`. No further tool effects are
allowed. The model briefly explains the limit, completed work and unfinished work.
The turn finishes with `stop_reason` and `limited:true` in its durable completion
event. If the provider cannot generate that final message, a host-authored fallback
states that the summary is unavailable; it never invents completed work.

## Correctable tool arguments

Invalid JSON/schema arguments return `invalid_tool_arguments`, with `executed:false`
and a safe schema hint. First-party file tools also explain that absolute paths
must be workspace-relative. Corrections still pass all approval and path checks.
Traversal, policy denials and unknown execution outcomes remain distinct; no error
handler automatically retries an action.

Five consecutive invalid requests stop the turn with a short explanation. Valid
arguments reset that consecutive-error streak; total validation errors remain in
turn accounting. Model-call/token budgets independently bound recovery attempts.

## Existing process tools, longer-lived ownership

`run_shell` runs a fresh shell and returns an output snapshot. If still running,
it returns a handle. Use `process_start` for servers or deliberately long jobs,
then `process_status`, `process_logs` and `process_stop`. Do not rely on `cmd &`.
A spawn receipt is not HTTP readiness; check the endpoint separately.

For an environment-backed OpenShell service session without temporary resource
policy grants, the runtime uses a sandbox-local process manager. It owns bounded
process groups and log buffers across worker/MCP attachments. The same opaque
handle can be checked in a later turn; no OS PID is accepted. Handles are scoped
to the canonical workspace and manager generation, with same-UID private local IPC.
Agents sharing that workspace must coordinate edits, processes and ports.

The manager inherits sandbox isolation. It receives no model key and does not
change filesystem/network grants or run programs on the API/SDK host. Profiles
with explicit temporary credential/resource policies keep turn-bounded execution;
we do not prolong such grants for a preview. Local attachment-owned coding tools
retain their existing cleanup rules.

Process deadlines default to one hour and are capped at one day. At most eight
processes run concurrently per manager, with bounded retained snapshots. An idle
manager with no active processes exits after approximately 15 minutes. Environment
teardown terminates it. A manager or sandbox crash does **not** recover an old
process: stale/unavailable handles and uncertain launch replies require status or
endpoint reconciliation. The client never automatically repeats a delivered launch.

## Telemetry and storage

Typed, content-free operations are exported through the existing recorder/OTel
path: `turn.budget_exceeded`, `tool.validation_failed`, `process.started`,
`process.stopped`, `process.stale` and `tool.unknown_outcome` where applicable.
Identities are hashed; raw arguments, commands, prompts and credentials are not
added to those events. Detailed safe limits, counters and stop reasons remain in
the durable journal and turn snapshot. Normal model usage/duration records continue
to include the summary, reviewer and compaction requests.

SQLite stores resolved turn state and override identity atomically with admission.
PostgreSQL also retains limits on queued submissions. Run the existing explicit
PostgreSQL migration before upgrading workers: it adds a nullable JSONB `limits`
column without resetting existing state. No second budget database is needed.

See [the implementation ledger](CODING-CONTINUITY-PLAN.md) and
[the prior acceptance findings](LOCAL-CODING-ACCEPTANCE.md).
