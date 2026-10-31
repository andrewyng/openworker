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
fn tool_data(event: &Value) -> Value {
    let result = &event["data"]["result"];
    result["content"][0]["text"]
        .as_str()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or_else(|| result.clone())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable OW_PG_TEST_URL and OW_PROBE_* local OpenShell"]
async fn releases_profiles_provisioning_and_shared_verifier() {
    tokio::time::timeout(Duration::from_secs(360), qualify())
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
    let tools = json!({"version":2,"servers":[{"id":"coding","program":"$OPENWORKER","args":["mcp-coding","--workspace","$WORKSPACE"]}],"bindings":{
        "read_file":{"server":"coding","tool":"read_file"},"write_file":{"server":"coding","tool":"write_file"},"run_shell":{"server":"coding","tool":"run_shell"},"git_status":{"server":"coding","tool":"git_status"},"git_log":{"server":"coding","tool":"git_log"}
    }});
    std::fs::write(
        bundle.join("tools.json"),
        serde_json::to_vec(&tools).unwrap(),
    )
    .unwrap();

    let value = json!({"service":{"id":"m2-test","namespace":"test","agent":"builder"},"database_env":"OW_PG_TEST_URL","schema":format!("ow_service_{}",uuid::Uuid::new_v4().simple()),"observability":{"schema":format!("ow_record_{}",uuid::Uuid::new_v4().simple())},"database_loopback_plaintext":true,"state_root":root.path().join("state"),"bundle":bundle,"runner":std::env::var("OW_PROBE_RUNNER").unwrap(),"model":"fixture","scripted":true,"api_token_file":token_file,"actor":"tester","openshell":{"identity":"m2-test","endpoint":std::env::var("OW_PROBE_ENDPOINT").unwrap(),"certificates":std::env::var("OW_PROBE_CERTS").unwrap(),"tls_server_name":std::env::var("OW_PROBE_TLS_NAME").ok(),"workspace":"default","image":std::env::var("OW_PROBE_IMAGE").unwrap()}});
    std::fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    let cfg: Config = serde_json::from_value(value).unwrap();
    let db = cfg.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let recording_db = cfg.observability.as_ref().unwrap().database(&cfg).unwrap();
    let recording_setup = recording_db.clone();
    tokio::task::spawn_blocking(move || {
        openworker_observability::postgres::Postgres::migrate(&recording_setup)
    })
    .await
    .unwrap()
    .unwrap();
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
        (
            "coder@1",
            vec![
                "read_file",
                "write_file",
                "run_shell",
                "git_status",
                "git_log",
            ],
        ),
        (
            "verifier@1",
            vec!["read_file", "run_shell", "git_status", "git_log"],
        ),
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
        // Exercise the actual catalog service-worker factory. A live monitor can
        // emit storage spans even when slot connections accidentally lose their
        // instrumentation, so storage counters alone do not prove coverage.
        let recording_setup = recording_db.clone();
        let query = openworker_observability::query::Query {
            deployment: cfg.schema.clone(),
            scopes: vec![openworker_observability::query::Scope {
                session_hash: openworker_runtime::bundle::digest(reference.session_id.as_bytes()),
                turn_hash: openworker_runtime::bundle::digest(run.id.as_bytes()),
            }],
            after: 0,
            through: None,
            limit: 1000,
        };
        tokio::task::spawn_blocking(move || {
            use openworker_observability::Store as RecordingStore;
            let mut recordings =
                openworker_observability::postgres::Postgres::open(&recording_setup).unwrap();
            let deadline = std::time::Instant::now() + Duration::from_secs(10);
            loop {
                let rows = recordings.timeline(&query).unwrap();
                if ["model.attempt", "tool.execute", "agent.execute"]
                    .iter()
                    .all(|op| rows.steps.iter().any(|r| r.step.operation == *op))
                {
                    break;
                }
                assert!(
                    std::time::Instant::now() < deadline,
                    "Catalog worker lost model/tool/turn instrumentation; observed operations: {:?}",
                    rows.steps.iter().map(|r|&r.step.operation).collect::<Vec<_>>()
                );
                std::thread::sleep(Duration::from_millis(100));
            }
        })
        .await
        .unwrap();
        let env = service.environment(&environment_id).await.unwrap();
        assert_eq!(env["runtimeProfile"], "team@1");
        assert_eq!(env["principal"], "alice");
        // Private dynamic types reuse this original sandbox. Two independent
        // controllers race deterministic compilation; a replacement worker must
        // reconstruct the same approved runtime before the next child can run.
        let lead=coder.new_session_with(openworker_sdk::service::SessionOptions {
            environment_id:Some(environment_id.clone()),project:Some("game".into()),..Default::default()
        },Some("private-type-lead")).await.unwrap();
        let team=lead.create_team(serde_json::from_value(json!({"agentId":"lead","agentTypes":{"verifier":"verifier@1","builder":"coder@1"},"policy":{"communication":{"board":true,"directMessages":false},"max_instances":8},"limits":null,"delegation":{"tools":["coding.read_file"],"placements":["team"],"models":[],"profiles":{}}})).unwrap()).await.unwrap();
        let define=lead.send(&plan("team_define_type",json!({"name":"reader","instructions":"Read the requested file and return evidence. Do not write.","tools":["coding.read_file"]})),Some("define-reader")).await.unwrap();
        assert_eq!(define.result().await.unwrap().status,"completed");
        let member=team.spawn("spawn-reader-one","reader","reader-one",&plan("read_file",json!({"path":"shared-proof.txt"}))).await.unwrap();
        let child_id=member["instance"]["session_id"].as_str().unwrap();
        let waiting=lead.send(&plan("team_wait_agent",json!({"agent_ids":["reader-one"],"timeout_ms":120000})),Some("wait-reader-one")).await.unwrap();
        assert_eq!(waiting.result().await.unwrap().status,"completed");
        let child=service.json("GET",&format!("/v1/sessions/{child_id}"),None,None).await.unwrap();
        assert_eq!(child["session"]["environment"]["environment_id"],environment_id);
        assert!(child["session"]["coworker"].as_str().unwrap().starts_with("tt_"));
        let evidence=service.json("GET",&format!("/v1/sessions/{child_id}/events/page?after=0"),None,None).await.unwrap();
        assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && e["data"]["name"]=="read_file" && e.to_string().contains("coder output for verifier")),"{evidence}");
        worker.kill().await.unwrap();let _=worker.wait().await;worker=role("service-worker");
        let member=team.spawn("spawn-reader-two","reader","reader-two",&plan("read_file",json!({"path":"shared-proof.txt"}))).await.unwrap();
        let second_id=member["instance"]["session_id"].as_str().unwrap();
        let waiting=lead.send(&plan("team_wait_agent",json!({"agent_ids":["reader-two"],"timeout_ms":120000})),Some("wait-reader-two")).await.unwrap();
        assert_eq!(waiting.result().await.unwrap().status,"completed");
        let second=service.json("GET",&format!("/v1/sessions/{second_id}"),None,None).await.unwrap();
        assert_eq!(second["session"]["coworker"],child["session"]["coworker"]);
        let evidence=service.json("GET",&format!("/v1/sessions/{second_id}/events/page?after=0"),None,None).await.unwrap();
        assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && e["data"]["name"]=="read_file" && e.to_string().contains("coder output for verifier")),"{evidence}");
        assert_eq!(store.service_team_type_page(&openworker_server_reference::management::installation(&cfg).unwrap(),"",8).unwrap().len(),1);
        eprintln!("PASS approved private type, original shared OpenShell sandbox, two controllers, worker restart and real child file reads");
        let initialized=lead.send(&plan("run_shell",json!({"command":"mkdir source && cd source && git init -q && printf 'base\\n' > proof.txt && git add proof.txt && git -c user.name=Fixture -c user.email=f@example.invalid commit -q -m base","yield_ms":10000})),Some("initialize-source")).await.unwrap();
        assert_eq!(initialized.result().await.unwrap().status,"completed");
        let mut builders=vec![];
        for id in ["ui","api"] {
            let task=format!("TEST_RESOURCE_GRANTS {}",json!([
                {"name":"write_file","arguments":{"path":"proof.txt","content":format!("{id} change")}},
                {"name":"git_status","arguments":{}},
                {"name":"run_shell","arguments":{"command":format!("pwd && git add proof.txt && git -c user.name=Fixture -c user.email=f@example.invalid commit -q -m '{id} change' && git rev-parse HEAD > submitted.sha"),"yield_ms":10000}},
                {"name":"write_file","arguments":{"path":"../sibling-escape.txt","content":"must be rejected"}}
            ]));
            let child=team.spawn_with_options(&format!("worktree-{id}"),"builder",id,&task,serde_json::from_value(json!({"workspace":{"mode":"worktree","path":format!("workers/{id}"),"repository":"source","branch":id}})).unwrap()).await.unwrap();
            builders.push((id,child["instance"]["session_id"].as_str().unwrap().to_owned()));
        }
        let waited=lead.send(&format!("TEST_RESOURCE_GRANTS {}",json!([{"name":"team_wait_agent","arguments":{"agent_ids":["ui"],"timeout_ms":120000}},{"name":"team_wait_agent","arguments":{"agent_ids":["api"],"timeout_ms":120000}}])),Some("wait-builders")).await.unwrap();
        assert_eq!(waited.result().await.unwrap().status,"completed");
        for (id,sid) in &builders {
            let child=service.json("GET",&format!("/v1/sessions/{sid}"),None,None).await.unwrap();
            assert_eq!(child["session"]["workspace"],format!("/sandbox/workers/{id}"));
            assert_eq!(child["session"]["environment"]["remote_id"],original.remote_id);
            let evidence=service.json("GET",&format!("/v1/sessions/{sid}/events/page?after=0"),None,None).await.unwrap();
            let events=evidence["events"].as_array().unwrap();
            assert!(events.iter().any(|e| e["type"]=="tool_finished" && e["data"]["name"]=="git_status" && e["data"]["result"]["isError"]!=true),"{evidence}");
            assert!(events.iter().any(|e| e["type"]=="tool_finished" && e["data"]["name"]=="run_shell" && tool_data(e)["exit_code"]==0 && tool_data(e)["stdout"].as_str().is_some_and(|s|s.contains(&format!("/sandbox/workers/{id}")))),"Builder did not commit successfully: {evidence}");
            assert!(events.iter().any(|e| e["type"]=="tool_finished" && e["data"]["name"]=="write_file" && e["data"]["result"]["denied"]==true && e["data"]["result"]["executed"]==false),"Traversal was not rejected before dispatch");
            assert!(evidence.to_string().contains(&format!("/sandbox/workers/{id}")),"{evidence}");
        }
        // A verifier intentionally reuses the selected directory, not a linked
        // worktree in a separate sandbox. Lead integration remains explicit.
        let verifier=team.spawn_with_options("verify-worktree","verifier","exact-verifier",&plan("run_shell",json!({"command":"test \"$(git rev-parse HEAD)\" = \"$(cat submitted.sha)\" && test \"$(cat proof.txt)\" = 'ui change' && printf 'verified exact submission'","yield_ms":10000})),serde_json::from_value(json!({"workspace":{"mode":"directory","path":"workers/ui"}})).unwrap()).await.unwrap();
        let waited=lead.send(&plan("team_wait_agent",json!({"agent_ids":["exact-verifier"],"timeout_ms":120000})),Some("wait-exact-verifier")).await.unwrap();assert_eq!(waited.result().await.unwrap().status,"completed");
        let vsid=verifier["instance"]["session_id"].as_str().unwrap();
        let evidence=service.json("GET",&format!("/v1/sessions/{vsid}/events/page?after=0"),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && tool_data(e)["exit_code"]==0 && tool_data(e)["stdout"]=="verified exact submission"),"{evidence}");
        let inspect=lead.send(&plan("run_shell",json!({"command":"test ! -e workers/sibling-escape.txt && test \"$(cat workers/api/proof.txt)\" = 'api change' && test \"$(cat workers/ui/proof.txt)\" = 'ui change' && git -C source show ui:proof.txt && git -C source show api:proof.txt","yield_ms":10000})),Some("inspect-builder-commits")).await.unwrap();assert_eq!(inspect.result().await.unwrap().status,"completed");
        let evidence=service.json("GET",&format!("/v1/sessions/{}/events/page?after=0",lead.id()),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && tool_data(e)["exit_code"]==0 && tool_data(e)["stdout"].as_str().is_some_and(|s|s.contains("ui change") && s.contains("api change"))));
        eprintln!("PASS two shared-sandbox worktrees, child cwd/file scope, registered Git metadata, exact-commit verifier and lead inspection");
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
        pg.batch_execute(&format!(
            "DROP SCHEMA {} CASCADE; DROP SCHEMA {} CASCADE",
            db.schema, recording_db.schema
        ))
        .unwrap();
    })
    .await
    .unwrap();
    if let Err(panic) = outcome {
        std::panic::resume_unwind(panic);
    }
    eprintln!("PASS immutable releases, resource profile, two controllers, bounded worker, shared coder/verifier, Rust and TypeScript SDKs");
}
