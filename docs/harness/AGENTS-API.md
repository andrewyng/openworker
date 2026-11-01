# Friendly agents API (`openworker_sdk::agents`)

For the agreed next API direction (not implemented yet), see
[Agent / Session / send / state design](AGENT-RUN-PROTOCOL.md).
The examples below describe the current API.

Status: development API. Agent compilation, team sessions, run completion,
`interrupt_on` auto-approval, busy retries and journal streaming run inside the
SDK host (embedded or child process) behind the `agent/*` and `run/*` protocol
methods, so other-language SDKs get the same behaviour; this Rust module is a
thin typed wrapper. Wire contract: [Agent run protocol](AGENT-RUN-PROTOCOL.md).

The [Node.js TypeScript SDK](../../harness/typescript-sdk/README.md) wraps those
same methods over managed private stdio. It does not duplicate the agent loop,
team scheduler, approval policy, or SQL adapters. Rust also supports embedding;
TypeScript initially supports a separate Rust process only.

## Why

The protocol client is correct but low level: agents are bundle directories
registered at host start, a run is `create_session` + `attach` + `start_turn` +
polling snapshots, approvals are raw `interaction/respond` calls, and teams are
`team_command(operation: String, arguments: Value)`.

This layer borrows the **shape** of the OpenAI Agents SDK (an agent is a value;
a run returns a result with typed interruptions you approve and resume) and the
**built-ins** of LangChain Deep Agents (declarative subagents, a todo tool, a
per-tool `interrupt_on` map). Durability, recovery and permission checks stay
exactly where they are today.

## At a glance

```rust,ignore
use openworker_sdk::agents::*;

let researcher = Agent::new("researcher")
    .description("Finds and verifies facts")
    .instructions("Research the assigned item and report evidence.")
    .tool("web.search");

let assistant = Agent::new("assistant")
    .instructions("Help the user with their workspace.")
    .tools(["files.read", "files.write"])
    .model("openai", "gpt-6-sol")    // named model connection
    .planning(true)                  // durable todo list (update_plan)
    .interrupt_on(["files.write"])   // only writes wait for a human
    .subagent(researcher);           // lead/worker team per session

let harness = Harness::builder().state_dir(state_dir).executable(runner)
    .agent(assistant)
    .tools(registry)                 // native / MCP / HTTP providers
    .storage(Storage::Sqlite)
    .embedded()                      // or .managed(program, RestartPolicy::default())
    .await?;

let session = harness.session("assistant", workspace).await?;

// 1. Simple: handle interruptions inline.
let result = session
    .run_with("Tidy the docs folder", |i| match &i.kind {
        InterruptionKind::Approval { .. } => Response::Approve(ApprovalDecision::Once),
        InterruptionKind::Question { options, .. } => {
            Response::Answer(options.first().cloned().unwrap_or_default())
        }
    })
    .await?;

// 2. OpenAI-style: run, inspect interruptions, respond, resume.
let mut result = session.run("Tidy the docs folder").await?;
while result.status == RunStatus::Interrupted {
    for i in &result.interruptions {
        session.respond(i, ask_the_user(i).await).await?;
    }
    result = session.resume().await?;
}

// 3. Streaming.
let mut stream = session.run_streamed("Summarise the changes").await?;
while let Some(event) = stream.next().await {
    if let RunEvent::Message { agent, text } = event { println!("{agent}: {text}") }
}
let result = stream.finish().await?;
```

## Concept mapping

| This API | OpenAI Agents SDK | Deep Agents | Engine underneath |
|---|---|---|---|
| `Agent::new(id).instructions(..).tools(..)` | `Agent(name, instructions, tools)` | `create_deep_agent(...)` | Generated coworker bundle (`coworker/manifest.md` + `tools.json` v3) under `state_dir/agents/<id>` |
| `.model(connection, model)` | `model=` | `model=` | Manifest `default_model` |
| `.planning(true)` | – | `write_todos` | Manifest `planning: true` (`update_plan`) |
| `.subagent(agent)` | `handoffs` / `as_tool` | `subagents=[...]` | Lead/worker manifests + a durable Team per session |
| `.interrupt_on([...])` | tool approvals / HITL | `interrupt_on={...}` | Session `ask` mode; the host approves unlisted calls once as they occur |
| `Approvals::Reviewer` / `Never` | – | – | Session `auto-approve` / `bypass` |
| `session.run(input)` → `RunResult` | `Runner.run` → `final_output` | `agent.invoke` | `turn/start` + snapshot/event loop |
| `RunResult.interruptions` + `resume()` | `result.interruptions` + resume | `Command(resume=...)` | Pending durable interactions |
| `run_streamed` → `RunEvent` | `Runner.run_streamed` | `stream` | Notification stream (wake-up hints) + snapshots |
| `SessionRef` | `Session` | thread id / checkpointer | Session IDs (+ team ID and worker IDs) you persist |
| `session.team()` → `Team` | – | – | Typed `team/*` requests |

## Semantics worth knowing

- **Agents are fixed at harness start.** Definitions compile into bundles before
  the host starts. Output is deterministic, so an unchanged agent keeps its
  bundle digest and existing sessions remain valid. Changing an agent's prompt,
  tools or role changes the digest and, as with hand-written bundles, requires
  new sessions for it. The host only rewrites directories it created (marker file).
- **At least one tool per agent.** The engine requires a bound tool; this is
  validated up front with a clear error.
- **`interrupt_on` is application policy, not an engine bypass.** Sessions stay
  in `ask` mode; the host answers `once` for calls whose tool is not listed, even when no client is waiting. Each
  auto-approval is a normal durable interaction record with `origin: app_policy`
  and a `RunEvent::AutoApproved`, including replay after restart. It is never
  recorded as a human decision and cannot answer a user question.
  Engine hard checks (path containment, pinned identities) still apply first.
  Team and plan tools are not approval-gated by the engine.
