//! Identical memory/schedule domain contract for SQLite and PostgreSQL.
use openworker_runtime::{
    assistant_tools,
    store::{Session, StateStore},
};
use serde_json::{json, Value};
pub fn session(id: &str, scope: Option<&str>) -> Session {
    let mut s:Session=serde_json::from_value(json!({"id":id,"name":id,"workspace":"/work","coworker":id,"bundle_digest":"bundle","model":"fixture","execution_backend":"off","scripted":true,"schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
    s.package.memory = true;
    s.package.memory_scope = scope.map(str::to_owned);
    s.package.scheduling = true;
    s
}
pub fn prepare(db: &mut dyn StateStore, sid: &str, name: &str, args: &Value) -> (String, Value) {
    let mut s = db.get(sid).unwrap();
    let t = s.turn.as_mut().unwrap();
    t.status = "running".into();
    let tid = t.id.clone();
    let call = json!({"type":"function_call","call_id":openworker_runtime::id("call"),"name":name,"arguments":args.to_string()});
    t.queue = vec![call.clone()];
    s.history.push(call.clone());
    db.save(&s, "model_response", json!({})).unwrap();
    (tid, call)
}
pub fn call(db: &mut dyn StateStore, sid: &str, name: &str, args: Value) -> Value {
    let (tid, call) = prepare(db, sid, name, &args);
    db.assistant_call(sid, &tid, &call, &args).unwrap().data["result"].clone()
}
pub fn exercise(db: &mut dyn StateStore) {
    for (id, scope) in [
        ("coder", Some("learner-a")),
        ("evaluator", Some("learner-a")),
        ("other", Some("learner-b")),
        ("private", None),
    ] {
        db.save(&session(id, scope), "session_created", json!({}))
            .unwrap();
        db.accept(id, "first", "User asked to build a game")
            .unwrap();
    }
    let m = call(
        db,
        "coder",
        "memory_remember",
        json!({"summary":"Theme","content":"Use ocean blue"}),
    )["memory"]
        .clone();
    assert_eq!(
        call(db, "evaluator", "memory_read", json!({"id":m["id"]}))["memory"]["content"],
        "Use ocean blue"
    );
    for sid in ["other", "private"] {
        assert_eq!(
            call(db, sid, "memory_search", json!({"query":""}))["memories"],
            json!([])
        );
        assert_eq!(
            call(db, sid, "memory_read", json!({"id":m["id"]}))["isError"],
            true
        );
    }
    assert_eq!(
        call(
            db,
            "coder",
            "memory_update",
            json!({"id":m["id"],"expected_revision":99,"summary":"Theme","content":"stale"})
        )["isError"],
        true
    );
    let updated=call(db,"evaluator","memory_update",json!({"id":m["id"],"expected_revision":m["revision"],"summary":"Theme","content":"Use green"}))["memory"].clone();
    assert_eq!(
        call(db, "coder", "memory_search", json!({"query":"GREEN"}))["memories"][0]["id"],
        m["id"]
    );
    assert_eq!(
        call(
            db,
            "coder",
            "memory_forget",
            json!({"id":m["id"],"expected_revision":updated["revision"]})
        )["deleted"],
        m["id"]
    );
    let now = assistant_tools::now();
    let task = call(
        db,
        "coder",
        "schedule_create",
        json!({"prompt":"Review the project","run_at":now+60,"interval_seconds":60,"max_runs":2}),
    )["schedule"]
        .clone();
    assert_eq!(
        call(
            db,
            "coder",
            "schedule_update",
            json!({"id":task["id"],"expected_revision":99,"prompt":"bad","run_at":now+80})
        )["isError"],
        true
    );
    assert_eq!(
        call(db, "other", "schedule_list", json!({}))["schedules"],
        json!([])
    );
    let mut s = db.get("coder").unwrap();
    s.schedules[0].next_run_at = now.saturating_sub(1);
    assert!(
        assistant_tools::due(&s, now).is_none(),
        "Busy sessions must not be awakened"
    );
    s.turn.as_mut().unwrap().status = "completed".into();
    db.save(&s, "turn_completed", json!({})).unwrap();
    let (key, text) = assistant_tools::due(&s, now).unwrap();
    let (s, fresh, _) = db.accept("coder", &key, &text).unwrap();
    assert!(fresh);
    assert_eq!(s.schedules[0].fired, 1);
    assert_eq!(
        s.history.last().unwrap()["_runtime_notice"],
        "scheduled_task"
    );
    assert!(s.schedules[0].next_run_at >= now + 60);
    assert!(!db.accept("coder", &key, &text).unwrap().1);
    assert_eq!(
        db.get("coder").unwrap().schedules[0].fired,
        1,
        "Duplicate admission must not increment schedule"
    );
    let result = call(
        db,
        "coder",
        "schedule_cancel",
        json!({"id":task["id"],"expected_revision":2}),
    );
    assert_eq!(result["schedule"]["status"], "cancelled");
    let mut s = db.get("coder").unwrap();
    s.turn.as_mut().unwrap().status = "completed".into();
    assert!(assistant_tools::due(&s, now + 999).is_none());
    db.save(&s, "turn_completed", json!({})).unwrap();
}
