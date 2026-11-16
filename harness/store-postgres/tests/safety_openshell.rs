//! One real, disposable OpenShell environment. The current trusted runtime
//! screens remote ToolRunner results; no vendor/model keys or cloud resources.
#![cfg(all(
    feature = "openshell-test",
    feature = "guardrails",
    feature = "compliance",
    not(windows)
))]
use anyhow::{ensure, Result};
use openworker_runtime::{
    environment::{Management, Provider, Registry, Sharing},
    environment_openshell::OpenShell,
    execution::Backend,
    model::Model,
    runtime::Runtime,
    safety::{
        Action, CheckFuture, Config as Safety, Context, Decision, Guardrails, Handler, HandlerRef,
        Hook, HookPhase, Rule, ToolRules,
    },
    sandbox::ExecConnectionConfig,
};
use openworker_store_postgres::{Config, PostgresStore, Tls};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc, time::Duration};
struct Deny;
impl Handler for Deny {
    fn call(&self, _: Context, _: tokio_util::sync::CancellationToken) -> CheckFuture<'_> {
        Box::pin(async { Ok(Decision::block("READ_ONLY")) })
    }
}
async fn run(rt: &Arc<Runtime>, sid: &str, key: &str, calls: Value) -> Result<()> {
    rt.dispatch("turn/start",json!({"session_id":sid,"submission_id":key,"text":format!("TEST_RESOURCE_GRANTS {calls}")})).await?;
    tokio::time::timeout(Duration::from_secs(90), async {
        loop {
            let s = rt.snapshot(sid)?;
            let state = s["turn"]["status"].as_str().unwrap_or("");
            if state == "completed" && !rt.is_busy(sid) {
                return Ok(());
            }
            ensure!(
                !matches!(
                    state,
                    "failed" | "blocked" | "interrupted" | "waiting_safety"
                ),
                "Remote qualification stopped: {state}"
            );
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await?
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and OW_PROBE_ENDPOINT/CERTS/IMAGE/RUNNER"]
async fn remote_read_redaction_and_before_tool_denial_in_bypass() -> Result<()> {
    let provider = Arc::new(OpenShell::new(
        "openshell-k8s".into(),
        "safety-qualification".into(),
        ExecConnectionConfig {
            endpoint: std::env::var("OW_PROBE_ENDPOINT")?,
            certificates: PathBuf::from(std::env::var("OW_PROBE_CERTS")?),
            tls_server_name: std::env::var("OW_PROBE_TLS_NAME").ok(),
            workspace: "default".into(),
        },
        std::env::var("OW_PROBE_IMAGE")?,
    )?);
    let mut config = Config::new(
        std::env::var("OW_PG_TEST_URL")?,
        format!("ow_safety_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = Tls::LoopbackPlaintext;
    config.lease_duration = Duration::from_secs(120);
    PostgresStore::migrate(&config)?;
    let store = PostgresStore::open(config.clone())?;
    let (record, _) = store.begin_environment(
        "safety-create",
        "qualification",
        provider.spec(Sharing::Shared, Management::Harness),
    )?;
    eprintln!(
        "Owned safety fixture: schema={} environment={}",
        config.schema, record.id
    );
    let binding = provider.create(record.clone()).await?;
    store.complete_environment(&record.id, record.revision, Some(binding.clone()))?;
    provider.inspect(binding.clone()).await?;
    let mut registry = Registry::default();
    registry.register(provider.clone())?;
    let root = tempfile::tempdir()?;
    let mut rt = Runtime::open_with_store(
        root.path(),
        &PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant"),
        &PathBuf::from(std::env::var("OW_PROBE_RUNNER")?),
        Model::new("fixture".into(), true),
        Backend::Openshell,
        None,
        None,
        Default::default(),
        Some(Box::new(store.clone())),
    )?;
    Runtime::configure_environments(&mut rt, Arc::new(registry))?;
    let reference = HandlerRef {
        name: "read-only".into(),
        version: "1".into(),
    };
    rt.safety_handlers
        .register(reference.clone(), Arc::new(Deny))?;
    let safety = Safety {
        guardrails: Guardrails {
            tool_results: vec![ToolRules {
                tools: vec!["example-tools.read_file".into()],
                rules: vec![Rule::regex("secrets", "SECRET_[A-Z]+", Action::redact())],
            }],
            ..Default::default()
        },
        hooks: vec![Hook {
            name: "read-only".into(),
            version: "1".into(),
            when: HookPhase::BeforeTool,
            tools: vec!["example-tools.write_file".into()],
            handler: reference,
            timeout_ms: 1000,
        }],
        ..Default::default()
    };
    let mut sessions = vec![];
    let result:Result<()>=async {
  for (name,policy) in [("seed",Safety::default()),("screened",safety)] {let s=rt.dispatch("session/create",json!({"name":name,"coworker":"assistant-example","workspace":"/sandbox","environment_id":record.id,"permissions":{"mode":"bypass"},"safety":policy,"compliance":{},"compaction":{"enabled":false}})).await?;sessions.push(s["session"]["id"].as_str().unwrap().to_owned());}
  run(&rt,&sessions[0],"seed-file",json!([{"name":"write_file","arguments":{"path":"protected.txt","content":"SECRET_SENTINEL"}}])).await?;
  run(&rt,&sessions[1],"screen-read",json!([{"name":"read_file","arguments":{"path":"protected.txt"}}])).await?;
  let reader=store.inspect(&sessions[1])?;ensure!(!json!(reader.history).to_string().contains("SECRET_SENTINEL"),"Remote result escaped inspection");ensure!(json!(reader.history).to_string().contains("[redacted]"),"Redacted remote result missing");
  run(&rt,&sessions[1],"deny-write",json!([{"name":"write_file","arguments":{"path":"forbidden.txt","content":"unapproved"}}])).await?;
  let events=rt.dispatch("event/read",json!({"session_id":sessions[1],"after":0})).await?;ensure!(!events.to_string().contains("SECRET_SENTINEL"),"Remote result leaked into events");ensure!(events["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_started"&&e["data"]["name"]=="read_file"),"No remote read was dispatched");ensure!(!events["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_started"&&e["data"]["name"]=="write_file"),"Blocked action dispatched");
  let evidence=store.compliance_page(&sessions[1],Default::default())?;ensure!(evidence["activities"].as_array().unwrap().iter().any(|e|e["facts"]["outcome"]=="redacted"),"Missing redaction evidence");ensure!(evidence["activities"].as_array().unwrap().iter().any(|e|e["facts"]["outcome"]=="blocked"),"Missing block evidence");
  Ok(())
 }.await;
    for sid in &sessions {
        rt.dispatch("session/close", json!({"session_id":sid}))
            .await?;
    }
    rt.shutdown().await?;
    let operation = uuid::Uuid::new_v4().to_string();
    let (deleting, _) = store.begin_environment_delete(&record.id, &operation, "qualification")?;
    provider.delete(binding, operation.clone()).await?;
    store.complete_environment_delete(&record.id, &operation, deleting.revision)?;
    tokio::task::block_in_place(|| -> Result<()> {
        let mut db = postgres::Client::connect(&config.connection, postgres::NoTls)?;
        db.batch_execute(&format!("DROP SCHEMA {} CASCADE", config.schema))?;
        Ok(())
    })?;
    result
}
