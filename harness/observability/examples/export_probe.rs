//! Deterministic metadata/usage fixture; no actual model or tool is invoked.
//! Pass CONFIG.json NEW_OUTPUT_DIRECTORY. Export credentials are env references.
use anyhow::{ensure, Result};
use openworker_observability::{
    query::{Page, Query, Scope},
    sqlite::Sqlite,
    summary::{self, Canonical},
    Recorder, RecordingSink, Store,
};
use openworker_telemetry::{
    attributes as a, otlp, scope_turn, Capture, ContentPolicy, ModelPurpose, Operation, Outcome,
    Telemetry, TokenUsage, TraceContext,
};
use serde_json::json;
use std::{
    path::PathBuf,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}
fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap()
}
fn model(
    t: &Telemetry,
    purpose: ModelPurpose,
    attempt: u32,
    usage: Option<TokenUsage>,
    outcome: Outcome,
) {
    let mut s = t.span(
        Operation::Model,
        Some("PRIVATE_SESSION"),
        Some("PRIVATE_CALL"),
    );
    s.purpose(purpose);
    s.attempt(attempt);
    s.metadata(a::MODEL_NAME, "fixture-model");
    s.model_started();
    s.content_tail(a::PROMPT, || {
        "PRIVATE_PROMPT sk-notarealsecret1234567890".into()
    });
    s.content(a::COMPLETION, || "PRIVATE_ANSWER".into());
    if let Some(usage) = usage {
        s.reported_usage(usage);
    }
    s.first_output();
    s.finish(outcome);
}
fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let config: otlp::Config = serde_json::from_slice(&std::fs::read(
        args.next()
            .ok_or_else(|| anyhow::anyhow!("Config path required"))?,
    )?)?;
    let output = PathBuf::from(
        args.next()
            .ok_or_else(|| anyhow::anyhow!("New output directory required"))?,
    );
    ensure!(args.next().is_none(), "Unexpected arguments");
    std::fs::create_dir(&output)?;
    let database = output.join("recording.sqlite");
    let (recorder, done) = Recorder::start(
        Box::new(Sqlite::open(&database)?),
        config.deployment.clone(),
        64,
        30,
    )?;
    let mut previous: Option<TraceContext> = None;
    let mut first_started = 0;
    let mut pauses = vec![];
    let mut snapshots = vec![];
    for worker in 0..2 {
        let sink = RecordingSink::new(
            recorder.clone(),
            config.deployment.clone(),
            format!("worker-{worker}"),
            Some(Box::new(config.build_sink("worker")?)),
            config.content.capture,
        )?;
        // Recording may capture approved names without enabling remote metadata export.
        let (t, c) = Telemetry::start_with_content(
            sink,
            64,
            ContentPolicy {
                capture: Capture::Metadata.max(config.content.capture),
                ..config.content.clone()
            },
        )?;
        let mut root = t.span(
            Operation::Turn,
            Some("PRIVATE_SESSION"),
            Some("PRIVATE_TURN"),
        );
        root.turn("PRIVATE_TURN");
        if let Some(p) = previous {
            root.link(p);
        }
        let cx = root.context();
        if worker == 0 {
            first_started = now();
        }
        runtime().block_on(scope_turn("PRIVATE_TURN", cx, async {
            if worker == 0 {
                model(
                    &t,
                    ModelPurpose::Main,
                    1,
                    Some(TokenUsage {
                        input: Some(10),
                        output: Some(2),
                        ..Default::default()
                    }),
                    Outcome::Error,
                );
                model(
                    &t,
                    ModelPurpose::Main,
                    2,
                    Some(TokenUsage {
                        input: Some(100),
                        output: Some(20),
                        cache_read: Some(40),
                        cache_write: Some(10),
                        reasoning: Some(5),
                    }),
                    Outcome::Ok,
                );
                let policy = t.span(Operation::Policy, Some("PRIVATE_SESSION"), None);
                let pc = policy.context();
                openworker_telemetry::scope(pc, async {
                    model(
                        &t,
                        ModelPurpose::AutoApprover,
                        1,
                        Some(TokenUsage {
                            input: Some(0),
                            output: Some(0),
                            ..Default::default()
                        }),
                        Outcome::Ok,
                    )
                })
                .await;
                policy.finish(Outcome::Ok);
            } else {
                model(&t, ModelPurpose::Compaction, 1, None, Outcome::Ok);
                let mut tool = t.span(
                    Operation::Tool,
                    Some("PRIVATE_SESSION"),
                    Some("PRIVATE_TOOL_CALL"),
                );
                tool.metadata(a::TOOL_NAME, "fixture.read_file");
                tool.metadata(a::SANDBOX_PROVIDER, "off");
                tool.metadata(a::SANDBOX_ENFORCEMENT, "none");
                tool.output_bytes(512);
                tool.content(a::TOOL_ARGUMENTS, || "PRIVATE_ARGUMENTS".into());
                tool.content(a::TOOL_RESULT, || "PRIVATE_RESULT".into());
                tool.finish(Outcome::Unknown);
            }
        }));
        root.finish(if worker == 0 {
            Outcome::Waiting
        } else {
            Outcome::Ok
        });
        if worker == 0 {
            let start = now();
            std::thread::sleep(Duration::from_millis(10));
            let end = now();
            t.interval(
                Operation::ApprovalWait,
                Outcome::Ok,
                "PRIVATE_SESSION",
                "PRIVATE_TURN",
                "PRIVATE_APPROVAL",
                start,
                end,
            );
            pauses.push(end - start);
        }
        ensure!(
            c.shutdown(Duration::from_secs(5)),
            "Export dispatcher did not drain"
        );
        snapshots.push(c.snapshot());
        previous = Some(cx);
    }
    ensure!(
        done.shutdown(Duration::from_secs(5)),
        "Recorder did not drain"
    );
    let mut sql = Sqlite::open(&database)?;
    let session = openworker_runtime_digest("PRIVATE_SESSION");
    let turn = openworker_runtime_digest("PRIVATE_TURN");
    let page: Page = sql.timeline(&Query {
        deployment: config.deployment.clone(),
        scopes: vec![Scope {
            session_hash: session,
            turn_hash: turn,
        }],
        after: 0,
        through: None,
        limit: 4096,
    })?;
    let last = previous
        .unwrap()
        .span
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let summary = summary::build(
        &page,
        Canonical {
            terminal: true,
            expected_segments: Some(2),
            first_started_ms: Some(first_started),
            last_span_id: Some(last),
            pending_waits: Some(0),
        },
    );
    ensure!(
        summary.usage.input.total == Some(110) && summary.usage.output.total == Some(22),
        "Fixture usage drift"
    );
    ensure!(
        summary.usage.input.known == 3 && summary.usage.input.unknown == 1,
        "Fixture missing-usage drift"
    );
    let encoded = serde_json::to_string(&page)?;
    ensure!(
        !encoded.contains("PRIVATE") && !encoded.contains("sk-notareal"),
        "Recording content leak"
    );
    std::fs::write(
        output.join("records.json"),
        serde_json::to_vec_pretty(&page)?,
    )?;
    std::fs::write(
        output.join("summary.json"),
        serde_json::to_vec_pretty(&summary)?,
    )?;
    std::fs::write(
        output.join("probe.json"),
        serde_json::to_vec_pretty(
            &json!({"version":1,"fixture":"export_reconciliation","capture":config.content.capture,"exporter":snapshots,"recording":recorder.statistics().snapshot(),"pause_ms":pauses,"steps":page.steps.len(),"schema":a::VERSION,"semconv":a::SEMCONV}),
        )?,
    )?;
    println!("export probe: {} recorded steps", page.steps.len());
    Ok(())
}
fn openworker_runtime_digest(value: &str) -> String {
    // Use the shared typed identity hashing contract, not a second implementation.
    openworker_telemetry::Identity::new(Some(value), None)
        .session
        .unwrap()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
