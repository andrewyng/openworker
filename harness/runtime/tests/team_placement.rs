//! Policy unit cases. Provider provisioning/enforcement is separately qualified.
use openworker_runtime::{
    agent_team::Document,
    store::Store,
    team_definition::{Definition, Sandbox},
    team_type_catalog::{Record, Status},
};
use serde_json::json;
#[path = "../../tests/agent_team_contract.rs"]
#[allow(dead_code)]
mod contract;
fn fixture(enabled: bool) -> (Document, openworker_runtime::store::Session) {
    let mut store = Store::memory().unwrap();
    store
        .save(&contract::session("lead", "lead"), "created", json!({}))
        .unwrap();
    let doc=store.agent_team("lead","create",&json!({"agent_id":"lead","agent_types":["builder"],"placement_selection":enabled,"delegation":{"tools":["coding.read_file"],"placements":["team","new","member"],"profiles":{"small":{"files":[],"network":[],"credentials":[],"cpu":2,"memory_mb":1024}}}}),None).unwrap();
    (
        serde_json::from_value(doc).unwrap(),
        store.get("lead").unwrap(),
    )
}
#[test]
fn unqualified_adapter_never_silently_runs_a_new_sandbox_request_in_the_team() {
    let (doc, lead) = fixture(false);
    let req=serde_json::from_value(json!({"agent_type":"builder","agent_id":"one","message":"Work","sandbox":{"mode":"new","profile":"small"}})).unwrap();
    assert!(
        openworker_runtime::team_placement::select(&doc, &lead, &req)
            .unwrap_err()
            .to_string()
            .contains("adapter")
    );
}
fn record(doc: &mut Document, cpu: u16, revision: u32) -> Record {
    let c = doc.type_catalog.as_mut().unwrap();
    let def:Definition=serde_json::from_value(json!({"name":"custom","instructions":"Read","tools":["coding.read_file"],"sandbox":{"mode":"new","profile":"small","cpu":cpu,"memory_mb":1024}})).unwrap();
    let binding = openworker_runtime::team_definition::ScopedDefinition::propose(
        def,
        revision,
        &c.ceiling,
        &c.prebuilt,
    )
    .unwrap();
    let r = Record {
        binding,
        status: Status::Approved,
        proposal_turn: "fixture".into(),
        proposal_call: format!("fixture-{revision}"),
        created_at_ms: 0,
        interaction_id: Some("fixture-exact-approval".into()),
        approval_origin: Some("trusted-unit-fixture".into()),
        resolved_at_ms: Some(0),
    };
    c.records
        .entry("custom".into())
        .or_default()
        .push(r.clone());
    doc.roster.register_type("custom").unwrap();
    r
}
#[test]
fn original_custom_default_survives_new_revision_and_instance_choices_cannot_enlarge_it() {
    let (mut doc, lead) = fixture(true);
    let original = record(&mut doc, 1, 1);
    let req: openworker_runtime::team_coordination::SpawnRequest =
        serde_json::from_value(json!({"agent_type":"custom","agent_id":"one","message":"Work"}))
            .unwrap();
    let selected = openworker_runtime::team_placement::select(&doc, &lead, &req).unwrap();
    doc.roster.reserve(req.clone(), || "child".into()).unwrap();
    doc.type_bindings
        .insert("one".into(), original.binding.digest().into());
    doc.sandbox_choices.insert("one".into(), selected.clone());
    record(&mut doc, 2, 2);
    assert_eq!(
        openworker_runtime::team_placement::select(&doc, &lead, &req).unwrap(),
        selected
    );
    let mut widened = req.clone();
    widened.sandbox = Some(
        serde_json::from_value(json!({"mode":"new","profile":"small","cpu":2,"memory_mb":1024}))
            .unwrap(),
    );
    assert!(openworker_runtime::team_placement::select(&doc, &lead, &widened).is_err());
    widened.sandbox = Some(Sandbox::Team {});
    assert!(openworker_runtime::team_placement::select(&doc, &lead, &widened).is_err());
    let next =
        serde_json::from_value(json!({"agent_type":"custom","agent_id":"two","message":"Work"}))
            .unwrap();
    let chosen = serde_json::to_value(
        openworker_runtime::team_placement::select(&doc, &lead, &next).unwrap(),
    )
    .unwrap();
    assert_eq!(chosen["cpu"], 2);
    doc.type_catalog
        .as_mut()
        .unwrap()
        .records
        .get_mut("custom")
        .unwrap()[0]
        .status = Status::Denied;
    assert!(openworker_runtime::team_placement::select(&doc, &lead, &req).is_err());
}
