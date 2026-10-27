//! Service SDK qualification: real PostgreSQL, API processes and OpenShell tools.
#![cfg(all(feature = "openshell-test", unix))]
use futures_util::{FutureExt, StreamExt};
use openworker_runtime::environment::{Management, Provider, Sharing};
use openworker_sdk::{
    agents::Harness,
    service::{ConnectOptions, Credential},
};
use openworker_server_reference::Config;
use openworker_store_postgres::PostgresStore;
use serde_json::{json, Value};
use std::{path::Path, process::Stdio, sync::Arc, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::{Child, Command},
};
const BIN: &str = env!("CARGO_BIN_EXE_openworker-server-reference");
async fn api(config: &Path, node: &str) -> (Child, String) {
    let mut child = Command::new(BIN)
        .arg("--config")
        .arg(config)
        .args(["api", "--node", node, "--listen", "127.0.0.1:0"])
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut reader = BufReader::new(child.stdout.take().unwrap());
    let mut line = String::new();
    tokio::time::timeout(Duration::from_secs(30), reader.read_line(&mut line))
        .await
        .unwrap()
        .unwrap();
    let url = line.split_whitespace().last().unwrap().to_owned();
    assert!(url.starts_with("http://127.0.0.1:"), "{line}");
    (child, url)
}
fn worker(config: &Path, node: &str) -> Child {
    Command::new(BIN)
        .arg("--config")
        .arg(config)
        .args(["worker", "--node", node, "--slots", "1"])
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap()
}

