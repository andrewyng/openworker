#[path = "../../tests/team_type_catalog_contract.rs"]
mod contract;
use serde_json::json;
#[test]
fn sqlite_exact_definition_approval_contract() {
    contract::exercise(&mut openworker_runtime::store::Store::memory().unwrap());
}
#[test]
fn pending_and_approved_receipts_survive_actual_database_restart() {
    let root = tempfile::tempdir().unwrap();
    let mut db = openworker_runtime::store::Store::open(root.path()).unwrap();
    contract::create(&mut db);
    let args = contract::definition();
    let (tid, call) = contract::queue(&mut db, &args, "restart");
    db.agent_team("type-lead", "type_prepare", &args, None)
        .unwrap();
    drop(db);
    let mut db = openworker_runtime::store::Store::open(root.path()).unwrap();
    assert_eq!(
        contract::catalog(&mut db).records["reader"][0].status,
        openworker_runtime::team_type_catalog::Status::Pending
    );
    contract::decision(&mut db, &call, "once", "human");
    db.agent_team("type-lead", "type_resolve", &args, Some((&tid, &call)))
        .unwrap();
    drop(db);
    let mut db = openworker_runtime::store::Store::open(root.path()).unwrap();
    assert_eq!(
        contract::catalog(&mut db)
            .approved("reader")
            .unwrap()
            .binding
            .revision(),
        1
    );
    let raw = rusqlite::Connection::open(root.path().join("runtime.sqlite3")).unwrap();
    raw.execute_batch("CREATE TRIGGER fail_type_event BEFORE INSERT ON events WHEN NEW.kind='agent_team' BEGIN SELECT RAISE(ABORT,'injected'); END;").unwrap();
    assert!(db
        .agent_team(
            "type-lead",
            "team_spawn",
            &json!({"agent_type":"reader","agent_id":"rollback","message":"Read"}),
            None
        )
        .is_err());
    raw.execute_batch("DROP TRIGGER fail_type_event").unwrap();
    let doc = db
        .agent_team("type-lead", "snapshot", &json!({}), None)
        .unwrap();
    assert!(doc["roster"]["instances"].get("rollback").is_none());
}
