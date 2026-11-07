//! C5 full private-source -> separate sandbox -> native publication qualification.
//! Disposable file remote tests service wiring; separate HTTPS tests cover auth.
use super::*;
use openworker_runtime::store::StateStore;
fn plan(name: &str, args: Value) -> String {
    let name = if [
        "run_shell",
        "read_file",
        "write_file",
        "git_status",
        "git_log",
    ]
    .contains(&name)
    {
        format!("coding__{name}")
    } else {
        name.into()
    };
    super::plan(&name, args)
}

fn local_git(root: &Path, args: &[&str]) -> String {
    let output = std::process::Command::new("/usr/bin/git")
        .current_dir(root)
        .args([
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
        ])
        .args(args)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .output()
        .unwrap();
    assert!(output.status.success(), "Controlled Git command failed");
    String::from_utf8(output.stdout).unwrap().trim().into()
}
async fn cleanup(cfg: &Config, db: &openworker_store_postgres::Config) {
    assert!(
        db.schema.starts_with("ow_broker_")
            && db.schema[10..].bytes().all(|b| b.is_ascii_hexdigit())
    );
    let store = PostgresStore::open(db.clone()).unwrap();
    let identities: Vec<(String, String)> = tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        pg.query(
            &format!(
                "SELECT environment_id,profile_id FROM {}.service_environment_owners",
                db.schema
            ),
            &[],
        )
        .unwrap()
        .iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect()
    });
    for (id, profile) in identities {
        let env = store.clone().environment(&id).unwrap();
        let entry = store.catalog_get("profile", &profile).unwrap();
        let provider = openworker_server_reference::management::provider(cfg, &entry).unwrap();
        let binding = if let Some(b) = env.binding.clone() {
            Some(b)
        } else {
            provider
                .bind_existing(
                    &env,
                    &format!("owr-{}", &id.strip_prefix("env_").unwrap()[..15]),
                )
                .await
                .ok()
        };
        if let Some(binding) = binding {
            provider
                .delete(binding.clone(), uuid::Uuid::new_v4().to_string())
                .await
                .unwrap();
            assert!(provider.confirm_deleted(binding).await.unwrap());
        }
    }
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", db.schema))
            .unwrap();
    });
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable PostgreSQL and local OpenShell"]
async fn trusted_source_and_registered_publication() {
    qualify(false, false).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable PostgreSQL and local OpenShell"]
async fn parallel_mixed_team_qualification() {
    qualify(true, false).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires explicit live model ID/key env plus local PostgreSQL/OpenShell"]
async fn bounded_live_coding_team() {
    qualify(false, true).await;
}
async fn qualify(parallel: bool, live: bool) {
    let root = tempfile::tempdir().unwrap();
    let root_path = root.path().canonicalize().unwrap();
    let source = root_path.join("seed");
    std::fs::create_dir(&source).unwrap();
    local_git(&source, &["init", "-q", "-b", "main"]);
    std::fs::write(source.join("proof.txt"), "base\n").unwrap();
    local_git(&source, &["add", "proof.txt"]);
    local_git(&source, &["commit", "-qm", "base"]);
    let base = local_git(&source, &["rev-parse", "HEAD"]);
    let remote = root_path.join("remote.git");
    local_git(
        &root_path,
        &[
            "clone",
            "--bare",
            "-q",
            source.to_str().unwrap(),
            remote.to_str().unwrap(),
        ],
    );
    let bundle = root_path.join("bundle");
    std::fs::create_dir_all(bundle.join("coworker")).unwrap();
    std::fs::write(bundle.join("coworker/manifest.md"),"---\nid: broker-test\nname: Broker test\nplanning: true\n---\nWork only within granted permissions.").unwrap();
    let coding: Vec<String> = [
        "read_file",
        "write_file",
        "run_shell",
        "git_status",
        "git_log",
    ]
    .into_iter()
    .map(|n| format!("coding.{n}"))
    .collect();
    let mut tools = coding.clone();
    tools.push("github_broker.publish_code".into());
    std::fs::write(
        bundle.join("tools.json"),
        serde_json::to_vec(&json!({"version":3,"tools":tools})).unwrap(),
    )
    .unwrap();
    let token = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let token_file = root_path.join("token");
    std::fs::write(&token_file, &token).unwrap();
    let config = root_path.join("config.json");
    let mut value = json!({"service":{"id":"broker-test","namespace":"test","agent":"bootstrap-unused"},"database_env":"OW_PG_TEST_URL","schema":format!("ow_broker_{}",uuid::Uuid::new_v4().simple()),"database_loopback_plaintext":true,"state_root":root_path.join("state"),"bundle":bundle,"runner":std::env::var("OW_PROBE_RUNNER").unwrap(),"model":"fixture","scripted":true,"api_token_file":token_file,"actor":"tester","artifacts":{"kind":"filesystem","root":root_path.join("artifacts"),"deployment":"broker-test","shared_durable":true},"repository_grants":{"publisher@1":{"credential_env":"UNUSED_CONTROLLED_FILE_REMOTE","repositories":{"org/game":{"github":"org/game","read_refs":["refs/heads/main"],"write_prefixes":["agents/"]}}}},"repository_test_remotes":{"org/game":remote},"openshell":{"identity":"broker-test","endpoint":std::env::var("OW_PROBE_ENDPOINT").unwrap(),"certificates":std::env::var("OW_PROBE_CERTS").unwrap(),"tls_server_name":std::env::var("OW_PROBE_TLS_NAME").ok(),"workspace":"default","image":std::env::var("OW_PROBE_IMAGE").unwrap()}});
    if live {
        let model =
            std::env::var("OW_C7_LIVE_MODEL").expect("Provide an authorized pinned live model ID");
        assert!(std::env::var("ANTHROPIC_API_KEY").is_ok());
        value["model"] = json!(model);
        value["model_provider"] = json!("anthropic");
        value["scripted"] = json!(false);
    }
    std::fs::create_dir(root_path.join("artifacts")).unwrap();
    std::fs::write(&config, serde_json::to_vec(&value).unwrap()).unwrap();
    let cfg: Config = serde_json::from_value(value).unwrap();
    let db = cfg.database().unwrap();
    if let Ok(schema) = std::env::var("OW_CLEANUP_BROKER_SCHEMA") {
        let mut original = db.clone();
        original.schema = schema;
        cleanup(&cfg, &original).await;
    }
    PostgresStore::migrate(&db).unwrap();
    let store = PostgresStore::open(db.clone()).unwrap();
    store.auth_bootstrap(&token, "tester").unwrap();
    let (mut a, url) = api(&config, "broker-api").await;
    let mut worker = Command::new(BIN)
        .arg("--config")
        .arg(&config)
        .arg("service-worker")
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut controller = Command::new(BIN)
        .arg("--config")
        .arg(&config)
        .arg("service-controller")
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let outcome=std::panic::AssertUnwindSafe(tokio::time::timeout(Duration::from_secs(480),async {
        let admin=Harness::connect(options(&url,&token)).await.unwrap();
        let user_token=uuid::Uuid::new_v4().simple().to_string().repeat(2);store.authorized(&token).unwrap().auth_issue("user",serde_json::from_value(json!({"principal":"alice","role":"operator","scope":{"kind":"owned"}})).unwrap(),&user_token).unwrap();
        for (id,grant,memory) in [("team@1",Some("publisher@1"),768),("small@1",None,512)] {
            let profile:openworker_sdk::service::RuntimeProfile=serde_json::from_value(json!({"id":id,"provider":"openshell","cpu":1,"memoryMB":memory,"sharing":"shared","approvalMode":"bypass","autoSkipQuestions":true,"allowedPrincipals":["alice"],"repositoryAccess":grant})).unwrap();admin.register_runtime_profile(&profile,&format!("register-{id}")).await.unwrap();admin.activate_runtime_profile(id,None,&format!("activate-{id}")).await.unwrap();
        }
        let release:openworker_sdk::service::AgentRelease=serde_json::from_value(json!({"id":"coder@1","instructions":"Use explicit granted tools.","tools":tools,"runtimeProfiles":["team@1","small@1"],"defaultRuntimeProfile":"team@1","allowedPrincipals":["alice"],"limits":{"maxModelCalls":24,"maxOutputTokens":120000,"summaryTokens":512}})).unwrap();admin.deploy_agent(&release,"deploy-coder").await.unwrap();admin.activate_agent("coder@1",Some("coder"),"activate-coder").await.unwrap();
        let service=Harness::connect(options(&url,&user_token)).await.unwrap();let coder=service.agent("coder").await.unwrap();let lead=coder.new_session_with(Default::default(),Some("broker-lead")).await.unwrap();let mut options=handoff::options(&base);options.coding.as_mut().unwrap().source.remote_ref=Some("refs/heads/main".into());if live{options.limits=serde_json::from_value(json!({"maxModelCalls":48,"maxOutputTokens":200000,"summaryTokens":512})).unwrap();} else if parallel{options.agent_types.insert("reader".into(),"coder@1".into());}let team=lead.create_team(options).await.unwrap();
        // May already be ready on an unusually fast host; otherwise first turn
        // must be rejected before it can observe a partially imported checkout.
        if team.snapshot().await.unwrap()["source_setup"]["status"]!="ready" {assert!(lead.send("Do not start before source setup",Some("too-early")).await.is_err());}
        tokio::time::timeout(Duration::from_secs(180),async {loop {let s=team.snapshot().await.unwrap();assert_ne!(s["source_setup"]["status"],"blocked","{s}");if s["source_setup"]["status"]=="ready"{break}tokio::time::sleep(Duration::from_millis(200)).await;}}).await.unwrap();
        assert_eq!(local_git(&remote,&["rev-parse","refs/heads/main"]),base);
        if live{super::live_team::qualify(&service,&lead,&team,&base,&cfg,&db,&root_path).await;return;}
        if parallel{super::parallel::qualify(&service,&coder,&lead,&team,&base,&config,&mut worker,&db).await;return;}
        let task=plan("run_shell",json!({"command":format!("test \"$(git rev-parse HEAD)\" = '{base}' && test -z \"$GITHUB_TOKEN\" && printf 'checkout-a\\n' > proof.txt && git add proof.txt && git -c user.name=Fixture -c user.email=f@example.invalid commit -qm child && printf child-ready"),"yield_ms":10000}));
        let builder=team.spawn("builder-one","builder","checkout-a",&task).await.unwrap();completed_member(&team,"checkout-a").await;
        let result=handoff::qualify(&service,&lead,&team,&builder,&base,"source","coding__").await;
        let id=result["submissionId"].as_str().unwrap();let commit=result["commit"].as_str().unwrap();
        let publish=lead.send(&plan("github_broker__publish_code",json!({"submission_id":id,"branch":"agents/test-result","expected_head":null})),Some("publish-result")).await.unwrap();assert_eq!(publish.result().await.unwrap().status,"completed");
        tokio::time::timeout(Duration::from_secs(90),async {loop {let rows=team.submissions(None,20).await.unwrap();let p=&rows[0]["publications"];if let Some(p)=p.as_array().and_then(|a|a.first()) {assert!(p["error"].is_null(),"{p}");if p["status"]=="published" {break}}tokio::time::sleep(Duration::from_millis(200)).await;}}).await.unwrap();
        assert_eq!(local_git(&remote,&["rev-parse","refs/heads/agents/test-result"]),commit);assert_eq!(local_git(&remote,&["rev-parse","refs/heads/main"]),base);
        let retry=lead.send(&plan("github_broker__publish_code",json!({"submission_id":id,"branch":"agents/test-result","expected_head":null})),Some("publish-again-new-call")).await.unwrap();assert_eq!(retry.result().await.unwrap().status,"completed");assert_eq!(team.submissions(None,20).await.unwrap()[0]["publications"].as_array().unwrap().len(),1);
        eprintln!("PASS C5 trusted remote source, durable lead import, independent builder/verifier, registered native publication, branch CAS and deduplication");
    })).catch_unwind().await;
    if outcome.is_err() {
        tokio::task::block_in_place(|| {
            let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
            for row in pg.query(&format!("SELECT data->>'error' FROM {}.events WHERE kind='turn_completed' AND data->>'status'='failed'",db.schema),&[]).unwrap(){eprintln!("Synthetic fixture turn error: {}",row.get::<_,Option<String>>(0).unwrap_or_default());}
        });
    }
    for p in [&mut worker, &mut controller, &mut a] {
        let _ = p.kill().await;
    }
    cleanup(&cfg, &db).await;
    match outcome {
        Err(panic) => std::panic::resume_unwind(panic),
        Ok(result) => result.expect("Bounded C5 workflow deadline exceeded"),
    }
}
