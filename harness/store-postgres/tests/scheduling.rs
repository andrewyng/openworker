use openworker_runtime::{
    execution::Backend,
    store::{Session, StateStore},
};
use openworker_store_postgres::{worker::Selector, Config, PostgresStore, Tls, TurnSubmission};
use serde_json::json;
use std::sync::{Arc, Barrier};
fn selector() -> Selector {
    Selector {
        coworker: "test".into(),
        bundle_digest: "bundle".into(),
        backend: Backend::Off,
        scripted: true,
        fallback_model: "fixture".into(),
        model_provider: Default::default(),
        model_connections: Default::default(),
        environment_provider: None,
        environment_identity: None,
        environment_profile: None,
    }
}
fn fixture() -> (Config, PostgresStore) {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_sched_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let mut owner = PostgresStore::open(c.clone()).unwrap();
    for id in ["s_one", "s_two"] {
        let s:Session=serde_json::from_value(json!({"id":id,"name":"Schedule","workspace":"/test","coworker":"test","bundle_digest":"bundle","scripted":true,"model":"fixture","execution_backend":"off","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        owner.save(&s, "session_created", json!({})).unwrap();
        owner
            .submit_turn(TurnSubmission {
                limits: None,
                session_id: id.into(),
                submission_id: "one".into(),
                text: "hello".into(),
            })
            .unwrap();
        owner.release(id).unwrap();
    }
    (c, owner)
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn competing_slots_claim_distinct_compatible_sessions_once() {
    let (c, _) = fixture();
    let a = PostgresStore::open(c.clone()).unwrap();
    let b = PostgresStore::open(c.clone()).unwrap();
    let barrier = Arc::new(Barrier::new(3));
    let aa = barrier.clone();
    let bb = barrier.clone();
    let x = std::thread::spawn(move || {
        aa.wait();
        a.claim_next(selector()).unwrap().unwrap()
    });
    let y = std::thread::spawn(move || {
        bb.wait();
        b.claim_next(selector()).unwrap().unwrap()
    });
    barrier.wait();
    let x = x.join().unwrap();
    let y = y.join().unwrap();
    assert_ne!(x.session_id, y.session_id);
    assert!(PostgresStore::open(c)
        .unwrap()
        .claim_next(selector())
        .unwrap()
        .is_none());
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn incompatible_pools_and_parked_approval_do_not_spin_or_steal() {
    let (c, _) = fixture();
    let mut worker = PostgresStore::open(c.clone()).unwrap();
    let mut wrong = selector();
    wrong.bundle_digest = "different".into();
    assert!(worker.claim_next(wrong).unwrap().is_none());
    let mut wrong = selector();
    wrong.fallback_model = "different".into();
    assert!(worker.claim_next(wrong).unwrap().is_none());
    let mut wrong = selector();
    wrong.model_provider = openworker_runtime::provider::Kind::Anthropic;
    assert!(worker.claim_next(wrong).unwrap().is_none());
    let mut wrong = selector();
    wrong.environment_provider = Some("remote".into());
    wrong.environment_identity = Some("cluster".into());
    wrong.environment_profile = Some("profile".into());
    assert!(worker.claim_next(wrong).unwrap().is_none());
    let lease = worker.claim_next(selector()).unwrap().unwrap();
    assert!(worker.claim_next(selector()).is_err());
    let (mut s, _, _) = worker.accept(&lease.session_id, "one", "hello").unwrap();
    s.turn.as_mut().unwrap().status = "waiting".into();
    worker.save(&s, "checkpoint", json!({})).unwrap();
    worker
        .submit_turn(TurnSubmission {
            limits: None,
            session_id: s.id.clone(),
            submission_id: "two".into(),
            text: "later".into(),
        })
        .unwrap();
    worker.release(&s.id).unwrap();
    let other = worker.claim_next(selector()).unwrap().unwrap();
    assert_ne!(other.session_id, s.id);
    let mut blocked = worker.get(&other.session_id).unwrap();
    blocked.provider_blocked = true;
    worker
        .save(&blocked, "provider_blocked", json!({}))
        .unwrap();
    worker.release(&other.session_id).unwrap();
    assert!(PostgresStore::open(c)
        .unwrap()
        .claim_next(selector())
        .unwrap()
        .is_none());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn managed_agent_and_reviewer_identities_must_both_match() {
    let (c, mut owner) = fixture();
    for id in ["s_one", "s_two"] {
        owner.claim(id).unwrap();
        let mut session = owner.get(id).unwrap();
        session.model_selection = Some(openworker_runtime::connections::Selection {
            provider: openworker_runtime::provider::Kind::Anthropic,
            connection: "anthropic".into(),
            model: "claude-test".into(),
            identity: "agent-identity".into(),
        });
        session.reviewer_selection = Some(openworker_runtime::connections::Selection {
            provider: openworker_runtime::provider::Kind::Openai,
            connection: "reviewer".into(),
            model: "openai-test".into(),
            identity: "reviewer-identity".into(),
        });
        owner
            .save(&session, "test_connection_binding", json!({}))
            .unwrap();
        owner.release(id).unwrap();
    }
    let worker = PostgresStore::open(c).unwrap();
    assert!(worker.claim_next(selector()).unwrap().is_none());
    let mut compatible = selector();
    compatible
        .model_connections
        .insert("anthropic".into(), "agent-identity".into());
    assert!(worker.claim_next(compatible.clone()).unwrap().is_none());
    compatible
        .model_connections
        .insert("reviewer".into(), "wrong-identity".into());
    assert!(worker.claim_next(compatible.clone()).unwrap().is_none());
    compatible
        .model_connections
        .insert("reviewer".into(), "reviewer-identity".into());
    assert!(worker.claim_next(compatible).unwrap().is_some());
}
