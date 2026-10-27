//! Service SDK qualification: real PostgreSQL, API processes and OpenShell tools.
#![cfg(all(feature = "openshell-test", unix))]
use futures_util::FutureExt;
use openworker_runtime::environment::Provider;
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
#[ignore = "requires disposable OW_PG_TEST_URL and OW_PROBE_* local OpenShell"]
async fn releases_profiles_provisioning_and_shared_verifier() {
    tokio::time::timeout(Duration::from_secs(240), qualify())
        .await
        .unwrap();
}
async fn qualify() {
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

    let value = json!({"service":{"id":"m2-test","namespace":"test","agent":"builder"},"database_env":"OW_PG_TEST_URL","schema":format!("ow_service_{}",uuid::Uuid::new_v4().simple()),"database_loopback_plaintext":true,"state_root":root.path().join("state"),"bundle":bundle,"runner":std::env::var("OW_PROBE_RUNNER").unwrap(),"model":"fixture","scripted":true,"api_token_file":token_file,"actor":"tester","openshell":{"identity":"m2-test","endpoint":std::env::var("OW_PROBE_ENDPOINT").unwrap(),"certificates":std::env::var("OW_PROBE_CERTS").unwrap(),"tls_server_name":std::env::var("OW_PROBE_TLS_NAME").ok(),"workspace":"default","image":std::env::var("OW_PROBE_IMAGE").unwrap()}});
    std::fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    let cfg: Config = serde_json::from_value(value).unwrap();
    let db = cfg.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let store = PostgresStore::open(db.clone()).unwrap();
    store.auth_bootstrap(&token, "tester").unwrap();

    let (mut a, url_a) = api(&path, "m2-api-a").await;
    let (mut b, url_b) = api(&path, "m2-api-b").await;
    let admin = Harness::connect(options(&url_a, &token)).await.unwrap();
    let user_token = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    store
        .authorized(&token)
        .unwrap()
        .auth_issue(
            "alice",
            serde_json::from_value(
                json!({"principal":"alice","role":"operator","scope":{"kind":"owned"}}),
            )
            .unwrap(),
            &user_token,
        )
        .unwrap();
    let profile:openworker_sdk::service::RuntimeProfile=serde_json::from_value(json!({"id":"team@1","provider":"openshell","cpu":1,"memoryMB":768,"sharing":"shared","approvalMode":"bypass","autoSkipQuestions":true,"allowedPrincipals":["alice"]})).unwrap();
    let entry = admin
        .register_runtime_profile(&profile, "profile-create")
        .await
        .unwrap();
    assert!(!entry.activated);
    admin
        .activate_runtime_profile(&profile.id, Some("default"), "profile-activate")
        .await
        .unwrap();
    for (id, tools) in [
        ("coder@1", vec!["read_file", "write_file", "run_shell"]),
        ("verifier@1", vec!["read_file", "run_shell"]),
    ] {
        let release:openworker_sdk::service::AgentRelease=serde_json::from_value(json!({"id":id,"instructions":format!("You are {id}. Work in your shared project."),"tools":tools,"runtimeProfiles":["team@1"],"defaultRuntimeProfile":if id=="coder@1"{Some("team@1")}else{None},"allowedPrincipals":["alice"]})).unwrap();
        admin
            .deploy_agent(&release, &format!("deploy-{id}"))
            .await
            .unwrap();
        admin
            .activate_agent(
                id,
                Some(id.split('@').next().unwrap()),
                &format!("activate-{id}"),
            )
            .await
            .unwrap();
    }
    let service = Harness::connect(options(&url_b, &user_token))
        .await
        .unwrap();
    let coder = service.agent("coder").await.unwrap();
    assert_eq!(coder.id, "coder@1");
    let accepted = coder
        .admit_session(
            openworker_sdk::service::SessionOptions {
                project: Some("game".into()),
                ..Default::default()
            },
            "game-create",
        )
        .await
        .unwrap();
    let op_id = accepted["operation"]["id"].as_str().unwrap().to_owned();
    let environment_id = accepted["operation"]["environmentId"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(accepted["operation"]["status"], "queued");
    let same = coder
        .admit_session(
            openworker_sdk::service::SessionOptions {
                project: Some("game".into()),
                ..Default::default()
            },
            "game-create",
        )
        .await
        .unwrap();
    assert_eq!(same["operation"]["id"], op_id);
    // Fault injection: provider created the sandbox, controller died before
    // saving its result. Expire only that claim; the next controller must inspect
    // and bind this same remote UID without sending another create.
    let (claimed, first) = store.service_claim().unwrap().unwrap();
    assert!(first);
    let prof = store.catalog_get("profile", "team@1").unwrap();
    let provider = openworker_server_reference::management::provider(&cfg, &prof).unwrap();
    use openworker_runtime::store::StateStore;
    let pending = store.clone().environment(&environment_id).unwrap();
    let original = provider.create(pending).await.unwrap();
    let connection = db.connection.clone();
    let schema = db.schema.clone();
    let claimed_id = claimed.id.clone();
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!("SET search_path={},pg_catalog", schema))
            .unwrap();
        pg.execute(
            "UPDATE service_operations SET next_check=clock_timestamp() WHERE id=$1",
            &[&claimed_id],
        )
        .unwrap();
    })
    .await
    .unwrap();
    let role = |name: &str| {
        Command::new(BIN)
            .arg("--config")
            .arg(&path)
            .arg(name)
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()
            .unwrap()
    };
    let mut controller = role("service-controller");
    let mut other_controller = role("service-controller");
    let mut worker = role("service-worker");
    let outcome = std::panic::AssertUnwindSafe(async {
        let operation = service
            .wait_operation(&op_id, Duration::from_secs(140))
            .await
            .unwrap();
        assert_eq!(operation.environment_id, environment_id);
        assert_eq!(
            store
                .clone()
                .environment(&environment_id)
                .unwrap()
                .binding
                .unwrap()
                .remote_id,
            original.remote_id
        );
        let reference: openworker_sdk::service::SessionRef =
            serde_json::from_value(operation.receipt.unwrap()).unwrap();
        let session = service.resume(reference.clone()).await.unwrap();
        let run = session
            .send(
                &plan(
                    "write_file",
                    json!({"path":"shared-proof.txt","content":"coder output for verifier"}),
                ),
                Some("write"),
            )
            .await
            .unwrap();
        assert_eq!(run.result().await.unwrap().status, "completed");
        let env = service.environment(&environment_id).await.unwrap();
        assert_eq!(env["runtimeProfile"], "team@1");
        assert_eq!(env["principal"], "alice");
        let status = Command::new("node")
            .arg(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("../typescript-sdk/test/service-v2-live.mjs"),
            )
            .env("OW_SERVICE_URL", &url_a)
            .env("OW_SERVICE_TOKEN", &user_token)
            .env(
                "OW_SERVICE_REFERENCE",
                serde_json::to_string(&reference).unwrap(),
            )
            .kill_on_drop(true)
            .status()
            .await
            .unwrap();
        assert!(status.success());
        assert_eq!(
            service
                .resume(reference)
                .await
                .unwrap()
                .snapshot()
                .await
                .unwrap()["session"]["permissions"]["mode"],
            "bypass"
        );
    })
    .catch_unwind()
    .await;
    service.disconnect();
    admin.disconnect();
    for child in [
        &mut controller,
        &mut other_controller,
        &mut worker,
        &mut a,
        &mut b,
    ] {
        let _ = child.kill().await;
    }
    let p = store.catalog_get("profile", "team@1").unwrap();
    let provider = openworker_server_reference::management::provider(&cfg, &p).unwrap();
    let connection = db.connection.clone();
    let schema = db.schema.clone();
    let environment_ids: Vec<String> = tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&connection, postgres::NoTls).unwrap();
        pg.query(
            &format!(
                "SELECT environment_id FROM {}.service_environment_owners",
                schema
            ),
            &[],
        )
        .unwrap()
        .iter()
        .map(|r| r.get(0))
        .collect()
    })
    .await
    .unwrap();
    for id in environment_ids {
        let env = store.clone().environment(&id).unwrap();
        if let Some(binding) = env.binding {
            provider
                .delete(binding, uuid::Uuid::new_v4().to_string())
                .await
                .unwrap();
        }
    }
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
    eprintln!("PASS immutable releases, resource profile, two controllers, bounded worker, shared coder/verifier, Rust and TypeScript SDKs");
}
