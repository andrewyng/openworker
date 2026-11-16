#[path = "../../tests/assistant_contract.rs"]
mod contract;
use openworker_runtime::store::Store;
use serde_json::json;
#[test]
fn sqlite_contract_and_restart() {
    let tmp = tempfile::tempdir().unwrap();
    let mut s = Store::open(tmp.path()).unwrap();
    contract::exercise(&mut s);
    drop(s);
    let mut s = Store::open(tmp.path()).unwrap();
    assert_eq!(s.get("coder").unwrap().schedules[0].status, "cancelled");
    assert_eq!(
        contract::call(&mut s, "evaluator", "memory_search", json!({"query":""}))["memories"],
        json!([])
    );
}
#[test]
fn memory_and_result_roll_back_together() {
    let tmp = tempfile::tempdir().unwrap();
    let mut s = Store::open(tmp.path()).unwrap();
    s.save(&contract::session("a", Some("scope")), "created", json!({}))
        .unwrap();
    s.accept("a", "first", "Remember a fact").unwrap();
    let args = json!({"summary":"Fact","content":"must roll back"});
    let (tid, call) = contract::prepare(&mut s, "a", "memory_remember", &args);
    let db = rusqlite::Connection::open(tmp.path().join("runtime.sqlite3")).unwrap();
    db.execute_batch("CREATE TRIGGER fail_memory_event BEFORE INSERT ON events WHEN NEW.kind='tool_finished' BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
    assert!(s.assistant_call("a", &tid, &call, &args).is_err());
    assert_eq!(
        db.query_row("SELECT COUNT(*) FROM assistant_memories", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        0
    );
    assert_eq!(s.get("a").unwrap().turn.unwrap().queue, vec![call.clone()]);
    db.execute_batch("DROP TRIGGER fail_memory_event").unwrap();
    s.assistant_call("a", &tid, &call, &args).unwrap();
    assert!(
        s.assistant_call("a", &tid, &call, &args).is_err(),
        "Settled tool cannot replay"
    );
}

#[test]
fn future_memory_schema_is_rejected() {
    let tmp = tempfile::tempdir().unwrap();
    drop(Store::open(tmp.path()).unwrap());
    let db = rusqlite::Connection::open(tmp.path().join("runtime.sqlite3")).unwrap();
    db.execute_batch("UPDATE assistant_version SET version=999")
        .unwrap();
    assert!(Store::open(tmp.path()).is_err());
}

#[test]
fn schedule_delivery_and_admission_roll_back_together() {
    use openworker_runtime::assistant_tools::{self as a, MemoryBank};
    let tmp = tempfile::tempdir().unwrap();
    let mut store = Store::open(tmp.path()).unwrap();
    let mut s = contract::session("scheduled", None);
    let now = a::now();
    a::operate(
        &mut s,
        &mut MemoryBank::default(),
        &json!({"name":"schedule_create","call_id":"fixture"}),
        &json!({"prompt":"work","run_at":now+60}),
        now,
    )
    .unwrap();
    s.schedules[0].next_run_at = now - 1;
    store.save(&s, "created", json!({})).unwrap();
    let (key, text) = a::due(&s, now).unwrap();
    let db = rusqlite::Connection::open(tmp.path().join("runtime.sqlite3")).unwrap();
    db.execute_batch("CREATE TRIGGER fail_admission BEFORE INSERT ON events WHEN NEW.kind='turn_accepted' BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
    assert!(store.accept(&s.id, &key, &text).is_err());
    let unchanged = store.get(&s.id).unwrap();
    assert_eq!(unchanged.schedules[0].fired, 0);
    assert!(unchanged.turn.is_none());
    assert!(unchanged.history.is_empty());
    db.execute_batch("DROP TRIGGER fail_admission").unwrap();
    let (accepted, fresh, _) = store.accept(&s.id, &key, &text).unwrap();
    assert!(fresh);
    assert_eq!(accepted.schedules[0].fired, 1);
    assert!(!store.accept(&s.id, &key, &text).unwrap().1);
}

#[test]
fn scheduling_bounds_coalescing_and_closed_sessions() {
    use openworker_runtime::assistant_tools::{self as a, MemoryBank};
    let mut s = contract::session("a", None);
    let mut bank = MemoryBank::default();
    let call = json!({"name":"schedule_create","call_id":"fixture"});
    for args in [
        json!({"prompt":"work","run_at":99}),
        json!({"prompt":"work","run_at":100+31*86400}),
        json!({"prompt":"work","run_at":120,"max_runs":2}),
        json!({"prompt":"work","run_at":120,"interval_seconds":1}),
    ] {
        assert!(a::operate(&mut s, &mut bank, &call, &args, 100).is_err());
    }
    a::operate(
        &mut s,
        &mut bank,
        &call,
        &json!({"prompt":"work","run_at":120,"interval_seconds":60,"max_runs":2}),
        100,
    )
    .unwrap();
    let (key, text) = a::due(&s, 1000).unwrap();
    assert!(a::admit(&mut s, &key, "different task", 1000).is_err());
    a::admit(&mut s, &key, &text, 1000).unwrap();
    assert_eq!(s.schedules[0].next_run_at, 1060);
    assert!(a::due(&s, 1000).is_none());
    s.closed = true;
    assert!(a::due(&s, 1060).is_none());
    s.closed = false;
    let (key, text) = a::due(&s, 1060).unwrap();
    a::admit(&mut s, &key, &text, 1060).unwrap();
    assert_eq!(s.schedules[0].status, "completed");
    assert!(a::due(&s, 9999).is_none());
    s.package.memory = false;
    assert!(a::operate(
        &mut s,
        &mut bank,
        &json!({"name":"memory_remember"}),
        &json!({"summary":"x","content":"x"}),
        100
    )
    .is_err());
}
