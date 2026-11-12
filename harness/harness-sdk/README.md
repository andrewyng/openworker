# OpenWorker Harness SDK for Rust

Embed an agent runtime in your Rust application, with model connections, tools,
approvals and durable conversations. Use the same engine through a managed child
process when your product needs that process boundary.

[SDK overview](../README.md) · [Desktop guide](../../docs/harness/guides/desktop.md) · [Tools](../../docs/harness/guides/tools.md) · [Examples](../examples/)

## Add the SDK

Use a pinned OpenWorker checkout and add the crate to your application's
`Cargo.toml`. These instructions use source dependencies; npm publication does
not publish a Rust crate. Use the checkout's [pinned Rust toolchain](../rust-toolchain.toml).

```toml
[dependencies]
openworker-sdk = { path = "/absolute/path/to/openworker/harness/harness-sdk" }
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }
anyhow = "1"
```

The current builder also requires a runner helper, even when the agent loop is
embedded and the agent has no tools. Build it from the same checkout:

```sh
cargo +1.96.0 build --manifest-path /absolute/path/to/openworker/harness/Cargo.toml \
  --locked -p openworker-sdk --bin openworker-harness
```

Set `OPENWORKER_EXECUTABLE` to its absolute path under `harness/target/debug/`
(`openworker-harness.exe` on Windows). Bundle this helper with a distributed app.

## Run your first agent

Save as `src/main.rs`. Set `OPENAI_API_KEY` privately in the process environment
and `OPENWORKER_MODEL` to an exact model ID available to your account. Cargo does
not load env files automatically.

```rust
use openworker_sdk::{Agent, Credentials, Harness, Model, ModelProvider, Sandbox};
use openworker_sdk::agents::RunStatus;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let model = std::env::var("OPENWORKER_MODEL")?;
    let harness = Harness::builder()
        .state_dir(std::env::current_dir()?.join(".openworker-state"))
        .executable(std::env::var("OPENWORKER_EXECUTABLE")?)
        .sandbox(Sandbox::Off)
        .start().await?;
    let outcome = async {
        harness.set_model_provider(ModelProvider::responses("OpenAI")
            .base_url(std::env::var("OPENWORKER_MODEL_URL")
                .unwrap_or_else(|_| "https://api.openai.com/v1".into()))
            .credentials(Credentials::from_env("OPENAI_API_KEY").memory_only()))
            .await?;
        let agent = harness.create_agent(Agent::new("first-agent-v1")
            .instructions("Give concise, helpful answers.")
            .default_model(Model::new("OpenAI", model))
            .tools([] as [&str; 0]).planning(false).memory(false).scheduling(false))
            .await?;
        let session = agent.new_session().await?;
        let result = session.run("Suggest three names for a file-organizing app.").await?;
        anyhow::ensure!(matches!(result.status, RunStatus::Completed),
            "Run ended: {:?}", result.status);
        println!("{}", result.final_output.unwrap_or_default());
        Ok::<(), anyhow::Error>(())
    }.await;
    harness.shutdown().await?;
    outcome
}
```

```sh
cargo +1.96.0 run
```

`OPENWORKER_MODEL_URL` optionally selects another Responses-compatible endpoint.

Rust embeds the harness by default. This text-only example exposes no tools and
explicitly selects `Sandbox::Off`. App callbacks keep the application's privileges.
For isolated shell/file execution, configure [OpenShell](../../docs/harness/guides/openshell.md).
Use `HarnessClient::managed_stdio` or the custom host interfaces only when you
need an explicit process boundary; see the [native host guide](../partner/README.md).

## Add the next capability

| You want to… | Start here |
| --- | --- |
| Register native or app-owned tools | [Tools and MCP](../../docs/harness/guides/tools.md) · [Tools SDK](../tools-sdk/README.md) |
| Handle streaming, approvals and cancellation | [Streaming](../../docs/harness/guides/streaming.md) · [Sessions](../../docs/harness/guides/sessions.md) |
| Use local models or rotate credentials | [Models and credentials](../../docs/harness/guides/providers.md) |
| Add teams, memory or scheduling | [Sessions and teams](../../docs/harness/guides/sessions.md) |
| Add guardrails or inspect traces | [Guardrails](../../docs/harness/guides/guardrails.md) · [Observability](../../docs/harness/guides/observability.md) |
| Deploy a service | [Deployment options](../../docs/harness/guides/deployment.md) |

## Integration contract

- Define agents once and create a session per conversation. Persist its reference
  and call `agent.resume_session(reference)` after restart.
- Definitions persist and are immutable. Use a new agent name when changing its
  instructions, tools or approvals; identical registration is idempotent.
- Credentials resolve in the Rust application process. `memory_only()` requires
  supplying them again after app restart. Keep keys out of instructions and files
  available to agent tools.
- A workspace selects the working directory and supported path-policy scope.
  Arbitrary callbacks retain application privileges; it is not an OS sandbox.
- Treat interruptions as requests for a real app/user decision. Dropping an event
  stream does not cancel execution; call `session.cancel()` explicitly.
- Reconcile unknown tool outcomes before retrying, and keep state outside tool-readable directories.

Generate full API documentation from the checkout with
`cargo doc --manifest-path harness/Cargo.toml -p openworker-sdk --no-deps`.
See [versions](../../docs/harness/guides/releases.md) and
[troubleshooting](../../docs/harness/guides/troubleshooting.md) before upgrading an app.
