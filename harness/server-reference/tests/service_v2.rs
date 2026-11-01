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

/// wait_agent may wake on any inbox message. Qualification waits on durable
/// child outcome too, rather than treating a completed lead turn as child proof.
async fn completed_member(team: &openworker_sdk::service::AgentTeam, agent: &str) {
    tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let roster = team.members(None, 32).await.unwrap();
            let row = roster["members"]
                .as_array()
                .unwrap()
                .iter()
                .find(|r| r["instance"]["agent_id"] == agent)
                .unwrap();
            assert!(row["error"].is_null(), "Child failed admission: {row}");
            if row["last_outcome"] == "completed" {
                break;
            }
            assert!(
                !matches!(
                    row["last_outcome"].as_str(),
                    Some("failed" | "interrupted" | "cancelled")
                ),
                "Child failed: {row}"
            );
            tokio::time::sleep(Duration::from_millis(150)).await;
        }
    })
    .await
    .unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable OW_PG_TEST_URL and OW_PROBE_* local OpenShell"]
async fn releases_profiles_provisioning_and_shared_verifier() {
    tokio::time::timeout(Duration::from_secs(720), qualify())
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
    let bindings: serde_json::Map<String, Value> = openworker_runtime::coding::schemas()
        .into_iter()
        .map(|s| {
            let name = s["name"].as_str().unwrap().to_owned();
            (name.clone(), json!({"server":"coding","tool":name}))
        })
        .collect();
    let tools = json!({"version":2,"servers":[{"id":"coding","program":"$OPENWORKER","args":["mcp-coding","--workspace","$WORKSPACE"]}],"bindings":bindings});
    let manifest = bundle.join("coworker/manifest.md");
    std::fs::write(
        &manifest,
        std::fs::read_to_string(&manifest).unwrap().replace(
            "requires_folder: true",
            "requires_folder: true\nplanning: true",
        ),
    )
    .unwrap();
    std::fs::write(
        bundle.join("tools.json"),
        serde_json::to_vec(&tools).unwrap(),
    )
    .unwrap();

    let mut value = json!({"service":{"id":"m2-test","namespace":"test","agent":"builder"},"database_env":"OW_PG_TEST_URL","schema":format!("ow_service_{}",uuid::Uuid::new_v4().simple()),"observability":{"schema":format!("ow_record_{}",uuid::Uuid::new_v4().simple())},"database_loopback_plaintext":true,"state_root":root.path().join("state"),"bundle":bundle,"runner":std::env::var("OW_PROBE_RUNNER").unwrap(),"model":"fixture","scripted":true,"api_token_file":token_file,"actor":"tester","openshell":{"identity":"m2-test","endpoint":std::env::var("OW_PROBE_ENDPOINT").unwrap(),"certificates":std::env::var("OW_PROBE_CERTS").unwrap(),"tls_server_name":std::env::var("OW_PROBE_TLS_NAME").ok(),"workspace":"default","image":std::env::var("OW_PROBE_IMAGE").unwrap()}});
    let artifact_root = root.path().join("artifacts");
    std::fs::create_dir(&artifact_root).unwrap();
    value["artifacts"] = json!({"kind":"filesystem","root":artifact_root,"deployment":"coding-handoff-test","shared_durable":true});
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
    let small:openworker_sdk::service::RuntimeProfile=serde_json::from_value(json!({"id":"small@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared","approvalMode":"bypass","autoSkipQuestions":true,"allowedPrincipals":["alice"]})).unwrap();
    admin
        .register_runtime_profile(&small, "small-create")
        .await
        .unwrap();
    admin
        .activate_runtime_profile(&small.id, None, "small-activate")
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
        let release:openworker_sdk::service::AgentRelease=serde_json::from_value(json!({"id":id,"instructions":format!("You are {id}. Work in your shared project."),"tools":tools,"runtimeProfiles":["team@1","small@1"],"defaultRuntimeProfile":if id=="coder@1"{Some("team@1")}else{None},"allowedPrincipals":["alice"]})).unwrap();
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
        let team=lead.create_team(serde_json::from_value(json!({"agentId":"lead","agentTypes":{"verifier":"verifier@1","builder":"coder@1"},"policy":{"communication":{"board":true,"directMessages":false},"max_instances":16},"limits":{"maxModelCalls":256,"maxOutputTokens":2000000,"summaryTokens":512},"delegation":{"tools":["coding.read_file"],"placements":["team","member","new"],"models":[],"profiles":{"small@1":{"files":[],"network":[],"credentials":[],"cpu":1,"memory_mb":512}}}})).unwrap()).await.unwrap();
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
        // Two instances of one prebuilt type receive independent sandboxes.
        // A private verifier then reuses the first builder's original sandbox.
        controller.kill().await.unwrap();let _=controller.wait().await;
        other_controller.kill().await.unwrap();let _=other_controller.wait().await;
        let isolated=team.spawn_with_options("isolated-a","builder","isolated-a",&plan("write_file",json!({"path":"build/proof.txt","content":"isolated builder evidence"})),serde_json::from_value(json!({"sandbox":{"mode":"new","profile":"small@1"}})).unwrap()).await.unwrap();
        // Commit the create marker and let the real provider create the child,
        // then deliberately lose its reply before saving a binding. Restarted
        // controllers must inspect this exact original object, never create again.
        let target=store.catalog_get("profile","small@1").unwrap();
        let child_provider=openworker_server_reference::management::provider(&cfg,&target).unwrap();
        store.reserve_team_environment(lead.id(),"isolated-a",target,child_provider.spec(openworker_runtime::environment::Sharing::Shared,openworker_runtime::environment::Management::Harness)).unwrap();
        let installed=openworker_server_reference::management::installation(&cfg).unwrap();
        let lost=store.claim_team_environment(&installed).unwrap().unwrap();assert!(lost.dispatch);
        let original_child=child_provider.create(lost.environment.clone()).await.unwrap();
        let dbcopy=db.clone();let lost_env=lost.environment.id.clone();
        tokio::task::spawn_blocking(move||{
            let mut pg=postgres::Client::connect(&dbcopy.connection,postgres::NoTls).unwrap();
            pg.execute(&format!("UPDATE {}.service_team_environments SET next_check=clock_timestamp()-interval '1 second' WHERE environment_id=$1",dbcopy.schema),&[&lost_env]).unwrap();
        }).await.unwrap();
        controller=role("service-controller");other_controller=role("service-controller");
        let independent=team.spawn_with_options("isolated-b","builder","isolated-b",&plan("run_shell",json!({"command":"test ! -e build/proof.txt && pwd && printf 'independent sandbox'","yield_ms":10000})),serde_json::from_value(json!({"sandbox":{"mode":"new","profile":"small@1"}})).unwrap()).await.unwrap();
        let waited=lead.send(&format!("TEST_RESOURCE_GRANTS {}",json!([{"name":"team_wait_agent","arguments":{"agent_ids":["isolated-a"],"timeout_ms":120000}},{"name":"team_wait_agent","arguments":{"agent_ids":["isolated-b"],"timeout_ms":120000}}])),Some("wait-isolated-builders")).await.unwrap();assert_eq!(waited.result().await.unwrap().status,"completed");
        completed_member(&team,"isolated-a").await;completed_member(&team,"isolated-b").await;
        let asid=isolated["instance"]["session_id"].as_str().unwrap();let bsid=independent["instance"]["session_id"].as_str().unwrap();
        let astate=service.json("GET",&format!("/v1/sessions/{asid}"),None,None).await.unwrap();
        let bstate=service.json("GET",&format!("/v1/sessions/{bsid}"),None,None).await.unwrap();
        let aenv=&astate["session"]["environment"];let benv=&bstate["session"]["environment"];
        assert!(aenv["environment_id"].is_string() && benv["environment_id"].is_string());
        assert_ne!(aenv["environment_id"],benv["environment_id"]);assert_ne!(aenv["environment_id"],environment_id);assert_ne!(aenv["remote_id"],benv["remote_id"]);
        assert_eq!(aenv["remote_id"],original_child.remote_id);
        assert_eq!(astate["session"]["workspace"],"/sandbox");
        let ap=store.catalog_get("profile","small@1").unwrap();let ap=openworker_server_reference::management::provider(&cfg,&ap).unwrap();
        assert_eq!(serde_json::from_value::<openworker_runtime::environment::Binding>(aenv.clone()).unwrap().spec.profile_digest,ap.spec(openworker_runtime::environment::Sharing::Shared,openworker_runtime::environment::Management::Harness).profile_digest);
        let evidence=service.json("GET",&format!("/v1/sessions/{bsid}/events/page?after=0"),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && tool_data(e)["exit_code"]==0 && tool_data(e)["stdout"].as_str().is_some_and(|s|s.contains("independent sandbox"))),"{evidence}");
        let define=lead.send(&plan("team_define_type",json!({"name":"isolated-reader","instructions":"Read original builder evidence. No writes.","tools":["coding.read_file"],"sandbox":{"mode":"member","agent_id":"isolated-a"}})),Some("define-isolated-reader")).await.unwrap();assert_eq!(define.result().await.unwrap().status,"completed");
        let verifier=team.spawn_with_options("isolated-reader","isolated-reader","isolated-verifier",&plan("read_file",json!({"path":"proof.txt"})),serde_json::from_value(json!({"workspace":{"mode":"directory","path":"build"}})).unwrap()).await.unwrap();
        let waited=lead.send(&plan("team_wait_agent",json!({"agent_ids":["isolated-verifier"],"timeout_ms":120000})),Some("wait-isolated-verifier")).await.unwrap();assert_eq!(waited.result().await.unwrap().status,"completed");
        completed_member(&team,"isolated-verifier").await;
        let vsid=verifier["instance"]["session_id"].as_str().unwrap();
        let vstate=service.json("GET",&format!("/v1/sessions/{vsid}"),None,None).await.unwrap();assert_eq!(vstate["session"]["environment"],*aenv);assert_eq!(vstate["session"]["workspace"],"/sandbox/build");
        let evidence=service.json("GET",&format!("/v1/sessions/{vsid}/events/page?after=0"),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && e["data"]["name"]=="read_file" && e.to_string().contains("isolated builder evidence")),"{evidence}");
        let checked=lead.send(&plan("run_shell",json!({"command":"test ! -e build/proof.txt && printf 'lead sandbox unchanged'","yield_ms":10000})),Some("check-lead-isolation")).await.unwrap();assert_eq!(checked.result().await.unwrap().status,"completed");
        let evidence=service.json("GET",&format!("/v1/sessions/{}/events/page?after=0",lead.id()),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && e["turn_id"]==checked.id && tool_data(e)["exit_code"]==0 && tool_data(e)["stdout"]=="lead sandbox unchanged"));
        eprintln!("PASS independent child sandboxes, bounded smaller profile, selected child cwd, private member verifier and preserved lead filesystem");
        let initialized=lead.send(&plan("run_shell",json!({"command":"mkdir source && cd source && git init -q && printf 'base\\n' > proof.txt && git add proof.txt && git -c user.name=Fixture -c user.email=f@example.invalid commit -q -m base && git rev-parse HEAD","yield_ms":10000})),Some("initialize-source")).await.unwrap();
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
            completed_member(&team,id).await;
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
        completed_member(&team,"exact-verifier").await;
        let vsid=verifier["instance"]["session_id"].as_str().unwrap();
        let evidence=service.json("GET",&format!("/v1/sessions/{vsid}/events/page?after=0"),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && tool_data(e)["exit_code"]==0 && tool_data(e)["stdout"]=="verified exact submission"),"{evidence}");
        let inspect=lead.send(&plan("run_shell",json!({"command":"test ! -e workers/sibling-escape.txt && test \"$(cat workers/api/proof.txt)\" = 'api change' && test \"$(cat workers/ui/proof.txt)\" = 'ui change' && git -C source show ui:proof.txt && git -C source show api:proof.txt","yield_ms":10000})),Some("inspect-builder-commits")).await.unwrap();assert_eq!(inspect.result().await.unwrap().status,"completed");
        let evidence=service.json("GET",&format!("/v1/sessions/{}/events/page?after=0",lead.id()),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && tool_data(e)["exit_code"]==0 && tool_data(e)["stdout"].as_str().is_some_and(|s|s.contains("ui change") && s.contains("api change"))));
        eprintln!("PASS two shared-sandbox worktrees, child cwd/file scope, registered Git metadata, exact-commit verifier and lead inspection");
        let integrate=lead.send(&plan("run_shell",json!({"command":"git -C source merge --ff-only ui && if git -C source -c user.name=Fixture -c user.email=f@example.invalid cherry-pick api; then exit 19; else git -C source rev-parse --verify CHERRY_PICK_HEAD >/dev/null && test \"$(git -C source diff --name-only --diff-filter=U)\" = proof.txt && git -C source cherry-pick --abort; fi && test \"$(git -C source rev-parse HEAD)\" = \"$(cat workers/ui/submitted.sha)\" && test \"$(cat source/proof.txt)\" = 'ui change' && printf 'verified integration and explicit conflict'","yield_ms":10000})),Some("explicit-integration-conflict")).await.unwrap();assert_eq!(integrate.result().await.unwrap().status,"completed");
        let evidence=service.json("GET",&format!("/v1/sessions/{}/events/page?after=0",lead.id()),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && e["turn_id"]==integrate.id && tool_data(e)["exit_code"]==0 && tool_data(e)["stdout"].as_str().is_some_and(|s|s.contains("verified integration and explicit conflict"))));
        let evidence=service.json("GET",&format!("/v1/sessions/{}/events/page?after=0",lead.id()),None,None).await.unwrap();
        let base=evidence["events"].as_array().unwrap().iter().find(|e|e["type"]=="tool_finished" && e["turn_id"]==initialized.id && tool_data(e)["exit_code"]==0).map(|e|tool_data(e)["stdout"].as_str().unwrap().trim().to_owned()).unwrap();
        use openworker_sdk::agents::{coding,Approvals};
        let mut preset=coding::shared("preset","source",&base).unwrap();
        preset.lead=preset.lead.approvals(Approvals::Never).auto_skip_questions(true);
        preset.builder=preset.builder.approvals(Approvals::Never).auto_skip_questions(true);
        preset.verifier=preset.verifier.approvals(Approvals::Never).auto_skip_questions(true);
        let profile:openworker_sdk::service::RuntimeProfile=serde_json::from_value(json!({"id":"team@1","provider":"openshell","cpu":1,"memoryMB":768,"sharing":"shared","approvalMode":"bypass","autoSkipQuestions":true,"allowedPrincipals":["alice"]})).unwrap();
        for agent in preset.agents(){
            let release=coding::service_release(&agent,"1",&profile).unwrap();
            admin.deploy_agent(&release,&format!("preset-deploy-{}",agent.id())).await.unwrap();
            admin.activate_agent(&release.id,None,&format!("preset-activate-{}",agent.id())).await.unwrap();
        }
        let preset_agent=service.agent("preset-lead@1").await.unwrap();
        let preset_lead=preset_agent.new_session_with(openworker_sdk::service::SessionOptions{environment_id:Some(environment_id.clone()),project:Some("game".into()),..Default::default()},Some("preset-lead-session")).await.unwrap();
        let preset_team=preset_lead.create_team(preset.service_team("1")).await.unwrap();
        // C4 source-staging slice: the application pins source before starting
        // the lead. The actual trusted controller exports/publishes; no fake
        // model turn or sandbox artifact-store credential is introduced.
        let backend=cfg.artifacts.as_ref().unwrap().build().unwrap();
        let source=store.authorized(&user_token).unwrap().configure_team_source(preset_lead.id(),serde_json::from_value(json!({"repository":"org/game","directory":"source","object_format":"sha1","commit":base})).unwrap(),backend.binding().clone()).unwrap();
        let artifact=tokio::time::timeout(Duration::from_secs(60),async {
            loop {
                if let Some(a)=store.artifacts(preset_lead.id()).unwrap().into_iter().find(|a|matches!(&a.registration.origin,openworker_artifacts::Origin::TeamSetup {setup_id,..} if *setup_id==source.id) && a.status==openworker_artifacts::Status::Ready) {break a}
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }).await.unwrap();
        assert!(artifact.registration.turn_id.is_empty());
        let response=reqwest::Client::new().get(format!("{url_a}/v1/sessions/{}/artifacts/{}/content",preset_lead.id(),artifact.id)).bearer_auth(&user_token).send().await.unwrap();
        assert!(response.status().is_success(),"Source artifact download: {}",response.status());
        let bytes=response.bytes().await.unwrap();
        assert_eq!(openworker_runtime::bundle::digest(&bytes),artifact.registration.expected.sha256);
        let source_manifest:openworker_runtime::coding_handoff::Manifest=serde_json::from_value(json!({"version":1,"kind":"source","repository":"org/game","producer":{"team_id":source.team,"agent_id":"lead","session_id":preset_lead.id()},"object_format":"sha1","base_commit":base,"submitted_commit":base,"artifact":{"session_id":preset_lead.id(),"artifact_id":artifact.id,"size":bytes.len(),"sha256":artifact.registration.expected.sha256},"reports":[]})).unwrap();
        let exported=openworker_runtime::coding_handoff::git::Bundle {request:source.export.clone(),path:format!(".openworker/handoffs/{}/source.bundle",source.id),size:bytes.len() as u64,sha256:artifact.registration.expected.sha256.clone(),advertised_ref:format!("refs/openworker/handoffs/{}",source.id)};
        // These explicit private calls qualify receiver mechanics in physically
        // distinct filesystems. Durable child-source/default admission and model
        // submission tools remain the next C4 slice, not implied by this test.
        use base64::{engine::general_purpose::STANDARD,Engine};
        for (id,env) in [("builder",aenv),("verifier",benv)] {
            let receiver=openworker_runtime::coding_handoff::transfer::Transfer {request:openworker_runtime::coding_handoff::Import {operation_id:format!("source-import-{id}"),scope_digest:openworker_runtime::bundle::digest(format!("{}:{id}",source.id).as_bytes()),manifest_digest:source_manifest.digest().unwrap(),directory:"artifact-source".into()},manifest:source_manifest.clone(),bundle:exported.clone()};
            let binding=serde_json::from_value(env.clone()).unwrap();
            let mut channel=ap.attach(binding,uuid::Uuid::new_v4().to_string(),vec!["/opt/openworker/openworker".into(),"workspace-stdio".into(),"--root".into(),"/sandbox".into()]).await.unwrap();
            channel.request("handoff/prepare",json!(receiver)).await.unwrap();
            for (index,chunk) in bytes.chunks(openworker_runtime::coding_handoff::transfer::CHUNK).enumerate() {
                channel.request("handoff/write",json!({"handle":receiver.handle(),"offset":index*openworker_runtime::coding_handoff::transfer::CHUNK,"data":STANDARD.encode(chunk)})).await.unwrap();
            }
            let imported=channel.request("handoff/finish",json!(receiver.handle())).await.unwrap();
            assert_eq!(imported["commit"],base);assert_eq!(imported["path"],"/sandbox/artifact-source");
            assert_eq!(channel.request("handoff/inspect_checkout",json!(receiver.handle())).await.unwrap(),imported);
            let _=channel.close().await;
        }
        eprintln!("PASS C4 trusted setup export, authorized artifact publication/download and independent Git checkout in two separate OpenShell sandboxes (controller/default admission still pending)");

        let child=preset_team.spawn("preset-build","preset-builder","ui",&plan("write_file",json!({"path":"proof.txt","content":"preset-scoped change"}))).await.unwrap();
        assert_eq!(child["workspace"]["mode"],"worktree");assert_eq!(child["workspace"]["base"],base);
        let waited=preset_lead.send(&plan("team_wait_agent",json!({"agent_ids":["ui"],"timeout_ms":120000})),Some("wait-preset-builder")).await.unwrap();assert_eq!(waited.result().await.unwrap().status,"completed");
        completed_member(&preset_team,"ui").await;
        let sid=child["instance"]["session_id"].as_str().unwrap();
        let selected=service.json("GET",&format!("/v1/sessions/{sid}"),None,None).await.unwrap();assert_eq!(selected["session"]["workspace"],format!("/sandbox/{}",child["workspace"]["path"].as_str().unwrap()));
        let read=preset_lead.send(&plan("read_file",json!({"path":format!("{}/proof.txt",child["workspace"]["path"].as_str().unwrap())})),Some("read-preset-proof")).await.unwrap();assert_eq!(read.result().await.unwrap().status,"completed");
        let evidence=service.json("GET",&format!("/v1/sessions/{}/events/page?after=0",preset_lead.id()),None,None).await.unwrap();assert!(evidence["events"].as_array().unwrap().iter().any(|e|e["type"]=="tool_finished" && e["data"]["name"]=="read_file" && e.to_string().contains("preset-scoped change")));
        eprintln!("PASS versioned coding preset releases, RuntimeProfile compatibility and automatically scoped service builder worktree");
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
    let connection = db.connection.clone();
    let schema = db.schema.clone();
    let environment_ids: Vec<(String, String)> = tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&connection, postgres::NoTls).unwrap();
        pg.query(
            &format!(
                "SELECT environment_id,profile_id FROM {}.service_environment_owners",
                schema
            ),
            &[],
        )
        .unwrap()
        .iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect()
    })
    .await
    .unwrap();
    for (id, profile) in environment_ids {
        let p = store.catalog_get("profile", &profile).unwrap();
        let provider = openworker_server_reference::management::provider(&cfg, &p).unwrap();
        let env = store.clone().environment(&id).unwrap();
        // Cleanup includes a create whose acknowledgement was deliberately lost.
        // Reconstruct only its original identity; cleanup never creates/rebinds.
        let binding = if let Some(b) = env.binding.clone() {
            Some(b)
        } else {
            let raw = env.id.strip_prefix("env_").unwrap();
            provider
                .bind_existing(&env, &format!("owr-{}", &raw[..15]))
                .await
                .ok()
        };
        if let Some(binding) = binding {
            let key = uuid::Uuid::new_v4().to_string();
            provider.delete(binding.clone(), key).await.unwrap();
            assert!(
                provider.confirm_deleted(binding).await.unwrap(),
                "Original fixture sandbox still exists"
            );
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
