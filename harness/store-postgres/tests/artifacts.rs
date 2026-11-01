use openworker_artifacts::{hash, Binding, Expected, Proof, Registration, Status};
use openworker_runtime::store::{Session, StateStore};
use openworker_store_postgres::{Config, PostgresStore, Tls, TurnSubmission};
use serde_json::json;
use std::sync::{Arc, Barrier};

fn fixture() -> (Config, PostgresStore, Session, Registration) {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_art_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let mut owner = PostgresStore::open(c.clone()).unwrap();
    let s: Session = serde_json::from_value(json!({"id":"s_artifacts","name":"Artifacts","workspace":"/work","coworker":"test","bundle_digest":"test","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
    owner.save(&s, "session_created", json!({})).unwrap();
    owner
        .submit_turn(TurnSubmission {
            limits: None,
            session_id: s.id.clone(),
            submission_id: "one".into(),
            text: "make file".into(),
        })
        .unwrap();
    let (s, _, _) = owner.accept(&s.id, "one", "make file").unwrap();
    let r = Registration {
        origin: Default::default(),
        request_id: "upload-one".into(),
        session_id: s.id.clone(),
        turn_id: s.turn.as_ref().unwrap().id.clone(),
        name: "output.txt".into(),
        content_type: "text/plain".into(),
        expected: Expected {
            size: 5,
            sha256: hash(b"hello"),
        },
    };
    (c, owner, s, r)
}
fn binding() -> Binding {
    Binding {
        deployment: "test".into(),
        identity: hash(b"store"),
    }
}
fn admin(c: &Config) -> postgres::Client {
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    db
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn catalog_races_idempotency_restart_and_worker_revision() {
    let (c, mut owner, mut session, r) = fixture();
    let gate = Arc::new(Barrier::new(3));
    let tasks: Vec<_> = (0..2)
        .map(|_| {
            let gate = gate.clone();
            let c = c.clone();
            let r = r.clone();
            std::thread::spawn(move || {
                let api = PostgresStore::open(c).unwrap();
                gate.wait();
                api.register_artifact(r, binding(), "app").unwrap()
            })
        })
        .collect();
    gate.wait();
    let mut results = tasks.into_iter().map(|t| t.join().unwrap());
    let a = results.next().unwrap();
    assert_eq!(a, results.next().unwrap());
    assert_eq!(a.status, Status::Pending);
    let api = PostgresStore::open(c.clone()).unwrap();
    let mut changed = r.clone();
    changed.name = "different.txt".into();
    assert!(api.register_artifact(changed, binding(), "app").is_err());
    assert!(api
        .register_artifact(r.clone(), binding(), "other")
        .is_err());
    let mut changed = r.clone();
    changed.request_id = "other".into();
    changed.turn_id = "t_wrong".into();
    assert!(api.register_artifact(changed, binding(), "app").is_err());
    let mut other = session.clone();
    other.id = "s_other".into();
    other.storage_revision = 0;
    other.turn = None;
    owner.save(&other, "session_created", json!({})).unwrap();
    assert!(api.artifact(&other.id, &a.id).is_err());
    let proof = Proof {
        expected: r.expected.clone(),
        manifest_sha256: hash(b"manifest"),
    };
    let mut wrong = proof.clone();
    wrong.expected.sha256 = hash(b"wrong");
    assert!(api
        .publish_artifact(&session.id, &a.id, wrong, "app")
        .is_err());
    // Artifact cursor updates must not invalidate the worker's canonical revision.
    session.turn.as_mut().unwrap().status = "completed".into();
    owner
        .save(&session, "turn_completed", json!({"status":"completed"}))
        .unwrap();
    let ready = api
        .publish_artifact(&session.id, &a.id, proof.clone(), "app")
        .unwrap();
    let cursor = api.read_snapshot(&session.id).unwrap()["cursor"].clone();
    assert_eq!(
        api.publish_artifact(&session.id, &a.id, proof, "app")
            .unwrap(),
        ready
    );
    assert_eq!(api.read_snapshot(&session.id).unwrap()["cursor"], cursor);
    let reopened = PostgresStore::open(c).unwrap();
    assert_eq!(
        reopened.artifacts(&session.id).unwrap(),
        vec![ready.clone()]
    );
    assert_eq!(
        reopened.register_artifact(r, binding(), "app").unwrap(),
        ready
    );
    let events = reopened.read_events(&session.id, 0).unwrap();
    assert_eq!(
        events
            .iter()
            .filter(|e| e.kind == "artifact_pending")
            .count(),
        1
    );
    assert_eq!(
        events.iter().filter(|e| e.kind == "artifact_ready").count(),
        1
    );
    assert!(!format!("{events:?}").contains("output.txt"));
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn catalog_audit_rollback_and_missing_receipts_fail_closed() {
    let (c, _, s, r) = fixture();
    let api = PostgresStore::open(c.clone()).unwrap();
    let mut db = admin(&c);
    let cursor = api.read_snapshot(&s.id).unwrap()["cursor"].clone();
    db.batch_execute("CREATE FUNCTION reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fault'; END $$; CREATE TRIGGER reject_audit BEFORE INSERT ON audit FOR EACH ROW EXECUTE FUNCTION reject_audit()").unwrap();
    assert!(api.register_artifact(r.clone(), binding(), "app").is_err());
    assert_eq!(
        db.query_one("SELECT count(*) FROM artifacts", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
    db.batch_execute("DROP TRIGGER reject_audit ON audit")
        .unwrap();
    let api = PostgresStore::open(c.clone()).unwrap();
    assert_eq!(api.read_snapshot(&s.id).unwrap()["cursor"], cursor);
    let a = api.register_artifact(r.clone(), binding(), "app").unwrap();
    db.batch_execute("CREATE TRIGGER reject_audit BEFORE INSERT ON audit FOR EACH ROW EXECUTE FUNCTION reject_audit()").unwrap();
    assert!(api
        .publish_artifact(
            &s.id,
            &a.id,
            Proof {
                expected: r.expected.clone(),
                manifest_sha256: hash(b"manifest")
            },
            "app"
        )
        .is_err());
    db.batch_execute("DROP TRIGGER reject_audit ON audit")
        .unwrap();
    let api = PostgresStore::open(c).unwrap();
    assert_eq!(api.artifact(&s.id, &a.id).unwrap().status, Status::Pending);
    db.execute(
        "DELETE FROM artifact_requests WHERE artifact_id=$1",
        &[&a.id],
    )
    .unwrap();
    assert!(api.artifact(&s.id, &a.id).is_err());
    assert!(api.register_artifact(r, binding(), "app").is_err());
    assert_eq!(
        db.query_one("SELECT count(*) FROM artifacts", &[])
            .unwrap()
            .get::<_, i64>(0),
        1
    );
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn extension_is_explicit_and_preserves_existing_sessions() {
    let (c, _, s, r) = fixture();
    let mut db = admin(&c);
    db.batch_execute("DROP TABLE artifact_requests,artifacts,artifact_totals,artifact_version")
        .unwrap();
    let api = PostgresStore::open(c.clone()).unwrap();
    assert_eq!(api.read_snapshot(&s.id).unwrap()["session"]["id"], s.id);
    assert!(api.register_artifact(r.clone(), binding(), "app").is_err());
    PostgresStore::migrate(&c).unwrap();
    let api = PostgresStore::open(c).unwrap();
    api.register_artifact(r, binding(), "app").unwrap();
    assert_eq!(api.read_snapshot(&s.id).unwrap()["session"]["id"], s.id);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn a_setup_origin_label_is_not_authority_to_register_or_publish_source() {
    let (c, p, s, mut r) = fixture();
    r.origin = openworker_artifacts::Origin::TeamSetup {
        team_id: "at_unregistered".into(),
        setup_id: "source_unregistered".into(),
    };
    r.turn_id.clear();
    r.content_type = "application/x-git-bundle".into();
    r.validate().unwrap();
    assert_eq!(
        p.register_artifact(r.clone(), binding(), "test")
            .unwrap_err()
            .downcast_ref::<openworker_artifacts::Error>(),
        Some(&openworker_artifacts::Error::Invalid)
    );
    assert!(p.artifacts(&s.id).unwrap().is_empty());
    r.origin = Default::default();
    r.turn_id = s.turn.as_ref().unwrap().id.clone();
    let a = p.register_artifact(r, binding(), "test").unwrap();
    assert_eq!(a.status, Status::Pending);
    admin(&c)
        .batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}
