//! Real PostgreSQL execution+recording databases and two HTTP API instances.
//! No model keys, OpenShell tools, cloud resources or UI are needed.
use openworker_observability::{postgres::Postgres, Step, Store};
use openworker_runtime::{
    model::Model,
    runtime::Runtime,
    store::{Session, StateStore, TurnObservation},
    telemetry::TraceContext,
};
use openworker_server_reference::{http::App, Config};
use openworker_store_postgres::{
    auth::{Role, Scope, Spec},
    PostgresStore,
};
use serde_json::{json, Value};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
const ADMIN: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ALICE: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const BOB: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
const LIMITED: &str = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
fn issue(admin: &PostgresStore, key: &str, name: &str, scope: Scope) -> Value {
    admin
        .auth_issue(
            name,
            Spec {
                principal: name.into(),
                role: Role::Observer,
                scope,
                environments: vec![],
                expires_at: None,
            },
            key,
        )
        .unwrap()
}
fn seed(root: &mut PostgresStore, sid: &str, name: &str) -> Vec<String> {
    let s:Session=serde_json::from_value(json!({"id":sid,"name":name,"workspace":"/unused","coworker":"query-test","model":"fixture","bundle_digest":"test","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
    root.save(&s, "session_created", json!({})).unwrap();
    let mut turns = vec![];
    for n in 0..if sid == "s_alice" { 2 } else { 1 } {
        let (s, _, _) = root
            .accept(sid, &format!("request-{n}"), "PRIVATE_PROMPT_NEVER_SEARCH")
            .unwrap();
        let tid = s.turn.unwrap().id;
        turns.push(tid.clone());
        let mut s = root.get(sid).unwrap();
        let turn = s.turn.as_mut().unwrap();
        turn.observation = Some(TurnObservation {
            first_started_at_ms: 1000,
            last_segment: TraceContext {
                trace: [1; 16],
                span: (n as u64 + 1).to_be_bytes(),
            },
            segments: 1,
        });
        root.save(
            &s,
            "execution_segment_started",
            json!({"observation":s.turn.as_ref().unwrap().observation}),
        )
        .unwrap();
        let mut s = root.get(sid).unwrap();
        s.turn.as_mut().unwrap().status = "completed".into();
        root.save(&s, "turn_completed", json!({"status":"completed"}))
            .unwrap();
    }
    turns
}
fn row(deploy: &str, sid: &str, turn: &str, id: u64, op: &str, input: u64) -> Step {
    let mut value = json!({"version":2,"deployment":deploy,"role":"worker","trace_id":"01010101010101010101010101010101","span_id":format!("{id:016x}"),"session_hash":openworker_runtime::bundle::digest(sid.as_bytes()),"turn_hash":openworker_runtime::bundle::digest(turn.as_bytes()),"operation":op,"outcome":"ok","started_ms":1000,"ended_ms":1100,"duration_us":100000});
    if op == "model.attempt" {
        value["model"] = json!("fixture");
        value["purpose"] = json!("main");
        value["usage"] = json!({"input":input,"output":10,"cache_read":null,"cache_write":null,"reasoning":null});
    }
    serde_json::from_value(value).unwrap()
}
async fn api(
    config: &Config,
    store: PostgresStore,
    runtime: Arc<Runtime>,
) -> (String, tokio::task::JoinHandle<()>) {
    let app = App::new(config, runtime, store, CancellationToken::new()).unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(listener, app.router()).await.unwrap();
    });
    (url, server)
}
async fn request(url: &str, key: &str, path: &str) -> (u16, Value) {
    let response = reqwest::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .get(format!("{url}{path}"))
        .bearer_auth(key)
        .header("x-openworker-namespace", "test")
        .send()
        .await
        .unwrap();
    let code = response.status().as_u16();
    assert_eq!(
        response
            .headers()
            .get("cache-control")
            .and_then(|v| v.to_str().ok()),
        Some("no-store")
    );
    let text = response.text().await.unwrap();
    assert!(!text.contains("PRIVATE_PROMPT") && !text.contains("postgresql://"));
    (
        code,
        serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw":text})),
    )
}
fn get(rt: &tokio::runtime::Runtime, url: &str, key: &str, path: &str) -> (u16, Value) {
    rt.block_on(request(url, key, path))
}
#[test]
#[ignore = "two disposable OW_EXECUTION_TEST_URL/OW_OBSERVABILITY_TEST_URL databases required"]
fn authorized_search_timeline_summaries_revocation_and_separate_database_outage() {
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .enable_all()
        .build()
        .unwrap();
    let execution = std::env::var("OW_EXECUTION_TEST_URL").unwrap();
    let observations = std::env::var("OW_OBSERVABILITY_TEST_URL").unwrap();
    assert_ne!(execution, observations);
    let root = tempfile::tempdir().unwrap();
    let id = uuid::Uuid::new_v4().simple().to_string();
    let exec_env = format!("OW_QUERY_EXEC_{}", id.to_uppercase());
    let obs_env = format!("OW_QUERY_OBS_{}", id.to_uppercase());
    std::env::set_var(&exec_env, &execution);
    std::env::set_var(&obs_env, &observations);
    let schema = format!("ow_query_{}", &id[..12]);
    let obs_schema = format!("ow_query_record_{}", &id[..12]);
    let config:Config=serde_json::from_value(json!({"database_env":exec_env,"schema":schema,"database_loopback_plaintext":true,"state_root":root.path().join("state"),"bundle":"unused","runner":"unused","model":"fixture","scripted":true,"api_token_file":"unused","actor":"admin","openshell":{"identity":"fixture","endpoint":"https://127.0.0.1:1","certificates":"unused","workspace":"unused","image":"unused"},"service":{"id":"query-service","namespace":"test","agent":"query-test"},"observability":{"schema":obs_schema,"database":{"database_env":obs_env,"database_loopback_plaintext":true,"timeout_ms":3000}}})).unwrap();
    let db = config.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let mut owner = PostgresStore::open(db.clone()).unwrap();
    owner.auth_bootstrap(ADMIN, "admin").unwrap();
    let admin = owner.authorized(ADMIN).unwrap();
    let alice_key = issue(&admin, ALICE, "alice", Scope::Owned);
    issue(&admin, BOB, "bob", Scope::Owned);
    let a = seed(&mut owner, "s_alice", "Alpha % Game");
    let b = seed(&mut owner, "s_bob", "Beta Private");
    let c = seed(&mut owner, "s_empty", "Empty Alpha");
    issue(
        &admin,
        LIMITED,
        "limited",
        Scope::Sessions {
            ids: vec!["s_alice".into()],
        },
    );
    let mut exec_sql = postgres::Client::connect(&execution, postgres::NoTls).unwrap();
    exec_sql.batch_execute(&format!("INSERT INTO {schema}.api_session_owners VALUES('s_alice','alice'),('s_empty','alice'),('s_bob','bob');UPDATE {schema}.submissions SET created='2026-01-01 00:00:00+00'" )).unwrap();
    let recording = config
        .observability
        .as_ref()
        .unwrap()
        .database(&config)
        .unwrap();
    Postgres::migrate(&recording).unwrap();
    let mut store = Postgres::open(&recording).unwrap();
    let mut first = row(&schema, "s_alice", &a[0], 1, "agent.execute", 0);
    first.span_id = "0000000000000001".into();
    store.put(&first).unwrap();
    store
        .put(&row(&schema, "s_alice", &a[0], 10, "model.attempt", 100))
        .unwrap();
    let mut started = row(&schema, "s_alice", &a[0], 11, "model.requested", 0);
    started.duration_us = Some(0);
    started.ended_ms = started.started_ms;
    started.parent_trace_id = Some(first.trace_id.clone());
    started.parent_span_id = Some("000000000000000a".into());
    store.put(&started).unwrap();
    store
        .put(&row(&schema, "s_alice", &a[1], 12, "model.attempt", 1000))
        .unwrap();
    store
        .put(&row(&schema, "s_bob", &b[0], 13, "model.attempt", 500))
        .unwrap();
    store
        .put(&row(
            "foreign_deployment",
            "s_alice",
            &a[0],
            14,
            "model.attempt",
            99999,
        ))
        .unwrap();
    let bundle = root.path().join("bundle");
    std::fs::create_dir_all(bundle.join("coworker")).unwrap();
    std::fs::write(
        bundle.join("coworker/manifest.md"),
        "---\nid: query-test\nname: Query\nplanning: true\n---\nunused",
    )
    .unwrap();
    std::fs::write(bundle.join("tools.json"), r#"{"version":3,"tools":[]}"#).unwrap();
    let runner = root.path().join("runner");
    std::fs::write(&runner, b"\x7fELFtest-only-no-execution").unwrap();
    let runtime = Runtime::open(
        &root.path().join("runtime"),
        &bundle,
        &runner,
        Model::new("fixture".into(), true),
    )
    .unwrap();
    let (url1, server1) = rt.block_on(api(
        &config,
        PostgresStore::open(db.clone()).unwrap(),
        runtime.clone(),
    ));
    let (url2, server2) = rt.block_on(api(
        &config,
        PostgresStore::open(db.clone()).unwrap(),
        runtime.clone(),
    ));
    assert_eq!(
        get(&rt, &url1, "invalid-token", "/v1/observability/runs").0,
        401
    );
    let wrong_namespace = rt.block_on(async {
        reqwest::Client::builder()
            .no_proxy()
            .build()
            .unwrap()
            .get(format!("{url1}/v1/observability/runs"))
            .bearer_auth(ADMIN)
            .header("x-openworker-namespace", "wrong")
            .send()
            .await
            .unwrap()
            .status()
            .as_u16()
    });
    assert_eq!(wrong_namespace, 403);
    issue(
        &admin,
        "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "expiring",
        Scope::Owned,
    );
    exec_sql.batch_execute(&format!("UPDATE {schema}.api_credentials SET spec=jsonb_set(spec,'{{expires_at}}','1'::jsonb) WHERE request_id='expiring'")).unwrap();
    assert_eq!(
        get(
            &rt,
            &url1,
            "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            "/v1/observability/runs"
        )
        .0,
        401
    );
    let (code, page) = get(&rt, &url1, ALICE, "/v1/observability/runs?limit=1");
    assert_eq!(code, 200, "{page}");
    assert_eq!(page["runs"].as_array().unwrap().len(), 1);
    let cursor = page["next_cursor"].as_str().unwrap();
    let (code, next) = get(
        &rt,
        &url2,
        ALICE,
        &format!("/v1/observability/runs?limit=1&after={cursor}"),
    );
    assert_eq!(code, 200, "{next}");
    assert_ne!(page["runs"][0]["turn_id"], next["runs"][0]["turn_id"]);
    let mut seen = std::collections::BTreeSet::new();
    seen.insert(page["runs"][0]["turn_id"].as_str().unwrap().to_owned());
    seen.insert(next["runs"][0]["turn_id"].as_str().unwrap().to_owned());
    let last_cursor = next["next_cursor"].as_str().unwrap();
    let (code, last) = get(
        &rt,
        &url1,
        ALICE,
        &format!("/v1/observability/runs?limit=1&after={last_cursor}"),
    );
    assert_eq!(code, 200);
    seen.insert(last["runs"][0]["turn_id"].as_str().unwrap().to_owned());
    assert_eq!(seen.len(), 3);
    assert!(last["next_cursor"].is_null());
    assert_eq!(
        get(
            &rt,
            &url1,
            BOB,
            &format!("/v1/observability/runs?after={cursor}")
        )
        .0,
        400
    );
    assert_eq!(
        get(
            &rt,
            &url1,
            ALICE,
            &format!("/v1/observability/runs?q=changed&after={cursor}")
        )
        .0,
        400
    );
    for path in [
        "/v1/observability/runs?after=broken",
        "/v1/observability/runs?limit=101",
        "/v1/observability/runs?from_ms=10&to_ms=1",
        "/v1/observability/summary?limit=26",
    ] {
        assert_eq!(get(&rt, &url1, ALICE, path).0, 400, "{path}");
    }
    let (_, page) = get(
        &rt,
        &url1,
        ALICE,
        "/v1/observability/runs?q=%25&agent=query-test",
    );
    assert_eq!(page["runs"].as_array().unwrap().len(), 2);
    assert_eq!(
        get(&rt, &url1, ALICE, "/v1/observability/runs?q=PRIVATE_PROMPT").1["runs"],
        json!([])
    );
    assert_eq!(
        get(&rt, &url1, LIMITED, "/v1/observability/runs").1["runs"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(
        get(
            &rt,
            &url1,
            ALICE,
            "/v1/observability/runs?from_ms=1790000000000"
        )
        .1["runs"],
        json!([])
    );
    let path = format!("/v1/sessions/s_alice/observability/{}/summary", a[0]);
    let (code, value) = get(&rt, &url2, ALICE, &path);
    assert_eq!(code, 200, "{value}");
    assert_eq!(value["summary"]["usage"]["input"]["total"], 100);
    assert_eq!(value["summary"]["wall_elapsed_us"], 100000);
    assert_eq!(get(&rt, &url1, BOB, &path).0, 403);
    let (_, global) = get(&rt, &url1, ALICE, "/v1/observability/summary");
    assert_eq!(global["summary"]["usage"]["input"]["total"], 1100);
    assert!(global["summary"]["wall_elapsed_us"].is_null());
    assert!(!global.to_string().contains("Beta Private"));
    let (_, empty) = get(
        &rt,
        &url1,
        ALICE,
        &format!("/v1/sessions/s_empty/observability/{}/summary", c[0]),
    );
    assert!(empty["summary"]["usage"]["input"]["total"].is_null());
    assert!(!empty["summary"]["coverage"]["all_visible_segments_closed"]
        .as_bool()
        .unwrap());
    let timeline = format!("/v1/sessions/s_alice/observability/{}/timeline", a[0]);
    let (_, p) = get(&rt, &url1, ALICE, &format!("{timeline}?limit=1"));
    let through = p["page"]["through"].as_u64().unwrap();
    let after = p["page"]["next"].as_u64().unwrap();
    store
        .put(&row(&schema, "s_alice", &a[0], 15, "model.attempt", 100))
        .unwrap();
    let (_, p) = get(
        &rt,
        &url2,
        ALICE,
        &format!("{timeline}?after={after}&through={through}"),
    );
    assert_eq!(p["page"]["steps"].as_array().unwrap().len(), 2);
    let (_, value) = get(&rt, &url1, ALICE, &path);
    assert_eq!(value["summary"]["usage"]["input"]["total"], 200);
    assert_eq!(value["summary"]["coverage"]["completions_without_start"], 1);
    assert_eq!(
        get(&rt, &url1, ALICE, &format!("{timeline}?limit=201")).0,
        400
    );
    let mut timings = vec![];
    for _ in 0..20 {
        let at = Instant::now();
        let (code, value) = get(&rt, &url1, ALICE, &path);
        assert_eq!(code, 200);
        assert_eq!(value["summary"]["coverage"]["scanned_steps"], 4);
        timings.push(at.elapsed().as_micros() as u64);
    }
    timings.sort_unstable();
    println!(
        "OBSERVABILITY_QUERY_PROBE {}",
        json!({"requests":20,"scanned_steps_per_request":4,"separate_recording_database":true,"fresh_read_connection_per_request":true,"p50_us":timings[9],"p95_us":timings[18],"scope":"small serial local Docker/PostgreSQL HTTP probe; not capacity guidance"})
    );
    // Prove authority is checked before any recording access, even while that
    // database is unavailable. Known/unknown foreign IDs behave identically.
    let mut record_sql = postgres::Client::connect(&observations, postgres::NoTls).unwrap();
    let mut lock = record_sql.transaction().unwrap();
    lock.batch_execute(&format!(
        "LOCK TABLE {obs_schema}.observation_steps IN ACCESS EXCLUSIVE MODE"
    ))
    .unwrap();
    assert_eq!(get(&rt, &url1, BOB, &path).0, 403);
    assert_eq!(
        get(
            &rt,
            &url1,
            BOB,
            "/v1/sessions/not-owned/observability/not-found/summary"
        )
        .0,
        403
    );
    assert_eq!(
        get(
            &rt,
            &url1,
            ADMIN,
            "/v1/sessions/not-found/observability/not-found/summary"
        )
        .0,
        404
    );
    assert_eq!(get(&rt, &url1, ALICE, &path).0, 503);
    assert_eq!(get(&rt, &url2, ALICE, "/v1/observability/runs").0, 200);
    lock.rollback().unwrap();
    assert_eq!(get(&rt, &url1, ALICE, &path).0, 200);
    // Four reader jobs hold their permits until SQL finishes, independent of
    // the execution-store connection and run search.
    let mut lock = record_sql.transaction().unwrap();
    lock.batch_execute(&format!(
        "LOCK TABLE {obs_schema}.observation_steps IN ACCESS EXCLUSIVE MODE"
    ))
    .unwrap();
    let pending: Vec<_> = (0..4)
        .map(|_| {
            let (url, path) = (url1.clone(), path.clone());
            rt.spawn(async move { request(&url, ALICE, &path).await })
        })
        .collect();
    let mut inspect = postgres::Client::connect(&observations, postgres::NoTls).unwrap();
    let deadline = Instant::now() + Duration::from_millis(1500);
    loop {
        let count:i64=inspect.query_one("SELECT count(*) FROM pg_locks l JOIN pg_class r ON r.oid=l.relation JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=$1 AND NOT l.granted",&[&obs_schema]).unwrap().get(0);
        if count >= 4 {
            break;
        }
        assert!(Instant::now() < deadline, "Four readers did not reach lock");
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(get(&rt, &url1, ALICE, &path).0, 429);
    assert_eq!(get(&rt, &url1, ALICE, "/v1/observability/runs").0, 200);
    lock.rollback().unwrap();
    for job in pending {
        assert_eq!(rt.block_on(job).unwrap().0, 200);
    }
    let mut disabled = config.clone();
    disabled.observability = None;
    let (disabled_url, disabled_server) = rt.block_on(api(
        &disabled,
        PostgresStore::open(db.clone()).unwrap(),
        runtime.clone(),
    ));
    assert_eq!(get(&rt, &disabled_url, ALICE, &path).0, 503);
    assert_eq!(get(&rt, &disabled_url, BOB, &path).0, 403);
    assert_eq!(
        get(&rt, &disabled_url, ALICE, "/v1/observability/runs").0,
        200
    );
    disabled_server.abort();
    let _ = rt.block_on(disabled_server);
    // Revoke after the pre-read check while the recording read is blocked. The
    // response must be rejected after the lock releases, not return cached data.
    let mut lock = record_sql.transaction().unwrap();
    lock.batch_execute(&format!(
        "LOCK TABLE {obs_schema}.observation_steps IN ACCESS EXCLUSIVE MODE"
    ))
    .unwrap();
    let (url, waiting_path) = (url1.clone(), path.clone());
    let inflight = rt.spawn(async move { request(&url, ALICE, &waiting_path).await });
    let deadline = Instant::now() + Duration::from_millis(1500);
    loop {
        let mut inspect = postgres::Client::connect(&observations, postgres::NoTls).unwrap();
        let waiting:bool=inspect.query_one("SELECT EXISTS(SELECT 1 FROM pg_locks l JOIN pg_class r ON r.oid=l.relation JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=$1 AND NOT l.granted)",&[&obs_schema]).unwrap().get(0);
        if waiting {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "Reader did not reach recording lock"
        );
        std::thread::sleep(Duration::from_millis(5));
    }
    admin
        .auth_revoke(alice_key["id"].as_str().unwrap())
        .unwrap();
    lock.rollback().unwrap();
    assert_eq!(rt.block_on(inflight).unwrap().0, 401);
    assert_eq!(get(&rt, &url2, ALICE, "/v1/observability/runs").0, 401);
    server1.abort();
    server2.abort();
    let _ = rt.block_on(server1);
    let _ = rt.block_on(server2);
    rt.block_on(runtime.shutdown()).unwrap();
    drop(store);
    drop(owner);
    drop(admin);
    record_sql
        .batch_execute(&format!("DROP SCHEMA {obs_schema} CASCADE"))
        .unwrap();
    exec_sql
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .unwrap();
    std::env::remove_var(exec_env);
    std::env::remove_var(obs_env);
}
