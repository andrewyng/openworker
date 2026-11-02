# Tool-free guide agents

A guide can answer from lesson context without any tool access. An empty tool
list is valid in the Rust SDK and TypeScript managed-subprocess SDK, including
startup definitions and registration after startup. No dummy tool or approval
bypass is needed.

```typescript
const guide = harness.createAgent({
  name: "guide",
  instructions: "Give hints and judge lesson progress using the context supplied by the app.",
  tools: [],
  planning: false,
  memory: false,
  scheduling: false,
  defaultModel: { provider: "Anthropic", name: modelId },
});
const session = await guide.newSession();
const run = await session.send(lessonContext);
for await (const event of run.events()) { /* show streamed text */ }
const result = await run.result();
```

```rust,no_run
let guide = harness.create_agent(
    Agent::new("guide")
        .instructions("Give hints and judge progress using supplied lesson context.")
        .tools([] as [&str; 0])
        .planning(false)
        .memory(false)
        .scheduling(false)
        .default_model(Model::new("Anthropic", model_id)),
).await?;
let session = guide.new_session().await?;
let run = session.send(lesson_context).await?;
let mut events = run.events();
while let Some(event) = events.next().await { /* render event? */ }
let result = run.result().await?;
```

Configure the named provider through the existing harness provider APIs first.
The Rust streaming example uses `futures_util::StreamExt`. Planning, memory and
scheduling default to false; the explicit settings show the intended guide
configuration. A tool-free agent can still retain its ordinary conversation;
disabling memory tools does not turn off session persistence or provider context.

## Exact boundary

The runtime builds the selected tool catalog before adding convenience tools.
When that catalog is empty, it does not add `ask_user` or `read_history`. The
provider receives no tool definitions (`tools: []` or an omitted tools field,
according to the selected API). Provider output still passes through the runtime's
bound-tool check: an unexpected tool call fails the turn before approval or
execution, including implicit helper names and tools registered for another agent.

Registering a tool in the application registry does not expose it to the guide.
An agent explicitly enabled for planning, memory, scheduling, skills or team tools
has a nonempty capability catalog and retains the existing helper behavior. Keep
the DLAI guide separate from those tool-bearing roles if it must remain tool-free.
Coding-agent tools, planning/todo tools, approvals and defaults are unchanged.

The trusted application can still supply lesson context and use authorized session
APIs. This is a model/tool authorization boundary, not an additional OS sandbox
around the application's own code.

## Repeatable regression coverage

```shell
cargo test --locked --manifest-path harness/Cargo.toml \
  -p openworker-sdk --features test-support --test tool_free
cargo test --locked --manifest-path harness/Cargo.toml \
  -p openworker-runtime --test registered_selection
```

After building the same revision's development `sdk-test-host` binary:

```shell
cd harness/typescript-sdk
OW_TS_TEST_HOST=/absolute/path/to/sdk-test-host npm run test:tool-free
```

The Rust suite covers embedded and managed child execution against synthetic
OpenAI Responses, Chat Completions and Anthropic HTTP/SSE providers. TypeScript
runs the same provider protocols through the actual Rust child over private
stdio. Each scenario verifies registration, an empty advertised catalog,
incremental text arriving before provider completion, and rejection of a
registered-but-unselected tool, `ask_user`, `read_history`, and an unknown name.
Tests count callback effects and verify no tool/approval events occur. These
fixtures use synthetic credentials and make no live provider calls.

Source baseline: `673e5d62`. See the [qualification ledger](TOOL-FREE-AGENTS-PLAN.md)
for actual runs and remaining platform/provider boundaries.