- **When is a run finished?** Single agent: the lead turn for this submission is
  terminal and nothing is pending. With subagents: every member turn is terminal,
  no deliveries are queued/running, and no completion notifications await
  processing. Admission is held while observing these facts, so capacity or
  missing credentials cannot make queued work look completed. A team that stops
  with unassigned/dependency-blocked items open or in review still finishes;
  inspect `RunResult.board`.
- **Busy engine.** Starting a turn or answering right as a turn parks can hit
  "checkpointing" or the concurrency limit; the host retries the same idempotent
  request with back-off for up to 20 seconds.
- **Streaming is journal-backed.** `run_streamed` emits events read with
  `event/read` up to each observed snapshot, so fast turns and restarts lose
  nothing. The stream channel is unbounded so an unread stream never stalls a run.
- **Compaction prompts and other non-tool interactions** surface as
  `InterruptionKind::Question` and take an `Answer`.
- **`Session::cancel`** cancels active turns only; `Team::cancel` ends a team
  permanently.
- **Subagents** cannot be started directly (`harness.session("worker")` fails);
  at most 15 per lead, 16 agents per harness, unique display names.
- **Runs have durable receipts.** `run/start` journals the original input and
  replay cursors before admitting a turn. A terminal result (including the team
  board and ending cursors) is saved by submission ID. Retrying an old submission
  returns that result, even after later turns or a host restart. A new run cannot
  replace an unfinished run; resume/respond/cancel it first. Journal pagination
  honors both the adapter's byte/count limits and the caller's event limit.
- **Runs are reconstructed from durable state.** Live events are only wake-up
  hints; each loop re-reads snapshots, so replay, `Lagged`, `Gap` and child
  restarts are handled by re-observing. Nothing is resubmitted automatically.
  `run_with_submission(id, input)` gives you a stable ID to retry after
  `Error::OutcomeUnknown`.
- **Interruptions span the team.** A worker's approval or question surfaces from
  the lead session's run, tagged with `agent`.
- **Restart recovery.** Persist `session.reference()` (serde) and call
  `harness.resume(reference)`; then `session.resume()` continues waiting.

## Telemetry

Off by default. `HarnessBuilder::otlp(endpoint)` or `.telemetry(TelemetryConfig { .. })`
enables the OTLP/HTTP exporter for the SDK host in both embedded and managed
mode (`HostConfig.telemetry`). The same `otlp::Config` is used by the server
reference.

| Field | Meaning |
|---|---|
| `endpoint` | Base URL; `/v1/{traces,metrics,logs}` is appended. A base path is allowed (`https://cloud.langfuse.com/api/public/otel`, Grafana Cloud's `/otlp`). HTTPS unless loopback. |
| `headers` | Up to 8. `Header::secret(name, ENV_VAR)` reads the value from the environment at startup (never from config, URL or argv; marked sensitive, never printed). `Header::literal(name, value)` for non-secrets. Exporter-managed headers (`host`, `content-*`, `proxy-*` ...) are refused. |
| `signals` | Any of `traces`, `metrics`, `logs` (default all). Langfuse ingests traces only. |
| `content` | `ContentPolicy { capture, max_bytes, redact_secrets }`, see below. |

### Content capture (opt-in)

| `capture` | Adds to spans |
|---|---|
| `off` (default) | Nothing: timings, outcomes, token counts and hashed IDs only. |
| `metadata` | `gen_ai.request.model`, `gen_ai.tool.name`, `gen_ai.operation.name`, `langfuse.observation.type`, `session.id` (the session hash). No text. |
| `full` | Also `gen_ai.prompt` (last 20 history items, tail-truncated), `gen_ai.completion`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`. |

Each `full` value is truncated to `max_bytes` (default 8 KiB, max 64 KiB) and,
by default, passed through best-effort secret masking (API-key prefixes,
bearer tokens, PEM private keys). Masking is not a guarantee: `full` capture
can still contain personal or confidential data, so treat the backend as a
system of record for conversation content and set retention accordingly.
Content is attached to spans only, never to metric labels or the lifecycle log
stream, and is computed lazily so `off`/`metadata` cost nothing.

### Backends

- **Langfuse (direct):** `endpoint = https://<region>.cloud.langfuse.com/api/public/otel`,
  `Authorization` secret = `Basic base64(public_key:secret_key)`, literal
  `x-langfuse-ingestion-version: 4`, `signals = [traces]`, `capture = full` (or
  `metadata` for timings/models only).
- **Grafana Cloud (direct):** OTLP gateway URL ending in `/otlp`, `Authorization`
  secret = `Basic base64(instance_id:token)`.
- **Datadog:** prefer the local Datadog Agent's OTLP receiver
  (`http://127.0.0.1:4318`, no headers). Datadog's agentless intakes use
  per-signal paths that this base-URL exporter does not target.
- **Anything else:** an OpenTelemetry Collector on loopback.

Export failures never fail a run; shutdown flushes for at most 5 seconds.

## Not in this change (follow-ups)

- Skills on generated agents (copying `coworker/skills/*` into bundles).
- Async/steerable subagents and `fork` mode (needs engine support).
- Typed `Agent::tool_fn(...)` closures; today tools come from `ToolRegistry`.
- Agent-level guardrails and output schemas (`output_type` / `response_format`).
  The proposed guardrail/hook extension is documented in the
  [draft contract](GUARDRAILS-AND-HOOKS.md) and
  [implementation plan](GUARDRAILS-AND-HOOKS-PLAN.md); those APIs are not yet shipped.
- Session listing/closing helpers on `Harness`, and a typed model-connection API.
