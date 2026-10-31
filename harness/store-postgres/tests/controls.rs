use openworker_runtime::{
    control::{Action, Binding, Command, Status},
    store::{Session, StateStore},
};
use openworker_store_postgres::{Config, PostgresStore, Tls, TurnSubmission};
use serde_json::{json, Value};
use std::sync::{Arc, Barrier};

fn fixture() -> (Config, PostgresStore, Command) {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").expect("disposable DB required"),
        format!("ow_ctl_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let mut owner = PostgresStore::open(c.clone()).unwrap();
    let mut s:Session=serde_json::from_value(json!({"id":"s_ctl","name":"Control","workspace":"/work","coworker":"test","bundle_digest":"bundle","schema_digest":"schema","interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
    s.tool_policy.digest = "policy".into();
    s.resource_policy.digest = "resource".into();
    owner.save(&s, "session_created", json!({})).unwrap();
    let (mut s, _, _) = owner.accept("s_ctl", "turn", "Task").unwrap();
    let tid = s.turn.as_ref().unwrap().id.clone();
    let call =
        json!({"type":"function_call","call_id":"call_one","name":"test__probe","arguments":"{}"});
    s.history.push(call.clone());
    s.turn.as_mut().unwrap().queue = vec![call.clone()];
    s.turn.as_mut().unwrap().status = "waiting".into();
    s.interactions.push(serde_json::from_value(json!({"id":"i_one","turn_id":tid,"kind":"approval","call":call,"bundle_digest":"bundle","schema_digest":"schema","response":null,"resource_policy_digest":"resource","policy_digest":"policy"})).unwrap());
    owner
        .save(
            &s,
            "interaction_required",
            json!({"interaction_id":"i_one"}),
        )
        .unwrap();
    let command = Command {
        session_id: "s_ctl".into(),
        request_id: "control-one".into(),
        turn_id: tid,
        actor: "principal-1".into(),
        action: Action::Response {
            interaction_id: "i_one".into(),
            response: "once".into(),
            binding: Binding {
                bundle_digest: "bundle".into(),
                schema_digest: "schema".into(),
                policy_digest: "policy".into(),
                resource_policy_digest: "resource".into(),
            },
        },
    };
    (c, owner, command)
}
fn admin(c: &Config) -> postgres::Client {
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    db
}
fn applied(owner: &mut PostgresStore, c: &Command) -> anyhow::Result<()> {
    let mut s = owner.get(&c.session_id)?;
    if let Action::Response { response, .. } = &c.action {
        s.interactions[0].response = Some(response.clone());
        s.interactions[0].origin = Some("human".into());
        s.turn.as_mut().unwrap().status = "accepted".into();
        owner.save(
            &s,
            "interaction_resolved",
            json!({"interaction_id":"i_one","control_request_id":c.request_id,"actor":c.actor}),
        )?;
    } else {
        s.turn.as_mut().unwrap().status = "cancelling".into();
        owner.save(
            &s,
            "turn_cancelling",
            json!({"control_request_id":c.request_id,"actor":c.actor}),
        )?;
    }
    Ok(())
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn closed_session_rejects_new_cancel_but_preserves_prior_receipts() {
    let (config, mut owner, command) = fixture();
    let api = PostgresStore::open(config).unwrap();
    api.submit_control(command.clone()).unwrap();
    applied(&mut owner, &command).unwrap();
    let mut session = owner.get("s_ctl").unwrap();
    session.turn.as_mut().unwrap().status = "completed".into();
    session.turn.as_mut().unwrap().queue.clear();
    session.closed = true;
    owner.save(&session, "session_closed", json!({})).unwrap();
    assert_eq!(
        api.submit_control(command.clone()).unwrap().status,
        Status::Applied
    );
    let cancel = Command {
        request_id: "closed-cancel".into(),
        action: Action::Cancel,
        ..command
    };
    assert_eq!(
        api.submit_control(cancel)
            .unwrap_err()
            .downcast_ref::<openworker_store_postgres::SubmissionError>(),
        Some(&openworker_store_postgres::SubmissionError::Closed)
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn late_control_defers_turn_admission_without_consuming_or_replacing_it() {
    use openworker_store_postgres::{SubmissionError, SubmissionStatus};
    let (c, mut owner, command) = fixture();
    let api = PostgresStore::open(c).unwrap();
    let mut session = owner.get("s_ctl").unwrap();
    session.turn.as_mut().unwrap().status = "completed".into();
    session.turn.as_mut().unwrap().queue.clear();
    owner
        .save(&session, "turn_completed", json!({"status":"completed"}))
        .unwrap();
    let receipt = api
        .submit_turn(TurnSubmission {
            limits: None,
            session_id: "s_ctl".into(),
            submission_id: "next".into(),
            text: "Next".into(),
        })
        .unwrap();
    let peek = owner.next_turn("s_ctl").unwrap().unwrap();
    // Deterministic interleaving: the worker has peeked, but not admitted work.
    let cancel = Command {
        request_id: "late-cancel".into(),
        action: Action::Cancel,
        ..command
    };
    api.submit_control(cancel.clone()).unwrap();
    let error = owner
        .accept(&peek.session_id, &peek.submission_id, &peek.text)
        .unwrap_err();
    assert_eq!(
        error.downcast_ref::<SubmissionError>(),
        Some(&SubmissionError::ControlsPending)
    );
    assert_eq!(
        api.submission("s_ctl", "next").unwrap().status,
        SubmissionStatus::Queued
    );
    owner
        .settle_control("s_ctl", &cancel.request_id, false, "already_terminal")
        .unwrap();
    let (started, fresh, _) = owner
        .accept(&peek.session_id, &peek.submission_id, &peek.text)
        .unwrap();
    assert!(fresh);
    assert_eq!(started.turn.unwrap().id, receipt.turn_id);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn control_domain_errors_are_typed_and_do_not_poison_the_connection() {
    use openworker_store_postgres::SubmissionError as E;
    let (c, _owner, command) = fixture();
    let api = PostgresStore::open(c).unwrap();
    let mut bad = command.clone();
    bad.request_id.clear();
    assert_eq!(
        api.submit_control(bad).unwrap_err().downcast_ref::<E>(),
        Some(&E::InvalidRequest)
    );
    assert_eq!(
        api.control_receipt("s_ctl", "missing")
            .unwrap_err()
            .downcast_ref::<E>(),
        Some(&E::NotFound)
    );
    api.submit_control(command.clone()).unwrap();
    let mut bad = command.clone();
    bad.actor = "different".into();
    assert_eq!(
        api.submit_control(bad).unwrap_err().downcast_ref::<E>(),
        Some(&E::Conflict)
    );
    let mut bad = command.clone();
    bad.request_id = "another-id".into();
    assert_eq!(
        api.submit_control(bad).unwrap_err().downcast_ref::<E>(),
        Some(&E::ControlConflict)
    );
    assert_eq!(api.submit_control(command).unwrap().status, Status::Pending);
    api.ready().unwrap();
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn closing_cannot_strand_a_control_admitted_before_the_close() {
    use openworker_store_postgres::SubmissionError;
    let (c, mut owner, command) = fixture();
    let api = PostgresStore::open(c).unwrap();
    let mut s = owner.get("s_ctl").unwrap();
    s.turn.as_mut().unwrap().status = "completed".into();
    s.turn.as_mut().unwrap().queue.clear();
    owner
        .save(&s, "turn_completed", json!({"status":"completed"}))
        .unwrap();
    let cancel = Command {
        request_id: "before-close".into(),
        action: Action::Cancel,
        ..command
    };
    api.submit_control(cancel.clone()).unwrap();
    let mut s = owner.get("s_ctl").unwrap();
    s.closed = true;
    assert_eq!(
        owner
            .save(&s, "session_closed", json!({}))
            .unwrap_err()
            .downcast_ref::<SubmissionError>(),
        Some(&SubmissionError::ControlsPending)
    );
    assert!(!owner.get("s_ctl").unwrap().closed);
    owner
        .settle_control("s_ctl", &cancel.request_id, false, "already_terminal")
        .unwrap();
    let mut s = owner.get("s_ctl").unwrap();
    s.closed = true;
    owner.save(&s, "session_closed", json!({})).unwrap();
    assert_eq!(api.submit_control(cancel).unwrap().status, Status::Applied);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn concurrent_decisions_are_durable_idempotent_and_attributed() {
    let (c, mut owner, command) = fixture();
    let a = PostgresStore::open(c.clone()).unwrap();
    let b = PostgresStore::open(c.clone()).unwrap();
    let gate = Arc::new(Barrier::new(3));
    let ga = gate.clone();
    let gb = gate.clone();
    let ca = command.clone();
    let cb = command.clone();
    let first = std::thread::spawn(move || {
        ga.wait();
        a.submit_control(ca).unwrap()
    });
    let second = std::thread::spawn(move || {
        gb.wait();
        b.submit_control(cb).unwrap()
    });
    gate.wait();
    assert_eq!(first.join().unwrap(), second.join().unwrap());
    let api = PostgresStore::open(c).unwrap();
    assert!(owner.get("s_ctl").unwrap().interactions[0]
        .response
        .is_none());
    let mut conflict = command.clone();
    conflict.actor = "other".into();
    assert!(api.submit_control(conflict).is_err());
    let mut conflict = command.clone();
    if let Action::Response { response, .. } = &mut conflict.action {
        *response = "deny".into();
    }
    assert!(api.submit_control(conflict).is_err());
    assert!(api.next_control("s_ctl").is_err());
    applied(&mut owner, &command).unwrap();
    assert_eq!(
        api.control_receipt("s_ctl", &command.request_id)
            .unwrap()
            .status,
        Status::Applied
    );
    assert_eq!(api.submit_control(command).unwrap().status, Status::Applied);
    assert!(owner.next_control("s_ctl").unwrap().is_none());
    let events = owner.audit("s_ctl", 0).unwrap();
    assert_eq!(
        events
            .iter()
            .filter(|e| e["actor"] == "principal-1")
            .count(),
        2
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn cancellation_overtakes_response_and_fences_new_tool_intents() {
    let (c, mut owner, response) = fixture();
    let api = PostgresStore::open(c).unwrap();
    api.submit_turn(TurnSubmission {
        limits: None,
        session_id: "s_ctl".into(),
        submission_id: "next".into(),
        text: "Next".into(),
    })
    .unwrap();
    api.submit_control(response.clone()).unwrap();
    let cancel = Command {
        request_id: "cancel-one".into(),
        action: Action::Cancel,
        ..response.clone()
    };
    api.submit_control(cancel.clone()).unwrap();
    assert_eq!(
        owner
            .next_control("s_ctl")
            .unwrap()
            .unwrap()
            .command
            .request_id,
        "cancel-one"
    );
    assert!(owner.next_turn("s_ctl").unwrap().is_none());
    assert!(owner
        .append_event("s_ctl", "tool_started", json!({"call_id":"call_one"}))
        .is_err());
    assert!(applied(&mut owner, &response).is_err());
    assert!(owner.get("s_ctl").unwrap().interactions[0]
        .response
        .is_none());
    applied(&mut owner, &cancel).unwrap();
    assert_eq!(
        api.control_receipt("s_ctl", "cancel-one").unwrap().status,
        Status::Applied
    );
    assert!(owner.uncertain_calls("s_ctl").unwrap().is_empty());
    owner
        .settle_control("s_ctl", "control-one", true, "stale_identity_or_target")
        .unwrap();
    assert_eq!(
        api.control_receipt("s_ctl", "control-one").unwrap().status,
        Status::Rejected
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn saved_identity_questions_and_stale_turns_are_validated() {
    let (c, mut owner, command) = fixture();
    let api = PostgresStore::open(c.clone()).unwrap();
    let mut bad = command.clone();
    bad.turn_id = "not_current".into();
    assert!(api.submit_control(bad).is_err());
    let mut bad = command.clone();
    if let Action::Response { binding, .. } = &mut bad.action {
        binding.resource_policy_digest = "changed".into();
    }
    assert!(api.submit_control(bad).is_err());
    let mut bad = command.clone();
    if let Action::Response { response, .. } = &mut bad.action {
        *response = "always_forever".into();
    }
    assert!(api.submit_control(bad).is_err());
    let mut s = owner.get("s_ctl").unwrap();
    s.interactions[0].kind = "question".into();
    s.interactions[0].call["arguments"] = json!(json!({"options":["Yes","No"]}).to_string());
    owner.save(&s, "question", json!({})).unwrap();
    assert!(api.submit_control(command.clone()).is_err());
    let mut question = command.clone();
    if let Action::Response { response, .. } = &mut question.action {
        *response = "Yes".into();
    }
    api.submit_control(question.clone()).unwrap();
    applied(&mut owner, &question).unwrap();
    assert_eq!(
        owner.get("s_ctl").unwrap().interactions[0]
            .response
            .as_deref(),
        Some("Yes")
    );
    let mut bad = question;
    bad.request_id = "another".into();
    assert!(api.submit_control(bad).is_err());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn receipt_application_and_audit_roll_back_together() {
    let (c, mut owner, command) = fixture();
    let api = PostgresStore::open(c.clone()).unwrap();
    admin(&c).batch_execute("CREATE FUNCTION fail_control() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'SECRET_CONTROL_SQL'; END $$; CREATE TRIGGER fail_control BEFORE INSERT ON audit FOR EACH ROW WHEN (NEW.kind='control_received') EXECUTE FUNCTION fail_control()").unwrap();
    assert!(!api
        .submit_control(command.clone())
        .unwrap_err()
        .to_string()
        .contains("SECRET_CONTROL_SQL"));
    admin(&c)
        .batch_execute("DROP TRIGGER fail_control ON audit")
        .unwrap();
    let api = PostgresStore::open(c.clone()).unwrap();
    assert!(api.control_receipt("s_ctl", "control-one").is_err());
    api.submit_control(command.clone()).unwrap();
    admin(&c).batch_execute("CREATE TRIGGER fail_control BEFORE INSERT ON audit FOR EACH ROW WHEN (NEW.kind='interaction_resolved') EXECUTE FUNCTION fail_control()").unwrap();
    assert!(applied(&mut owner, &command).is_err());
    assert_eq!(
        api.control_receipt("s_ctl", "control-one").unwrap().status,
        Status::Pending
    );
    assert!(api.inspect("s_ctl").unwrap().interactions[0]
        .response
        .is_none());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn takeover_preserves_pending_decision_and_rejects_old_owner() {
    let (c, mut old, command) = fixture();
    let api = PostgresStore::open(c.clone()).unwrap();
    api.submit_control(command.clone()).unwrap();
    admin(&c)
        .execute(
            "UPDATE sessions SET expires=clock_timestamp()-interval '1 second'",
            &[],
        )
        .unwrap();
    let mut new = PostgresStore::open(c.clone()).unwrap();
    new.claim("s_ctl").unwrap();
    new.recover().unwrap();
    assert_eq!(new.next_control("s_ctl").unwrap().unwrap().command, command);
    assert!(applied(&mut old, &command).is_err());
    applied(&mut new, &command).unwrap();
    assert_eq!(
        api.control_receipt("s_ctl", "control-one").unwrap().status,
        Status::Applied
    );
    admin(&c).execute("DELETE FROM controls", &[]).unwrap();
    assert!(api.control_receipt("s_ctl", "control-one").is_err());
    assert!(api.submit_control(command).is_err());
    assert!(new
        .append_event("s_ctl", "tool_started", Value::Null)
        .is_err());
}
