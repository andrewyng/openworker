# OpenWorker TypeScript Harness SDK

Registry candidate: **0.1.0-beta.1**. After publication, install with `npm install @openworkerai/harness-sdk@0.1.0-beta.1`; the matching native runtime is selected automatically. Until then, use the locally prepared client and platform archives. See the [Desktop guide](https://openworker.com/docs/sdk/desktop/) and [app-building skill](https://github.com/andrewyng/openworker/tree/main/skills).

Beta API, Node.js 20+, ESM. Registry-ready source/tarball distribution. The Rust host owns model/tool execution, policy, persistence,
agent teams and run semantics. This package owns typed APIs and a managed private
stdio connection. No browser runtime, native Node binding, REST server, or Python
dependency. Python/Desktop remains separate.

## Desktop QuickStart

For service deployments, [OpenShell integration](../../docs/harness/guides/openshell.md)
covers `RuntimeProfile.gitAccess` and `runtimeProfileGrants(profile)`. These select
operator-installed grants without passing Git credentials through the SDK.
For artifact-first builders and verifiers, see [Sessions and teams](../../docs/harness/guides/sessions.md)
and `isolatedCodingServiceTeam`; exact submitted revisions cross independent
sandboxes without sharing a filesystem or GitHub credential.

For server/container coding agents, see [Tools and MCP](../../docs/harness/guides/tools.md).
`autoSkipQuestions` is independent of `approvals`; select `coding.*` tools explicitly.

The [desktop starters](../desktop-starters/README.md) bundle the runtime and wire
model settings, chat, approvals and an isolated playable preview. Application code
runs in Electron's trusted main process, not the renderer.

```typescript
import { Harness } from '@openworkerai/harness-sdk';

const harness = await Harness.start({ sandbox: 'off' }); // Trusted app callbacks only.
await harness.setModelProvider({
  name: 'OpenAI', api: 'responses',
  credentials: { apiKeyEnv: 'OPENAI_API_KEY', storage: 'secure' },
});
// A key collected by your UI can instead be supplied as { apiKey, storage: 'secure' }.
await harness.registerTools({ namespace: 'app', tools: {
  show_status: {
    description: 'Display a status update in the app.',
    inputSchema: { type: 'object', properties: { text: { type: 'string' } },
      required: ['text'], additionalProperties: false },
    execute: async (args, context) => {
      await context.progress('Updating app');
      // Forward args.text to your UI; honor context.signal for cancellation.
      return { displayed: true };
    },
  },
}});
const agent = harness.createAgent({
  name: 'assistant', instructions: 'Help the user and report progress in the app.',
  defaultModel: { provider: 'OpenAI', name: 'YOUR_MODEL_ID' },
  tools: ['app.show_status'],
});
const session = await agent.newSession();
const reference = session.reference(); // Save in your app's project metadata.
const run = await session.send('Help me plan my day.');
for await (const event of run.events()) { /* render event */ }
const result = await run.result(); // Check interruptions; do not assume success.
await harness.shutdown();
```

For a guide that must only return text, set `tools: []`, `planning: false`,
`memory: false` and `scheduling: false`. No implicit `ask_user` or `read_history`
tools are exposed. See [Sessions and teams](../../docs/harness/guides/sessions.md).

When the built-in `read_history` tool is available, its `after` and `offset`
arguments are transcript positions, not journal event IDs. Start with
`after: 0, offset: 0`, then pass each result's `next_index` as `after` and
`next_offset` as `offset`. Stop when `has_more` is false. Invalid positions return
an `invalid_history_cursor` tool error with reset guidance, allowing the model to
correct the call in the same turn. Missing sessions and storage failures remain
runtime errors. History excerpts never expose provider-private reasoning.

No-key local providers use `api: 'chat-completions'` and an explicit `baseUrl`,
such as `http://127.0.0.1:11434/v1`. Configure the provider before creating a live
session. Use the agent's `defaultModel`, or override `model` in `newSession()`.
The resolved selection survives restart. Endpoint/API changes require a new name.

`Harness.start()` accepts optional executable/state paths, `appId`,
`sessions: { persist, directory }`, `scratch: { directory }`,
`compaction: { triggerAtContextFraction, contextWindow }`, `modelProviders` and
`mcpServers`. State defaults to the app directory under `~/.openworker`.
The bundled executable is resolved without PATH lookup. Persistent state is the
default; opting out does not erase workspace files or logs.

`session.state()` reports active work, pending interactions and whether another
message is admissible. `session.send({type:'approval', interactionId, decision:'once'})`
and `session.send({type:'answer', interactionId, text})` are explicit controls.
Strings or `{text}` are only messages. `run.events()` and `run.result()` are
independent; stopping observation does not stop execution. Use `session.cancel()`.

Secure keys use Windows DPAPI/macOS Keychain. Linux requires explicit `storage:
'memory'` or server provisioning; no plaintext fallback. Application callbacks use
private stdio, not a listening MCP server. Register them again after app restart.
The SDK rebinds retained callbacks after child restart but never replays uncertain
side effects. Callbacks retain the app's privileges, not an OS sandbox's.

## Application-tool screenshots

An application callback can explicitly return one image alongside JSON metadata:

```typescript
import { ToolImageResult } from '@openworkerai/harness-sdk';
// Inside execute(): capture only the app-authorized surface.
return new ToolImageResult(
  { width: 960, height: 600, scope: 'game-preview' },
  { mimeType: 'image/jpeg', base64: screenshotBytes.toString('base64') },
);
```

Requires a rebuilt host advertising `application_tool_images` and a vision-capable
model. JPEG/PNG only, one image, at most 768,000 base64 characters; ordinary metadata
retains its 128,000-character bound. The host validates image signatures. Only the
latest image per session is kept ephemerally for up to five minutes; durable events
and history contain a reference, not pixels. Older images must be recaptured when
needed. The provider receives pixels and applies its own retention rules. This API
does not grant capture permission or make callbacks sandboxed: applications must
authorize and constrain what they capture. Plain JSON return values are unchanged.

## Chat Completions streaming compatibility

Google Gemini's compatible endpoint supports signed tool conversations; the
harness preserves provider-private thought signatures across tool results and
restart. See [Gemini tool conversations](../../docs/harness/guides/providers.md#gemini-tool-conversations).
Anthropic Messages enables automatic prompt caching by default; use
`promptCaching: false` to opt out. See [Anthropic prompt caching](../../docs/harness/guides/providers.md#anthropic-prompt-caching).

The harness requests streaming token usage by default. Set `streamUsage: false`
on `setModelProvider({ api: "chat-completions", ... })` only when a compatible
server rejects `stream_options`. This persisted setting survives credential
rotation; omitting it during re-registration preserves the existing choice.
Missing usage remains unknown, not zero. See
[streaming token usage](../../docs/harness/guides/providers.md#streaming-token-usage).

For partner proxies with short-lived bearer JWTs, use `auth.getToken` and
`harness.updateProviderCredentials()`. See [models and credentials](../../docs/harness/guides/providers.md) for provider setup.
This requires rebuilding the native harness; access tokens stay in memory.

The Rust host accepts both OpenAI's empty-choices usage trailer and OpenRouter's
content-free usage trailer that repeats the finish reason. The latter must match
the generation and finish reason, contain valid usage and no new text/tool payload,
and still end with `[DONE]`. It does not create another message or execute a tool
twice. This behavior lives in the Rust runtime: updating the TypeScript package
alone will not fix an older bundled executable. Rebuild your partner runtime too.

Incremental text is available in `run.events()` as the raw event
`event.type === "assistant_delta"`, with `event.data.text` and
`event.data.attempt_id`. Summaries of type `message` describe completed messages,
not text deltas. Keep partial output grouped by attempt and handle failed/retried
attempts separately; do not blindly concatenate a failed attempt into its retry.
The game-builder starter's existing UI displays progress and completed messages;
incremental-text rendering is a separate UI improvement.

Application tools receive `context.callId`. Tool-start and tool-finish summaries
expose that same ID as `item.summary.callId`; the raw journal uses
`item.event.data.call_id`. Use it to correlate calls, including repeated calls to
the same tool. The runtime records `tool_started` before executing the callback,
but callback delivery and journal consumption are independent: your callback can
run before your event iterator observes that record. Do not make a callback wait
for the UI to consume `tool_started`.

Providers may reuse call IDs in later turns. Upsert UI entries by
`(sessionId, turnId, callId)` from either channel. A late start event
should enrich an existing entry without resetting an already finished status:

```ts
// In the application's tool callback:
toolRows.upsert(context.sessionId, context.turnId, context.callId, { label: "Showing preview" });

// In the application's run event loop:
const summary = item.summary;
if (summary?.type === "tool_started" || summary?.type === "tool_finished") {
  toolRows.upsert(item.sessionId, item.event.turn_id, summary.callId, {
    tool: summary.tool,
    ...(summary.type === "tool_finished" ? { status: "finished" } : {}),
  });
}
```

`toolRows` above represents your UI state store, not an SDK object. The same IDs
are preserved when you consume the journal after the run has already completed.

To run the synthetic OpenRouter compatibility tests against a built native host:

```sh
# Set OW_TS_TEST_HOST to an absolute openworker-harness executable path.
npm run test:openrouter-streaming
```

These tests use loopback HTTP, the real Rust runtime, and private stdio. They cover
incremental delivery, usage trailers, approval-gated fragmented tool calls and
rejection of content/conflicts after completion. No cloud key or request is used.

## Existing coworker packages

Trusted application code can load an existing coworker package without rewriting
its MCP/policy configuration as an inline agent:

```typescript
const harness = await Harness.start({
  executable, stateDir, sandbox: 'off', // Explicit host execution for this app.
  coworkers: [{ bundle: bundlePath, model: modelId,
    servers: serversPath, toolPolicy: classificationPath,
    runtimePolicy: resourcePolicyPath }],
});
```

Paths must be absolute. Package policy remains authoritative. An optional
`onJournalHint((method, params) => ...)` subscription exposes `session/event` and
`stream/gap` as best-effort wakeups; return its unsubscribe function when done.
Use durable snapshots and event cursors to reconstruct the UI, not the hints alone.
Most apps should use `run.events()` instead. See the desktop starters for
SDK-owned process recovery and conversation restoration.

## Register agents after startup

The host can start without `agents`. `createAgent()` returns a reusable handle;
`ready()` confirms registration, and `newSession()` also waits for it. Definitions
persist under the app's state directory, so managed process recovery does not lose
them. Exact repeats are idempotent; changed definitions need a new agent name.

```typescript
const harness = await Harness.start({ executable, stateDir });
const builder = harness.createAgent({
  name: "builder",
  instructions: "Build the requested project.",
  tools: ["game.read_file"], // Must already exist in the harness/MCP registry.
  defaultModel: { provider: "OpenAI", name: modelId },
  servers: serversPath,
});
await builder.ready();
// Configure the shared OpenAI provider before creating a live session.
const session = await builder.newSession({ workspace });
// Optional per-session override, leaving the definition unchanged:
const other = await builder.newSession({
  workspace, model: { provider: "OpenAI", name: otherModelId },
});
```

`listAgents()` reads the current catalog. The `agents` property is the startup
snapshot. `resumeSession(reference)` checks the session's actual agent identity.
The examples below also show the retained lower-level APIs. New applications should
prefer shared providers and the desktop facade above.

## Install and pair with a runtime

After publication:

```sh
npm install @openworkerai/harness-sdk@0.1.0-beta.1
```

The client installs the matching `@openworkerai/harness-runtime-<platform>-<arch>`
optional dependency. That package contains the prebuilt executable. Keep optional
dependencies enabled: there is no postinstall download or compilation, and the
application does not fetch a helper at startup. Electron apps must bundle the
helper outside ASAR; `resolveRuntimeExecutable()` returns its installed path.

Before publication, install the prepared client and your platform archive together:

```sh
npm install /absolute/release/openworkerai-harness-sdk-0.1.0-beta.1.tgz \
  /absolute/release/openworkerai-harness-runtime-darwin-arm64-0.1.0-beta.1.tgz
```

Choose the actual platform/architecture. The source manifest intentionally remains
private and does not list unpublished runtime dependencies; use the repository's
release preparer to stage distributable packages instead of publishing the source
folder directly.

For a custom native tool registry, build a partner executable from the same SDK
revision and pass its absolute path through `executable`. The partner entry point
registers those tools with `openworker_sdk::cli::main_with_registry(registry)`.
Keep SDK and executable versions paired; protocol 0.1 alone does not promise
compatibility between arbitrary releases.

## QuickStart

Run the included example with an absolute `OPENWORKER_BIN`, an available model ID
in `OPENWORKER_MODEL`, and a privately supplied `OPENAI_API_KEY`:

```sh
node examples/quickstart.mjs /absolute/path/to/demo-data
```

It registers the included Node MCP echo provider, asks for human approval, runs
one tool, saves a session reference, and shuts down. `sandbox: "off"` is explicit
and **provides no isolation**; only use this example with trusted tools. OpenShell
is the SDK default and requires the corresponding environment/setup and tool
placement policy. Scripted acceptance can use `OW_EXAMPLE_SCRIPTED=1` with a
test-support binary; that is not a live model demonstration.

The core API in an installed consumer is:

```typescript
import { Harness, type Agent } from '@openworkerai/harness-sdk';

const assistant: Agent = {
  id: 'assistant',
  instructions: 'Research the user question and cite your sources.',
  tools: ['acme.search'], // Must exist in your binary registry or configured MCP provider.
  servers: '/absolute/path/to/servers.json',
  model: { connection: 'primary', model: process.env.OPENWORKER_MODEL! },
  planning: true,
};

const harness = await Harness.start({
  executable: '/absolute/path/to/partner-harness',
  stateDir: '/absolute/path/to/private-state',
  agents: [assistant],
  storage: { type: 'sqlite' },
  sandbox: 'off', // Trusted local smoke test only.
});
try {
  await harness.setConnection({
    coworker: 'assistant', id: 'primary', label: 'OpenAI',
    provider: 'openai', apiKey: process.env.OPENAI_API_KEY!,
  });
  const session = await harness.session('assistant', {
    workspace: '/absolute/path/to/existing-project',
  });
  const result = await session.run('Compare database options.');
  if (result.status === 'interrupted') {
    // Render result.interruptions in your UI. Do not invent user responses.
  } else {
    console.log(result.status, result.finalOutput);
  }
} finally {
  await harness.shutdown();
}
```

State, workspace, bundle and configuration paths must satisfy the Rust host's
containment rules. `acme.search` is illustrative, not a bundled built-in.
The complete runnable echo example avoids this placeholder.

## Approvals and questions

`session.run()` stops at completion, failure, cancellation or an interruption.
After a real user decision:

```typescript
await session.respond(interruption, { decision: 'once' });
// Or: { decision: 'session' }, { decision: 'deny' }, { answer: userText }
const result = await session.resume();
```

`session` approval covers the entire tool for this session, including changed
valid arguments; grants survive restarts. `session.runWith(input, asyncHandler)`
is available for inline UI/CLI handlers; return `{ defer: true }` to leave pending.
Default policy asks. `approvals: { mode: 'only', tools: [...] }` is interrupt-on:
the **Rust host approves unlisted tools once**, with durable `app_policy` attribution.
Hard restrictions still apply. `reviewer` and `never` select the runtime reviewer
and dangerous bypass modes; neither is a substitute for sandbox isolation.

## Streaming and recovery

```typescript
const stream = await session.runStreamed('Review the project', {
  submissionId: savedRequestId,
});
for await (const event of stream) {
  // Forward event.summary/event.event to your UI; after processing, persist
  // stream.checkpoint() alongside session.reference().
}
const result = await stream.result(); // Includes pending interruptions.
```

Streaming reads the durable journal with bounded long-polls, not just transient
notifications. Events include raw records and typed summaries. A slow reader does
not stall Rust execution or accumulate an unbounded SDK queue. All pages are
drained even when the host already reports completion. Only one consumer at a
time; after breaking iteration you can continue it or call `result()` to drain.

Save `session.reference()` as JSON; after application restart use
`await harness.resume(reference)`, then `session.resume({ submissionId })`.
For replay use `resumeStreamed({ submissionId, cursors })` with saved checkpoints.
Without cursors, resume streaming starts at current journal positions. UI delivery
is at-least-once across application crashes; deduplicate `(session_id,event_id)`.

The SDK restarts the child up to three times by default. `await harness.ready()`
waits for recovery; saved session objects remain usable. Journal reads reconnect
automatically. **Mutations never retry automatically.** `HarnessError` distinguishes:

- `Unavailable`: this request was not sent; wait for readiness.
- `OutcomeUnknown`: it may have executed. Inspect durable state first.
- `Remote`: the host rejected it. Raw tool/provider diagnostics are not reflected.
- `Closed`, `Aborted`, `InvalidInput`, `Protocol`, `Setup`: explicit client states.

`run()` errors carry `context.submissionId`; persist your own ID before sending if
you must survive an app crash. To reconcile a lost `run/start` response, reuse the
**same ID and text**; the host deduplicates it. Do not automatically replay tool
calls. Team mutations accept explicit request IDs and include them in error context.
`agent/session` is not idempotent: a lost creation response requires reconciliation
through the session catalog, not a blind second creation.

`AbortSignal` stops waiting (normally within the bounded long-poll); it does not
cancel agent work. Use `session.cancel()` for active turns, or
`session.cancel({ endTeam: true })` to cancel queued team work too. `session.close()`
requires idle members. Partial member failures are errors, not reported successes.
Cancellation is not rollback. PostgreSQL's durable call ledger can return
`status: 'failed', failure: 'interrupted'` with an outcome-unknown journal record
when a tool was already admitted but has no saved result. Preserve and display
that uncertainty; do not relabel it as cleanly cancelled or automatically retry.
SQLite's local cancellation does not expose that same server call-ledger detail.
Always await `harness.shutdown()`; it closes stdin gracefully, then force-stops a
child that exceeds the shutdown deadline. It does not erase durable state.

## Teams and storage

Add `subagents: [workerDefinition]` to a lead Agent. Starting the lead provisions
the team and member sessions. Worker approvals appear in the lead run, tagged by
agent and worker session ID. `session.team()` exposes `board`, `events`,
`createItem`, `assign`, `transition`, `postMessage` and `cancel`.
At most 15 workers per lead, 16 profiles per host, one subagent level and 1–64
selected tools per agent. Application callbacks return bounded JSON. Changed
callback semantics should use a new qualified name/version; descriptor identity
is immutable. Timers and steering are reported as unsupported by `state()`.

```typescript
storage: {
  type: 'postgres', connectionEnv: 'DATABASE_URL', schema: 'ow_product',
  caFile: '/absolute/path/to/root-ca.pem',
}
```

Run the existing Rust storage migration **explicitly before startup**, using
`Storage::migrate()`; follow the
[storage migration instructions](../../docs/harness/guides/guardrails.md#upgrade-an-existing-installation). Node does not implement
SQL adapters or run unrequested migrations. The same agent/team APIs work with
either store. A PostgreSQL SDK host is **one supervisor per schema**, not a
multi-node worker pool; use the distributed server deployment for that role.

## Tools, credentials and process boundaries

Named multi-instance teams use `harness.newTeam(...)` and optional
`delegation: { tools: ["coding.read_file"] }`. This exposes `team_define_type`
under the app's ceiling. Exact approvals and private role revisions survive
restart on either database; they are not global agent registrations. See
[Sessions and teams](../../docs/harness/guides/sessions.md).
Run `npm run test:team-types` with `OW_TS_TEST_HOST` and, for PostgreSQL,
an isolated `OW_PG_TEST_URL` to exercise both real managed-process adapters.

- TypeScript app tools use callbacks over private stdio. External tools can use
  app-configured MCP servers. Rust-native tools are compiled into the selected
  partner executable.
- Agent definitions are serialized data; JS callbacks cannot cross stdio.
- Agents are fixed at startup. Changing prompt/tools changes the pinned identity;
  create new sessions for changed definitions.
- Model keys go through `setConnection`, not configuration/argv/agent prompts.
  They are memory-only by default, not cached by this SDK, and **must be supplied
  again after a process restart**. The app owns secure credential sourcing.
  Subscribe with `harness.onStateChange(listener)` to update the UI and resupply
  connections on `ready` after recovery; it returns an unsubscribe function.
  Handle asynchronous credential-provider errors in your application callback.
  Process readiness does not imply that memory-only model keys are configured.
  `persistence: 'secure'` is currently Windows DPAPI only; no plaintext fallback.
- Only a small OS environment allowlist, the declared database/telemetry variables
  and explicit `inheritEnv` names reach the child. Do not broadly forward secrets.
- For Anthropic, use `provider: 'anthropic'` on the model connection and the
  corresponding agent provider/fallback configuration. No TypeScript model driver.
- No automatic binary download, npm publication, native in-process mode, browser
  secret handling, arbitrary JS tool callbacks, or distributed scheduling here.

## Verification

```sh
npm ci
npm test
cd ..
cargo build --locked -p openworker-sdk --features test-support --bin sdk-test-host
cd typescript-sdk
OW_TS_TEST_HOST=/absolute/path/to/sdk-test-host npm run test:integration
```

Run integration on Linux. Set `OW_PG_TEST_URL` to a disposable loopback PostgreSQL
database to include that adapter; the fixture creates fresh schemas and migrates
them explicitly. No provider secrets needed. The unit suite runs process faults
on macOS/Linux. Platform-native Windows and live-model qualification remain
separate; cross-platform Node APIs alone are not evidence of Windows acceptance.

### Turn limits

Agent definitions accept `limits: { maxModelCalls, maxOutputTokens, summaryTokens? }`.
A new message can override them with `session.send(text, { limits })`; the service
SDK accepts the same limits plus its usual `idempotencyKey`. Resuming keeps the
original budget. See [Sessions and teams](../../docs/harness/guides/sessions.md)
for counting rules, the final-summary reserve and sandbox-owned process tools.

### Service teams with private types

`ServiceSession.createTeam` accepts optional `delegation: { tools, models? }`.
Tools must belong to the lead's activated release; models must be explicitly
allowed by that release. The lead can then use `team_define_type` through the
existing exact-definition approval flow. Definitions remain private to that lead.
The service controller and worker restore the same approved revision after restart.
This adapter currently shares the original team environment; nondefault remote
workspace/new/member placement is still gated. See the
[Sessions and teams](../../docs/harness/guides/sessions.md).
