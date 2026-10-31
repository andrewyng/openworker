use openworker_runtime::store::{Session, StateStore, Store};
use openworker_store_postgres::{Config, PostgresStore, Tls};
use serde_json::json;
use std::time::Duration;
fn config() -> Config {
    let url =
        std::env::var("OW_PG_TEST_URL").expect("Use a dedicated disposable PostgreSQL database");
    let mut c = Config::new(url, format!("ow_test_{}", uuid::Uuid::new_v4().simple()));
    c.tls = Tls::LoopbackPlaintext;
    c.timeout = Duration::from_secs(3);
    c
}
fn fixture() -> (Config, PostgresStore) {
    let c = config();
    PostgresStore::migrate(&c).unwrap();
    PostgresStore::migrate(&c).unwrap();
    let p = PostgresStore::open(c.clone()).unwrap();
    (c, p)
}
fn session(id: &str) -> Session {
    serde_json::from_value(json!({"id":id,"name":"Test","workspace":"/work","coworker":"test","bundle_digest":"bundle","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap()
}
fn admin(c: &Config) -> postgres::Client {
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    db
}
fn expire(c: &Config, sid: &str) {
    admin(c)
        .execute(
            "UPDATE sessions SET expires=clock_timestamp()-interval '1 second' WHERE id=$1",
            &[&sid],
        )
        .unwrap();
}
fn exercise(store: &mut dyn StateStore) {
    store
        .save(&session("s_test"), "session_created", json!({}))
        .unwrap();
    let (s, fresh, _) = store.accept("s_test", "submission", "Hello").unwrap();
    assert!(fresh);
    let (again, fresh, _) = store.accept("s_test", "submission", "Hello").unwrap();
    assert!(!fresh);
    assert_eq!(s.turn.unwrap().id, again.turn.unwrap().id);
    assert!(store.accept("s_test", "submission", "Different").is_err());
    assert!(store.accept("s_test", "busy", "New task").is_err());
    store.start_attempt("s_test", "attempt", 1).unwrap();
    store.attempt_delta("s_test", "attempt", "abc").unwrap();
    store
        .finish_attempt("s_test", "attempt", "completed", None)
        .unwrap();
    assert_eq!(store.attempts("s_test").unwrap()[0]["text"], "abc");
    let mut s = store.get("s_test").unwrap();
    s.history
        .push(json!({"type":"reasoning","encrypted_content":"PRIVATE_REASONING"}));
    s.history.push(json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"Done"}]}));
    s.turn.as_mut().unwrap().status = "completed".into();
    store
        .save(&s, "turn_completed", json!({"status":"completed"}))
        .unwrap();
    let page = store.history_page("s_test", 0).unwrap();
    assert!(!page.to_string().contains("PRIVATE_REASONING"));
    assert_eq!(page["total_items"], 3);
    assert_eq!(page["items"].as_array().unwrap().len(), 2);
    assert_eq!(store.recent_messages("s_test").unwrap().0.len(), 2);
    assert!(!store.transcript_excerpt("s_test", 0, 0).unwrap()["text"]
        .as_str()
        .unwrap()
        .is_empty());
    let (new, fresh, _) = store.accept("s_test", "next", "Next task").unwrap();
    assert!(fresh);
    let (old, fresh, _) = store.accept("s_test", "submission", "Hello").unwrap();
    assert!(!fresh);
    assert_ne!(old.turn.unwrap().id, new.turn.unwrap().id);
    assert!(store.events("s_test", -1).is_err());
    assert!(store.events("s_test", i64::MAX).is_err());
    let events = store.events("s_test", 0).unwrap();
    assert_eq!(
        store.cursor("s_test").unwrap(),
        events.last().unwrap().event_id
    );
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn sqlite_postgres_domain_conformance() {
    let tmp = tempfile::tempdir().unwrap();
    exercise(&mut Store::open(tmp.path()).unwrap());
    let (_, mut pg) = fixture();
    exercise(&mut pg);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn durable_plan_atomicity_non_owner_snapshot_and_fenced_takeover() {
    let (c, mut owner) = fixture();
    let api = PostgresStore::open(c.clone()).unwrap();
    let mut initial = session("s_plan");
    initial.package.planning = true;
    owner.save(&initial, "session_created", json!({})).unwrap();
    let (mut s, _, _) = owner.accept("s_plan", "submit", "Inspect only").unwrap();
    let tid = s.turn.as_ref().unwrap().id.clone();
    let args = json!({"expected_revision":0,"explanation":"PRIVATE_PLAN_MARKER","steps":[{"id":"inspect","description":"Read files","status":"in_progress"}]});
    let call = json!({"type":"function_call","name":"update_plan","call_id":"plan-call","arguments":args.to_string()});
    s.history.push(call.clone());
    s.turn.as_mut().unwrap().queue = vec![call];
    s.turn.as_mut().unwrap().status = "running".into();
    owner.save(&s, "fixture_queue", json!({})).unwrap();
    let mut s = owner.get("s_plan").unwrap();
    let before = s.clone();
    let plan = openworker_runtime::planning::next(None, &tid, "plan-call", &args).unwrap();
    s.plan = Some(plan.clone());
    s.history.push(json!({"type":"function_call_output","call_id":"plan-call","output":json!({"plan":plan,"advisory":true}).to_string()}));
    s.turn.as_mut().unwrap().queue.clear();
    let mut db = admin(&c);
    db.batch_execute("CREATE FUNCTION reject_plan_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test failure'; END $$; CREATE TRIGGER reject_plan_event BEFORE INSERT ON events FOR EACH ROW EXECUTE FUNCTION reject_plan_event();").unwrap();
    assert!(owner
        .save(&s, "plan_updated", json!({"plan":plan}))
        .is_err());
    let failed = api.inspect("s_plan").unwrap();
    assert!(failed.plan.is_none());
    assert_eq!(failed.history, before.history);
    assert_eq!(
        failed.turn.as_ref().unwrap().queue,
        before.turn.as_ref().unwrap().queue
    );
    assert_eq!(failed.storage_revision, before.storage_revision);
    db.batch_execute("DROP TRIGGER reject_plan_event ON events")
        .unwrap();
    // A failed transaction poisons the adapter by design. Reconnect, fence the
    // failed owner and reconcile the retained queue before an explicit retry.
    assert!(owner.get("s_plan").is_err());
    expire(&c, "s_plan");
    owner = PostgresStore::open(c.clone()).unwrap();
    owner.claim("s_plan").unwrap();
    s.storage_revision = owner.get("s_plan").unwrap().storage_revision;
    let event = owner
        .save(&s, "plan_updated", json!({"plan":plan}))
        .unwrap();
    let snapshot = api.read_snapshot("s_plan").unwrap();
    assert_eq!(snapshot["plan"], json!(plan));
    assert_eq!(snapshot["cursor"], event.event_id);
    assert!(!api
        .session_page(None, 10)
        .unwrap()
        .to_string()
        .contains("PRIVATE_PLAN_MARKER"));
    let stale = owner.get("s_plan").unwrap();
    expire(&c, "s_plan");
    let mut next = PostgresStore::open(c).unwrap();
    next.claim("s_plan").unwrap();
    next.recover().unwrap();
    assert!(owner
        .save(&stale, "plan_updated", json!({"plan":plan}))
        .is_err());
    let recovered = next.get("s_plan").unwrap();
    assert_eq!(recovered.plan.as_ref(), Some(&plan));
    assert!(recovered.turn.as_ref().unwrap().queue.is_empty());
    assert_eq!(
        next.events("s_plan", 0)
            .unwrap()
            .iter()
            .filter(|e| e.kind == "plan_updated")
            .count(),
        1
    );
    assert_eq!(api.read_snapshot("s_plan").unwrap()["plan"], json!(plan));
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn revisions_ownership_and_recovery_are_session_scoped() {
    let (c, mut a) = fixture();
    let mut b = PostgresStore::open(c.clone()).unwrap();
    a.save(&session("s_a"), "session_created", json!({}))
        .unwrap();
    b.save(&session("s_b"), "session_created", json!({}))
        .unwrap();
    assert!(b.claim("s_a").is_err());
    assert!(b.get("s_a").is_err());
    let stale = a.get("s_a").unwrap();
    a.append_event("s_a", "progress", json!({})).unwrap();
    // Journal-only progress changes the event cursor, not canonical metadata.
    // A real metadata write must still invalidate the old snapshot.
    let mut updated = a.get("s_a").unwrap();
    assert_eq!(updated.storage_revision, stale.storage_revision);
    updated.name = "Renamed".into();
    a.save(&updated, "renamed", json!({})).unwrap();
    assert!(a.save(&stale, "stale", json!({})).is_err());
    a.accept("s_a", "a", "Hello").unwrap();
    b.accept("s_b", "b", "Other").unwrap();
    b.recover().unwrap();
    assert_eq!(a.get("s_a").unwrap().turn.unwrap().status, "accepted");
    let old = a.renew("s_a").unwrap();
    expire(&c, "s_a");
    let new = b.claim("s_a").unwrap();
    assert!(new.epoch > old.epoch);
    assert!(a.renew("s_a").is_err());
    assert!(a.append_event("s_a", "tool_started", json!({})).is_err());
    b.recover().unwrap();
    assert_eq!(b.get("s_a").unwrap().turn.unwrap().status, "interrupted");
    let cursor = b.cursor("s_a").unwrap();
    b.recover().unwrap();
    assert_eq!(b.cursor("s_a").unwrap(), cursor);
    assert_eq!(PostgresStore::open(c).unwrap().list().unwrap().len(), 0);
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn approval_and_unknown_result_survive_takeover_without_replay() {
    let (c, mut a) = fixture();
    a.save(&session("s_test"), "session_created", json!({}))
        .unwrap();
    let (mut s, _, _) = a.accept("s_test", "submit", "secret prompt").unwrap();
    let tid = s.turn.as_ref().unwrap().id.clone();
    let call = json!({"type":"function_call","call_id":"c_one","name":"write_file","arguments":"SECRET_ARGUMENTS"});
    s.history.push(call.clone());
    s.turn.as_mut().unwrap().queue = vec![call.clone()];
    s.turn.as_mut().unwrap().status = "waiting".into();
    s.interactions.push(serde_json::from_value(json!({"id":"i_one","turn_id":tid,"kind":"approval","call":call,"bundle_digest":"bundle","schema_digest":"schema","response":null,"resource_policy_digest":"resource"})).unwrap());
    a.save(
        &s,
        "interaction_required",
        json!({"private":"SECRET_ARGUMENTS"}),
    )
    .unwrap();
    a.release("s_test").unwrap();
    let mut b = PostgresStore::open(c.clone()).unwrap();
    b.claim("s_test").unwrap();
    b.recover().unwrap();
    let mut s = b.get("s_test").unwrap();
    assert_eq!(s.turn.as_ref().unwrap().status, "recoverable");
    assert_eq!(s.interactions[0].id, "i_one");
    assert!(s.interactions[0].response.is_none());
    s.interactions[0].response = Some("once".into());
    s.interactions[0].origin = Some("human".into());
    s.turn.as_mut().unwrap().status = "executing".into();
    b.save(
        &s,
        "tool_started",
        json!({"call_id":"c_one","arguments":"SECRET_ARGUMENTS"}),
    )
    .unwrap();
    expire(&c, "s_test");
    let mut next = PostgresStore::open(c.clone()).unwrap();
    next.claim("s_test").unwrap();
    next.recover().unwrap();
    let recovered = next.get("s_test").unwrap();
    assert_eq!(recovered.interactions[0].response.as_deref(), Some("once"));
    assert_eq!(recovered.turn.unwrap().status, "interrupted");
    let result = recovered.history.last().unwrap();
    assert_eq!(result["call_id"], "c_one");
    assert!(result["output"]
        .as_str()
        .unwrap()
        .contains("tool_outcome_unknown"));
    let length = recovered.history.len();
    next.recover().unwrap();
    assert_eq!(next.get("s_test").unwrap().history.len(), length);
    let audit = next.audit("s_test", 0).unwrap();
    let raw = serde_json::to_string(&audit).unwrap();
    assert!(!raw.contains("SECRET_ARGUMENTS"));
    assert!(!raw.contains("secret prompt"));
    assert_eq!(audit.len(), next.events("s_test", 0).unwrap().len());
    assert!(b
        .save(&s, "tool_finished", json!({"success":true}))
        .is_err());
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn audit_failure_rolls_back_state_history_and_event() {
    let (c, mut a) = fixture();
    a.save(&session("s_test"), "session_created", json!({}))
        .unwrap();
    let mut s = a.get("s_test").unwrap();
    s.name = "CHANGED".into();
    s.history
        .push(json!({"role":"user","content":"secret content"}));
    admin(&c).batch_execute("CREATE FUNCTION fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'DO_NOT_EXPOSE_SQL_SECRET'; END $$; CREATE TRIGGER fail_audit BEFORE INSERT ON audit FOR EACH ROW EXECUTE FUNCTION fail_audit()").unwrap();
    let e = a
        .save(&s, "changed", json!({"private":"secret"}))
        .unwrap_err()
        .to_string();
    assert!(!e.contains("DO_NOT_EXPOSE_SQL_SECRET"));
    let b = PostgresStore::open(c.clone()).unwrap();
    let saved = b.inspect("s_test").unwrap();
    assert_eq!(saved.name, "Test");
    assert!(saved.history.is_empty());
    assert_eq!(saved.storage_revision, 1);
    assert_eq!(b.audit("s_test", 0).unwrap().len(), 1);
    assert!(a
        .get("s_test")
        .unwrap_err()
        .to_string()
        .contains("unusable"));
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn history_prefix_and_customer_deletion_fail_explicitly() {
    let (c, mut a) = fixture();
    a.save(&session("s_test"), "session_created", json!({}))
        .unwrap();
    a.accept("s_test", "s", "one").unwrap();
    let mut s = a.get("s_test").unwrap();
    s.history[0]["content"] = json!("rewritten");
    assert!(a.save(&s, "bad", json!({})).is_err());
    admin(&c)
        .batch_execute("DELETE FROM audit WHERE cursor=1; DELETE FROM events WHERE cursor=1")
        .unwrap();
    assert!(a
        .events("s_test", 0)
        .unwrap_err()
        .to_string()
        .contains("gap"));
    assert!(a
        .audit("s_test", 0)
        .unwrap_err()
        .to_string()
        .contains("gap"));
    assert!(a.audit("s_test", -1).is_err());
    admin(&c)
        .execute("DELETE FROM history WHERE session_id='s_test'", &[])
        .unwrap();
    assert!(a.get("s_test").unwrap_err().to_string().contains("missing"));
    assert!(a.history_page("s_test", 0).is_err());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn blocked_database_operation_is_bounded_and_never_retried() {
    let (mut c, mut a) = fixture();
    a.save(&session("s_test"), "session_created", json!({}))
        .unwrap();
    a.release("s_test").unwrap();
    c.timeout = std::time::Duration::from_millis(800);
    let mut worker = PostgresStore::open(c.clone()).unwrap();
    worker.claim("s_test").unwrap();
    let before = worker.cursor("s_test").unwrap();
    let mut client = admin(&c);
    let mut blocker = client.transaction().unwrap();
    blocker
        .query_one("SELECT id FROM sessions WHERE id='s_test' FOR UPDATE", &[])
        .unwrap();
    let start = std::time::Instant::now();
    assert!(worker
        .append_event("s_test", "must_not_commit", json!({}))
        .is_err());
    assert!(start.elapsed() < std::time::Duration::from_secs(3));
    blocker.rollback().unwrap();
    assert!(worker.get("s_test").is_err());
    let observer = PostgresStore::open(c).unwrap();
    assert_eq!(observer.inspect("s_test").unwrap().storage_revision, before);
    assert_eq!(observer.audit("s_test", before).unwrap().len(), 0);
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn concurrent_claims_have_exactly_one_owner() {
    let (c, mut a) = fixture();
    a.save(&session("s_test"), "session_created", json!({}))
        .unwrap();
    a.release("s_test").unwrap();
    let b = PostgresStore::open(c.clone()).unwrap();
    let d = PostgresStore::open(c).unwrap();
    let barrier = std::sync::Arc::new(std::sync::Barrier::new(3));
    let b1 = barrier.clone();
    let b2 = barrier.clone();
    let t1 = std::thread::spawn(move || {
        b1.wait();
        b.claim("s_test").is_ok()
    });
    let t2 = std::thread::spawn(move || {
        b2.wait();
        d.claim("s_test").is_ok()
    });
    barrier.wait();
    assert_eq!(
        u8::from(t1.join().unwrap()) + u8::from(t2.join().unwrap()),
        1
    );
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn schema_tls_and_executor_modes_fail_closed() {
    let (c, a) = fixture();
    let mut bad = c.clone();
    bad.schema = "public;DROP".into();
    assert!(PostgresStore::open(bad).is_err());
    let mut secure = c.clone();
    secure.tls = Tls::Verified { ca_file: None };
    assert!(PostgresStore::open(secure).is_err());
    let mut remote = c.clone();
    remote.connection = zeroize::Zeroizing::new("host=example.com user=postgres".into());
    assert!(PostgresStore::open(remote).is_err());
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    rt.block_on(async {
        assert!(a.list().unwrap_err().to_string().contains("multi-threaded"));
    });
    admin(&c)
        .execute("UPDATE version SET version=99", &[])
        .unwrap();
    assert!(PostgresStore::migrate(&c).is_err());
    assert!(PostgresStore::open(c).is_err());
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn lost_database_connection_is_poisoned_without_retry() {
    let (c, mut a) = fixture();
    a.save(&session("s_test"), "session_created", json!({}))
        .unwrap();
    let lease = a.renew("s_test").unwrap();
    admin(&c)
        .query(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name=$1",
            &[&format!("ow-store-{}", lease.owner)],
        )
        .unwrap();
    assert!(a.append_event("s_test", "tool_started", json!({})).is_err());
    assert!(a.get("s_test").is_err());
    let fresh = PostgresStore::open(c).unwrap();
    assert_eq!(fresh.inspect("s_test").unwrap().storage_revision, 1);
}
