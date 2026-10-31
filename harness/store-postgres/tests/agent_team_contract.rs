#[path = "../../tests/agent_team_contract.rs"]
mod contract;
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_contract() {
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_agents_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::exercise(&mut db);
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_child_briefing_contract() {
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_brief_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::exercise_briefing(&mut db);
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn concurrent_api_board_writes_are_ordered_and_cannot_bypass_fencing() {
    use openworker_runtime::store::StateStore;
    use serde_json::json;
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_team_race_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut owner = openworker_store_postgres::PostgresStore::open(config.clone()).unwrap();
    contract::create(&mut owner);
    let barrier = std::sync::Arc::new(std::sync::Barrier::new(5));
    let mut jobs = vec![];
    for n in 0..4 {
        let db = openworker_store_postgres::PostgresStore::open(config.clone()).unwrap();
        let barrier = barrier.clone();
        jobs.push(std::thread::spawn(move||{barrier.wait();for i in 0..8{db.agent_team_api("lead","command",json!({"request_id":format!("writer-{n}-{i}"),"operation":"team_post_board","arguments":{"message":format!("writer {n} message {i}")}})).unwrap();}}));
    }
    barrier.wait();
    for j in jobs {
        j.join().unwrap();
    }
    let page = owner
        .agent_team(
            "lead",
            "team_read_board",
            &json!({"message_id":null,"limit":50}),
            None,
        )
        .unwrap();
    assert_eq!(page["messages"].as_array().unwrap().len(), 32);
    let seqs = page["messages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| m["sequence"].as_u64().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(seqs, (1..=32).collect::<Vec<_>>());
    let mut stale = openworker_store_postgres::PostgresStore::open(config).unwrap();
    assert!(
        stale
            .agent_team("lead", "deliver", &json!({}), None)
            .is_err(),
        "Non-owner must never acknowledge an inbox"
    );
    owner.release("lead").unwrap();
    stale.claim("lead").unwrap();
    assert!(owner
        .agent_team(
            "lead",
            "team_post_board",
            &json!({"message":"stale tool owner"}),
            None
        )
        .is_err());
    assert_eq!(
        stale
            .agent_team(
                "lead",
                "team_read_board",
                &json!({"message_id":null,"limit":50}),
                None
            )
            .unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        32
    );
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn observer_and_unrelated_session_scopes_cannot_send_or_read_other_teams() {
    use openworker_runtime::store::StateStore;
    use openworker_store_postgres::auth::{Error, Role, Scope, Spec};
    use serde_json::json;
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_team_auth_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut root = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::create(&mut root);
    root.save(&contract::session("other", "other"), "created", json!({}))
        .unwrap();
    let admin = "a".repeat(64);
    root.auth_bootstrap(&admin, "admin").unwrap();
    let admin = root.authorized(&admin).unwrap();
    for (token, name, role, scope) in [
        ("b".repeat(64), "observer", Role::Observer, Scope::All),
        (
            "c".repeat(64),
            "unrelated",
            Role::Operator,
            Scope::Sessions {
                ids: vec!["other".into()],
            },
        ),
    ] {
        admin
            .auth_issue(
                name,
                Spec {
                    principal: name.into(),
                    role,
                    scope,
                    environments: vec![],
                    expires_at: None,
                },
                &token,
            )
            .unwrap();
        let caller = root.authorized(&token).unwrap();
        let result=caller.agent_team_api("lead","command",json!({"request_id":"forbidden","operation":"team_post_board","arguments":{"message":"unauthorized"}}));
        assert_eq!(
            result.err().unwrap().downcast_ref::<Error>(),
            Some(&Error::Forbidden)
        );
        let roster =
            caller.agent_team_api("lead", "team_manage", json!({"action":"list","limit":10}));
        assert_eq!(roster.is_ok(), name == "observer");
        assert!(caller
            .agent_team_api(
                "lead",
                "team_manage",
                json!({"action":"retire","agent_id":"builder"})
            )
            .is_err());
        if name == "unrelated" {
            assert!(caller
                .agent_team_api("lead", "snapshot", json!({}))
                .is_err());
        } else {
            assert!(caller.agent_team_api("lead", "snapshot", json!({})).is_ok());
        }
    }
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn progress_does_not_invalidate_canonical_snapshot_and_stop_cancels_unstarted_work() {
    use openworker_runtime::store::StateStore;
    use serde_json::json;
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_team_stop_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::create(&mut db);
    let mut snapshot = db.get("lead").unwrap();
    db.append_event("lead", "agent_run_state", json!({"read_receipt":true}))
        .unwrap();
    snapshot.name = "Still current".into();
    db.save(&snapshot, "updated", json!({}))
        .expect("Progress must not invalidate unchanged metadata");
    let stale = db.get("lead").unwrap();
    let mut current = stale.clone();
    current.name = "Canonical mutation".into();
    db.save(&current, "updated", json!({})).unwrap();
    assert!(
        db.save(&stale, "stale", json!({})).is_err(),
        "Actual state mutation must retain revision fencing"
    );
    db.submit_turn(openworker_store_postgres::TurnSubmission {
        session_id: "lead".into(),
        submission_id: "queued-work".into(),
        text: "Do not execute".into(),
        limits: None,
    })
    .unwrap();
    db.agent_team_api(
        "lead",
        "command",
        json!({"request_id":"stop","operation":"stop","arguments":{}}),
    )
    .unwrap();
    assert!(db.reconcile_stopped_agent_team().unwrap());
    assert!(!db.reconcile_stopped_agent_team().unwrap());
    assert!(db.next_turn("lead").unwrap().is_none());
    assert!(db
        .submit_turn(openworker_store_postgres::TurnSubmission {
            session_id: "lead".into(),
            submission_id: "too-late".into(),
            text: "No resurrection".into(),
            limits: None
        })
        .is_err());
    let mut s = db.get("lead").unwrap();
    s.closed = true;
    db.save(&s, "session_closed", json!({}))
        .expect("An unstarted stopped team must close without stranded submissions");
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn idle_inbox_wake_never_bypasses_an_existing_user_submission() {
    use openworker_runtime::store::StateStore;
    use serde_json::json;
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_team_fifo_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::create(&mut db);
    let child = contract::child(&mut db, "builder-ui");
    db.agent_team(
        &child,
        "team_post_board",
        &json!({"message":"@lead Message before user task"}),
        None,
    )
    .unwrap();
    db.submit_turn(openworker_store_postgres::TurnSubmission {
        session_id: "lead".into(),
        submission_id: "user-first".into(),
        text: "Original user task".into(),
        limits: None,
    })
    .unwrap();
    db.enqueue_agent_inbox_wake("lead").unwrap();
    assert_eq!(
        db.next_turn("lead").unwrap().unwrap().submission_id,
        "user-first"
    );
    db.accept("lead", "user-first", "Original user task")
        .unwrap();
    assert!(db.agent_team("lead", "deliver", &json!({}), None).is_ok());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn a_never_started_idle_lead_is_schedulable_for_an_explicit_mention() {
    use openworker_runtime::store::StateStore;
    use serde_json::json;
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_idle_team_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config.clone()).unwrap();
    contract::create(&mut db);
    let child = contract::child(&mut db, "builder-ui");
    db.agent_team(
        &child,
        "team_post_board",
        &json!({"message":"@lead Start from this evidence"}),
        None,
    )
    .unwrap();
    db.release("lead").unwrap();
    let worker = openworker_store_postgres::PostgresStore::open(config).unwrap();
    let selector = openworker_store_postgres::worker::Selector {
        coworker: "lead".into(),
        bundle_digest: "bundle".into(),
        backend: openworker_runtime::execution::Backend::Off,
        scripted: true,
        fallback_model: "fixture".into(),
        model_provider: Default::default(),
        model_connections: Default::default(),
        environment_provider: None,
        environment_identity: None,
        environment_profile: None,
    };
    assert_eq!(
        worker.claim_next(selector).unwrap().unwrap().session_id,
        "lead"
    );
    assert_eq!(
        worker
            .clone()
            .agent_team("lead", "wake", &json!({}), None)
            .unwrap()["wake"],
        true
    );
    worker.enqueue_agent_inbox_wake("lead").unwrap();
    assert!(worker.next_turn("lead").unwrap().is_some());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_workspace_intent_contract() {
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_ws_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut store = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::exercise_workspace_intent(&mut store);
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_retirement_contract() {
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_retire_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::exercise_retirement(&mut db);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn controller_retires_an_unprovisioned_reservation_without_creating_a_session() {
    use openworker_runtime::store::StateStore;
    use serde_json::json;
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_empty_retire_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::create(&mut db);
    let reserved = db
        .agent_team(
            "lead",
            "team_spawn",
            &json!({"agent_id":"never-started","agent_type":"builder","message":"Work"}),
            None,
        )
        .unwrap();
    db.agent_team_api("lead","command",json!({"request_id":"retire","operation":"team_manage","arguments":{"action":"retire","agent_id":"never-started"}})).unwrap();
    assert!(db.reconcile_unstarted_retirement().unwrap());
    assert!(!db.reconcile_unstarted_retirement().unwrap());
    assert!(db
        .get(reserved["instance"]["session_id"].as_str().unwrap())
        .is_err());
    assert_eq!(
        db.agent_team_api("lead", "snapshot", json!({})).unwrap()["roster"]["instances"]
            ["never-started"]["phase"],
        "closed"
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn pending_cancel_fences_work_without_blocking_team_reads_or_reconciliation() {
    use openworker_runtime::store::StateStore;
    use serde_json::json;
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_cancel_read_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::create(&mut db);
    let (s, _, _) = db.accept("lead", "running", "Work").unwrap();
    db.submit_control(openworker_runtime::control::Command {
        session_id: "lead".into(),
        request_id: "cancel".into(),
        turn_id: s.turn.unwrap().id,
        actor: "trusted-test-controller".into(),
        action: openworker_runtime::control::Action::Cancel,
    })
    .unwrap();
    assert!(db.agent_team("lead", "snapshot", &json!({}), None).is_ok());
    assert!(db.agent_team("lead", "pending", &json!({}), None).is_ok());
    assert!(db
        .agent_team_api("lead", "team_manage", json!({"action":"list"}))
        .is_ok());
    let error = db
        .agent_team("lead", "reserve_model", &json!({}), None)
        .unwrap_err();
    assert!(
        error.to_string().contains("Cancellation intent pending"),
        "{error}"
    );
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_child_model_selection_contract() {
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_model_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::exercise_child_model_selection(&mut db);
}
