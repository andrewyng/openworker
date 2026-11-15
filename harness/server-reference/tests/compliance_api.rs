#![cfg(feature = "compliance")]
use openworker_runtime::{
    model::Model,
    runtime::Runtime,
    store::{Session, StateStore},
};
use openworker_server_reference::{http::App, Config};
use openworker_store_postgres::{
    auth::{Role, Scope, Spec},
    PostgresStore,
};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
const ADMIN: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const READER: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const AUDITOR: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn scoped_compliance_routes_work_across_two_api_nodes() {
    let root = tempfile::tempdir().unwrap();
    let id = uuid::Uuid::new_v4().simple().to_string();
    let key = format!("OW_COMPLIANCE_DB_{}", id.to_uppercase());
    std::env::set_var(&key, std::env::var("OW_PG_TEST_URL").unwrap());
    let mut config:Config=serde_json::from_value(json!({"database_env":key,"schema":format!("ow_capi_{}",&id[..20]),"database_loopback_plaintext":true,"state_root":root.path().join("state"),"bundle":"unused","runner":"unused","model":"fixture","scripted":true,"api_token_file":"unused","actor":"admin","openshell":{"identity":"fixture","endpoint":"https://127.0.0.1:1","certificates":"unused","workspace":"unused","image":"unused"},"service":{"id":"compliance-test","namespace":"test","agent":"test","legacy_compatibility":true}})).unwrap();
    config
        .safety
        .guardrails
        .user_input
        .push(openworker_runtime::safety::Rule::regex(
            "input-secret",
            "SECRET_[A-Z]+",
            openworker_runtime::safety::Action::Block,
        ));
    let db = config.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let mut store = PostgresStore::open(db.clone()).unwrap();
    store.auth_bootstrap(ADMIN, "admin").unwrap();
    let admin = store.authorized(ADMIN).unwrap();
    for sid in ["s_one", "s_two"] {
        let mut s:Session=serde_json::from_value(json!({"id":sid,"name":"Fixture","workspace":"/work","coworker":"test","bundle_digest":"b","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"compliance":{},"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        s.safety = config.safety.clone();
        s.history
            .push(json!({"role":"user","content":"accepted text"}));
        store.save(&s, "session_created", json!({})).unwrap();
        store
            .append_event(
                sid,
                "safety_evaluation",
                json!({"check":"pii","outcome":"blocked","text":"PRIVATE_REJECTED_SENTINEL"}),
            )
            .unwrap();
    }
    for (token, role, name) in [
        (READER, Role::ComplianceReader, "reader"),
        (AUDITOR, Role::ComplianceAuditor, "auditor"),
    ] {
        admin
            .auth_issue(
                name,
                Spec {
                    principal: name.into(),
                    role,
                    scope: Scope::Sessions {
                        ids: vec!["s_one".into()],
                    },
                    environments: vec![],
                    expires_at: None,
                },
                token,
            )
            .unwrap();
    }
    let bundle = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let runner = root.path().join("unused-runner");
    std::fs::write(&runner, b"\x7fELF-api-fixture-not-executed").unwrap();
    let runtime = Runtime::open(
        &root.path().join("runtime"),
        &bundle,
        &runner,
        Model::new("fixture".into(), true),
    )
    .unwrap();
    let mut urls = vec![];
    let mut servers = vec![];
    for _ in 0..2 {
        let app = App::new(
            &config,
            runtime.clone(),
            store.clone(),
            CancellationToken::new(),
        )
        .unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        urls.push(format!("http://{}", listener.local_addr().unwrap()));
        servers.push(tokio::spawn(async move {
            axum::serve(listener, app.router()).await.unwrap()
        }));
    }
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let mut bodies = vec![];
    for url in &urls {
        for (token, path, status) in [
            (READER, "s_one/activities?limit=1", 200),
            (READER, "s_two/activities", 403),
            (READER, "s_one/content", 403),
            (AUDITOR, "s_one/content", 200),
            (READER, "s_one/delivery", 200),
        ] {
            let response = client
                .get(format!("{url}/v1/compliance/sessions/{path}"))
                .header("x-openworker-namespace", "test")
                .bearer_auth(token)
                .send()
                .await
                .unwrap();
            assert_eq!(response.status().as_u16(), status, "{path}");
            let value: Value = response.json().await.unwrap();
            assert!(!value.to_string().contains("PRIVATE_REJECTED_SENTINEL"));
            if path.ends_with("limit=1") {
                assert_eq!(value["hasMore"], true);
                bodies.push(value);
            }
        }
    }
    assert_eq!(bodies[0], bodies[1]);
    for url in &urls {
        for (path, body) in [
            ("/v1/sessions/s_one/turns", json!({"text":"SECRET_INPUT"})),
            (
                "/sessions/s_one/turns",
                json!({"submission_id":"blocked","text":"SECRET_INPUT"}),
            ),
        ] {
            let response = client
                .post(format!("{url}{path}"))
                .header("x-openworker-namespace", "test")
                .header("idempotency-key", "blocked")
                .bearer_auth(ADMIN)
                .json(&body)
                .send()
                .await
                .unwrap();
            assert_eq!(response.status().as_u16(), 422, "blocked admission {path}");
            assert_eq!(
                response.json::<Value>().await.unwrap()["error"],
                "policy_blocked"
            );
        }
    }
    tokio::task::block_in_place(|| {
        let mut inspect = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        inspect
            .batch_execute(&format!("SET search_path={},pg_catalog", db.schema))
            .unwrap();
        assert_eq!(
            inspect
                .query_one("SELECT count(*) FROM submissions", &[])
                .unwrap()
                .get::<_, i64>(0),
            0
        );
        assert_eq!(
            inspect
                .query_one(
                    "SELECT count(*) FROM events WHERE data::text LIKE '%SECRET_INPUT%'",
                    &[]
                )
                .unwrap()
                .get::<_, i64>(0),
            0
        );
    });

    if let Ok(script) = std::env::var("OW_COMPLIANCE_UI_SCRIPT") {
        let status = tokio::process::Command::new("node")
            .arg(script)
            .env("OW_UI_TEST_URL", &urls[0])
            .env("OW_UI_TEST_TOKEN", ADMIN)
            .status()
            .await
            .unwrap();
        assert!(status.success());
    }
    for server in servers {
        server.abort();
        let _ = server.await;
    }
    runtime.shutdown().await.unwrap();
    tokio::task::block_in_place(|| {
        let mut sql = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        sql.batch_execute(&format!("DROP SCHEMA {} CASCADE", db.schema))
            .unwrap();
    });
    std::env::remove_var(key);
}
