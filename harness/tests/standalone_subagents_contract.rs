//! Identical durable coordination contract for SQLite and PostgreSQL.
#[path = "agent_team_contract.rs"]
#[allow(dead_code)]
mod fixture;
use openworker_runtime::{
    agent_team::{Document, Membership},
    store::StateStore,
};
use serde_json::{json, Value};
pub fn create(db: &mut dyn StateStore) {
    let mut parent = fixture::session("parent-session", "assistant");
    parent.schema_digest = Some("schema".into());
    parent.package.standalone_subagents = Some(Default::default());
    db.save(&parent, "created", json!({})).unwrap();
    let options = json!({"agent_id":"parent","agent_types":["standalone-placeholder"],"policy":{"communication":{"board":false,"directMessages":true},"max_instances":33},"delegation":{"tools":["coding.read_file"],"placements":["team"]},"limits":{"maxModelCalls":10,"maxOutputTokens":20000}});
    assert!(db
        .agent_team(&parent.id, "standalone_init", &options, None)
        .is_err());
    parent = db.get(&parent.id).unwrap();
    parent.package.standalone_subagents = Some(openworker_runtime::subagents::Config::enabled());
    db.save(&parent, "enabled", json!({})).unwrap();
    db.agent_team(&parent.id, "standalone_init", &options, None)
        .unwrap();
    assert!(db.agent_team(&parent.id, "create", &options, None).is_err());
}
pub fn snapshot(db: &mut dyn StateStore) -> Document {
    serde_json::from_value(
        db.agent_team("parent-session", "snapshot", &json!({}), None)
            .unwrap(),
    )
    .unwrap()
}
pub fn request(id: &str, mode: &str) -> Value {
    json!({"agent_id":id,"system_prompt":"Only inspect the supplied evidence.","context":"A fresh child briefing","tools":[],"execution":mode})
}
pub fn spawn(db: &mut dyn StateStore, args: &Value, approval: &str) -> Value {
    let (mut p, _, _) = db
        .accept(
            "parent-session",
            &format!("spawn-{}", args["agent_id"].as_str().unwrap()),
            "PARENT_PRIVATE_SENTINEL",
        )
        .unwrap();
    let call = json!({"type":"function_call","call_id":"spawn-call","name":"create_sub_agent","arguments":args.to_string()});
    p.turn.as_mut().unwrap().status = "running".into();
    p.turn.as_mut().unwrap().queue = vec![call.clone()];
    p.history.push(call.clone());
    let tid = p.turn.as_ref().unwrap().id.clone();
    db.save(&p, "assistant_message", json!({})).unwrap();
    db.agent_team(&p.id, "subagent_prepare", args, None)
        .unwrap();
    p = db.get(&p.id).unwrap();
    p.interactions.push(serde_json::from_value(json!({"id":"definition-approval","turn_id":tid,"kind":"approval","call":call,"bundle_digest":p.bundle_digest,"schema_digest":p.schema_digest,"response":approval,"origin":"human","server":null,"remote_tool":null,"policy_digest":p.tool_policy.digest,"resource_policy_digest":p.resource_policy.digest})).unwrap());
    db.save(&p, "interaction_resolved", json!({})).unwrap();
    db.agent_team(&p.id, "create_sub_agent", args, Some((&tid, &call)))
        .unwrap()
}
pub fn attach(db: &mut dyn StateStore, id: &str) -> String {
    let doc = snapshot(db);
    let instance = doc.roster.instance(id).unwrap();
    let mut child = fixture::session(
        &instance.session_id,
        &openworker_runtime::agent_team::runtime_type(&doc, instance).unwrap(),
    );
    child.agent_team = Some(Membership {
        team_id: doc.id.clone(),
        agent_id: id.into(),
        inbox_cursor: 0,
        last_output: None,
        notices: vec![],
        waiting: None,
    });
    db.save(
        &child,
        "team_instance_created",
        json!({"parent_session_id":"parent-session","agent_id":id}),
    )
    .unwrap();
    db.agent_team("parent-session", "ready", &json!({"agent_id":id}), None)
        .unwrap();
    child.id
}
pub fn finish(db: &mut dyn StateStore, sid: &str, status: &str) {
    let mut s = db.get(sid).unwrap();
    s.turn.as_mut().unwrap().status = status.into();
    s.turn.as_mut().unwrap().queue.clear();
    s.agent_team.as_mut().unwrap().last_output = Some("Child result".into());
    db.save(&s, "turn_completed", json!({"status":status}))
        .unwrap();
}
pub fn exercise(db: &mut dyn StateStore) {
    create(db);
    let args = request("reader", "async");
    let spawned = spawn(db, &args, "once");
    assert_eq!(spawned["status"], "queued");
    let child = attach(db, "reader");
    assert_eq!(
        db.agent_team("parent-session", "create_sub_agent", &args, None)
            .unwrap()["session_id"],
        child
    );
    let mut conflict = args.clone();
    conflict["context"] = json!("Different");
    assert!(db
        .agent_team("parent-session", "subagent_prepare", &conflict, None)
        .is_err());
    assert!(db
        .agent_team(
            &child,
            "create_sub_agent",
            &request("grandchild", "async"),
            None
        )
        .is_err());
    assert!(db
        .agent_team(
            &child,
            "send_message",
            &json!({"agent_id":"reader","message":"sibling spoof"}),
            None
        )
        .is_err());
    assert!(db.get(&child).unwrap().history.is_empty());
    let (s, _, _) = db.accept(&child, "initial-reader", "Fresh task").unwrap();
    assert!(!json!(s.history)
        .to_string()
        .contains("PARENT_PRIVATE_SENTINEL"));
    // Busy delivery does not require the parent turn to finish.
    db.agent_team(
        "parent-session",
        "send_message",
        &json!({"agent_id":"reader","message":"Check authentication too"}),
        None,
    )
    .unwrap();
    assert_eq!(
        db.agent_team(&child, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert!(
        db.agent_team(&child, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    db.agent_team(
        &child,
        "send_message",
        &json!({"agent_id":"parent","message":"Found evidence"}),
        None,
    )
    .unwrap();
    finish(db, &child, "completed");
    let sequence = snapshot(db).sequence;
    finish(db, &child, "completed");
    db.agent_team("parent-session", "subagent_reconcile", &json!({}), None)
        .unwrap();
    assert_eq!(
        snapshot(db).sequence,
        sequence,
        "completion notification must not duplicate"
    );
    let delivered = db
        .agent_team("parent-session", "deliver", &json!({}), None)
        .unwrap();
    assert_eq!(delivered["messages"].as_array().unwrap().len(), 2);
    assert!(delivered.to_string().contains("Child task finished"));
    assert!(snapshot(db).standalone.as_ref().unwrap().active.is_empty());
    db.agent_team(
        "parent-session",
        "send_message",
        &json!({"agent_id":"reader","message":"Follow-up"}),
        None,
    )
    .unwrap();
    assert_eq!(
        db.agent_team(&child, "wake", &json!({}), None).unwrap()["wake"],
        true
    );
    assert!(snapshot(db)
        .standalone
        .as_ref()
        .unwrap()
        .active
        .contains("reader"));
    db.agent_team(&child, "deliver", &json!({}), None).unwrap();
    db.accept(&child, "follow-up", "Continue").unwrap();
    let cancelled = db
        .agent_team(
            "parent-session",
            "cancel_agent",
            &json!({"agent_id":"reader"}),
            None,
        )
        .unwrap();
    assert_eq!(cancelled["status"], "cancellation_requested");
    let old_turn = db.get(&child).unwrap().turn.unwrap().id;
    assert_eq!(
        snapshot(db).standalone.unwrap().cancellations["reader"],
        old_turn
    );
    finish(db, &child, "cancelled");
    assert!(
        db.agent_team("parent-session", "create_sub_agent", &args, None)
            .unwrap()["result"]
            .is_null(),
        "Cancelled follow-up must not surface a previous result"
    );
    assert!(snapshot(db).standalone.unwrap().cancellations.is_empty());
    db.agent_team(
        "parent-session",
        "send_message",
        &json!({"agent_id":"reader","message":"Do not undo explicit cancellation"}),
        None,
    )
    .unwrap();
    assert_eq!(
        db.agent_team(&child, "wake", &json!({}), None).unwrap()["wake"],
        false
    );
    db.agent_team(
        "parent-session",
        "close_agent",
        &json!({"agent_id":"reader"}),
        None,
    )
    .unwrap();
    assert!(db
        .agent_team(
            "parent-session",
            "send_message",
            &json!({"agent_id":"reader","message":"Late"}),
            None
        )
        .is_err());
    let public = openworker_runtime::agent_team::public_snapshot(json!(snapshot(db)));
    assert!(public.get("standalone").is_none());
}
pub fn sync_and_budget(db: &mut dyn StateStore) {
    create(db);
    let args = request("verifier", "sync");
    let result = spawn(db, &args, "once");
    assert_eq!(result["parked"], true);
    let child = attach(db, "verifier");
    assert_eq!(
        db.agent_team("parent-session", "wake", &json!({}), None)
            .unwrap()["wake"],
        false
    );
    db.accept(&child, "initial", "Verify").unwrap();
    // Both parent and child reserve from one durable allowance.
    for (sid, id) in [
        ("parent-session", "parent-attempt"),
        (child.as_str(), "child-attempt"),
    ] {
        assert_eq!(
            db.agent_team(
                sid,
                "reserve_model",
                &json!({"attempt_id":id,"output_tokens":8000}),
                None
            )
            .unwrap()["allowed"],
            true
        );
        db.agent_team(
            sid,
            "settle_model",
            &json!({"attempt_id":id,"output_tokens":10}),
            None,
        )
        .unwrap();
        db.agent_team(
            sid,
            "settle_model",
            &json!({"attempt_id":id,"output_tokens":10}),
            None,
        )
        .unwrap();
    }
    assert_eq!(snapshot(db).budget.model_calls, 2);
    assert_eq!(snapshot(db).budget.reported_output_tokens, 20);
    finish(db, &child, "completed");
    assert_eq!(
        snapshot(db).sequence,
        0,
        "sync result should not also wake a second parent turn"
    );
    assert_eq!(
        db.agent_team("parent-session", "wake", &json!({}), None)
            .unwrap()["resume"],
        true
    );
    let mut p = db.get("parent-session").unwrap();
    p.turn.as_mut().unwrap().status = "running".into();
    db.save(&p, "resumed", json!({})).unwrap();
    let t = p.turn.unwrap();
    let call = t.queue[0].clone();
    assert_eq!(
        db.agent_team(
            "parent-session",
            "create_sub_agent",
            &args,
            Some((&t.id, &call))
        )
        .unwrap()["status"],
        "completed"
    );
    assert!(db
        .get("parent-session")
        .unwrap()
        .turn
        .unwrap()
        .queue
        .is_empty());
}
