# Agent run protocol (for SDK authors)

Status: development API on the SDK host (`harness-stdio`, or `Host` embedded).
Initialize reports `capabilities.agent_runs: true` when available.

This is the contract a TypeScript, Python or any other SDK needs. Agent
compilation, team sessions, run completion, `interrupt_on` auto-approval,
busy retries and journal streaming are implemented once, in the host. An SDK
is a typed wrapper over these JSON-RPC methods plus process launch.
The Rust `openworker_sdk::agents` module is the reference wrapper
(`harness-sdk/src/agents/session.rs`).

Transport is the existing [stdio protocol](../../harness/partner/PROTOCOL.md):
newline-delimited JSON-RPC 2.0, 1 MiB frames, `initialize` first. Requests are
answered in order, so long-polls are bounded (`run/wait` ≤ 5 s).

## Named agent teams

Check `capabilities.agent_teams`. `agent-team/create` accepts a lead
`session_id`, `agent_id` (normally `lead`), registered `agent_types`, optional
`policy:{communication:{board,directMessages},max_instances}` and aggregate
`limits`. Identical retries attach to the original team; changed configuration
rejects. These are ordinary registered agent definitions, separate from legacy
`.subagent()` work-item definitions.

`agent-team/command` accepts `{session_id,operation,arguments}`. Read operations
are `snapshot` and `team_read_board`. Application writes use `operation:command`
with `{request_id,operation,arguments}`; the inner operation is `team_spawn`,
`team_post_board`, `team_send_message` or `stop`. Command IDs are scoped to their
author and persist with the result. Disabled channels reject at dispatch.

`run/wait` includes new ready members and preserves per-member journal cursors.
Attributed events carry `agent_id` and `team_id`, in addition to the registered
type in `agent`. `session/send` validates response targets against the live roster;
clients must not use a stale local member list to reject newly spawned children.
Waits, inbox batches, aggregate limits and stop reconciliation belong to the host.
See the [team QuickStart](AGENT-TEAMS-QUICKSTART.md) and
[decisions](AGENT-TEAMS-DECISIONS.md) for tools and delivery semantics.

## Desktop extensions (22 October 2026)

These apply to the SDK host, not the old single-coworker CLI endpoint. Check
`dynamic_agents`, `application_callbacks` and `session_send` capabilities.

| Method | Purpose |
| --- | --- |
| `agent/register`, `agent/list` | Durable immutable agent definitions, including an initially empty host |
| `provider/set`, `provider/list` | Harness-wide named provider API/endpoint and credential configuration |
| `mcp/register` | Immutable named stdio launcher or loopback Streamable HTTP service |
| `application/register` | Bind persistent tool descriptors to this live app executor |
| `session/send` | Message submission or explicit approval/question response |
| `session/state` | Authoritative aggregate state, pending interactions and journal cursors |

`provider/set` accepts `{name, api, base_url?, credentials?: {api_key, storage}}`.
API is `responses`, `chat-completions` or `anthropic`; storage is `secure` or
`memory`. No credentials means unauthenticated for a new provider, and preserves
credentials for an existing provider. SDKs resolve env references in the app.
New Chat Completions providers require an explicit base_url; a friendly provider
name does not select an endpoint. Responses/Anthropic default to their native APIs.
API/endpoint are immutable per name; key rotation preserves session identity.
The launch JSON never contains keys.

`application/register` accepts `{owner, tools:[{qualified_name, description,
input_schema, version}]}`. Descriptors are durable; executable closures are not.
The app selects qualified names on its agent. The usual schema, risk, policy,
approval and tool-start journal steps happen before dispatch. The host sends
`application/event` notifications with `kind:call|cancel`, an unpredictable
`invocation_id` and owner. Calls also carry tool, arguments and trusted
`context:{session_id,turn_id,call_id,workspace}`.

The app replies with **notifications** (no JSON-RPC id):
`application/result {owner, invocation_id, output}` or `{..., error}`;
`application/progress {owner, invocation_id, message, data}`. These reserved
notifications use an independent reader/control path while ordinary requests
are waiting. Embedded dispatch uses the same validation without pipes. A pipe
write acknowledgment is not a durable effect acknowledgment; the tool journal is
authoritative. Wrong-owner, expired and duplicate invocation results are refused.
An error or lost result becomes an uncertain execution outcome, never an automatic
retry. Errors are sanitized, not returned with application stack traces.

Limits: 1 MiB frame, 128 registered callback descriptors, 64 pending invocations,
128 KB JSON result and existing bounded progress/tool timeouts. Cancellation is
cooperative; aborting a JS handler cannot undo an external side effect. Slow event
consumers recover from the durable cursor journal. The SDK rebinds retained
descriptors/functions after a child restart, before reporting Ready. After a whole
app restart the application must register its functions again. No remote callback
leasing, distributed owner migration, binary callback attachments or OAuth here.

