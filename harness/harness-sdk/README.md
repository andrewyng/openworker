# OpenWorker Harness SDK (development API)

One Rust client, two deployment backends, the same native engine. This workspace
crate is not yet published or a stable SDK compatibility promise. It is distinct
from `openworker-tools`, which defines partner-owned tools.

**Storage-selectable host:** use `HostConfig` with either `Storage::Sqlite` or
`Storage::Postgres`, and register one or more trusted coworkers. Both
`HarnessClient::embedded` and `HarnessClient::managed_stdio` support this host,
including Teams. See [SDK storage and teams](../../docs/harness/SDK-STORAGE.md)
for configuration, native child packaging and the tested four-way matrix.
The host may also start with zero agents. `agents::Harness::create_agent(definition)`
registers an agent after startup and returns an `AgentHandle` with `new_session`,
`new_session_with_model` and `resume_session`. `Agent::default_model(ModelChoice)`
sets the reusable default. Live SDK agents require an explicit model choice.
Dynamic definitions persist in the app state directory and restore before session
recovery; exact re-registration is idempotent and conflicting identity reuse fails.
Use `list_agents().await` for the current catalog, not the startup `agents()` snapshot.
Desktop defaults, shared providers, application callbacks and the send/state API
are implemented. For a complete app, use the [desktop starters](../desktop-starters/README.md).
The executable is bundled beside your app (or supplied explicitly); no executable
is selected from PATH. Rust embeds the engine by default. A managed child is optional.
The examples below retain the compatible single-coworker `RuntimeConfig` API.

```text
Partner Rust application → HarnessClient
                            ├─ embedded → Runtime::dispatch
                            └─ managed stdio → owned child → existing JSON-RPC server
```

## Quick start: the agents API

```rust,no_run
use openworker_sdk::{Agent, Credentials, Harness, Model, ModelProvider, Sandbox, Tool};
use futures_util::StreamExt;
use serde_json::json;
# async fn example() -> anyhow::Result<()> {
let harness = Harness::builder()
    .sandbox(Sandbox::Off) // Explicit: trusted app callbacks, not OS-isolated tools.
    .start().await?;
harness.set_model_provider(ModelProvider::responses("OpenAI")
    .credentials(Credentials::from_env("OPENAI_API_KEY"))).await?;
harness.register_tools("app", [Tool::new("show_status")
    .description("Display a status update in this app.")
    .input_schema(json!({"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}))
    .handler(|args, context| async move {
        context.progress("Updating app", json!({})).await?;
        // Forward args["text"] to your application's UI here.
        let _ = args;
        Ok(json!({"displayed":true}))
    })]).await?;
let agent = harness.create_agent(Agent::new("assistant")
    .instructions("Help the user and report progress in the app.")
    .default_model(Model::new("OpenAI", "YOUR_MODEL_ID"))
    .tool("app.show_status")).await?;
let session = agent.new_session().await?;
let saved_reference = session.reference().clone(); // Persist in app project metadata.
let run = session.send("Help me plan my day.").await?;
let mut events = run.events();
while let Some(event) = events.next().await { let event = event?; /* render event */ }
let result = run.result().await?; // May be Interrupted; show the real approval/question.
let state = session.state().await?;
harness.shutdown().await?;
# Ok(()) }
```

For a local model, use `ModelProvider::chat_completions("Ollama")`
with `.base_url("http://127.0.0.1:11434/v1")` and no credential. Use the exact
installed model ID. Providers are shared by this harness; sessions pin the resolved
provider identity, API, endpoint and model. Rotation preserves that identity;
changing API/endpoint requires a new name. No model fallback is performed.

Windows uses DPAPI and macOS uses Keychain for secure credentials. On Linux,
choose `.memory_only()` explicitly or use server-managed credentials; there is no
plaintext fallback. Keys never belong in agent definitions. Re-register callback
functions after restarting your app; only descriptors persist. Callback code runs
with app privileges even when other tools use a sandbox.

Use `session.send(SessionInput::Approval { .. })` or `SessionInput::Answer { .. }`
for explicit controls. A plain string is always a user message, never an approval.
Use `agent.resume_session(saved_reference)` after restart. Dropping an event stream
does not cancel a run; call `session.cancel()` explicitly.

## Lower-level and compatibility examples

