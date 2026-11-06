#![cfg(any(windows, target_os = "linux"))]
use openworker_runtime::{execution::Backend, model::Model, runtime::Runtime};
use serde_json::json;
use std::path::Path;
#[tokio::test]
async fn session_model_precedence_reviewer_independence_and_restart_identity() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let bundle = root.join("bundle");
    std::fs::create_dir_all(bundle.join("coworker")).unwrap();
    std::fs::write(bundle.join("coworker/manifest.md"),"---\nid: test\nname: Test\ndefault_model:\n  connection: personal\n  model: gpt-6-astra\n---\nTest model selection.").unwrap();
    std::fs::write(
        bundle.join("tools.json"),
        std::fs::read(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant/tools.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let workspace = root.join("work");
    std::fs::create_dir(&workspace).unwrap();
    let binary = Path::new(env!("CARGO_BIN_EXE_openworker"));
    let state = root.join("state");
    let rt = Runtime::open_with_backend(
        &state,
        &bundle,
        binary,
        Model::new("process-default".into(), true),
        Backend::Off,
    )
    .unwrap();
    rt.dispatch("connection/set",json!({"id":"personal","label":"Personal","provider":"openai","api_key":"synthetic-test-key","persistence":"memory"})).await.unwrap();
    rt.dispatch("model/defaults/set",json!({"agent":{"connection":"personal","model":"gpt-6-sol"},"reviewer":{"connection":"personal","model":"reviewer-independent"}})).await.unwrap();
    let request = json!({"name":"test","coworker":"test","workspace":workspace});
    let a = rt
        .dispatch("session/create", request.clone())
        .await
        .unwrap();
    let sid = a["session"]["id"].as_str().unwrap().to_string();
    assert_eq!(
        rt.snapshot(&sid).unwrap()["session"]["model"],
        "gpt-6-astra"
    );
    let mut over = request.clone();
    over["model"] = json!({"connection":"personal","model":"session-override"});
    let b = rt.dispatch("session/create", over).await.unwrap();
    let bid = b["session"]["id"].as_str().unwrap();
    assert_eq!(
        rt.snapshot(bid).unwrap()["session"]["model"],
        "session-override"
    );
    assert_eq!(
        rt.snapshot(bid).unwrap()["session"]["reviewer_selection"]["model"],
        "reviewer-independent"
    );
    let mut bad = request;
    bad["model"] = json!({"connection":"missing","model":"gpt-6-sol"});
    assert!(rt.dispatch("session/create", bad).await.is_err());
    rt.dispatch(
        "model/defaults/set",
        json!({"agent":{"connection":"personal","model":"new-default"},"reviewer":null}),
    )
    .await
    .unwrap();
    assert_eq!(
        rt.snapshot(&sid).unwrap()["session"]["model"],
        "gpt-6-astra"
    );
    drop(rt);
    let rt = Runtime::open_with_backend(
        &state,
        &bundle,
        binary,
        Model::new("changed-process-default".into(), true),
        Backend::Off,
    )
    .unwrap();
    let snapshot = rt
        .dispatch("session/attach", json!({"session_id":sid}))
        .await
        .unwrap();
    assert_eq!(snapshot["session"]["model"], "gpt-6-astra");
    rt.dispatch("skills/list", json!({"session_id":sid}))
        .await
        .unwrap();
    assert!(!serde_json::to_string(&snapshot)
        .unwrap()
        .contains("synthetic-test-key"));
}
