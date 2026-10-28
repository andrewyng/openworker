//! Real PostgreSQL and service HTTP with a browser-facing UI process. No model/tool
//! execution: fixture sessions exercise scoped reads and durable control admission.
#![cfg(unix)]
use openworker_runtime::store::{Session, StateStore};
use openworker_server_reference::{http::App, Config};
use openworker_store_postgres::{PostgresStore, SessionCreation};
use serde_json::json;
use std::{path::Path, time::Duration};
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable OW_PG_TEST_URL; optional OW_PLAYWRIGHT_MODULE for browser QA"]
async fn browser_management_boundary() {
    let temp = tempfile::tempdir().unwrap();
    let cfg:Config=serde_json::from_value(json!({"service":{"id":"ui-test","namespace":"test","agent":"base"},"database_env":"OW_PG_TEST_URL","schema":format!("ow_ui_{}",uuid::Uuid::new_v4().simple()),"database_loopback_plaintext":true,"state_root":temp.path(),"bundle":Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant"),"runner":std::env::var("OW_PROBE_RUNNER").expect("Linux ToolRunner path required"),"model":"fixture","scripted":true,"api_token_file":temp.path().join("unused-token"),"actor":"admin","openshell":{"identity":"ui-local","endpoint":"https://127.0.0.1:1","certificates":temp.path(),"workspace":"default","image":format!("runner@sha256:{}","a".repeat(64))}})).unwrap();
    let db = cfg.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let store = PostgresStore::open(db.clone()).unwrap();
    let admin = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    store.auth_bootstrap(&admin, "admin").unwrap();
    let a = store.authorized(&admin).unwrap();
    let user = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let manager = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    for (principal, role, key) in [
        ("alice", "operator", &user),
        ("manager", "service_administrator", &manager),
    ] {
        a.auth_issue(principal,serde_json::from_value(json!({"principal":principal,"role":role,"scope":{"kind":if role=="operator"{"owned"}else{"all"}},"expires_at":std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs()+600})).unwrap(),key).unwrap();
    }
    let install = openworker_server_reference::management::installation(&cfg).unwrap();
    a.catalog_put("profile","profile",json!({"id":"standard@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"exclusive","allowedPrincipals":["alice"]}),&install).unwrap();
    a.catalog_activate("pa", "profile", "standard@1", None)
        .unwrap();
    a.catalog_put("release","release",json!({"id":"reviewer@1","instructions":"Review changes carefully.","tools":["read_file"],"runtimeProfiles":["standard@1"],"allowedPrincipals":["alice"]}),&install).unwrap();
    a.catalog_activate("ra", "release", "reviewer@1", None)
        .unwrap();
    for (sid, principal) in [("s_alice", "alice"), ("s_bob", "bob")] {
        let s:Session=serde_json::from_value(json!({"id":sid,"name":if principal=="alice"{"Review checkout changes"}else{"Private Bob session"},"workspace":"/sandbox","coworker":"reviewer","bundle_digest":"test","scripted":true,"schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        store
            .create_session(
                SessionCreation {
                    request_id: format!("create-{sid}"),
                    actor: principal.into(),
                    parameters: json!({}),
                },
                s,
            )
            .unwrap();
    }
    let fixture_db = db.clone();
    tokio::task::spawn_blocking(move || {
        let mut client=postgres::Client::connect(&fixture_db.connection,postgres::NoTls).unwrap();
        client.batch_execute(&format!("SET search_path={},pg_catalog; INSERT INTO api_session_owners VALUES ('s_alice','alice'),('s_bob','bob')",fixture_db.schema)).unwrap();
    }).await.unwrap();
    store.claim("s_alice").unwrap();
    let (mut active, _, _) = store
        .clone()
        .accept("s_alice", "task", "Review the checkout changes")
        .unwrap();
    let run_id = active.turn.as_ref().unwrap().id.clone();
    active.schema_digest = Some("schema".into());
    active.tool_policy.digest = "policy".into();
    active.resource_policy.digest = "resource".into();
    let call = json!({"type":"function_call","call_id":"call_one","name":"read_file","arguments":"{\"path\":\"README.md\"}"});
    active.history.push(call.clone());
    active.turn.as_mut().unwrap().queue = vec![call.clone()];
    active.turn.as_mut().unwrap().status = "waiting".into();
    active.interactions.push(serde_json::from_value(json!({"id":"i_one","turn_id":run_id,"kind":"approval","call":call,"bundle_digest":"test","schema_digest":"schema","response":null,"resource_policy_digest":"resource","policy_digest":"policy"})).unwrap());
    store
        .clone()
        .save(
            &active,
            "interaction_required",
            json!({"interaction_id":"i_one"}),
        )
        .unwrap();
    store.service_bind_pool(serde_json::from_value(json!({"id":"default","namespace":"test","deployment":"worker","uid":"test-uid","ownership":"manual","maxReplicas":4})).unwrap(),1).unwrap();
    let rt = cfg
        .api_runtime("ui-api", PostgresStore::open(db.clone()).unwrap())
        .unwrap();
    let stop = tokio_util::sync::CancellationToken::new();
    let router = App::new(&cfg, rt.clone(), store.clone(), stop.clone())
        .unwrap()
        .router();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let serving = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("../service-ui/test/live.mjs");
    let result = tokio::time::timeout(
        Duration::from_secs(120),
        tokio::process::Command::new("node")
            .arg(script)
            .env("OW_UI_TEST_URL", url)
            .env("OW_UI_ADMIN", admin)
            .env("OW_UI_USER", user)
            .env("OW_UI_MANAGER", manager)
            .env("OW_UI_RUN", run_id)
            .status(),
    )
    .await;
    serving.abort();
    stop.cancel();
    rt.shutdown().await.unwrap();
    tokio::task::spawn_blocking(move || {
        let mut client = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        client
            .batch_execute(&format!("DROP SCHEMA {} CASCADE", db.schema))
            .unwrap();
    })
    .await
    .unwrap();
    assert!(result.unwrap().unwrap().success());
}
