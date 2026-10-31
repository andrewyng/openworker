#[path = "../../tests/agent_team_contract.rs"]
mod contract;
use openworker_runtime::store::Store;
use serde_json::json;
#[test]
fn sqlite_contract() {
    contract::exercise(&mut Store::memory().unwrap());
}
#[test]
fn sqlite_child_briefing_contract() {
    contract::exercise_briefing(&mut Store::memory().unwrap());
}
#[test]
fn restart_preserves_inbox_receipts_and_waits() {
    let root = tempfile::tempdir().unwrap();
    let mut db = Store::open(root.path()).unwrap();
    contract::create(&mut db);
    let child = contract::child(&mut db, "builder-ui");
    db.agent_team(
        "lead",
        "team_post_board",
        &json!({"message":"@builder-ui original"}),
        None,
    )
    .unwrap();
    db.agent_team(
        "lead",
        "team_wait_agent",
        &json!({"agent_ids":["builder-ui"],"timeout_ms":60000}),
        None,
    )
    .unwrap();
    drop(db);
    let mut db = Store::open(root.path()).unwrap();
    assert!(db
        .get("lead")
        .unwrap()
        .agent_team
        .unwrap()
        .waiting
        .is_some());
    db.agent_team(&child, "deliver", &json!({}), None).unwrap();
    drop(db);
    let mut db = Store::open(root.path()).unwrap();
    db.recover().unwrap();
    assert_eq!(
        db.agent_team(&child, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    assert_eq!(db.get(&child).unwrap().history.len(), 1);
}
#[test]
fn transaction_failure_rolls_back_board_fanout_result_and_inbox_cursor() {
    let root = tempfile::tempdir().unwrap();
    let mut db = Store::open(root.path()).unwrap();
    contract::create(&mut db);
    let child = contract::child(&mut db, "builder-ui");
    let connection = rusqlite::Connection::open(root.path().join("runtime.sqlite3")).unwrap();
    connection.execute_batch("CREATE TRIGGER fail_agent_team_event BEFORE INSERT ON events WHEN NEW.kind='agent_team' BEGIN SELECT RAISE(ABORT,'injected failure'); END;").unwrap();
    assert!(db
        .agent_team(
            "lead",
            "team_post_board",
            &json!({"message":"@builder-ui must rollback"}),
            None
        )
        .is_err());
    connection
        .execute_batch("DROP TRIGGER fail_agent_team_event")
        .unwrap();
    assert_eq!(
        db.agent_team(&child, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    db.agent_team(
        "lead",
        "team_post_board",
        &json!({"message":"@builder-ui retained"}),
        None,
    )
    .unwrap();
    connection.execute_batch("CREATE TRIGGER fail_agent_team_event BEFORE INSERT ON events WHEN NEW.kind='agent_team' BEGIN SELECT RAISE(ABORT,'injected failure'); END;").unwrap();
    assert!(db.agent_team(&child, "deliver", &json!({}), None).is_err());
    assert!(db.get(&child).unwrap().history.is_empty());
    assert_eq!(db.get(&child).unwrap().agent_team.unwrap().inbox_cursor, 0);
    connection
        .execute_batch("DROP TRIGGER fail_agent_team_event")
        .unwrap();
    assert_eq!(
        db.agent_team(&child, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}
#[test]
fn team_tool_result_and_effect_commit_once_and_disabled_channels_cannot_dispatch() {
    let mut db = Store::memory().unwrap();
    contract::create(&mut db);
    let child = contract::child(&mut db, "builder-ui");
    let (mut s, _, _) = db.accept("lead", "task", "Coordinate").unwrap();
    let t = s.turn.as_mut().unwrap();
    t.status = "running".into();
    let tid = t.id.clone();
    let args = json!({"message":"@builder-ui exact effect"});
    let call = json!({"type":"function_call","call_id":"post","name":"team_post_board","arguments":args.to_string()});
    s.history.push(call.clone());
    s.turn.as_mut().unwrap().queue = vec![call.clone()];
    db.save(&s, "assistant_message", json!({})).unwrap();
    db.agent_team("lead", "team_post_board", &args, Some((&tid, &call)))
        .unwrap();
    assert!(db
        .agent_team("lead", "team_post_board", &args, Some((&tid, &call)))
        .is_err());
    assert_eq!(
        db.get("lead")
            .unwrap()
            .history
            .iter()
            .filter(|v| v["type"] == "function_call_output")
            .count(),
        1
    );
    assert_eq!(
        db.agent_team(&child, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    for (board, direct) in [(false, false), (false, true), (true, false), (true, true)] {
        let mut db = Store::memory().unwrap();
        db.save(&contract::session("lead", "lead"), "created", json!({}))
            .unwrap();
        db.agent_team("lead","create",&json!({"agent_id":"lead","agent_types":["builder"],"policy":{"communication":{"board":board,"directMessages":direct},"max_instances":32}}),None).unwrap();
        let child = contract::child(&mut db, "builder-ui");
        assert_eq!(
            db.agent_team(
                "lead",
                "team_post_board",
                &json!({"message":"@builder-ui notice"}),
                None
            )
            .is_ok(),
            board
        );
        assert_eq!(
            db.agent_team(
                "lead",
                "team_send_message",
                &json!({"agent_id":"builder-ui","message":"Private"}),
                None
            )
            .is_ok(),
            direct
        );
        let doc =
            serde_json::from_value(db.agent_team("lead", "snapshot", &json!({}), None).unwrap())
                .unwrap();
        let schemas = openworker_runtime::agent_team::schemas(&doc, "builder-ui");
        assert_eq!(
            schemas.iter().any(|v| v["name"] == "team_send_message"),
            direct
        );
        assert_eq!(
            schemas.iter().any(|v| v["name"] == "team_post_board"),
            board
        );
        assert!(!schemas.iter().any(|v| v["name"] == "team_spawn"));
        assert_eq!(
            db.agent_team(&child, "deliver", &json!({}), None).unwrap()["messages"]
                .as_array()
                .unwrap()
                .len(),
            usize::from(board) + usize::from(direct)
        );
    }
}
#[test]
fn waits_handle_completion_message_timeout_and_keep_cancelled_sessions_stopped() {
    let mut db = Store::memory().unwrap();
    contract::create(&mut db);
    let child = contract::child(&mut db, "builder-ui");
    let (mut lead, _, _) = db.accept("lead", "lead-task", "Coordinate").unwrap();
    lead.turn.as_mut().unwrap().status = "waiting_agents".into();
    db.save(&lead, "park", json!({})).unwrap();
    db.agent_team(
        "lead",
        "team_wait_agent",
        &json!({"agent_ids":["builder-ui"],"timeout_ms":60000}),
        None,
    )
    .unwrap();
    assert_eq!(
        db.agent_team("lead", "wake", &json!({}), None).unwrap()["wake"],
        false
    );
    db.agent_team(
        &child,
        "team_post_board",
        &json!({"message":"@lead update while waiting"}),
        None,
    )
    .unwrap();
    assert_eq!(
        db.agent_team("lead", "wake", &json!({}), None).unwrap()["resume"],
        true
    );
    let mut s = db.get("lead").unwrap();
    s.turn.as_mut().unwrap().status = "cancelled".into();
    db.save(&s, "cancel", json!({})).unwrap();
    db.agent_team(
        "lead",
        "team_wait_agent",
        &json!({"agent_ids":["builder-ui"],"timeout_ms":1}),
        None,
    )
    .unwrap();
    std::thread::sleep(std::time::Duration::from_millis(2));
    assert_eq!(
        db.agent_team("lead", "wake", &json!({}), None).unwrap()["wake"],
        false
    );
    let mut s = db.get("lead").unwrap();
    s.turn.as_mut().unwrap().status = "waiting_agents".into();
    s.agent_team.as_mut().unwrap().inbox_cursor = 100;
    db.save(&s, "park", json!({})).unwrap();
    assert_eq!(
        db.agent_team("lead", "wake", &json!({}), None).unwrap()["resume"],
        true
    );
}
#[test]
fn aggregate_model_budget_counts_all_instances_and_settles_conservatively() {
    let mut db = Store::memory().unwrap();
    db.save(&contract::session("lead", "lead"), "created", json!({}))
        .unwrap();
    db.agent_team("lead","create",&json!({"agent_id":"lead","agent_types":["builder"],"limits":{"maxModelCalls":2,"maxOutputTokens":20000}}),None).unwrap();
    let child = contract::child(&mut db, "builder-ui");
    for (sid, id) in [("lead", "a"), (child.as_str(), "b")] {
        assert_eq!(
            db.agent_team(
                sid,
                "reserve_model",
                &json!({"attempt_id":id,"output_tokens":1000}),
                None
            )
            .unwrap()["allowed"],
            true
        );
    }
    assert_eq!(
        db.agent_team(
            "lead",
            "reserve_model",
            &json!({"attempt_id":"c","output_tokens":1000}),
            None
        )
        .unwrap()["allowed"],
        false
    );
    db.agent_team(
        "lead",
        "settle_model",
        &json!({"attempt_id":"a","output_tokens":10}),
        None,
    )
    .unwrap();
    db.agent_team(
        "lead",
        "settle_model",
        &json!({"attempt_id":"a","output_tokens":10}),
        None,
    )
    .unwrap();
    db.agent_team(
        &child,
        "settle_model",
        &json!({"attempt_id":"b","output_tokens":null}),
        None,
    )
    .unwrap();
    let doc = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    assert_eq!(doc["budget"]["charged_output_tokens"], 1010);
    assert_eq!(doc["budget"]["model_calls"], 2);
}
#[test]
fn command_identity_is_author_scoped_and_public_snapshots_hide_private_receipts() {
    let mut db = Store::memory().unwrap();
    contract::create(&mut db);
    let child = contract::child(&mut db, "builder-ui");
    let command = json!({"request_id":"same-id","operation":"team_send_message","arguments":{"agent_id":"builder-ui","message":"private-credential-free-note"}});
    let lead = db.agent_team("lead", "command", &command, None).unwrap();
    let child_own = db.agent_team(&child, "command", &command, None).unwrap();
    assert_ne!(lead["id"], child_own["id"]);
    assert_eq!(child_own["sender"], "builder-ui");
    let private = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    assert!(private["limited"].as_bool().unwrap());
    assert_eq!(private["budget"]["limits"]["maxModelCalls"], 100);
    let public = openworker_runtime::agent_team::public_snapshot(private);
    assert!(!public.to_string().contains("private-credential-free-note"));
    assert!(public.get("commands").is_none());
    assert!(public["roster"].get("requests").is_none());
}
#[test]
fn wake_checkpoint_survives_restart_and_wait_notices_follow_parallel_tool_results() {
    let root = tempfile::tempdir().unwrap();
    let mut db = Store::open(root.path()).unwrap();
    contract::create(&mut db);
    contract::child(&mut db, "builder-ui");
    let (mut s, _, _) = db.accept("lead", "parallel", "Coordinate").unwrap();
    let tid = s.turn.as_ref().unwrap().id.clone();
    let wait_args = json!({"agent_ids":["builder-ui"],"timeout_ms":1});
    let wait = json!({"type":"function_call","call_id":"wait","name":"team_wait_agent","arguments":wait_args.to_string()});
    let post_args = json!({"message":"Passive parallel post"});
    let post = json!({"type":"function_call","call_id":"post","name":"team_post_board","arguments":post_args.to_string()});
    s.turn.as_mut().unwrap().status = "running".into();
    s.turn.as_mut().unwrap().queue = vec![wait.clone(), post.clone()];
    s.history.extend([wait.clone(), post.clone()]);
    db.save(&s, "assistant_message", json!({})).unwrap();
    db.agent_team("lead", "team_wait_agent", &wait_args, Some((&tid, &wait)))
        .unwrap();
    std::thread::sleep(std::time::Duration::from_millis(2));
    assert_eq!(
        db.agent_team("lead", "wake", &json!({}), None).unwrap()["resume"],
        true
    );
    assert_eq!(
        db.get("lead").unwrap().history.len(),
        4,
        "Wake must not insert user input between pending parallel tool results"
    );
    assert!(db.agent_team("lead", "deliver", &json!({}), None).is_err());
    drop(db);
    let mut db = Store::open(root.path()).unwrap();
    db.recover().unwrap();
    let mut s = db.get("lead").unwrap();
    assert_eq!(s.turn.as_ref().unwrap().status, "resuming_agents");
    assert_eq!(s.agent_team.as_ref().unwrap().notices.len(), 1);
    s.turn.as_mut().unwrap().status = "running".into();
    db.save(&s, "turn_started", json!({})).unwrap();
    db.agent_team("lead", "team_post_board", &post_args, Some((&tid, &post)))
        .unwrap();
    db.agent_team("lead", "deliver", &json!({}), None).unwrap();
    let s = db.get("lead").unwrap();
    assert_eq!(s.history[4]["type"], "function_call_output");
    assert_eq!(s.history[5]["_runtime_notice"], "agent_wait");
    assert!(s.agent_team.as_ref().unwrap().notices.is_empty());
    openworker_runtime::anthropic::body("fixture", "Coordinate", &s.history, &[], 8192, None)
        .expect("Parallel tool ordering must remain valid for Anthropic");
    openworker_runtime::chat_completions::body(
        "fixture",
        "Coordinate",
        &s.history,
        &[],
        8192,
        None,
    )
    .expect("Parallel tool ordering must remain valid for Chat Completions");
}
#[test]
fn sqlite_workspace_intent_contract() {
    contract::exercise_workspace_intent(&mut Store::memory().unwrap());
}
#[test]
fn workspace_dispatch_survives_reopen_without_mutation_permission() {
    let tmp = tempfile::tempdir().unwrap();
    let mut db = Store::open(tmp.path()).unwrap();
    db.save(&contract::session("lead", "lead"), "created", json!({}))
        .unwrap();
    db.agent_team(
        "lead",
        "create",
        &json!({"agent_id":"lead","agent_types":["builder"],"workspace_selection":true}),
        None,
    )
    .unwrap();
    db.agent_team("lead","team_spawn",&json!({"agent_id":"builder-one","agent_type":"builder","message":"Build","workspace":{"mode":"directory","path":"ui"}}),None).unwrap();
    let first = db
        .agent_team(
            "lead",
            "workspace_dispatch",
            &json!({"agent_id":"builder-one"}),
            None,
        )
        .unwrap();
    drop(db);
    let mut db = Store::open(tmp.path()).unwrap();
    let recovered = db
        .agent_team(
            "lead",
            "workspace_dispatch",
            &json!({"agent_id":"builder-one"}),
            None,
        )
        .unwrap();
    assert_eq!(recovered["dispatch"], false);
    assert_eq!(first["intent"], recovered["intent"]);
}
