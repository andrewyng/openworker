#[path = "../../tests/team_domain_contract.rs"]
mod contract;
#[test]
fn sqlite_shared_team_domain_contract() {
    let root = tempfile::tempdir().unwrap();
    let mut store = openworker_runtime::store::Store::open(root.path()).unwrap();
    contract::exercise(&mut store);
}

#[test]
fn sqlite_team_transaction_rolls_back_result_history_board_and_delivery_together() {
    use openworker_runtime::{
        store::{Session, Store},
        team_service::{Command, Create},
        teams::{Member, Role},
    };
    use serde_json::{json, Value};
    let root = tempfile::tempdir().unwrap();
    let mut db = Store::open(root.path()).unwrap();
    for (id, role) in [("lead", Role::Lead), ("worker", Role::Worker)] {
        let mut s:Session=serde_json::from_value(json!({"id":id,"name":id,"workspace":"/work","coworker":id,"bundle_digest":"bundle","model":"fixture","execution_backend":"off","scripted":true,"schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        s.package.team_role = Some(role);
        db.save(&s, "created", json!({})).unwrap();
    }
    let team = db
        .team_create(Create {
            request_id: "team".into(),
            name: "Team".into(),
            members: vec![
                Member {
                    session_id: "lead".into(),
                    name: "Lead".into(),
                    role: Role::Lead,
                },
                Member {
                    session_id: "worker".into(),
                    name: "Worker".into(),
                    role: Role::Worker,
                },
            ],
        })
        .unwrap()["team_id"]
        .as_str()
        .unwrap()
        .to_owned();
    let item=db.team_command(&team,Command{request_id:"item".into(),operation:"team_create_item".into(),arguments:json!({"title":"Task","description":"Evidence","criteria":"Verify","dependencies":[]})}).unwrap()["data"]["item"]["id"].as_str().unwrap().to_owned();
    let (mut s, _, _) = db.accept("lead", "start", "Work").unwrap();
    s.turn.as_mut().unwrap().status = "running".into();
    let args = json!({"item_id":item,"expected_revision":1,"session_id":"worker"});
    let call = json!({"type":"function_call","call_id":"assign-call","name":"team_assign","arguments":args.to_string()});
    s.history.push(call.clone());
    s.turn.as_mut().unwrap().queue = vec![call.clone()];
    db.save(&s, "response", json!({})).unwrap();
    let tid = s.turn.as_ref().unwrap().id.clone();
    let before = db.team_snapshot(&team).unwrap();
    let cursor = db.cursor("lead").unwrap();
    let sql = rusqlite::Connection::open(root.path().join("runtime.sqlite3")).unwrap();
    sql.execute_batch("CREATE TRIGGER fail_team_event BEFORE INSERT ON team_events BEGIN SELECT RAISE(ABORT,'injected failure'); END;").unwrap();
    assert!(db.team_call("lead", &tid, &call, &args).is_err());
    assert_eq!(db.team_snapshot(&team).unwrap(), before);
    assert_eq!(db.cursor("lead").unwrap(), cursor);
    assert_eq!(db.get("lead").unwrap().history, s.history);
    assert!(db.team_next("worker").unwrap().is_none());
    sql.execute_batch("DROP TRIGGER fail_team_event").unwrap();
    db.team_call("lead", &tid, &call, &args).unwrap();
    assert!(db.team_call("lead", &tid, &call, &args).is_err());
    let delivery = db.team_next("worker").unwrap().unwrap();
    drop(db);
    let mut db = Store::open(root.path()).unwrap();
    db.recover().unwrap();
    assert_eq!(db.team_next("worker").unwrap().unwrap(), delivery);
    assert!(db.accept("worker", &delivery.0, "forged text").is_err());
    assert_eq!(
        db.team_snapshot(&team).unwrap()["board"]["items"][item]["assignee"],
        Value::String("worker".into())
    );
}

#[test]
fn sqlite_future_team_schema_is_rejected_without_reset() {
    let root = tempfile::tempdir().unwrap();
    drop(openworker_runtime::store::Store::open(root.path()).unwrap());
    let db = rusqlite::Connection::open(root.path().join("runtime.sqlite3")).unwrap();
    db.execute("UPDATE team_version SET version=999", [])
        .unwrap();
    assert!(openworker_runtime::store::Store::open(root.path()).is_err());
    assert_eq!(
        db.query_row("SELECT version FROM team_version", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        999
    );
}
