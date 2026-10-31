use openworker_runtime::store::{Session, StateStore};
use openworker_store_postgres::{
    Config, PostgresStore, SubmissionError, SubmissionStatus as Status, Tls, TurnSubmission,
};
use serde_json::json;
use std::sync::{Arc, Barrier};

fn fixture(limit: u16) -> (Config, PostgresStore) {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").expect("Use a disposable DB"),
        format!("ow_queue_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    c.max_queued_turns = limit;
    PostgresStore::migrate(&c).unwrap();
    let mut store = PostgresStore::open(c.clone()).unwrap();
    let s: Session=serde_json::from_value(json!({"id":"s_queue","name":"Queue","workspace":"/work","coworker":"test","bundle_digest":"test","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
    store.save(&s, "session_created", json!({})).unwrap();
    (c, store)
}
fn admin(c: &Config) -> postgres::Client {
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    db
}
fn request(id: &str, text: &str) -> TurnSubmission {
    TurnSubmission {
        limits: None,
        session_id: "s_queue".into(),
        submission_id: id.into(),
        text: text.into(),
    }
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn twelve_producers_respect_queue_bound_and_retries_remain_available_when_full() {
    let (config, mut owner) = fixture(4);
    let barrier = Arc::new(Barrier::new(13));
    let producers: Vec<_> = (0..12)
        .map(|n| {
            let config = config.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                let api = PostgresStore::open(config).unwrap();
                let request = request(&format!("burst-{n}"), "bounded task");
                barrier.wait();
                (request.clone(), api.submit_turn(request))
            })
        })
        .collect();
    barrier.wait();
    let outcomes: Vec<_> = producers.into_iter().map(|p| p.join().unwrap()).collect();
    let admitted: Vec<_> = outcomes
        .iter()
        .filter_map(|(r, s)| s.as_ref().ok().map(|s| (r, s)))
        .collect();
    assert_eq!(admitted.len(), 4);
    for (_, result) in outcomes.iter().filter(|(_, s)| s.is_err()) {
        assert_eq!(
            result
                .as_ref()
                .unwrap_err()
                .downcast_ref::<SubmissionError>(),
            Some(&SubmissionError::QueueFull)
        );
    }
    let mut sequence: Vec<_> = admitted.iter().map(|(_, s)| s.sequence).collect();
    sequence.sort();
    assert_eq!(sequence, vec![1, 2, 3, 4]);
    let api = PostgresStore::open(config.clone()).unwrap();
    for (original, receipt) in admitted {
        assert_eq!(
            api.submit_turn(original.clone()).unwrap().turn_id,
            receipt.turn_id
        );
        let mut changed = original.clone();
        changed.text = "changed payload".into();
        error(api.submit_turn(changed), SubmissionError::Conflict);
    }
    error(
        api.submit_turn(request("fifth", "new task")),
        SubmissionError::QueueFull,
    );
    let mut other = owner.get("s_queue").unwrap();
    other.id = "s_other_burst".into();
    other.storage_revision = 0;
    owner.save(&other, "session_created", json!({})).unwrap();
    let mut independent = request("independent", "other session");
    independent.session_id = other.id.clone();
    assert_eq!(api.submit_turn(independent).unwrap().sequence, 1);
    assert_eq!(
        api.read_events("s_queue", 0)
            .unwrap()
            .iter()
            .filter(|e| e.kind == "command_accepted")
            .count(),
        4
    );
}
fn error<T>(result: anyhow::Result<T>, expected: SubmissionError) {
    let e = result.err().expect("operation unexpectedly succeeded");
    assert_eq!(e.downcast_ref::<SubmissionError>(), Some(&expected), "{e}");
}
fn complete(owner: &mut PostgresStore) {
    let mut s = owner.get("s_queue").unwrap();
    s.turn.as_mut().unwrap().status = "completed".into();
    owner
        .save(&s, "turn_completed", json!({"status":"completed"}))
        .unwrap();
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn competing_payloads_and_capacity_are_serialized_and_session_scoped() {
    let (c, mut owner) = fixture(1);
    let a = PostgresStore::open(c.clone()).unwrap();
    let b = PostgresStore::open(c.clone()).unwrap();
    let gate = Arc::new(Barrier::new(3));
    let ga = gate.clone();
    let gb = gate.clone();
    let first = std::thread::spawn(move || {
        ga.wait();
        a.submit_turn(request("stable", "one"))
    });
    let second = std::thread::spawn(move || {
        gb.wait();
        b.submit_turn(request("stable", "two"))
    });
    gate.wait();
    let outcomes = [first.join().unwrap(), second.join().unwrap()];
    assert_eq!(outcomes.iter().filter(|r| r.is_ok()).count(), 1);
    assert!(outcomes
        .iter()
        .filter_map(|r| r.as_ref().err())
        .all(|e| e.downcast_ref::<SubmissionError>() == Some(&SubmissionError::Conflict)));
    let api = PostgresStore::open(c.clone()).unwrap();
    error(
        api.submit_turn(request("other", "third")),
        SubmissionError::QueueFull,
    );
    let mut other = owner.get("s_queue").unwrap();
    other.id = "s_other".into();
    other.storage_revision = 0;
    owner.save(&other, "session_created", json!({})).unwrap();
    let mut req = request("stable", "independent");
    req.session_id = "s_other".into();
    let other_receipt = api.submit_turn(req).unwrap();
    assert_ne!(
        api.submission("s_queue", "stable").unwrap().turn_id,
        other_receipt.turn_id
    );
    assert_eq!(other_receipt.sequence, 1);
    assert!(owner.next_turn("s_queue").unwrap().is_some());
    assert!(owner.next_turn("s_other").unwrap().is_some());
    let (c, _owner) = fixture(1);
    let a = PostgresStore::open(c.clone()).unwrap();
    let b = PostgresStore::open(c).unwrap();
    let gate = Arc::new(Barrier::new(3));
    let ga = gate.clone();
    let gb = gate.clone();
    let first = std::thread::spawn(move || {
        ga.wait();
        a.submit_turn(request("different-a", "A"))
    });
    let second = std::thread::spawn(move || {
        gb.wait();
        b.submit_turn(request("different-b", "B"))
    });
    gate.wait();
    let outcomes = [first.join().unwrap(), second.join().unwrap()];
    assert_eq!(outcomes.iter().filter(|r| r.is_ok()).count(), 1);
    assert!(outcomes
        .iter()
        .filter_map(|r| r.as_ref().err())
        .all(|e| e.downcast_ref::<SubmissionError>() == Some(&SubmissionError::QueueFull)));
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn concurrent_api_nodes_create_one_receipt_and_one_turn() {
    let (c, mut owner) = fixture(100);
    let a = PostgresStore::open(c.clone()).unwrap();
    let b = PostgresStore::open(c.clone()).unwrap();
    let gate = Arc::new(Barrier::new(3));
    let ga = gate.clone();
    let gb = gate.clone();
    let first = std::thread::spawn(move || {
        ga.wait();
        a.submit_turn(request("stable", "sensitive prompt"))
            .unwrap()
    });
    let second = std::thread::spawn(move || {
        gb.wait();
        b.submit_turn(request("stable", "sensitive prompt"))
            .unwrap()
    });
    gate.wait();
    let receipt = first.join().unwrap();
    assert_eq!(receipt, second.join().unwrap());
    assert_eq!(receipt.status, Status::Queued);
    assert_eq!(receipt.sequence, 1);
    assert!(owner.get("s_queue").unwrap().turn.is_none());
    assert_eq!(
        owner
            .events("s_queue", 0)
            .unwrap()
            .iter()
            .filter(|e| e.kind == "command_accepted")
            .count(),
        1
    );
    let api = PostgresStore::open(c).unwrap();
    error(
        api.submit_turn(request("stable", "changed")),
        SubmissionError::Conflict,
    );
    assert!(api.next_turn("s_queue").is_err());
    let next = owner.next_turn("s_queue").unwrap().unwrap();
    assert_eq!(next.submission_id, "stable");
    let (started, fresh, _) = owner
        .accept("s_queue", &next.submission_id, &next.text)
        .unwrap();
    assert!(fresh);
    assert_eq!(started.turn.unwrap().id, receipt.turn_id);
    assert_eq!(
        api.submission("s_queue", "stable").unwrap().status,
        Status::Started
    );
    assert!(
        !owner
            .accept("s_queue", "stable", "sensitive prompt")
            .unwrap()
            .1
    );
    assert_eq!(owner.get("s_queue").unwrap().history.len(), 1);
    complete(&mut owner);
    let final_receipt = api
        .submit_turn(request("stable", "sensitive prompt"))
        .unwrap();
    assert_eq!(final_receipt.status, Status::Completed);
    assert_eq!(receipt.turn_id, final_receipt.turn_id);
    assert!(!serde_json::to_string(&owner.audit("s_queue", 0).unwrap())
        .unwrap()
        .contains("sensitive prompt"));
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn fifo_capacity_and_interleaved_ingress_do_not_invalidate_worker_state() {
    let (c, mut owner) = fixture(2);
    let api = PostgresStore::open(c).unwrap();
    let mut snapshot = owner.get("s_queue").unwrap();
    let first = api.submit_turn(request("one", "First")).unwrap();
    api.submit_turn(request("two", "Second")).unwrap();
    error(
        api.submit_turn(request("three", "Third")),
        SubmissionError::QueueFull,
    );
    assert_eq!(api.submit_turn(request("one", "First")).unwrap(), first);
    // Queue events share the cursor, not canonical metadata revision.
    snapshot.name = "Worker update".into();
    owner.save(&snapshot, "name_changed", json!({})).unwrap();
    assert_eq!(owner.get("s_queue").unwrap().name, "Worker update");
    error(
        owner.accept("s_queue", "two", "Second"),
        SubmissionError::OutOfOrder,
    );
    error(
        owner.accept("s_queue", "bypass", "Unqueued"),
        SubmissionError::OutOfOrder,
    );
    owner.accept("s_queue", "one", "First").unwrap();
    assert!(owner.next_turn("s_queue").unwrap().is_none());
    api.submit_turn(request("three", "Third")).unwrap();
    complete(&mut owner);
    assert_eq!(
        owner.next_turn("s_queue").unwrap().unwrap().submission_id,
        "two"
    );
    owner.accept("s_queue", "two", "Second").unwrap();
    complete(&mut owner);
    owner.accept("s_queue", "three", "Third").unwrap();
    complete(&mut owner);
    assert!(owner.next_turn("s_queue").unwrap().is_none());
    let events = owner.events("s_queue", 0).unwrap();
    assert!(events
        .iter()
        .enumerate()
        .all(|(i, e)| e.event_id == i as i64 + 1));
    assert_eq!(events.len(), owner.audit("s_queue", 0).unwrap().len());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn lost_receipts_reconnect_and_takeover_preserve_queue_without_replay() {
    let (c, mut old) = fixture(100);
    {
        let api = PostgresStore::open(c.clone()).unwrap();
        // Simulate a committed acceptance whose transport response is lost.
        let _ = api.submit_turn(request("one", "First")).unwrap();
        let _ = api.submit_turn(request("two", "Second")).unwrap();
    }
    let api = PostgresStore::open(c.clone()).unwrap();
    let receipt = api.submission("s_queue", "one").unwrap();
    assert_eq!(api.submit_turn(request("one", "First")).unwrap(), receipt);
    old.accept("s_queue", "one", "First").unwrap();
    admin(&c)
        .execute(
            "UPDATE sessions SET expires=clock_timestamp()-interval '1 second' WHERE id='s_queue'",
            &[],
        )
        .unwrap();
    let mut new = PostgresStore::open(c).unwrap();
    new.claim("s_queue").unwrap();
    new.recover().unwrap();
    assert_eq!(
        api.submission("s_queue", "one").unwrap().status,
        Status::Interrupted
    );
    assert_eq!(
        api.submit_turn(request("one", "First")).unwrap().turn_id,
        receipt.turn_id
    );
    assert_eq!(
        new.next_turn("s_queue").unwrap().unwrap().submission_id,
        "two"
    );
    assert!(old.accept("s_queue", "two", "Second").is_err());
    new.accept("s_queue", "two", "Second").unwrap();
    assert_eq!(new.get("s_queue").unwrap().history.len(), 2);
    assert_eq!(
        api.submission("s_queue", "two").unwrap().owner_epoch,
        Some(2)
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn receipt_event_audit_and_queue_position_roll_back_together() {
    let (c, mut owner) = fixture(100);
    let api = PostgresStore::open(c.clone()).unwrap();
    admin(&c).batch_execute("CREATE FUNCTION fail_ingress() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'SECRET_SQL_DETAIL'; END $$; CREATE TRIGGER fail_ingress BEFORE INSERT ON audit FOR EACH ROW WHEN (NEW.kind='command_accepted') EXECUTE FUNCTION fail_ingress()").unwrap();
    let e = api
        .submit_turn(request("one", "secret text"))
        .unwrap_err()
        .to_string();
    assert!(!e.contains("SECRET_SQL_DETAIL"));
    let other = PostgresStore::open(c.clone()).unwrap();
    error(
        other.submission("s_queue", "one"),
        SubmissionError::NotFound,
    );
    assert!(owner.next_turn("s_queue").unwrap().is_none());
    assert_eq!(owner.cursor("s_queue").unwrap(), 1);
    admin(&c)
        .batch_execute("DROP TRIGGER fail_ingress ON audit")
        .unwrap();
    assert_eq!(
        other
            .submit_turn(request("one", "secret text"))
            .unwrap()
            .sequence,
        1
    );
    // The same atomicity applies when the owner promotes a queued submission.
    admin(&c).batch_execute("CREATE TRIGGER fail_start BEFORE INSERT ON audit FOR EACH ROW WHEN (NEW.kind='turn_accepted') EXECUTE FUNCTION fail_ingress()").unwrap();
    assert!(owner.accept("s_queue", "one", "secret text").is_err());
    assert_eq!(
        other.submission("s_queue", "one").unwrap().status,
        Status::Queued
    );
    assert!(other.inspect("s_queue").unwrap().history.is_empty());
    assert!(other.inspect("s_queue").unwrap().turn.is_none());
    admin(&c).batch_execute("DROP TRIGGER fail_start ON audit; UPDATE sessions SET expires=clock_timestamp()-interval '1 second'").unwrap();
    let mut replacement = PostgresStore::open(c.clone()).unwrap();
    replacement.claim("s_queue").unwrap();
    replacement.accept("s_queue", "one", "secret text").unwrap();
    admin(&c).batch_execute("CREATE TRIGGER fail_finish BEFORE INSERT ON audit FOR EACH ROW WHEN (NEW.kind='turn_completed') EXECUTE FUNCTION fail_ingress()").unwrap();
    let mut finishing = replacement.get("s_queue").unwrap();
    finishing.turn.as_mut().unwrap().status = "completed".into();
    finishing
        .history
        .push(json!({"type":"message","role":"assistant","content":"done"}));
    assert!(replacement
        .save(&finishing, "turn_completed", json!({"status":"completed"}))
        .is_err());
    assert_eq!(
        other.submission("s_queue", "one").unwrap().status,
        Status::Started
    );
    let unchanged = other.inspect("s_queue").unwrap();
    assert_eq!(unchanged.turn.unwrap().status, "accepted");
    assert_eq!(unchanged.history.len(), 1);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn validation_lifecycle_and_old_schema_are_explicit() {
    let (c, mut owner) = fixture(100);
    let api = PostgresStore::open(c.clone()).unwrap();
    error(
        api.submit_turn(request("", "Hello")),
        SubmissionError::InvalidRequest,
    );
    error(
        api.submit_turn(request("id", &"a".repeat(32001))),
        SubmissionError::InvalidRequest,
    );
    error(
        api.submit_turn(request("id", "\0")),
        SubmissionError::InvalidRequest,
    );
    let mut missing = request("id", "Hi");
    missing.session_id = "s_missing".into();
    error(api.submit_turn(missing), SubmissionError::NotFound);
    api.submit_turn(request("one", "First")).unwrap();
    let mut s = owner.get("s_queue").unwrap();
    s.closed = true;
    assert!(owner.save(&s, "session_closed", json!({})).is_err());
    assert!(!owner.get("s_queue").unwrap().closed);
    owner.accept("s_queue", "one", "First").unwrap();
    complete(&mut owner);
    let mut s = owner.get("s_queue").unwrap();
    s.provider_blocked = true;
    owner.save(&s, "blocked", json!({})).unwrap();
    error(
        api.submit_turn(request("two", "Second")),
        SubmissionError::ProviderBlocked,
    );
    let mut s = owner.get("s_queue").unwrap();
    s.closed = true;
    owner.save(&s, "closed", json!({})).unwrap();
    error(
        api.submit_turn(request("two", "Second")),
        SubmissionError::Closed,
    );
    assert_eq!(
        api.submit_turn(request("one", "First")).unwrap().status,
        Status::Completed
    );
    admin(&c)
        .execute("UPDATE version SET version=1", &[])
        .unwrap();
    assert!(PostgresStore::open(c.clone()).is_err());
    assert!(PostgresStore::migrate(&c).is_err());
    let mut bad = c;
    bad.max_queued_turns = 101;
    assert!(PostgresStore::open(bad).is_err());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn deleted_receipts_do_not_turn_a_duplicate_into_new_work() {
    let (c, mut owner) = fixture(100);
    let api = PostgresStore::open(c.clone()).unwrap();
    api.submit_turn(request("one", "First")).unwrap();
    owner.accept("s_queue", "one", "First").unwrap();
    complete(&mut owner);
    let length = owner.get("s_queue").unwrap().history.len();
    admin(&c)
        .execute("DELETE FROM submissions WHERE session_id='s_queue'", &[])
        .unwrap();
    error(
        api.submission("s_queue", "one"),
        SubmissionError::IncompleteState,
    );
    error(
        api.submit_turn(request("one", "First")),
        SubmissionError::IncompleteState,
    );
    error(
        owner.accept("s_queue", "one", "First"),
        SubmissionError::IncompleteState,
    );
    error(owner.next_turn("s_queue"), SubmissionError::IncompleteState);
    assert_eq!(owner.get("s_queue").unwrap().history.len(), length);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn turn_limits_survive_queue_and_bind_idempotency() {
    use openworker_runtime::budget::Limits;
    let (config, mut owner) = fixture(4);
    let api = PostgresStore::open(config).unwrap();
    let mut request = request("limited", "Build only");
    request.limits = Some(Limits {
        max_model_calls: 2,
        max_output_tokens: 2048,
        summary_tokens: 256,
    });
    let receipt = api.submit_turn(request.clone()).unwrap();
    assert_eq!(
        api.submit_turn(request.clone()).unwrap().turn_id,
        receipt.turn_id
    );
    let mut changed = request.clone();
    changed.limits.as_mut().unwrap().max_model_calls = 3;
    assert!(api.submit_turn(changed).is_err());
    let queued = owner.next_turn("s_queue").unwrap().unwrap();
    assert_eq!(queued.limits, request.limits);
    let (state, fresh, _) = owner
        .accept_with_limits(
            "s_queue",
            &queued.submission_id,
            &queued.text,
            queued.limits.clone(),
        )
        .unwrap();
    assert!(fresh);
    assert_eq!(
        state.turn.as_ref().unwrap().budget.limits,
        request.limits.unwrap()
    );
    let mut state = state;
    state.turn.as_mut().unwrap().budget.model_calls = 1;
    state.turn.as_mut().unwrap().budget.charged_output_tokens = 17;
    owner
        .save(&state, "budget_test_checkpoint", json!({}))
        .unwrap();
    let copy = owner.get("s_queue").unwrap();
    assert_eq!(copy.turn.unwrap().budget.model_calls, 1);
    complete(&mut owner);
    let (next, fresh, _) = owner.accept("s_queue", "next", "Next task").unwrap();
    assert!(fresh);
    assert_eq!(next.turn.unwrap().budget.limits, Limits::default());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn resilience_service_result_preserves_interruption_for_current_and_older_runs() {
    let (_config, mut owner) = fixture(4);
    let (mut state, _, _) = owner
        .accept("s_queue", "crashed", "Streaming task")
        .unwrap();
    state.turn.as_mut().unwrap().status = "running".into();
    owner.save(&state, "model_started", json!({})).unwrap();
    owner.recover().unwrap();
    let original = owner.service_run("s_queue", "crashed").unwrap();
    assert_eq!(original["status"], "interrupted");
    assert_eq!(original["failure"], "interrupted");
    assert!(original["finalOutput"].is_null());
    assert_eq!(original["interruptions"], json!([]));
    let (_next, _, _) = owner
        .accept("s_queue", "next", "Explicit continuation")
        .unwrap();
    complete(&mut owner);
    assert_eq!(
        owner.service_run("s_queue", "next").unwrap()["status"],
        "completed"
    );
    let older = owner.service_run("s_queue", "crashed").unwrap();
    assert_eq!(older["status"], "interrupted");
    assert_eq!(older["runId"], original["runId"]);
    assert!(older["finalOutput"].is_null());
}
