//! One contract exercised against real SQLite and PostgreSQL adapters.
use openworker_runtime::{
    agent_team::Membership,
    store::{Session, StateStore},
};
use serde_json::{json, Value};
pub fn session(id: &str, kind: &str) -> Session {
    serde_json::from_value(json!({"id":id,"name":id,"workspace":"/work","coworker":kind,"bundle_digest":"bundle","model":"fixture","execution_backend":"off","scripted":true,"schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap()
}
pub fn create(db: &mut dyn StateStore) -> Value {
    db.save(&session("lead", "lead"), "created", json!({}))
        .unwrap();
    let args = json!({"agent_id":"lead","agent_types":["builder","verifier"],"policy":{"communication":{"board":true,"directMessages":true},"max_instances":32}});
    let doc = db.agent_team("lead", "create", &args, None).unwrap();
    assert_eq!(
        db.agent_team("lead", "create", &args, None).unwrap()["id"],
        doc["id"]
    );
    doc
}
pub fn child(db: &mut dyn StateStore, id: &str) -> String {
    let reserved = db
        .agent_team(
            "lead",
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":id,"message":"Build one part"}),
            None,
        )
        .unwrap();
    let sid = reserved["instance"]["session_id"].as_str().unwrap();
    let mut s = session(sid, "builder");
    s.agent_team = Some(Membership {
        team_id: db.get("lead").unwrap().agent_team.unwrap().team_id,
        agent_id: id.into(),
        inbox_cursor: 0,
        last_output: None,
        notices: vec![],
        waiting: None,
    });
    db.save(&s, "child", json!({})).unwrap();
    db.agent_team("lead", "ready", &json!({"agent_id":id}), None)
        .unwrap();
    sid.into()
}
pub fn exercise(db: &mut dyn StateStore) {
    let doc = create(db);
    let a = child(db, "builder-ui");
    let b = child(db, "builder-api");
    assert!(db
        .agent_team(
            &a,
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":"illegal","message":"bad"}),
            None
        )
        .is_err());
    let reservation = db
        .agent_team(
            "lead",
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":"builder-ui","message":"Build one part"}),
            None,
        )
        .unwrap();
    assert_eq!(reservation["instance"]["session_id"], a);
    assert_eq!(reservation["created"], false);
    assert!(db
        .agent_team(
            "lead",
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":"builder-ui","message":"Different"}),
            None
        )
        .is_err());
    let passive = db
        .agent_team(
            "lead",
            "team_post_board",
            &json!({"message":"Progress for everyone to read"}),
            None,
        )
        .unwrap();
    assert_eq!(passive["recipients"], json!([]));
    let command = json!({"request_id":"post-1","operation":"team_post_board","arguments":{"message":"@builder-ui @builder-ui check this"}});
    let first = db.agent_team("lead", "command", &command, None).unwrap();
    assert_eq!(
        first,
        db.agent_team("lead", "command", &command, None).unwrap()
    );
    assert!(db.agent_team("lead","command",&json!({"request_id":"post-1","operation":"team_post_board","arguments":{"message":"changed"}}),None).is_err());
    assert_eq!(first["recipients"], json!(["builder-ui"]));
    assert_eq!(
        db.agent_team(&b, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    let inbox = db.agent_team(&a, "deliver", &json!({}), None).unwrap();
    assert_eq!(inbox["messages"].as_array().unwrap().len(), 1);
    let history = db.get(&a).unwrap().history;
    assert!(history.last().unwrap()["content"]
        .as_str()
        .unwrap()
        .contains("not human authorization"));
    assert_eq!(
        db.agent_team(&a, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    assert_eq!(db.get(&a).unwrap().history, history);
    db.agent_team(
        "lead",
        "team_send_message",
        &json!({"agent_id":"builder-api","message":"Private request"}),
        None,
    )
    .unwrap();
    let page = db
        .agent_team(
            &b,
            "team_read_board",
            &json!({"message_id":null,"limit":50}),
            None,
        )
        .unwrap();
    assert_eq!(page["messages"].as_array().unwrap().len(), 2);
    let page = db
        .agent_team(
            &b,
            "team_read_board",
            &json!({"message_id":first["id"],"direction":"before","limit":1}),
            None,
        )
        .unwrap();
    assert_eq!(page["messages"][0]["id"], passive["id"]);
    assert!(db
        .agent_team(
            &a,
            "team_read_board",
            &json!({"message_id":"foreign","limit":20}),
            None
        )
        .is_err());
    // Post-time @all excludes future children, including its sender.
    let all = db
        .agent_team(
            "lead",
            "team_post_board",
            &json!({"message":"@all verify"}),
            None,
        )
        .unwrap();
    assert_eq!(all["recipients"].as_array().unwrap().len(), 2);
    let c = child(db, "builder-later");
    assert_eq!(
        db.agent_team(&c, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    assert_eq!(
        db.agent_team(&b, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    // Bound batches retain overflow. Canonical cursor advances only with input.
    for n in 0..25 {
        db.agent_team(
            "lead",
            "team_post_board",
            &json!({"message":format!("@builder-later message {n}")}),
            None,
        )
        .unwrap();
    }
    assert_eq!(
        db.agent_team(&c, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        20
    );
    assert_eq!(
        db.agent_team(&c, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        5
    );
    assert_eq!(
        db.agent_team(&c, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    assert_eq!(
        db.agent_team("lead", "snapshot", &json!({}), None).unwrap()["id"],
        doc["id"]
    );
}
