//! The same domain assertions run against both storage adapters.
use openworker_runtime::{
    store::{Session, StateStore},
    team_service::{Command, Create},
    teams::{Member, Role},
};
use serde_json::{json, Value};
fn command(db: &mut dyn StateStore, team: &str, name: &str, args: Value) -> Value {
    db.team_command(
        team,
        Command {
            request_id: openworker_runtime::id("cmd"),
            operation: name.into(),
            arguments: args,
        },
    )
    .unwrap()
}
fn call(db: &mut dyn StateStore, sid: &str, name: &str, args: Value) -> Value {
    let mut s = db.get(sid).unwrap();
    let tid = s.turn.as_ref().unwrap().id.clone();
    let call = json!({"type":"function_call","call_id":openworker_runtime::id("call"),"name":name,"arguments":args.to_string()});
    s.history.push(call.clone());
    s.turn.as_mut().unwrap().queue = vec![call.clone()];
    db.save(&s, "model_response", json!({})).unwrap();
    db.team_call(sid, &tid, &call, &args).unwrap().data["result"].clone()
}
pub fn exercise(db: &mut dyn StateStore) {
    assert!(db.supports_teams());
    for (id, role) in [
        ("lead", Role::Lead),
        ("worker", Role::Worker),
        ("other", Role::Worker),
    ] {
        let mut s:Session=serde_json::from_value(json!({"id":id,"name":id,"workspace":"/work","coworker":id,"bundle_digest":"bundle","model":"fixture","execution_backend":"off","scripted":true,"schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        s.package.team_role = Some(role);
        db.save(&s, "session_created", json!({})).unwrap();
    }
    let request = Create {
        request_id: "create".into(),
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
    };
    let made = db.team_create(request.clone()).unwrap();
    let team = made["team_id"].as_str().unwrap();
    assert_eq!(db.team_create(request).unwrap()["duplicate"], true);
    command(
        db,
        team,
        "team_add_member",
        json!({"session_id":"other","name":"Other","role":"worker"}),
    );
    let a=command(db,team,"team_create_item",json!({"title":"First","description":"Full detail","criteria":"Exact evidence","dependencies":[]}))["data"]["item"]["id"].as_str().unwrap().to_owned();
    let b=command(db,team,"team_create_item",json!({"title":"Dependent","description":"Second","criteria":"Wait for acceptance","dependencies":[a]}))["data"]["item"]["id"].as_str().unwrap().to_owned();
    command(
        db,
        team,
        "team_assign",
        json!({"item_id":a,"expected_revision":1,"session_id":"worker"}),
    );
    command(
        db,
        team,
        "team_assign",
        json!({"item_id":b,"expected_revision":1,"session_id":"other"}),
    );
    assert!(db.team_next("other").unwrap().is_none());
    assert_eq!(db.team_snapshot(team).unwrap()["pending_work"], true);
    let (key, text) = db.team_next("worker").unwrap().unwrap();
    let (mut s, _, _) = db.accept("worker", &key, &text).unwrap();
    assert_eq!(
        s.history.last().unwrap()["_runtime_notice"],
        "team_delivery"
    );
    s.turn.as_mut().unwrap().status = "running".into();
    db.save(&s, "turn_started", json!({})).unwrap();
    assert_eq!(
        db.team_context("worker").unwrap().unwrap().active_item,
        Some(a.clone())
    );
    assert_eq!(
        call(db, "worker", "team_get_item", json!({"item_id":a}))["item"]["description"],
        "Full detail"
    );
    assert_eq!(
        call(
            db,
            "worker",
            "team_transition",
            json!({"item_id":a,"expected_revision":2,"status":"done"})
        )["isError"],
        true
    );
    assert!(call(
        db,
        "worker",
        "team_transition",
        json!({"item_id":a,"expected_revision":2,"status":"in_progress"})
    )
    .get("isError")
    .is_none());
    call(
        db,
        "worker",
        "team_post_message",
        json!({"body":"Evidence","item_id":a,"recipient":""}),
    );
    assert!(call(
        db,
        "worker",
        "team_transition",
        json!({"item_id":a,"expected_revision":3,"status":"review"})
    )
    .get("isError")
    .is_none());
    assert!(db.team_next("lead").unwrap().is_none());
    let mut s = db.get("worker").unwrap();
    s.turn.as_mut().unwrap().status = "completed".into();
    db.save(&s, "turn_completed", json!({"status":"completed"}))
        .unwrap();
    assert_eq!(
        db.team_snapshot(team).unwrap()["pending_work"],
        true,
        "unprocessed completion outbox is pending work"
    );
    db.team_tick().unwrap();
    assert_eq!(
        db.team_snapshot(team).unwrap()["pending_work"],
        true,
        "lead wake remains queued"
    );
    let first = db.team_next("lead").unwrap().unwrap();
    db.team_tick().unwrap();
    assert_eq!(db.team_next("lead").unwrap().unwrap(), first);
    command(
        db,
        team,
        "team_transition",
        json!({"item_id":a,"expected_revision":4,"status":"done"}),
    );
    assert!(db.team_next("other").unwrap().is_some());
    let cmd = Command {
        request_id: "message".into(),
        operation: "team_post_message".into(),
        arguments: json!({"body":"Hello","item_id":"","recipient":"worker"}),
    };
    let old = db.team_command(team, cmd.clone()).unwrap();
    assert_eq!(db.team_command(team, cmd).unwrap(), old);
    assert!(db.team_events(team, 0).unwrap()["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v["type"] == "message_posted"));
    command(db, team, "team_cancel", json!({}));
    assert!(db.team_next("other").unwrap().is_none());
    assert_eq!(db.team_snapshot(team).unwrap()["board"]["cancelled"], true);
}
