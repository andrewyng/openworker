#[path = "../../tests/standalone_subagents_contract.rs"]
mod contract;
use openworker_runtime::store::Store;
#[test]
fn sqlite_messages_lifecycle_and_receipts() {
    contract::exercise(&mut Store::memory().unwrap());
}
#[test]
fn sqlite_sync_and_shared_budget() {
    contract::sync_and_budget(&mut Store::memory().unwrap());
}
#[test]
fn completion_receipt_survives_database_restart() {
    let tmp = tempfile::tempdir().unwrap();
    let mut db = Store::open(tmp.path()).unwrap();
    contract::create(&mut db);
    contract::spawn(&mut db, &contract::request("reader", "async"), "once");
    let child = contract::attach(&mut db, "reader");
    db.accept(&child, "initial", "Read").unwrap();
    contract::finish(&mut db, &child, "completed");
    let sequence = contract::snapshot(&mut db).sequence;
    drop(db);
    let mut db = Store::open(tmp.path()).unwrap();
    contract::finish(&mut db, &child, "completed");
    assert_eq!(contract::snapshot(&mut db).sequence, sequence);
}

#[test]
fn capacity_and_denial_never_create_extra_children() {
    use serde_json::json;
    let mut db = Store::memory().unwrap();
    contract::create(&mut db);
    assert_eq!(
        contract::spawn(&mut db, &contract::request("denied", "async"), "deny")["status"],
        "denied"
    );
    assert_eq!(contract::snapshot(&mut db).roster.instances().count(), 1);
    contract::finish(&mut db, "parent-session", "completed");
    for n in 0..4 {
        contract::spawn(
            &mut db,
            &contract::request(&format!("reader-{n}"), "async"),
            "once",
        );
        contract::finish(&mut db, "parent-session", "completed");
    }
    assert!(db
        .agent_team(
            "parent-session",
            "subagent_prepare",
            &contract::request("fifth", "async"),
            None
        )
        .is_err());
    assert_eq!(contract::snapshot(&mut db).roster.instances().count(), 5);
    let mut bad = contract::request("escape", "async");
    bad["tools"] = json!(["coding.run_shell"]);
    assert!(db
        .agent_team("parent-session", "subagent_prepare", &bad, None)
        .is_err());
    let doc = contract::snapshot(&mut db);
    let parent = openworker_runtime::agent_team::schemas(&doc, "parent");
    assert_eq!(parent.len(), 5);
    assert!(parent.iter().any(|t| t["name"] == "create_sub_agent"));
    let child = openworker_runtime::agent_team::schemas(&doc, "reader-0");
    assert_eq!(child.len(), 1);
    assert_eq!(child[0]["name"], "send_message");
}

#[test]
fn restart_keeps_parked_spawn_identity_and_queued_messages() {
    use serde_json::json;
    let tmp = tempfile::tempdir().unwrap();
    let mut db = Store::open(tmp.path()).unwrap();
    contract::create(&mut db);
    let args = contract::request("reader", "sync");
    let first = contract::spawn(&mut db, &args, "once");
    drop(db);
    let mut db = Store::open(tmp.path()).unwrap();
    let same = db
        .agent_team("parent-session", "create_sub_agent", &args, None)
        .unwrap();
    assert_eq!(same["session_id"], first["session_id"]);
    assert_eq!(contract::snapshot(&mut db).roster.instances().count(), 2);
    assert_eq!(
        db.get("parent-session").unwrap().turn.unwrap().queue.len(),
        1
    );
    let child = contract::attach(&mut db, "reader");
    db.agent_team(
        "parent-session",
        "send_message",
        &json!({"agent_id":"reader","message":"DURABLE_CONTEXT"}),
        None,
    )
    .unwrap();
    drop(db);
    let mut db = Store::open(tmp.path()).unwrap();
    let delivered = db.agent_team(&child, "deliver", &json!({}), None).unwrap();
    assert!(delivered.to_string().contains("DURABLE_CONTEXT"));
    drop(db);
    let mut db = Store::open(tmp.path()).unwrap();
    assert_eq!(
        db.agent_team(&child, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    assert_eq!(db.get(&child).unwrap().history.len(), 1);
}

#[test]
fn child_policy_is_pinned_when_spawn_is_committed() {
    use openworker_runtime::safety::{Action, Rule};
    use serde_json::json;
    let mut db = Store::memory().unwrap();
    contract::create(&mut db);
    let mut parent = db.get("parent-session").unwrap();
    parent.safety.guardrails.model_output.push(Rule::regex(
        "secret",
        "SECRET_[A-Z]+",
        Action::Block,
    ));
    db.save(&parent, "policy", json!({})).unwrap();
    contract::spawn(&mut db, &contract::request("reader", "async"), "once");
    let original = contract::snapshot(&mut db).standalone.unwrap().policies["reader"]
        .safety
        .clone();
    assert_eq!(original.guardrails.model_output.len(), 1);
    let mut parent = db.get("parent-session").unwrap();
    parent.safety = Default::default();
    db.save(&parent, "later_parent_configuration", json!({}))
        .unwrap();
    let retained = contract::snapshot(&mut db).standalone.unwrap();
    assert_eq!(
        serde_json::to_value(&retained.policy_for_type("child-reader").unwrap().safety).unwrap(),
        json!(original)
    );
}