fn options(url: &str, token: &str) -> ConnectOptions {
    let token = token.to_owned();
    let mut o = ConnectOptions::new(url, "test", "unused");
    o.credentials = Credential::Callback(Arc::new(move |_| {
        let token = token.clone();
        Box::pin(async move { Ok(token.into()) })
    }));
    o
}
fn plan(tool: &str, args: Value) -> String {
    format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":tool,"arguments":args}])
    )
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable OW_PG_TEST_URL and existing OW_PROBE_* OpenShell gateway"]
async fn rust_and_typescript_service_clients_two_apis_real_tools() {
    tokio::time::timeout(Duration::from_secs(210), qualification())
        .await
        .unwrap();
}
async fn qualification() {
    let root = tempfile::tempdir().unwrap();
    let token = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let token_file = root.path().join("token");
    std::fs::write(&token_file, &token).unwrap();
    let path = root.path().join("config.json");
    let bundle = root.path().join("bundle");
    std::fs::create_dir_all(bundle.join("coworker")).unwrap();
    let original = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    for entry in std::fs::read_dir(original.join("coworker")).unwrap() {
        let entry = entry.unwrap();
        std::fs::copy(
            entry.path(),
            bundle.join("coworker").join(entry.file_name()),
        )
        .unwrap();
    }
    let mut tools: Value =
        serde_json::from_slice(&std::fs::read(original.join("tools.json")).unwrap()).unwrap();
    // The cached, digest-pinned qualification runner predates example_echo.
    tools["bindings"]
        .as_object_mut()
        .unwrap()
        .remove("example_echo");
    std::fs::write(
        bundle.join("tools.json"),
        serde_json::to_vec(&tools).unwrap(),
    )
    .unwrap();

    let value = json!({"service":{"id":"m1-test","namespace":"test","agent":"builder"},"database_env":"OW_PG_TEST_URL","schema":format!("ow_service_{}",uuid::Uuid::new_v4().simple()),"database_loopback_plaintext":true,"state_root":root.path().join("state"),"bundle":bundle,"runner":std::env::var("OW_PROBE_RUNNER").unwrap(),"model":"fixture","scripted":true,"api_token_file":token_file,"actor":"tester","openshell":{"identity":"m1-test","endpoint":std::env::var("OW_PROBE_ENDPOINT").unwrap(),"certificates":std::env::var("OW_PROBE_CERTS").unwrap(),"tls_server_name":std::env::var("OW_PROBE_TLS_NAME").ok(),"workspace":"default","image":std::env::var("OW_PROBE_IMAGE").unwrap()}});
    std::fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    let cfg: Config = serde_json::from_value(value).unwrap();
    let db = cfg.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let store = PostgresStore::open(db.clone()).unwrap();
    store.auth_bootstrap(&token, "tester").unwrap();
    let provider = cfg.provider().unwrap();
    let (env, _) = store
        .begin_environment(
            "service-test",
            "tester",
            provider.spec(Sharing::Shared, Management::Harness),
        )
        .unwrap();
    eprintln!("Creating M1 test environment {}", env.id);
    let binding = provider.create(env.clone()).await.unwrap();
    store
        .complete_environment(&env.id, env.revision, Some(binding.clone()))
        .unwrap();
    let outcome=std::panic::AssertUnwindSafe(async {
    let (mut a, url_a) = api(&path, "service-a").await;
    let (mut b, url_b) = api(&path, "service-b").await;
    let service = Harness::connect(options(&url_a, &token)).await.unwrap();
    let other = Harness::connect(options(&url_b, &token)).await.unwrap();
    assert_eq!(service.capabilities()["features"]["runtimeProfiles"], false);
    assert!(Harness::connect(options(&url_a, &"wrong".repeat(12)))
        .await
        .is_err());
    let mut wrong = options(&url_a, &token);
    wrong.namespace = "wrong".into();
    assert!(Harness::connect(wrong).await.is_err());
    assert!(store.bind_service("other", "test").is_err());
    let agent = service.agent("builder").await.unwrap();
    let second = other.agent("builder").await.unwrap();
    let (one, two) = tokio::join!(
        agent.new_session(&env.id, Some("create-once")),
        second.new_session(&env.id, Some("create-once"))
    );
    let session = one.unwrap();
    assert_eq!(session.id(), two.unwrap().id());
    let viewer_token=uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let viewer_spec=serde_json::from_value(json!({"principal":"viewer","role":"observer","scope":{"kind":"sessions","ids":[session.id()]}})).unwrap();
    store.authorized(&token).unwrap().auth_issue("viewer",viewer_spec,&viewer_token).unwrap();
    let viewer=Harness::connect(options(&url_b,&viewer_token)).await.unwrap();
    let viewer_agent=viewer.agent("builder").await.unwrap();
    assert_eq!(viewer_agent.new_session(&env.id,Some("denied-create")).await.err().unwrap().status,403);
    assert!(viewer.resume(session.reference()).await.is_ok());
    viewer.disconnect();
    let operator_token=uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let operator_spec=serde_json::from_value(json!({"principal":"no-env-grant","role":"operator","scope":{"kind":"owned"}})).unwrap();
    store.authorized(&token).unwrap().auth_issue("operator",operator_spec,&operator_token).unwrap();
    let operator=Harness::connect(options(&url_b,&operator_token)).await.unwrap();
    assert_eq!(operator.agent("builder").await.unwrap().new_session(&env.id,Some("denied-env")).await.err().unwrap().status,403);
    assert_eq!(operator.resume(session.reference()).await.err().unwrap().status,403);
    operator.disconnect();
    let reference = session.reference();
    let resumed = other.resume(reference.clone()).await.unwrap();
    let mut wrong_ref = reference.clone();
    wrong_ref.service_id = "wrong".into();
    assert!(other.resume(wrong_ref).await.is_err());
    let task = plan(
        "write_file",
        json!({"path":"service-proof.txt","content":"real tool across API nodes"}),
    );
    let run = session.send(&task, Some("write-once")).await.unwrap();
    let duplicate = resumed.send(&task, Some("write-once")).await.unwrap();
    assert_eq!(run.id, duplicate.id);
    assert_eq!(
        resumed
            .send("conflicting", Some("write-once"))
            .await
            .err()
            .unwrap()
            .status,
        409
    );
    assert_eq!(
        resumed
            .send("busy", Some("different-key"))
            .await
            .err()
            .unwrap()
            .status,
        409
    );
    let mut w = worker(&path, "service-worker");
    let pending = run.result().await.unwrap();
    assert_eq!(pending.status, "interrupted");
    assert_eq!(pending.interruptions.len(), 1);
    service.disconnect();
    a.kill().await.unwrap();
    let run = resumed.resume_run(&run.submission_id).await.unwrap();
    assert_eq!(run.snapshot().await.unwrap().status, "interrupted");
    let i = &pending.interruptions[0];
    let action = json!({"kind":"response","interaction_id":i["id"],"response":"once","binding":{"bundle_digest":i["bundle_digest"],"schema_digest":i["schema_digest"],"policy_digest":i["policy_digest"],"resource_policy_digest":i["resource_policy_digest"]}});
    let mut stream = other.events(resumed.id().into(), pending.cursor);
    let stream_task = tokio::spawn(async move {
        while let Some(e) = stream.next().await {
            let e = e.unwrap();
            if e.kind == "turn_completed" {
                return e;
            }
        }
        panic!("stream ended")
    });
    let receipt = resumed
        .control(&run.id, action.clone(), Some("approve-once"))
        .await
        .unwrap();
    let duplicate = resumed
        .control(&run.id, action, Some("approve-once"))
        .await
        .unwrap();
    assert_eq!(receipt["command"], duplicate["command"]);
    assert_eq!(stream_task.await.unwrap().data["status"], "completed");
    let finished=run.result().await.unwrap();
    assert_eq!(finished.status,"completed");
    assert!(finished.final_output.as_deref().unwrap().contains("Resource fixture finished"));
    let receipt_id = receipt["command"]["request_id"].as_str().unwrap();
    assert_eq!(
        resumed.control_receipt(receipt_id).await.unwrap()["status"],
        "applied"
    );
    let status = Command::new("node")
        .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("../typescript-sdk/test/service-live.mjs"))
        .env("OW_SERVICE_URL", &url_b)
        .env("OW_SERVICE_TOKEN", &token)
        .env("OW_SERVICE_ENV", &env.id)
        .env(
            "OW_SERVICE_REFERENCE",
            serde_json::to_string(&reference).unwrap(),
        )
        .kill_on_drop(true)
        .status()
        .await
        .unwrap();
    assert!(status.success());
    other.disconnect();
    w.kill().await.unwrap();
    b.kill().await.unwrap();
    }).catch_unwind().await;
    provider
        .delete(binding, uuid::Uuid::new_v4().to_string())
        .await
        .unwrap();
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", db.schema))
            .unwrap();
    })
    .await
    .unwrap();
    if let Err(panic) = outcome {
        std::panic::resume_unwind(panic);
    }
    eprintln!("PASS real PostgreSQL, two API processes, both SDKs, approval, cancellation and OpenShell tools");
}
