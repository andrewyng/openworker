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
const APP_AUDITOR: &str = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
const FINANCE_TOKEN: &str = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
const SUPPORT_TOKEN: &str = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";
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
    for (principal, token) in [
        ("app_finance", FINANCE_TOKEN),
        ("app_support", SUPPORT_TOKEN),
    ] {
        admin
            .issue_app_token(
                principal,
                Spec {
                    principal: principal.into(),
                    role: Role::App,
                    scope: Scope::Owned,
                    environments: vec![],
                    expires_at: Some(openworker_runtime::store::now_ms() as i64 / 1000 + 3600),
                },
                token,
                Default::default(),
            )
            .unwrap();
    }
    admin
        .auth_issue(
            "app-auditor",
            Spec {
                principal: "finance-auditor".into(),
                role: Role::ComplianceAuditor,
                scope: Scope::Apps {
                    principals: vec!["app_finance".into()],
                },
                environments: vec![],
                expires_at: None,
            },
            APP_AUDITOR,
        )
        .unwrap();
    seed_catalog(&mut store, &db);
    #[cfg(feature = "compliance-export")]
    let (delivery_stop, receiver, received) = start_delivery_fixture(&mut config, &store).await;
    let bundle = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let runner = root.path().join("unused-runner");
    std::fs::write(&runner, b"\x7fELF-api-fixture-not-executed").unwrap();
    config.bundle = bundle.clone();
    config.runner = runner.clone();
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
    verify_catalog(&client, &urls[0], &urls[1]).await;
    #[cfg(feature = "compliance-export")]
    verify_delivery(&client, &urls[0], &urls[1], &received).await;
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
            .env("OW_UI_SCOPED_AUDITOR", APP_AUDITOR)
            .env(
                "OW_EXPECT_EVIDENCE_DELIVERY",
                if cfg!(feature = "compliance-export") {
                    "1"
                } else {
                    "0"
                },
            )
            .status()
            .await
            .unwrap();
        assert!(status.success());
    }
    for server in servers {
        server.abort();
        let _ = server.await;
    }
    #[cfg(feature = "compliance-export")]
    {
        delivery_stop.cancel();
        receiver.abort();
    }
    runtime.shutdown().await.unwrap();
    tokio::task::block_in_place(|| {
        let mut sql = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        sql.batch_execute(&format!("DROP SCHEMA {} CASCADE", db.schema))
            .unwrap();
    });
    std::env::remove_var(key);
}

fn seed_catalog(store: &mut PostgresStore, db: &openworker_store_postgres::Config) {
    for (sid, agent) in [
        ("s_finance", "invoice-agent"),
        ("s_support", "support-agent"),
        ("s_quiet", "quiet-agent"),
    ] {
        let s: Session = serde_json::from_value(json!({"id":sid,"name":"Catalog fixture",
            "workspace":"/work","coworker":agent,"bundle_digest":"b","schema_digest":null,
            "interactions":[],"turn":null,"sandbox":null,"closed":false,
            "resource_policy":openworker_runtime::resource_policy::Resolved::default()}))
        .unwrap();
        store.save(&s, "session_created", json!({})).unwrap();
    }
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!(
            "SET search_path={},pg_catalog; SET ow.runtime_schema='7'",
            db.schema
        ))
        .unwrap();
        pg.batch_execute("INSERT INTO api_session_owners VALUES ('s_finance','app_finance'),('s_support','app_support'),('s_quiet','app_finance')").unwrap();
        let evidence = [
            (
                "s_finance",
                Some("t_finance"),
                "interaction_resolved",
                json!({"kind":"approval","origin":"human","response":"once","interaction_id":"i_run","call_id":"call_run","tool":"send_invoice","call":{"arguments":"PRIVATE_REJECTED_SENTINEL"}}),
            ),
            (
                "s_finance",
                Some("t_finance"),
                "tool_started",
                json!({"call_id":"call_run","interaction_id":"i_run"}),
            ),
            (
                "s_finance",
                Some("t_finance"),
                "tool_finished",
                json!({"call_id":"call_run","interaction_id":"i_run","executed":true,"result":"PRIVATE_REJECTED_SENTINEL"}),
            ),
            (
                "s_finance",
                Some("t_finance"),
                "interaction_resolved",
                json!({"kind":"approval","origin":"human","response":"once","interaction_id":"i_pending","call_id":"call_pending","tool":"send_later"}),
            ),
            (
                "s_finance",
                Some("t_finance"),
                "interaction_resolved",
                json!({"kind":"approval","origin":"hard_policy","response":"deny","interaction_id":"i_denied","call_id":"call_denied","tool":"external_upload"}),
            ),
            (
                "s_finance",
                Some("t_finance"),
                "tool_finished",
                json!({"call_id":"call_denied","interaction_id":"i_denied","executed":false}),
            ),
            (
                "s_finance",
                Some("t_finance"),
                "safety_evaluation",
                json!({"phase":"modelOutput","check":"response-scanner","version":"5","outcome":"unavailable"}),
            ),
            (
                "s_support",
                None,
                "safety_evaluation",
                json!({"phase":"userInput","check":"block-ssn","version":"2","outcome":"blocked","text":"PRIVATE_REJECTED_SENTINEL"}),
            ),
            (
                "s_finance",
                Some("t_finance"),
                "safety_evaluation",
                json!({"phase":"userInput","check":"mask-card","version":"3","outcome":"redacted","text":"PRIVATE_REJECTED_SENTINEL"}),
            ),
            ("s_quiet", Some("t_quiet"), "turn_started", json!({})),
        ];
        for (sid, turn, kind, payload) in evidence {
            let cursor: i64 = pg
                .query_one(
                    "UPDATE sessions SET cursor=cursor+1 WHERE id=$1 RETURNING cursor",
                    &[&sid],
                )
                .unwrap()
                .get(0);
            pg.execute(
                "INSERT INTO events VALUES($1,$2,$3,$4,$5)",
                &[&sid, &cursor, &turn, &kind, &payload],
            )
            .unwrap();
            pg.execute("INSERT INTO audit(session_id,cursor,turn_id,kind,owner_epoch) VALUES($1,$2,$3,$4,0)",&[&sid,&cursor,&turn,&kind]).unwrap();
        }
        // Exercise cursor continuation with a meaningful amount of data.
        pg.batch_execute("INSERT INTO events SELECT 's_support',1000+i,NULL,'safety_evaluation',
            jsonb_build_object('phase','userInput','check','block-ssn','version','2','outcome','blocked')
            FROM generate_series(1,280) i;
            INSERT INTO audit(session_id,cursor,kind,owner_epoch,created)
            SELECT 's_support',1000+i,'safety_evaluation',0,clock_timestamp()-i*INTERVAL '1 minute'
            FROM generate_series(1,280) i;").unwrap();
    });
}

