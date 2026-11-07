#![cfg(all(feature = "test-support", unix))]
use openworker_sdk::{agents::*, *};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    time::Duration,
};
fn plan(name: &str, args: Value) -> String {
    format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":name,"arguments":args}])
    )
}
async fn history(h: &Harness, s: &Session) -> Value {
    h.client()
        .request("history/read", json!({"session_id":s.id(),"after":0}))
        .await
        .unwrap()
}
async fn invoke(h: &Harness, s: &Session, name: &str, args: Value) -> Value {
    let r = s.run(&plan(name, args)).await.unwrap();
    assert_eq!(r.status, RunStatus::Completed, "{r:?}");
    let hist = history(h, s).await;
    let item = hist["items"]
        .as_array()
        .unwrap()
        .iter()
        .rev()
        .map(|v| &v["item"])
        .find(|v| v["type"] == "function_call_output")
        .unwrap();
    let value: Value = serde_json::from_str(item["output"].as_str().unwrap()).unwrap();
    assert_ne!(value["isError"], true, "{value}");
    value
}
async fn host(root: &Path, storage: Storage, managed: bool) -> Harness {
    let exe = PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let b = Harness::builder()
        .state_dir(root.join("state"))
        .executable(&exe)
        .storage(storage)
        .sandbox(Sandbox::Off)
        .scripted(true);
    if managed {
        b.managed(exe, RestartPolicy::default()).await.unwrap()
    } else {
        b.start().await.unwrap()
    }
}
async fn exercise(managed: bool, pg: bool) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    let storage = if pg {
        Storage::Postgres {
            connection_env: "OW_PG_TEST_URL".into(),
            schema: openworker_runtime::id("ow_assistant"),
            ca_file: None,
            loopback_plaintext: true,
        }
    } else {
        Storage::Sqlite
    };
    storage.migrate().unwrap();
    let h = host(&root, storage.clone(), managed).await;
    let definition = Agent::new("coder")
        .instructions("Help the learner.")
        .memory_scope("course/learner-a")
        .scheduling(true)
        .approvals(Approvals::Never)
        .tool("coding.write_file");
    let coder = h.create_agent(definition.clone()).await.unwrap();
    let s = coder.new_session_in(&work).await.unwrap();
    let m = invoke(
        &h,
        &s,
        "memory_remember",
        json!({"summary":"Theme","content":"Ocean blue"}),
    )
    .await["memory"]
        .clone();
    let evaluator = h
        .create_agent(
            Agent::new("evaluator")
                .instructions("Evaluate.")
                .memory_scope("course/learner-a")
                .approvals(Approvals::Never),
        )
        .await
        .unwrap();
    let e = evaluator.new_session_in(&work).await.unwrap();
    assert_eq!(
        invoke(&h, &e, "memory_read", json!({"id":m["id"]})).await["memory"]["content"],
        "Ocean blue"
    );
    let other = h
        .create_agent(
            Agent::new("other")
                .instructions("Another learner.")
                .memory_scope("course/learner-b")
                .approvals(Approvals::Never),
        )
        .await
        .unwrap();
    let o = other.new_session_in(&work).await.unwrap();
    assert_eq!(
        invoke(&h, &o, "memory_search", json!({"query":""})).await["memories"],
        json!([])
    );
    let deadline = openworker_runtime::assistant_tools::now() + 20;
    let task=invoke(&h,&s,"schedule_create",json!({"run_at":deadline,"prompt":plan("coding__write_file",json!({"path":"scheduled.txt","content":"once","create_only":true}))})).await;
    assert!(task["schedule"]["id"].is_string());
    assert_eq!(s.state().await.unwrap()["capabilities"]["timers"], true);
    assert_eq!(
        s.state().await.unwrap()["timers"].as_array().unwrap().len(),
        1
    );
    let reference = s.reference().clone();
    h.shutdown().await.unwrap();
    let h = host(&root, storage, managed).await;
    let coder = h.create_agent(definition).await.unwrap();
    let s = coder.resume_session(reference).await.unwrap();
    tokio::time::timeout(Duration::from_secs(60), async {
        loop {
            let snap = s.snapshot().await.unwrap();
            if work.join("scheduled.txt").exists() && snap["turn"]["status"] == "completed" {
                break;
            }
            assert_ne!(snap["turn"]["status"], "failed", "{snap}");
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(
        std::fs::read_to_string(work.join("scheduled.txt")).unwrap(),
        "once"
    );
    assert_eq!(s.state().await.unwrap()["timers"], json!([]));
    assert_eq!(
        invoke(&h, &s, "memory_read", json!({"id":m["id"]})).await["memory"]["content"],
        "Ocean blue"
    );
    let hst = history(&h, &s).await;
    assert_eq!(
        hst["items"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|v| v["item"]["runtime_notice"] == "scheduled_task")
            .count(),
        1
    );
    assert_eq!(
        hst["items"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|v| v["item"]["name"] == "coding__write_file")
            .count(),
        1
    );
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sqlite_embedded_memory_and_schedules() {
    exercise(false, false).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sqlite_managed_memory_and_schedules() {
    exercise(true, false).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_embedded_memory_and_schedules() {
    exercise(false, true).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_managed_memory_and_schedules() {
    exercise(true, true).await;
}
