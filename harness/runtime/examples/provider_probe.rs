//! Explicit development probe. Uses synthetic content and an in-memory echo only.
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    model::{Model, Progress},
    provider::Kind,
};
use serde_json::json;
use tokio::sync::mpsc;

#[tokio::main]
async fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    ensure!(args.len()==2,"Usage: provider_probe <openai|anthropic> <model-id>; configure the provider key in the environment");
    let kind = match args[0].as_str() {
        "openai" => Kind::Openai,
        "anthropic" => Kind::Anthropic,
        _ => anyhow::bail!("Unsupported provider"),
    };
    let model = Model::new(args[1].clone(), false).with_provider(kind);
    let tools = vec![
        json!({"type":"function","name":"echo_probe","description":"Return a synthetic integration marker. No file or network effects.","strict":true,"parameters":{"type":"object","properties":{"marker":{"type":"string"}},"required":["marker"],"additionalProperties":false}}),
    ];
    let mut history = vec![
        json!({"role":"user","content":"Call echo_probe exactly once with marker HARNESS_PROVIDER_CHECK. Then report that marker briefly."}),
    ];
    let mut calls = 0;
    let mut delta_count = 0;
    for _ in 0..3 {
        let (tx, mut rx) = mpsc::channel(32);
        let input_history = history.clone();
        let request = model.next(
            "Use the provided echo tool. Do not invent its result.",
            &input_history,
            &tools,
            tx,
        );
        tokio::pin!(request);
        let mut complete = None;
        let mut finished = false;
        let mut closed = false;
        while !finished || !closed {
            tokio::select! {
                r=&mut request,if !finished=>{r?;finished=true;},
                p=rx.recv(),if !closed=>match p {
                    Some(Progress::Delta(_))=>delta_count+=1,
                    Some(Progress::Complete(c))=>complete=Some(c),
                    Some(Progress::OutputStarted|Progress::Usage(_))=>{},
                    None=>closed=true,
                }
            }
        }
        let completion = complete.context("Missing terminal completion")?;
        let tool_calls = completion
            .output
            .iter()
            .filter(|v| v["type"] == "function_call")
            .cloned()
            .collect::<Vec<_>>();
        history.extend(completion.output);
        if tool_calls.is_empty() {
            ensure!(calls == 1, "Expected exactly one echo tool call");
            let answer = history
                .iter()
                .rfind(|v| v["type"] == "message")
                .context("No final answer")?;
            ensure!(
                answer.to_string().contains("HARNESS_PROVIDER_CHECK"),
                "Final answer did not retain tool result"
            );
            let summary = model.summarize("Summarize this synthetic input in one sentence.","The echo probe returned HARNESS_PROVIDER_CHECK; no file or network tool effects occurred.",256).await?;
            ensure!(!summary.is_empty(), "No summary");
            let review = model.review(r#"{"user_inputs":[{"source":"direct_user","content":"Read the public README in this workspace"}],"runtime_facts":{"workspace":"/workspace","category":"read"},"proposed_action":{"alias":"read_file","arguments":{"path":"README.md"}}}"#).await;
            ensure!(
                matches!(
                    review.verdict,
                    openworker_runtime::permissions::VerdictKind::Allow
                ),
                "Synthetic read review was not allow"
            );
            println!(
                "{}",
                json!({"provider":kind,"model":args[1],"tool_roundtrip":true,"text_delta_count":delta_count,"summary":true,"structured_review":true,"live":true})
            );
            return Ok(());
        }
        for call in tool_calls {
            ensure!(call["name"] == "echo_probe", "Unexpected tool");
            let input: serde_json::Value =
                serde_json::from_str(call["arguments"].as_str().context("Missing args")?)?;
            ensure!(input["marker"] == "HARNESS_PROVIDER_CHECK", "Wrong marker");
            calls += 1;
            ensure!(calls == 1, "Duplicate tool request");
            history.push(json!({"type":"function_call_output","call_id":call["call_id"],"output":json!({"marker":"HARNESS_PROVIDER_CHECK"}).to_string()}));
        }
    }
    anyhow::bail!("Provider probe exhausted its bounded turns")
}
