use openworker_runtime::store::{Session, StateStore};
use openworker_store_postgres::{Config, PostgresStore, Tls, TurnSubmission};
use serde_json::json;

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn operational_counts_are_current_content_free_and_host_only() {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_ops_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let mut store = PostgresStore::open(c.clone()).unwrap();
    let empty = store.operational_snapshot(30, 30).unwrap();
    assert_eq!(
        (
            empty.active,
            empty.queued,
            empty.approvals,
            empty.stalled,
            empty.unknown_recent
        ),
        (0, 0, 0, 0, 0)
    );
    for (id, status) in [
        ("s_active", "running"),
        ("s_waiting", "waiting"),
        ("s_closed", "running"),
    ] {
        let s: Session = serde_json::from_value(json!({"id":id,"name":"SECRET","workspace":"/SECRET","coworker":"fixture","bundle_digest":"fixture","schema_digest":null,"turn":null,"sandbox":null,"closed":false,"interactions":[],"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        store.save(&s, "session_created", json!({})).unwrap();
        store
            .submit_turn(TurnSubmission {
                limits: None,
                session_id: id.into(),
                submission_id: "one".into(),
                text: "SECRET".into(),
            })
            .unwrap();
        let (mut s, _, _) = store.accept(id, "one", "SECRET").unwrap();
        s.turn.as_mut().unwrap().status = status.into();
        let tid = s.turn.as_ref().unwrap().id.clone();
        if status == "waiting" {
            s.interactions=serde_json::from_value(json!([
              {"id":"i_a","turn_id":tid,"kind":"approval","call":{"SECRET":"SECRET"},"bundle_digest":"b","schema_digest":"s","response":null,"resource_policy_digest":"r"},
              {"id":"i_q","turn_id":tid,"kind":"question","call":{},"bundle_digest":"b","schema_digest":"s","response":null,"resource_policy_digest":"r"},
              {"id":"i_old","turn_id":"old","kind":"approval","call":{},"bundle_digest":"b","schema_digest":"s","response":null,"resource_policy_digest":"r"},
              {"id":"i_done","turn_id":tid,"kind":"approval","call":{},"bundle_digest":"b","schema_digest":"s","response":"allow","resource_policy_digest":"r"}
            ])).unwrap();
        }
        s.closed = id == "s_closed";
        store.save(&s, "checkpoint", json!({})).unwrap();
    }
    store
        .submit_turn(TurnSubmission {
            limits: None,
            session_id: "s_waiting".into(),
            submission_id: "two".into(),
            text: "SECRET".into(),
        })
        .unwrap();
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog; UPDATE audit SET created=now()-interval '60 seconds'; UPDATE submissions SET created=now()-interval '60 seconds'",c.schema)).unwrap();
    let snapshot = store.operational_snapshot(30, 30).unwrap();
    assert_eq!(
        (
            snapshot.active,
            snapshot.queued,
            snapshot.approvals,
            snapshot.questions,
            snapshot.stalled
        ),
        (1, 1, 1, 1, 1)
    );
    assert!(snapshot.oldest_queued_seconds >= 60);
    assert!(snapshot.observed_at > 1_700_000_000);
    let mut cancelled = store.get("s_waiting").unwrap();
    cancelled.turn.as_mut().unwrap().status = "cancelled".into();
    store
        .save(&cancelled, "turn_completed", json!({"status":"cancelled"}))
        .unwrap();
    let after_cancel = store.operational_snapshot(30, 30).unwrap();
    assert_eq!((after_cancel.approvals, after_cancel.questions), (0, 0));
    assert!(!serde_json::to_string(&snapshot).unwrap().contains("SECRET"));
    let s = store.get("s_active").unwrap();
    store
        .save(&s, "turn_recovered", json!({"outcome_unknown_count":2}))
        .unwrap();
    assert_eq!(
        store.operational_snapshot(30, 30).unwrap().unknown_recent,
        1
    );
    db.batch_execute("UPDATE audit SET created=now()-interval '60 seconds'")
        .unwrap();
    assert_eq!(
        store.operational_snapshot(30, 30).unwrap().unknown_recent,
        0
    );
    assert!(store.operational_snapshot(0, 30).is_err());
    let key = "a".repeat(64);
    store.auth_bootstrap(&key, "admin").unwrap();
    assert!(store
        .authorized(&key)
        .unwrap()
        .operational_snapshot(30, 30)
        .is_err());
    drop(store);
    db.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}