Current limits: 16 profiles per host, one subagent level, and 1–64 selected tools
per agent. Callback descriptors are immutable and bounded to 128 per host; changed
tool semantics should use a new qualified name/version. Callback outputs are JSON,
not binary streams. Timers and steering are explicitly unsupported in `state()`.

Most applications should start with `openworker_sdk::agents`: define agents as
values, run them, handle typed interruptions. It is a thin layer over the
`HarnessClient` described below. See [Agents API](../../docs/harness/AGENTS-API.md).

```rust,ignore
use openworker_sdk::agents::*;

let agent = Agent::new("assistant")
    .instructions("Help the user with their workspace.")
    .tools(["files.read", "files.write"])
    .planning(true)
    .interrupt_on(["files.write"]);

let harness = Harness::builder().state_dir(state_dir).executable(runner).agent(agent).tools(registry).embedded().await?;
let session = harness.session("assistant", workspace).await?;
let result = session
    .run_with("Tidy the docs folder", |_| Response::Approve(ApprovalDecision::Once))
    .await?;
println!("{}", result.final_output.unwrap_or_default());
```

## Choose a backend

```rust,ignore
use openworker_sdk::{ClientOptions, HarnessClient, ProcessConfig, RestartPolicy,
                     RuntimeConfig, Sandbox, ToolRegistry};

let mut config = RuntimeConfig::new(state_dir, coworker_bundle, runner_binary);
// Explicit opt-out; required for today's native Windows profile.
config.sandbox = Sandbox::Off;

// Optional fallback provider. Named model connections take precedence.
config.provider = openworker_sdk::ProviderKind::Anthropic;
config.model = "claude-sonnet-5-5".into();

// Embedded: your application supplies its own Tokio runtime and native tools.
let mut tools = ToolRegistry::default();
example_tools::register(&mut tools)?;
let client = HarnessClient::embedded(config, tools, ClientOptions::default()).await?;

// Alternatively: SDK starts the partner executable and privately owns its pipes.
let client = HarnessClient::managed_stdio(ProcessConfig {
    program: example_runtime_binary,
    runtime: config,
    restart: RestartPolicy::default(),
}, ClientOptions::default()).await?;
```

Those are alternatives, not two simultaneous owners of one state directory.
All configuration paths must be absolute. `RuntimeConfig` loads one coworker;
`HostConfig` routes multiple profiles through the same selected storage adapter.
Coworker native tool names resolve against the
embedded registry or the registry linked into the selected child executable.
The SDK cannot transmit a Rust implementation over stdio. Both modes may load
app-owned `servers.json` via `RuntimeConfig.servers`.

Embedded mode is direct dispatch, not an internal pipe, and does not install
signal handlers or start/stop the application's Tokio runtime. It still requires
a compatible OpenWorker runner executable for subprocess tool/sandbox helpers;
it does not mean every MCP/sandbox component runs inside the application.
Native tools share process trust and must not block/detach work. A native crash
can take down the embedding application; this is not a sandbox.

## Use the same operations

```rust,ignore
let mut notifications = client.subscribe(); // before attaching/starting work
let mut connection = client.connection();   // watch channel, current state retained
let capabilities = client.info();           // initial initialize handshake

let session = client.create_session(SessionOptions {
    name: "My assistant".into(), coworker: "example".into(), workspace,
    model: Some(ModelChoice { connection: "openai".into(), model: "gpt-6-sol".into() }),
    permissions: None, // existing runtime default: Ask
}).await?;
let snapshot = client.attach(&session, saved_processed_cursor).await?;
client.start_turn(&session, &persisted_submission_id, "Help with this task").await?;

// Only after the app obtains the user's actual decision:
client.approve(&session, &approval_id, ApprovalDecision::Session).await?;
client.answer(&session, &question_id, &user_answer).await?;
client.cancel_turn(&session, &turn_id).await?;
client.shutdown().await?; // deterministic cleanup; closes all client clones
```

`snapshot` reads current state without journal replay or cursor advancement.
`attach` returns a snapshot, remembers the session, and emits replay events from
the supplied cursor (up to 100 journal pages per operation). Subscribe first,
consume concurrently for long replays, and deduplicate `(session_id,event_id)`.
`acknowledge` updates the SDK's in-memory processed cursor only after your app has
processed the event; it does not acknowledge or approve tool execution. Persist
that cursor and session ID yourself to recover after an application restart.
Do not advance a processed cursor past an unresolved replay gap merely because
a later live notification arrived; snapshots and replay may overlap.

