//! Source-checkout facade example. Set OPENWORKER_EXECUTABLE to the built helper,
//! OPENWORKER_MODEL to an exact ID, and OPENAI_API_KEY privately, or set
//! OPENWORKER_LOCAL_URL to a no-key local Chat Completions base URL.
use futures_util::StreamExt;
use openworker_sdk::{Agent, Credentials, Harness, Model, ModelProvider, Sandbox, Tool};
use serde_json::json;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let model = std::env::var("OPENWORKER_MODEL")?;
    let provider = match std::env::var("OPENWORKER_LOCAL_URL") {
        Ok(url) => ModelProvider::chat_completions("Local").base_url(url),
        Err(_) => ModelProvider::responses("OpenAI")
            .credentials(Credentials::from_env("OPENAI_API_KEY").memory_only()),
    };
    let name = if std::env::var_os("OPENWORKER_LOCAL_URL").is_some() {
        "Local"
    } else {
        "OpenAI"
    };
    let harness = Harness::builder()
        .sandbox(Sandbox::Off)
        .model_provider(provider)
        .start()
        .await?;
    let outcome = async {
        harness.register_tools("app", [Tool::new("status")
            .description("Display a short status in this application.")
            .input_schema(json!({"type":"object","properties":{"text":{"type":"string","maxLength":1000}},"required":["text"],"additionalProperties":false}))
            .handler(|args, context| async move {
                if context.cancellation().is_cancelled() { anyhow::bail!("Cancelled"); }
                // A real desktop app sends this to its own UI, not another server.
                println!("App status: {}", args["text"].as_str().unwrap_or_default());
                Ok(json!({"displayed":true}))
            })]).await?;
        let agent = harness.create_agent(Agent::new("desktop-example")
            .instructions("Help the user. Use app.status for a short status update.")
            .default_model(Model::new(name, model)).tool("app.status")).await?;
        let session = agent.new_session().await?;
        let reference = session.reference().clone();
        println!("Save this session reference: {}", serde_json::to_string(&reference)?);
        let run = session.send("Suggest a tiny game I could build.").await?;
        let mut events = run.events();
        while let Some(event) = events.next().await { event?; }
        let result = run.result().await?;
        println!("Run status: {:?}", result.status);
        if !result.interruptions.is_empty() {
            println!("Awaiting user input. Render the durable interaction in your UI; never auto-invent an answer.");
        }
        Ok::<_, anyhow::Error>(())
    }.await;
    harness.shutdown().await?;
    outcome
}
