use openworker_runtime::{resource_policy::Resolved, store::Session};
use openworker_store_postgres::{Config, PostgresStore, SessionCreation, Tls};
use serde_json::json;
use std::sync::{Arc, Barrier};
fn fixture() -> Config {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_ingress_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    c
}
fn request() -> SessionCreation {
    SessionCreation {
        request_id: "create".into(),
        actor: "user".into(),
        parameters: json!({"name":"test"}),
    }
}
fn session() -> Session {
    serde_json::from_value(json!({"id":openworker_runtime::id("s"),"name":"test","workspace":"/work","coworker":"test","bundle_digest":"bundle","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":Resolved::default()})).unwrap()
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn two_api_creations_share_one_immediately_claimable_session_and_conflict_explicitly() {
    let c = fixture();
    let a = PostgresStore::open(c.clone()).unwrap();
    let b = PostgresStore::open(c.clone()).unwrap();
    let gate = Arc::new(Barrier::new(3));
    let ga = gate.clone();
    let gb = gate.clone();
    let x = std::thread::spawn(move || {
        ga.wait();
        a.create_session(request(), session()).unwrap()
    });
    let y = std::thread::spawn(move || {
        gb.wait();
        b.create_session(request(), session()).unwrap()
    });
    gate.wait();
    let x = x.join().unwrap();
    let y = y.join().unwrap();
    assert_eq!(x.session_id, y.session_id);
    assert_ne!(x.duplicate, y.duplicate);
    let api = PostgresStore::open(c.clone()).unwrap();
    assert_eq!(
        api.creation(request()).unwrap().unwrap().session_id,
        x.session_id
    );
    let mut changed = request();
    changed.actor = "other".into();
    assert!(api.creation(changed).is_err());
    let mut changed = request();
    changed.parameters["name"] = json!("different");
    assert!(api.create_session(changed, session()).is_err());
    let worker = PostgresStore::open(c).unwrap();
    worker.claim(&x.session_id).unwrap();
    assert_eq!(api.read_snapshot(&x.session_id).unwrap()["cursor"], 2);
    assert_eq!(api.read_events(&x.session_id, 0).unwrap().len(), 2);
    assert!(api.read_events(&x.session_id, 3).is_err());
    assert!(api.read_snapshot("missing").is_err());
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn creation_audit_failure_rolls_back_and_missing_receipt_never_recreates() {
    let c = fixture();
    let api = PostgresStore::open(c.clone()).unwrap();
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!(
        "SET ow.runtime_schema='7'; SET search_path={},pg_catalog",
        c.schema
    ))
    .unwrap();
    db.batch_execute("CREATE FUNCTION reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fault'; END $$; CREATE TRIGGER reject_audit BEFORE INSERT ON audit FOR EACH ROW EXECUTE FUNCTION reject_audit()").unwrap();
    assert!(api.create_session(request(), session()).is_err());
    assert_eq!(
        db.query_one("SELECT count(*) FROM sessions", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
    assert_eq!(
        db.query_one("SELECT count(*) FROM creations", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
    db.batch_execute("DROP TRIGGER reject_audit ON audit")
        .unwrap();
    let api = PostgresStore::open(c).unwrap();
    let made = api.create_session(request(), session()).unwrap();
    db.execute("DELETE FROM creations WHERE request_id='create'", &[])
        .unwrap();
    assert!(api.creation(request()).is_err());
    assert!(api.create_session(request(), session()).is_err());
    assert_eq!(
        api.read_snapshot(&made.session_id).unwrap()["session"]["id"],
        made.session_id
    );
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn session_directory_is_bounded_keyset_paginated_and_content_free() {
    let c = fixture();
    let api = PostgresStore::open(c).unwrap();
    for n in 0..3 {
        let mut r = request();
        r.request_id = format!("create-{n}");
        let mut s = session();
        s.name = format!("Session {n}");
        s.workspace = "SECRET-PATH".into();
        api.create_session(r, s).unwrap();
    }
    let first = api.session_page(None, 2).unwrap();
    assert_eq!(first["sessions"].as_array().unwrap().len(), 2);
    assert_eq!(first["sessions"][0]["name"], "Session 2");
    assert!(!first.to_string().contains("SECRET-PATH"));
    assert!(first["sessions"][0].get("interactions").is_none());
    let last = api
        .session_page(Some(first["next_after"].as_str().unwrap().into()), 2)
        .unwrap();
    assert_eq!(last["sessions"].as_array().unwrap().len(), 1);
    assert!(last["next_after"].is_null());
    assert_ne!(first["sessions"][1]["id"], last["sessions"][0]["id"]);
    assert!(api.session_page(None, 0).is_err());
    assert!(api.session_page(None, 101).is_err());
    assert!(api.session_page(Some("s_missing".into()), 2).is_err());
}