Requests are typed for the core flow; evolving snapshots/results remain JSON.
The bounded `request(method, params)` escape hatch covers model connections,
defaults, policies, permissions/revocation, skills, history and native tool controls
using the [existing protocol](../partner/PROTOCOL.md). Initialization is SDK-owned.
Use `attach` for replay subscriptions. Model credentials should be configured via
`connection/set`, never executable arguments or coworker bundles. Managed children
receive a narrow OS/runtime environment allowlist, not inherited provider keys.
Raw stderr is discarded, not copied into UI/telemetry. Memory credentials do not
survive a child restart; query connection status and obtain them from the app again.

## Process recovery contract

- Startup/handshake failures return an error; they do not enter a restart loop.
- After successful startup, unexpected exit, broken/malformed stdio or an unknown
  request outcome triggers bounded recovery. Default: **three restarts per client
  lifetime**, 250 ms exponential backoff capped at 5 seconds. Success does not reset
  the budget. States: Starting → Ready → Recovering → Ready, or Failed.
- SDK closes and reaps its exact child before launching another. No shell launch,
  network listener, port allocation or shared machine service is involved.
- Recovery initializes a new connection, attaches remembered sessions, emits
  replay plus `Recovered` snapshots, and returns Ready. A session-level failure
  is surfaced as `RecoveryFailed`, not silently recreated or resumed.
- **No requests or tool calls are automatically resubmitted.** An interrupted
  in-flight request returns `OutcomeUnknown`, even if it may have committed.
  Reconcile the snapshot/journal. For an intentionally retried turn submission,
  reuse the original stable submission ID—never invent one to hide ambiguity.
- Requests while unavailable/full queues are rejected, not held for surprise
  execution later. Queued requests are bound to their connection generation.
- Dropping a request future does not retract work already queued or accepted;
  use the explicit turn-cancellation operation and reconcile durable state.
- Pending approvals/questions remain durable. Started uncertain effects are
  interrupted by the existing harness recovery rules, never replayed. Restarts
  do not restore screen/input leases or bypass any permission checks.
- Events are at-least-once recovery hints, not exactly-once messages. The 256-slot
  broadcast stream can return `Lagged`; `Gap` also means recover by journal cursor.
  `event/read` remains available if automatic replay reaches its bounded limit.
- Explicit `shutdown` and last-handle drop never restart the child. Always await
  shutdown before tearing down the host Tokio runtime. Drop requests best-effort
  cleanup; it cannot synchronously await shutdown. Embedded shutdown failures are
  reported; an in-process SDK cannot forcibly isolate noncooperative native code.

This release keeps existing binary/tool/policy identity pinning. A process restart
with the same installation can resume saved sessions. Upgrading the executable
or switching an existing session between embedding and child binaries may require
a new session. Compatible upgrade/rebinding policy is separate work; the SDK does
not weaken saved approval identities to make upgrades appear seamless.

## Example and verification

`cargo run -p openworker-sdk --example connect -- <embedded|stdio> <absolute-state>
<absolute-bundle> <absolute-runner> <absolute-workspace> [--sandbox-off]`

The example discovers a coworker, creates/attaches a session and shuts down without
calling a model. Its empty registry supports MCP-based bundles; native partner
bundles require registering their crates in the embedded executable.

Native Linux/Windows conformance:

```sh
cargo test --locked -p openworker-sdk --features test-support -- --test-threads=1
cargo clippy --locked -p openworker-sdk --all-targets --features test-support -- -D warnings
```

The feature-gated `sdk-test-host` is a development fixture, not a partner binary.
Tests use scripted models and real subprocesses/native file effects, with no API
keys: both backends' session/approval flows, stable submissions, shutdown, crash
reattachment, no repeated uncertain effects, transient access revocation, bounded
restart exhaustion and in-flight outcome uncertainty. Test/debug binaries have
large hashed identities: Linux container runs use the normal 60-second request
timeout and serial test execution. Mac host compilation is not a native Mac port.

### Turn limits

Set an agent default with `.limits(TurnLimits { ..Default::default() })`; use
`session.send_with_limits(text, limits)` for one new message. Resuming keeps the
original limits and consumption. See [turn budgets and process ownership](../../docs/harness/TURN-LIMITS-AND-PROCESSES.md)
for the bounded final-summary reserve and sandbox-owned process tools.
