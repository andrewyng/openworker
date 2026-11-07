//! Run the identical definition state machine on real SQLite and PostgreSQL.
#[path = "agent_team_contract.rs"]
#[allow(dead_code)] // Shared fixture also contains older coordination scenarios.
mod fixture;
use openworker_runtime::{
    store::StateStore,
    team_type_catalog::{Catalog, Status},
};
use serde_json::{json, Value};
pub fn delegation() -> Value {
    json!({"tools":["coding.read_file"],"models":[],"placements":["team"],"profiles":{}})
}
pub fn definition() -> Value {
    json!({"name":"reader","instructions":"Read and report evidence.","tools":["coding.read_file"]})
}
pub fn create(db: &mut dyn StateStore) {
    let mut lead = fixture::session("type-lead", "lead");
    lead.schema_digest = Some("schema".into());
    db.save(&lead, "created", json!({})).unwrap();
    let args = json!({"agent_id":"lead","agent_types":["builder"],"delegation":delegation()});
    db.agent_team("type-lead", "create", &args, None).unwrap();
    db.agent_team("type-lead", "create", &args, None).unwrap();
    let mut changed = args;
    changed["delegation"]["tools"] = json!([]);
    assert!(db
        .agent_team("type-lead", "create", &changed, None)
        .is_err());
}
pub fn queue(db: &mut dyn StateStore, args: &Value, submission: &str) -> (String, Value) {
    let (mut s, _, _) = db
        .accept("type-lead", submission, "Define a reader")
        .unwrap();
    let call = json!({"type":"function_call","call_id":format!("define-{submission}"),"name":"team_define_type","arguments":args.to_string()});
    let turn = s.turn.as_mut().unwrap();
    turn.status = "running".into();
    turn.queue = vec![call.clone()];
    let tid = turn.id.clone();
    s.history.push(call.clone());
    db.save(&s, "assistant_message", json!({})).unwrap();
    (tid, call)
}
pub fn decision(db: &mut dyn StateStore, call: &Value, response: &str, origin: &str) {
    let mut s = db.get("type-lead").unwrap();
    let i = json!({"id":"i-exact","turn_id":s.turn.as_ref().unwrap().id,"kind":"approval","call":call,"bundle_digest":s.bundle_digest,"schema_digest":s.schema_digest,"response":response,"origin":origin,"server":null,"remote_tool":null,"policy_digest":s.tool_policy.digest,"resource_policy_digest":s.resource_policy.digest});
    s.interactions.push(serde_json::from_value(i).unwrap());
    db.save(&s, "interaction_resolved", json!({})).unwrap();
}
pub fn catalog(db: &mut dyn StateStore) -> Catalog {
    serde_json::from_value(
        db.agent_team("type-lead", "snapshot", &json!({}), None)
            .unwrap()["type_catalog"]
            .clone(),
    )
    .unwrap()
}
fn end_turn(db: &mut dyn StateStore, status: &str) {
    let mut s = db.get("type-lead").unwrap();
    s.turn.as_mut().unwrap().status = status.into();
    db.save(&s, "turn_completed", json!({"status":status}))
        .unwrap();
}
pub fn exercise(db: &mut dyn StateStore) {
    create(db);
    let args = definition();
    let (tid, call) = queue(db, &args, "first");
    let pending = db
        .agent_team("type-lead", "type_prepare", &args, None)
        .unwrap();
    assert_eq!(pending["status"], "pending");
    assert_eq!(
        db.agent_team("type-lead", "type_prepare", &args, None)
            .unwrap(),
        pending
    );
    assert!(catalog(db).approved("reader").is_err());
    assert!(db
        .agent_team(
            "type-lead",
            "team_spawn",
            &json!({"agent_type":"reader","agent_id":"reader-one","message":"Read"}),
            None
        )
        .is_err());
    assert!(db
        .agent_team("type-lead", "type_resolve", &args, Some((&tid, &call)))
        .is_err());
    for (response, origin) in [
        ("session", "human"),
        ("once", "session_grant"),
        ("once", "app_policy"),
    ] {
        decision(db, &call, response, origin);
        assert!(db
            .agent_team("type-lead", "type_resolve", &args, Some((&tid, &call)))
            .is_err());
        assert!(catalog(db).approved("reader").is_err());
    }
    decision(db, &call, "once", "human");
    let approved = db
        .agent_team("type-lead", "type_resolve", &args, Some((&tid, &call)))
        .unwrap();
    assert_eq!(approved["status"], "approved");
    assert!(db
        .agent_team("type-lead", "type_resolve", &args, Some((&tid, &call)))
        .is_err());
    let record = catalog(db).approved("reader").unwrap().clone();
    assert_eq!(record.status, Status::Approved);
    assert_eq!(record.interaction_id.as_deref(), Some("i-exact"));
    let spawned = db
        .agent_team(
            "type-lead",
            "team_spawn",
            &json!({"agent_type":"reader","agent_id":"reader-one","message":"Read"}),
            None,
        )
        .unwrap();
    let doc: openworker_runtime::agent_team::Document = serde_json::from_value(
        db.agent_team("type-lead", "snapshot", &json!({}), None)
            .unwrap(),
    )
    .unwrap();
    let instance = serde_json::from_value(spawned["instance"].clone()).unwrap();
    assert_eq!(
        openworker_runtime::agent_team::runtime_type(&doc, &instance).unwrap(),
        record.runtime_id()
    );
    let public = openworker_runtime::agent_team::public_snapshot(json!(doc));
    assert!(public.get("type_catalog").is_none());
    assert!(!public.to_string().contains("Read and report evidence"));
    let mut copied = catalog(db);
    let mut other = db.get("type-lead").unwrap();
    other.id = "other-lead".into();
    assert!(copied
        .propose(&other, serde_json::from_value(args.clone()).unwrap(), 0)
        .is_err());
    let mut current = db.get("type-lead").unwrap();
    current.resource_policy.digest = "changed".into();
    assert!(copied.check_scope(&current).is_err());
    // The catalog is not a direct application mutation endpoint; a model cannot
    // invent an interaction through the command wrapper either.
    assert!(db
        .agent_team(
            "type-lead",
            "command",
            &json!({"request_id":"spoof","operation":"type_resolve","arguments":args}),
            None
        )
        .is_err());
    // Editing a type creates a reviewed revision. Denial does not erase an old
    // approved role; accepting a later revision must not replace existing pins.
    end_turn(db, "completed");
    let mut revised = args.clone();
    revised["instructions"] = json!("Different reviewed instructions.");
    let (tid, call) = queue(db, &revised, "revision-two");
    let pending = db
        .agent_team("type-lead", "type_prepare", &revised, None)
        .unwrap();
    assert_eq!(pending["revision"], 2);
    assert_eq!(
        catalog(db).approved("reader").unwrap().binding.revision(),
        1
    );
    decision(db, &call, "deny", "human");
    db.agent_team("type-lead", "type_resolve", &revised, Some((&tid, &call)))
        .unwrap();
    assert_eq!(
        catalog(db).approved("reader").unwrap().binding.revision(),
        1
    );
    end_turn(db, "completed");
    let (tid, call) = queue(db, &revised, "revision-three");
    db.agent_team("type-lead", "type_prepare", &revised, None)
        .unwrap();
    decision(db, &call, "once", "human");
    db.agent_team("type-lead", "type_resolve", &revised, Some((&tid, &call)))
        .unwrap();
    let current = catalog(db)
        .approved("reader")
        .unwrap()
        .binding
        .digest()
        .to_owned();
    assert_ne!(current, record.binding.digest());
    let duplicate = db
        .agent_team(
            "type-lead",
            "team_spawn",
            &json!({"agent_type":"reader","agent_id":"reader-one","message":"Read"}),
            None,
        )
        .unwrap();
    assert_eq!(duplicate["created"], false);
    let new = db
        .agent_team(
            "type-lead",
            "team_spawn",
            &json!({"agent_type":"reader","agent_id":"reader-two","message":"Read"}),
            None,
        )
        .unwrap();
    assert_ne!(
        new["instance"]["session_id"],
        spawned["instance"]["session_id"]
    );
    let doc = db
        .agent_team("type-lead", "snapshot", &json!({}), None)
        .unwrap();
    assert_eq!(doc["type_bindings"]["reader-one"], record.binding.digest());
    assert_eq!(doc["type_bindings"]["reader-two"], current);
    // A cancelled pending approval cannot approve a new turn. It becomes an
    // explicit abandoned receipt when the next durable turn proposes a revision.
    end_turn(db, "completed");
    let mut other = args.clone();
    other["name"] = json!("cancelled-reader");
    let (tid, call) = queue(db, &other, "cancel-pending");
    db.agent_team("type-lead", "type_prepare", &other, None)
        .unwrap();
    end_turn(db, "cancelled");
    assert!(db
        .agent_team("type-lead", "type_resolve", &other, Some((&tid, &call)))
        .is_err());
    queue(db, &other, "fresh-after-cancel");
    db.agent_team("type-lead", "type_prepare", &other, None)
        .unwrap();
    assert_eq!(
        catalog(db).records["cancelled-reader"][0].status,
        Status::Abandoned
    );
}