`session/send` takes `{session_id, submission_id?, input}`. Input defaults to
`{type:"message",text}` if type is omitted. Controls are
`{type:"approval",interaction_id,decision:"once"|"session"|"deny",target_session_id?}`
or `{type:"answer",interaction_id,text,target_session_id?}`. Target membership and
current turn/interaction are validated; ordinary text never implies approval.
Responses identify the original run. State is `idle|running|waiting|closed`, with
active phases, pending interactions, `can_accept_message`, and a terminal outcome
only when the current turn is terminal. Recoverable work is not falsely idle.
`timers:null` and capabilities `steering:false,timers:false` mean unsupported.

Agent definitions also accept `memory` (default false), `memory_scope` (optional
trusted application sharing ID) and `scheduling` (default false). A scope requires
memory enabled; TypeScript `memoryScope` and Rust `.memory_scope()` enable it.
External `tools` may be omitted when memory, scheduling or planning is enabled.
Initialize advertises `memory_tools` and `scheduled_tasks`. These flags add
internal model tools, not external registry bindings. See
[memory and scheduling](ASSISTANT-TOOLS.md) for bounds and storage semantics.

When scheduling is enabled, `session/state` returns `capabilities.timers:true`
and `timers:[{id,next_run_at,remaining_runs}]` for that session. Timestamps are UTC
Unix seconds; an empty list means no active schedules. TypeScript maps the fields
to `nextRunAt`/`remainingRuns`. The SDK host admits due work through normal
`turn/start` with a reserved `ow-schedule/` submission ID and an agent-authored
history marker. The database validates the exact due task and commits delivery
with turn admission. Standalone server-reference workers do not dispatch these
timers automatically.

App-local catalogs/provider metadata remain under state_dir even with PostgreSQL
sessions. For multi-node deployments provision consistent registries/providers on
each worker. Choosing PostgreSQL does not turn parent-process callbacks into RPC
services accessible from other nodes.

## Launch

```sh
openworker-harness harness-stdio '<HostConfig JSON>'   # or '@/abs/path/config.json'
```

Use the `@path` form when the JSON exceeds ~32 KB (long instructions). The child
inherits only a small OS environment allowlist, plus variables you explicitly
name (`storage.connection_env`, telemetry `headers[].value_env`). Model keys go
through `connection/set`, never the environment.

```json
{
  "state_dir": "/abs/app-state",
  "runner": "/abs/openworker-harness",
  "sandbox": "openshell",
  "coworkers": [],
  "agents": [{
    "id": "assistant",
    "name": "Assistant",
    "description": "",
    "instructions": "Help the user with their workspace.",
    "tools": ["files.read", "files.write"],
    "model": {"connection": "openai", "model": "gpt-6-sol"},
    "provider": "openai",
    "fallback_model": "gpt-6-sol",
    "planning": true,
    "approvals": {"mode": "only", "tools": ["files.write"]},
    "subagents": [{"id": "researcher", "description": "Finds facts",
                   "instructions": "Research.", "tools": ["web.search"]}]
  }],
  "telemetry": null
}
```

`approvals.mode`: `always` (default), `only` (+`tools`: interrupt_on),
`reviewer`, `never`. Tools are qualified `provider.tool` names that resolve
against the host's tool registry (native tools linked into the executable, or
MCP/HTTP providers via `servers`). At least one tool per agent; at most 15
subagents per lead and 16 agents per host; one level of subagents.

## Methods

### `agent/register` `{agent: AgentDefinition}` → `{agent: Profile, duplicate}`

Available when initialization reports `capabilities.dynamic_agents`. A host may
start with no agents. Register a definition after startup without restarting the
host or disturbing existing sessions. Registration validates the complete agent
and subagent tree, its selected tools and runtime configuration before publishing
any profile. The 16-profile limit includes all startup and dynamically registered
agents and workers.

Repeating an identical root definition returns `duplicate: true`. Reusing an ID
with changed instructions/model/tools/policy, or colliding with another agent's
worker ID, fails. Use a new identity for a changed definition. Registration does
not grant approval or make tools visible to other agents.

Accepted dynamic definitions are committed to `state_dir/sdk-agents.sqlite` and
restored before session recovery. This is local app configuration, separate from
the selectable SQLite/PostgreSQL session store. Preserve this state directory
alongside app configuration when relocating an SDK host; this does not enable
distributed callback or agent-definition deployment. Credentials are not stored
in the catalog. Conflicting startup definitions fail instead of replacing saved
authority. Unknown registration outcomes can be reconciled by repeating the exact
definition; SDK transports still do not blindly replay mutations.

### `agent/list` → `{"agents": [Profile]}`
Profile: `{id, name, description, approvals, subagents: [id], worker_only}`.

### `agent/session` `{agent, workspace, name?, model?}` → `{"session": SessionRef}`
Creates the lead session and, for agents with subagents, one worker session
each plus a team. `SessionRef = {session_id, agent, team?: {team_id, workers:
[{session_id, agent}]}}`. Subagents cannot be started directly.

`model` uses `{connection, model}` on the wire and requires the
`session_model_override` capability. It overrides only the lead's default for this
session, not the agent definition or its workers' model defaults. Resolve and
persist the choice at session creation. SDK-defined live agents need a default
model or an explicit session override; scripted fixtures retain their test model.
Provider credentials must already be configured through the current connection
API. Harness-scoped provider registration is a subsequent implementation step.

