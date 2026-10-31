use openworker_runtime::{
    execution::Backend,
    store::{Session, StateStore},
};
use openworker_store_postgres::{worker::Selector, Config, PostgresStore, Tls, TurnSubmission};
use serde_json::json;

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn version_catalog_preserves_identity_counts_idle_sessions_and_expires_presence() {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_pools_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    PostgresStore::migrate(&c).unwrap();
    let mut store = PostgresStore::open(c.clone()).unwrap();
    let selector = Selector {
        coworker: "fixture".into(),
        bundle_digest: "v1".into(),
        backend: Backend::Off,
        scripted: false,
        fallback_model: "fixture".into(),
        model_provider: Default::default(),
        model_connections: Default::default(),
        environment_provider: None,
        environment_identity: None,
        environment_profile: None,
    };
    for (id, digest) in [("idle", "v1"), ("waiting", "v1"), ("new", "v2")] {
        let s:Session=serde_json::from_value(json!({"id":id,"name":"SECRET","workspace":"/SECRET","model":"fixture","execution_backend":"off","coworker":"fixture","bundle_digest":digest,"schema_digest":null,"turn":null,"sandbox":null,"closed":false,"interactions":[],"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        store.save(&s, "session_created", json!({})).unwrap();
    }
    store
        .submit_turn(TurnSubmission {
            limits: None,
            session_id: "waiting".into(),
            submission_id: "one".into(),
            text: "SECRET".into(),
        })
        .unwrap();
    let (mut waiting, _, _) = store.accept("waiting", "one", "SECRET").unwrap();
    waiting.turn.as_mut().unwrap().status = "waiting".into();
    waiting.interactions=serde_json::from_value(json!([{ "id":"i_a","turn_id":waiting.turn.as_ref().unwrap().id,"kind":"approval","call":{"SECRET":"SECRET"},"bundle_digest":"v1","schema_digest":"s","response":null,"resource_policy_digest":"r"}])).unwrap();
    store.save(&waiting, "checkpoint", json!({})).unwrap();
    store
        .submit_turn(TurnSubmission {
            limits: None,
            session_id: "waiting".into(),
            submission_id: "two".into(),
            text: "SECRET".into(),
        })
        .unwrap();
    let before = store.inspect("waiting").unwrap();
    store
        .advertise_worker("old-pool", selector.clone(), 2, false)
        .unwrap();
    let page = store.worker_pools(None, 1).unwrap();
    assert_eq!(page["next"], "v1");
    let p = &page["pools"][0];
    assert_eq!(p["open_sessions"], 2);
    assert_eq!(p["queued_turns"], 1);
    assert_eq!(p["approvals"], 1);
    assert_eq!(p["sessions_without_advertised_worker"], 0);
    assert_eq!(p["workers"][0]["slots"], 2);
    assert_eq!(
        store.worker_pools(Some("v1"), 1).unwrap()["pools"][0]["execution_digest"],
        "v2"
    );
    assert!(!page.to_string().contains("SECRET"));
    let mut changed = selector.clone();
    changed.bundle_digest = "v2".into();
    assert!(store
        .advertise_worker("old-pool", changed.clone(), 2, false)
        .is_err());
    store
        .advertise_worker("old-pool", selector.clone(), 2, true)
        .unwrap();
    assert_eq!(
        store.worker_pools(None, 1).unwrap()["pools"][0]["sessions_without_advertised_worker"],
        2
    );
    let new = PostgresStore::open(c.clone()).unwrap();
    new.advertise_worker("new-pool", changed, 1, false).unwrap();
    store.withdraw_worker().unwrap();
    assert_eq!(
        store.worker_pools(None, 1).unwrap()["pools"][0]["workers"],
        json!([])
    );
    // Same digest but wrong model cannot cover this cohort.
    let mut wrong = selector.clone();
    wrong.fallback_model = "different".into();
    store
        .advertise_worker("wrong-model", wrong, 1, false)
        .unwrap();
    assert_eq!(
        store.worker_pools(None, 1).unwrap()["pools"][0]["sessions_without_advertised_worker"],
        2
    );
    store.withdraw_worker().unwrap();
    store
        .advertise_worker("old-pool", selector.clone(), 2, false)
        .unwrap();
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog; UPDATE worker_presence SET expires=now()-interval '1 second'",c.schema)).unwrap();
    assert_eq!(
        store.worker_pools(None, 1).unwrap()["pools"][0]["workers"],
        json!([])
    );
    let key = "a".repeat(64);
    store.auth_bootstrap(&key, "admin").unwrap();
    let scoped = store.authorized(&key).unwrap();
    assert!(scoped.worker_pools(None, 1).is_err());
    assert!(scoped.advertise_worker("x", selector, 1, false).is_err());
    assert!(scoped.withdraw_worker().is_err());
    assert!(store.worker_pools(None, 0).is_err());
    let mut idle = store.get("idle").unwrap();
    idle.closed = true;
    store.save(&idle, "session_closed", json!({})).unwrap();
    assert_eq!(
        store.worker_pools(None, 1).unwrap()["pools"][0]["open_sessions"],
        1
    );
    // Simulate schema v5 from before the optional catalog existed. Its explicit
    // extension migration must not rewrite existing session/approval records.
    db.batch_execute("DROP TABLE worker_presence; DROP TABLE pool_version")
        .unwrap();
    PostgresStore::migrate(&c).unwrap();
    assert_eq!(
        store.worker_pools(None, 1).unwrap()["pools"][0]["open_sessions"],
        1
    );
    db.batch_execute("UPDATE pool_version SET version=2")
        .unwrap();
    assert!(PostgresStore::migrate(&c).is_err());
    let after = store.inspect("waiting").unwrap();
    assert_eq!(before.bundle_digest, after.bundle_digest);
    assert_eq!(before.storage_revision, after.storage_revision);
    assert_eq!(
        serde_json::to_value(before.interactions).unwrap(),
        serde_json::to_value(after.interactions).unwrap()
    );
    db.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}
