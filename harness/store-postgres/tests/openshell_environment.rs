//! Opt-in real gateway/DB qualification. Uses a fresh disposable environment.
#![cfg(all(feature = "openshell-test", not(windows)))]
use openworker_runtime::{
    environment::{Management, Provider, Registry, Sharing},
    environment_openshell::OpenShell,
    execution::Backend,
    model::Model,
    runtime::Runtime,
    sandbox::ExecConnectionConfig,
    store::StateStore,
};
use openworker_store_postgres::{Config, PostgresStore, Tls};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc, time::Duration};

async fn finished(rt: &Arc<Runtime>, sid: &str) -> Value {
    tokio::time::timeout(Duration::from_secs(45), async {
        loop {
            let snapshot = rt
                .dispatch("session/attach", json!({"session_id":sid}))
                .await
                .unwrap();
            if snapshot["turn"]["status"] == "completed" {
                return snapshot;
            }
            assert!(
                !matches!(
                    snapshot["turn"]["status"].as_str(),
                    Some("failed" | "interrupted")
                ),
                "{snapshot}"
            );
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap()
}
async fn lifecycle(cli: &str, name: &str, operation: &str) {
    let gateway = std::env::var("OW_PROBE_GATEWAY").expect("Explicit test gateway required");
    let config =
        std::env::var("OW_PROBE_CONFIG_HOME").expect("Task-local OpenShell CLI config required");
    let output = tokio::process::Command::new(cli)
        .env("XDG_CONFIG_HOME", &config)
        .args(["sandbox", operation, name, "-g", &gateway])
        .output()
        .await
        .unwrap();
    assert!(
        output.status.success(),
        "Lifecycle fixture failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let expected = if operation == "stop" {
        "Stopped"
    } else {
        "Ready"
    };
    tokio::time::timeout(Duration::from_secs(60), async {
        loop {
            let output = tokio::process::Command::new(cli)
                .env("XDG_CONFIG_HOME", &config)
                .args(["sandbox", "get", name, "-g", &gateway, "-o", "json"])
                .output()
                .await
                .unwrap();
            assert!(output.status.success());
            let state: Value = serde_json::from_slice(&output.stdout).unwrap();
            if state["phase"] == expected {
                break;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    })
    .await
    .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL, OW_PROBE_ENDPOINT/CERTS/IMAGE/RUNNER"]
async fn real_remote_sessions_share_original_workspace_without_host_fallback() {
    let provider = Arc::new(
        OpenShell::new(
            "openshell-k8s".into(),
            "isolated-qualification".into(),
            ExecConnectionConfig {
                endpoint: std::env::var("OW_PROBE_ENDPOINT").unwrap(),
                certificates: PathBuf::from(std::env::var("OW_PROBE_CERTS").unwrap()),
                tls_server_name: std::env::var("OW_PROBE_TLS_NAME").ok(),
                workspace: "default".into(),
            },
            std::env::var("OW_PROBE_IMAGE").unwrap(),
        )
        .unwrap(),
    );
    let mut config = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_remoteenv_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = Tls::LoopbackPlaintext;
    config.lease_duration = Duration::from_secs(120);
    PostgresStore::migrate(&config).unwrap();
    let store = PostgresStore::open(config.clone()).unwrap();
    let (record, fresh) = store
        .begin_environment(
            "create-environment",
            "test-operator",
            provider.spec(Sharing::Shared, Management::Harness),
        )
        .unwrap();
    assert!(fresh);
    eprintln!(
        "qualification environment {} schema {}",
        record.id, config.schema
    );
    let binding = provider.create(record.clone()).await.unwrap();
    eprintln!(
        "remote sandbox {} {}",
        binding.remote_name, binding.remote_id
    );
    store
        .complete_environment(&record.id, record.revision, Some(binding.clone()))
        .unwrap();
    provider.inspect(binding.clone()).await.unwrap();
    let mut changed = binding.clone();
    changed.remote_id = "replacement".into();
    assert!(provider.inspect(changed).await.is_err());
    let mut registry = Registry::default();
    registry.register(provider.clone()).unwrap();
    let registry = Arc::new(registry);
    let local = tempfile::tempdir().unwrap();
    let bundle = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let runner = PathBuf::from(std::env::var("OW_PROBE_RUNNER").unwrap());
    let mut rt = Runtime::open_with_store(
        local.path(),
        &bundle,
        &runner,
        Model::new("fixture".into(), true),
        Backend::Openshell,
        None,
        None,
        Default::default(),
        Some(Box::new(store.clone())),
    )
    .unwrap();
    Runtime::configure_environments(&mut rt, registry.clone()).unwrap();
    assert!(rt
        .dispatch(
            "session/create",
            json!({"name":"missing binding","coworker":"assistant-example","workspace":"/sandbox"})
        )
        .await
        .is_err());
    let mut sessions = vec![];
    for name in ["a", "b"] {
        let s=rt.dispatch("session/create",json!({"name":name,"coworker":"assistant-example","workspace":"/sandbox","environment_id":record.id,"permissions":{"mode":"bypass"}})).await.unwrap();
        sessions.push(s["session"]["id"].as_str().unwrap().to_owned());
    }
    let plan = |calls: Value| format!("TEST_RESOURCE_GRANTS {calls}");
    rt.dispatch("turn/start",json!({"session_id":sessions[0],"submission_id":"write","text":plan(json!([{"name":"write_file","arguments":{"path":"proof.txt","content":"original workspace"}}]))})).await.unwrap();
    finished(&rt, &sessions[0]).await;
    assert!(store
        .inspect(&sessions[0])
        .unwrap()
        .history
        .iter()
        .any(|v| v["type"] == "function_call_output" && v.to_string().contains("written")));
    rt.dispatch("session/close", json!({"session_id":sessions[0]}))
        .await
        .unwrap();
    provider.inspect(binding.clone()).await.unwrap();
    rt.dispatch("turn/start",json!({"session_id":sessions[1],"submission_id":"read","text":plan(json!([{"name":"read_file","arguments":{"path":"proof.txt"}}]))})).await.unwrap();
    finished(&rt, &sessions[1]).await;
    assert!(store
        .inspect(&sessions[1])
        .unwrap()
        .history
        .iter()
        .any(
            |v| v["type"] == "function_call_output" && v.to_string().contains("original workspace")
        ));
    assert!(store
        .begin_environment_delete(&record.id, "delete-too-soon", "operator")
        .is_err());
    rt.shutdown().await.unwrap();
    drop(rt);
    // Optional lifecycle qualification invokes only the explicitly selected
    // test gateway. The shipped provider has no CLI dependency.
    if let Ok(cli) = std::env::var("OW_PROBE_CLI") {
        lifecycle(&cli, &binding.remote_name, "stop").await;
        assert!(provider.inspect(binding.clone()).await.is_err());
        lifecycle(&cli, &binding.remote_name, "start").await;
        provider.inspect(binding.clone()).await.unwrap();
        eprintln!("PASS stopped environment refused; restart retained original identity");
    }
    store.release(&sessions[1]).unwrap();
    let replacement = PostgresStore::open(config).unwrap();
    replacement.claim(&sessions[1]).unwrap();
    let new_root = tempfile::tempdir().unwrap();
    let mut rt = Runtime::open_with_store(
        new_root.path(),
        &bundle,
        &runner,
        Model::new("fixture".into(), true),
        Backend::Openshell,
        None,
        None,
        Default::default(),
        Some(Box::new(replacement.clone())),
    )
    .unwrap();
    Runtime::configure_environments(&mut rt, registry).unwrap();
    rt.dispatch("turn/start",json!({"session_id":sessions[1],"submission_id":"read-after-restart","text":plan(json!([{"name":"read_file","arguments":{"path":"proof.txt"}}]))})).await.unwrap();
    finished(&rt, &sessions[1]).await;
    assert_eq!(
        replacement
            .inspect(&sessions[1])
            .unwrap()
            .history
            .iter()
            .filter(|v| v["type"] == "function_call_output"
                && v.to_string().contains("original workspace"))
            .count(),
        2
    );
    assert_eq!(
        replacement.get(&sessions[1]).unwrap().environment.unwrap(),
        binding
    );
    rt.dispatch("session/close", json!({"session_id":sessions[1]}))
        .await
        .unwrap();
    rt.shutdown().await.unwrap();
    let operation = uuid::Uuid::new_v4().to_string();
    let (deleting, fresh) = replacement
        .begin_environment_delete(&record.id, &operation, "test-operator")
        .unwrap();
    assert!(fresh);
    provider.delete(binding, operation.clone()).await.unwrap();
    replacement
        .complete_environment_delete(&record.id, &operation, deleting.revision)
        .unwrap();
}