### `run/start` `{session_id, submission_id, text}` → `{submission_id, turn, duplicate, cursors}`
`submission_id` is your stable idempotency key: resending it after a lost
response never starts a second turn (`duplicate: true`). `cursors` maps each
member session to its journal position before the turn; pass it to `run/wait`
to stream. Retries internally while the engine is briefly busy.

### `run/wait` `{session_id, submission_id?, wait_ms?, cursors?, max_events?}` → RunState
Long-poll. Returns when the run is no longer `running`, when there are new
events for a streaming caller, or after `wait_ms` (default 1000, max 5000).
Loop until `status != "running"` (and, when streaming, `more_events != true`).

```json
{
  "status": "running | completed | interrupted | cancelled | failed",
  "failure": "truncated",
  "final_output": "text or null",
  "interruptions": [Interruption],
  "submission_id": "…",
  "cursor": 42,
  "board": {"…team board…"} ,
  "events": [{"agent": "…", "session_id": "…", "event": Event, "summary": Summary}],
  "cursors": {"<session_id>": 42},
  "more_events": false
}
```

- `events`/`cursors`/`more_events` appear only when `cursors` was sent.
  Events come from the durable journal up to the observation, so nothing is
  lost to timing or restarts. `summary` is one of
  `{type:"message",text}`, `{type:"tool_started"|"tool_finished",tool}`,
  `{type:"auto_approved",tool}`, `{type:"plan_updated",plan}`, or `null`
  (use the raw `event`).
- Single agent: finished when the turn for `submission_id` (or the current
  turn) is terminal. Team: when every member is idle and the durable
  queue/completion-outbox check also shows no pending work; `board` shows anything
  left open. A quiet period alone is never evidence of completion.

Run admission and terminal results have durable journal receipts, keyed by
`submission_id`. Repeating an ID with different text is rejected. Original
start cursors are returned on retries; the terminal output, board and replay
boundary are retained across subsequent runs and restarts. A new run is rejected
while the preceding run still has work or an interaction pending. A start rejected
before turn acceptance has a `failed` result with `failure: not_accepted`; use a
new submission ID after fixing the request. If a crash occurs before admission,
retry `run/start` with the original ID/text; `run/wait` reports this explicitly
rather than waiting indefinitely. An unknown submission is an error.

Interruption:

```json
{"id": "…", "session_id": "…", "agent": "worker", "kind": "approval",
 "tool": "files__write", "arguments": {}, "category": "write_local"}
{"id": "…", "session_id": "…", "agent": "lead", "kind": "question",
 "question": "Proceed?", "options": ["Yes", "No"]}
```

Calls excluded by `interrupt_on` normally never appear: the host approves them
once as soon as they occur, whether or not a client is waiting. If the host
cannot approve one (for example a binding mismatch), it is surfaced as an
ordinary approval interruption instead of stalling the run.
Approval retries run in the background, including retries after capacity becomes
available. `run/wait` observes their state; it does not execute approval sweeps or
spend its long-poll budget retrying tool decisions.
These approvals persist `origin: app_policy` and the tool alias. Replay derives
`auto_approved` summaries from this durable record, not process memory. The
host-only approval entry point cannot answer questions or mint grants; clients
cannot claim this origin through `interaction/respond` parameters.

### `run/respond` `{session_id, interaction_id, decision | answer}` → `{…}`
Approvals take `decision`: `once`, `session` or `deny`. Questions (including
compaction choices) take `answer`. Use the interruption's own `session_id`
(it may be a worker). Already-resolved interactions return
`{"already_resolved": true, "response": "…"}`. An interaction that belongs to a
different session, or whose turn has ended, is an error. Retries while the
engine is briefly busy.
Repeating the same resolved response is idempotent; changing the decision or
answer is rejected rather than being acknowledged as successful.

### `run/cancel` `{session_id, team?: bool}` → `{cancelled, team_cancelled, errors}`
Cancels active turns of the session and its workers, attempting every member
and reporting per-member `errors`. Queued team deliveries may start again; pass
`team: true` to also end the team permanently (`team_cancel`).

### `agent/close` `{session_id}` → `{closed, errors}`
Closes the session and its workers, attempting every member. Cancel first.
The Rust `Session::cancel` and `Session::close` wrappers return an error when the
per-member `errors` map is nonempty; partial success is not reported as success.

Everything else (`team/*`, `connection/*`, `event/read`, `session/attach`
for restart recovery) remains available unchanged.

## Minimal client loop

```text
initialize
agent/session {agent, workspace}                → session_id
run/start {session_id, submission_id, text}      → cursors
loop:
  run/wait {session_id, submission_id, cursors}  → state
  emit state.events; cursors = state.cursors
  if state.status == "interrupted":
     for i in state.interruptions: run/respond {...}
     (continue looping)
  elif state.status != "running" and !state.more_events: return state
```

## Known limitations

- Tools written in the SDK's own language must be exposed as an MCP server
  (stdio or loopback HTTP) and registered via `servers`; native tools are
  Rust code linked into the host executable.