async fn verify_catalog(client: &reqwest::Client, url: &str, other_node: &str) {
    let get = |url: &str, path: &str, key: &str| {
        client
            .get(format!("{url}/v1/compliance/{path}"))
            .header("x-openworker-namespace", "test")
            .bearer_auth(key)
    };
    let export = |key: &str| {
        client
            .post(format!("{url}/v1/compliance/export"))
            .header("x-openworker-namespace", "test")
            .header("idempotency-key", "evidence-test")
            .bearer_auth(key)
            .json(&json!({"app":"app_finance"}))
    };
    let all: Value = get(url, "decisions?limit=2", ADMIN)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    let cursor = &all["next"];
    assert!(cursor["before_us"].is_number());
    let next: Value = get(
        other_node,
        &format!(
            "decisions?limit=2&before_us={}&before_id={}",
            cursor["before_us"],
            cursor["before_id"].as_str().unwrap()
        ),
        ADMIN,
    )
    .send()
    .await
    .unwrap()
    .error_for_status()
    .unwrap()
    .json()
    .await
    .unwrap();
    assert_ne!(all["records"][1]["id"], next["records"][0]["id"]);

    let response = get(url, "decisions", APP_AUDITOR).send().await.unwrap();
    assert_eq!(response.headers()["cache-control"], "no-store");
    let body: Value = response.error_for_status().unwrap().json().await.unwrap();
    let finance = body["records"].as_array().unwrap();
    assert!(finance.iter().all(|row| row["app"] == "app_finance"));
    assert!(!body.to_string().contains("PRIVATE_REJECTED_SENTINEL"));
    for (field, value, effect) in [
        ("tool", "send_invoice", "tool_result_recorded"),
        ("tool", "send_later", "unconfirmed"),
        ("tool", "external_upload", "tool_blocked"),
        ("policy", "response-scanner", "response_withheld"),
        ("policy", "mask-card", "unconfirmed"),
    ] {
        assert_eq!(
            finance.iter().find(|row| row[field] == value).unwrap()["effect"],
            effect
        );
    }
    let support: Value = get(url, "decisions?app=app_support&outcome=blocked", ADMIN)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(support["records"][0]["effect"], "request_blocked");
    assert!(support["records"][0]["turn_id"].is_null());
    let scoped: Value = get(url, "decisions?app=app_support", APP_AUDITOR)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(scoped["records"], json!([]));
    let overview: Value = get(url, "agents", APP_AUDITOR)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(overview["apps"], json!(["app_finance"]));
    assert!(overview["agents"]
        .as_array()
        .unwrap()
        .iter()
        .any(|row| row["agent"] == "quiet-agent" && row["decisions"] == 0));
    assert_eq!(
        get(url, "sessions/s_finance/content", APP_AUDITOR)
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    assert_eq!(
        get(url, "decisions", FINANCE_TOKEN)
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    assert_eq!(export(READER).send().await.unwrap().status(), 403);
    let exported: Value = export(APP_AUDITOR)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(exported["records"].as_array().unwrap().len(), finance.len());
    assert_eq!(exported["records"][0]["schema_version"], 1);
    let audit: Value = get(other_node, "audit", APP_AUDITOR)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    let actions = audit["records"].as_array().unwrap();
    assert!(actions
        .iter()
        .any(|row| row["app"] == "app_finance" && row["action"] == "credential.issue"));
    assert!(actions
        .iter()
        .any(|row| row["action"] == "compliance.evidence.download"
            && row["actor"] == "finance-auditor"));
    assert!(!audit.to_string().contains("app_support"));
}

#[cfg(feature = "compliance-export")]
async fn start_delivery_fixture(
    config: &mut Config,
    store: &PostgresStore,
) -> (
    CancellationToken,
    tokio::task::JoinHandle<()>,
    std::sync::Arc<std::sync::Mutex<Vec<Value>>>,
) {
    use axum::{
        extract::{Json, State},
        http::{HeaderMap, StatusCode},
        routing::post,
        Router,
    };
    let received = std::sync::Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
    let router = Router::new()
        .route(
            "/collect",
            post(
                |State(received): State<std::sync::Arc<std::sync::Mutex<Vec<Value>>>>,
                 headers: HeaderMap,
                 Json(payload): Json<Value>| async move {
                    assert_eq!(headers["authorization"], "Bearer local-evidence-test");
                    let mut received = received.lock().unwrap();
                    received.push(payload);
                    if received.len() == 1 {
                        StatusCode::SERVICE_UNAVAILABLE
                    } else {
                        StatusCode::NO_CONTENT
                    }
                },
            ),
        )
        .with_state(received.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    let env = format!("OW_COMPLIANCE_SINK_{}", uuid::Uuid::new_v4().simple());
    std::env::set_var(&env, "local-evidence-test");
    config
        .compliance_sinks
        .push(openworker_runtime::compliance_export::Sink {
            name: "test-siem".into(),
            endpoint: format!("http://{address}/collect"),
            credential_env: env,
            allow_loopback_http: true,
        });
    config.compliance = Some(Default::default());
    let stop = CancellationToken::new();
    for _ in 0..2 {
        openworker_server_reference::compliance_catalog::start_delivery(
            store.clone(),
            config.compliance_sinks.clone(),
            stop.clone(),
        )
        .unwrap();
    }
    (stop, task, received)
}

#[cfg(feature = "compliance-export")]
async fn verify_delivery(
    client: &reqwest::Client,
    url: &str,
    other_node: &str,
    received: &std::sync::Mutex<Vec<Value>>,
) {
    use std::time::Duration;
    let request = |url: &str, method: reqwest::Method, path: &str, token: &str| {
        client
            .request(method, format!("{url}/v1/compliance/{path}"))
            .header("x-openworker-namespace", "test")
            .header("idempotency-key", "evidence-forward")
            .bearer_auth(token)
    };
    let selection = json!({"app":"app_finance","until_ms":openworker_runtime::store::now_ms()});
    let expected: Value = request(url, reqwest::Method::POST, "export", ADMIN)
        .json(&selection)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    let body = json!({"sink":"test-siem","selection":selection});
    assert_eq!(
        request(url, reqwest::Method::POST, "delivery", APP_AUDITOR)
            .json(&body)
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    let response = request(url, reqwest::Method::POST, "delivery", ADMIN)
        .json(&body)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 202);
    let queued: Value = response.json().await.unwrap();
    let duplicate: Value = request(other_node, reqwest::Method::POST, "delivery", ADMIN)
        .json(&body)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(queued, duplicate);
    let mut saw_retry = false;
    tokio::time::timeout(Duration::from_secs(12), async {
        loop {
            let health: Value = request(other_node, reqwest::Method::GET, "delivery", ADMIN)
                .send()
                .await
                .unwrap()
                .error_for_status()
                .unwrap()
                .json()
                .await
                .unwrap();
            let record = &health["deliveries"][0];
            assert_eq!(record["id"], queued["id"]);
            if record["status"] == "delivered" {
                break;
            }
            if record["status"] == "retry_scheduled" {
                saw_retry = true;
            }
            tokio::time::sleep(Duration::from_millis(70)).await;
        }
    })
    .await
    .unwrap();
    assert!(saw_retry);
    let deliveries = received.lock().unwrap();
    assert_eq!(deliveries.len(), 2);
    assert_eq!(deliveries[0], deliveries[1]);
    assert_eq!(deliveries[0]["records"], expected["records"]);
}
