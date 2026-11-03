# Build an agentic app for Windows or Mac

Connect a model, give the agent useful tools, and stream its work into your UI.
Your users install one application. TypeScript uses a managed Rust child process;
Rust can keep the agent loop in the application process.

[Install the SDK and helper](installation.md) first. Set `OPENWORKER_MODEL` to the
model ID you want to use and provide `OPENAI_API_KEY` privately for this example.
The code blocks for each language form one runnable program and are checked together.

## Start the harness

<!-- sdk-tabs -->

### TypeScript

```typescript example=desktop
import { Harness } from "@openworker/harness";

const harness = await Harness.start({ appId: "game-builder", sandbox: "off" });
try {
```

### Rust

```rust example=desktop
use futures_util::StreamExt;
use openworker_sdk::{Agent, Credentials, Harness, Model, ModelProvider, Sandbox};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let harness = Harness::builder().sandbox(Sandbox::Off).start().await?;
    let outcome = async {
```

<!-- /sdk-tabs -->

This example uses explicit host execution (`sandbox: "off"`). Tool permissions
still apply, but this setting does not isolate the host filesystem or network.
For sandboxed execution, see [deployment choices](deployment.md).

<details>
<summary>State, memory and application options</summary>

By default, the harness keeps durable session state in an application-specific
folder under `~/.openworker`. TypeScript uses `appId` to name that folder; Rust
also supports an explicit `state_dir`. Use `stateDir`/`state_dir` when your app
already has a suitable data directory. Session persistence is distinct from an
agent's optional `memory` tool. Keep private state outside your project workspace.

You can also configure the bundled executable, scratch directory, compaction,
telemetry and required guardrails. See [SDK storage](../SDK-STORAGE.md) and
[guardrails](../GUARDRAILS-QUICKSTART.md).

</details>

## Connect a model

Register a provider separately from the agent. A desktop app can collect a key in
its settings UI and call `setModelProvider` later. This example reads an environment
variable and keeps the key in memory.

<!-- sdk-tabs -->

### TypeScript

```typescript example=desktop
  const modelId = process.env.OPENWORKER_MODEL;
  if (!modelId) throw new Error("Set OPENWORKER_MODEL to an exact model ID");
  await harness.setModelProvider({
    name: "OpenAI",
    api: "responses",
    baseUrl: process.env.OPENWORKER_MODEL_URL ?? "https://api.openai.com/v1",
    credentials: { apiKeyEnv: "OPENAI_API_KEY", storage: "memory" },
  });
```

### Rust

```rust example=desktop
        let model_id = std::env::var("OPENWORKER_MODEL")?;
        let base_url = std::env::var("OPENWORKER_MODEL_URL")
            .unwrap_or_else(|_| "https://api.openai.com/v1".into());
        harness.set_model_provider(
            ModelProvider::responses("OpenAI").base_url(base_url)
                .credentials(Credentials::from_env("OPENAI_API_KEY").memory_only())
        ).await?;
```

<!-- /sdk-tabs -->

<details>
<summary>Use Ollama instead</summary>

Register `Ollama` with `api: "chat-completions"` and
`baseUrl: "http://127.0.0.1:11434/v1"`. No cloud key is needed for an unauthenticated
local server. Set the agent's provider to `Ollama` and its model name to an installed
model ID. Rust uses `ModelProvider::chat_completions("Ollama")`. The server must
support the capabilities your agent needs, including tools when you enable them.

</details>

## Create agent

Give the agent a default model and an explicit tool list. A session can override
the default model. Stream events into your UI and inspect the result for completion
or a pending question/approval.

<!-- sdk-tabs -->

### TypeScript

```typescript example=desktop
  const builder = harness.createAgent({
    name: "builder",
    instructions: "Build small HTML, CSS and JavaScript games. Explain your plan first.",
    defaultModel: { provider: "OpenAI", name: modelId },
    tools: ["coding.read_file", "coding.write_file"],
  });
  const session = await builder.newSession();
  const run = await session.send("Suggest a small game we could build.");
  for await (const event of run.events()) {
    console.log(event.summary); // Replace with your UI's event renderer.
  }
  const result = await run.result();
  console.log(result.status, result.finalOutput);
  if (result.interruptions.length) console.log("Waiting for user input");
} finally {
  await harness.shutdown();
}
```

### Rust

```rust example=desktop
        let builder = harness.create_agent(Agent::new("builder")
            .instructions("Build small HTML, CSS and JavaScript games. Explain your plan first.")
            .default_model(Model::new("OpenAI", model_id))
            .tools(["coding.read_file", "coding.write_file"])).await?;
        let session = builder.new_session().await?;
        let run = session.send("Suggest a small game we could build.").await?;
        let mut events = run.events();
        while let Some(event) = events.next().await {
            println!("{:?}", event?); // Forward to your UI.
        }
        let result = run.result().await?;
        println!("{:?} {:?}", result.status, result.final_output);
        if !result.interruptions.is_empty() { println!("Waiting for user input"); }
        Ok::<_, anyhow::Error>(())
    }.await;
    harness.shutdown().await?;
    outcome
}
```

<!-- /sdk-tabs -->

<details>
<summary>Custom tools and durable sessions</summary>

Use `harness.registerTools(...)` in TypeScript or `register_tools(...)` in Rust for
application callbacks. These travel through the private application interface;
external MCP servers are a separate integration. See [tools](tools.md).

Persist `session.reference()` in your app to reconnect after a restart. Show
interruptions to the user, submit their answer/approval and resume. Never turn a
missing answer into consent. See [sessions and teams](sessions.md).

</details>

## Build your app

Start with the packaged UI and build configuration:

<!-- sdk-tabs -->

### TypeScript / Electron

```sh
npm create openworker@0.1.0-beta.1 my-game-app -- --template electron-game-builder
cd my-game-app
npm install
npm run dev
npm run package
```

### Rust / Tauri

```sh
npm create openworker@0.1.0-beta.1 my-game-app -- --template tauri-game-builder
cd my-game-app
npm install
npm run dev
npm run package
```

<!-- /sdk-tabs -->

Run packaging on the target operating system. Desktop starters require Node
22.12+ and npm 11+; Tauri additionally needs Rust and its platform build tools.
Signing/notarization uses your application's release credentials. Registry commands
become usable when the candidate is published; source-checkout use remains available
with `node harness/desktop-starters/create.mjs my-game-app --sdk-root /absolute/harness`.
