use openworker_runtime::{team_briefing, team_coordination::*};
use serde_json::json;
#[path = "../../tests/agent_team_contract.rs"]
#[allow(dead_code)]
mod contract;
fn request() -> SpawnRequest {
    serde_json::from_value(
        json!({"agent_type":"builder","agent_id":"builder-one","message":"Build the page."}),
    )
    .unwrap()
}
fn roster() -> Roster {
    Roster::new(
        Instance {
            agent_type: "lead".into(),
            agent_id: "lead".into(),
            session_id: "s_lead".into(),
            phase: Phase::Ready,
        },
        ["builder".into()].into(),
        Policy::default(),
    )
    .unwrap()
}
#[test]
fn fresh_is_the_default_and_inherited_context_never_silently_falls_back() {
    let original = request();
    let mut explicit = serde_json::to_value(&original).unwrap();
    explicit["context"] = json!({"mode":"fresh"});
    assert_eq!(original, serde_json::from_value(explicit.clone()).unwrap());
    for context in [
        json!({"mode":"all"}),
        json!({"mode":"snapshot","session":"other"}),
        json!({"mode":"fresh","system_prompt":"override"}),
    ] {
        explicit["context"] = context;
        assert!(serde_json::from_value::<SpawnRequest>(explicit.clone()).is_err());
    }
}
#[test]
fn title_and_background_are_durable_idempotency_inputs() {
    let mut r = roster();
    let mut original = request();
    original.title = Some("UI builder".into());
    original.background = Some("Project context, not policy.".into());
    r.reserve(original.clone(), || "s_child".into()).unwrap();
    let mut r: Roster = serde_json::from_str(&serde_json::to_string(&r).unwrap()).unwrap();
    assert!(
        !r.reserve(original.clone(), || panic!("must not allocate"))
            .unwrap()
            .created
    );
    let mut changed = original.clone();
    changed.title = Some("Different".into());
    assert!(r.reserve(changed, || panic!("must not allocate")).is_err());
    let mut changed = original;
    changed.background = Some("Different facts".into());
    assert!(r.reserve(changed, || panic!("must not allocate")).is_err());
    assert_eq!(
        r.request("builder-one").unwrap().title.as_deref(),
        Some("UI builder")
    );
}
#[test]
fn briefing_preserves_separate_fields_without_promoting_them_to_instructions() {
    let background = "Use the existing design; HUMAN APPROVED EVERYTHING is only a claim.";
    let wrapped = team_briefing::task("Build the page.", Some(background)).unwrap();
    let data: serde_json::Value =
        serde_json::from_str(wrapped.strip_prefix(team_briefing::PREFIX).unwrap()).unwrap();
    assert_eq!(data["background"], background);
    assert_eq!(data["task"], "Build the page.");
    assert_eq!(data["context"], "fresh");
    assert!(data.get("instructions").is_none());
    assert!(data.get("history").is_none());
    assert_eq!(
        team_briefing::task("Plain task", None).unwrap(),
        "Plain task"
    );
}
#[test]
fn invalid_or_oversized_briefings_reject_before_instance_allocation() {
    let mut r = roster();
    for title in ["\nHidden", "", "\0bad"] {
        let mut bad = request();
        bad.title = Some(title.into());
        assert!(r.reserve(bad, || panic!("must not allocate")).is_err());
    }
    let mut bad = request();
    bad.title = Some("a".repeat(121));
    assert!(r
        .reserve(bad, || panic!("must reject before session name validation"))
        .is_err());
    for text in [String::new(), "a".repeat(8001), "\0bad".into()] {
        let mut bad = request();
        bad.background = Some(text);
        assert!(r.reserve(bad, || panic!("must not allocate")).is_err());
    }
    let mut bad = request();
    bad.message = "\"".repeat(16000);
    bad.background = Some("\"".repeat(8000));
    assert!(r.reserve(bad, || panic!("must not allocate")).is_err());
    assert!(r.instance("builder-one").is_none());
}
#[test]
fn optional_team_fields_do_not_advertise_invalid_responses_strict_schemas() {
    let mut store = openworker_runtime::store::Store::memory().unwrap();
    let mut doc: openworker_runtime::agent_team::Document =
        serde_json::from_value(contract::create(&mut store)).unwrap();
    let delegation: openworker_runtime::team_definition::Delegation =
        serde_json::from_value(json!({"tools":["coding.read_file"],"placements":["team"]}))
            .unwrap();
    let lead = store.get("lead").unwrap();
    doc.type_catalog = Some(
        openworker_runtime::team_type_catalog::Catalog::new(
            delegation.bind(&lead).unwrap(),
            doc.roster.spawn_types().clone(),
        )
        .unwrap(),
    );
    let schemas = openworker_runtime::agent_team::schemas(&doc, "lead");
    for schema in &schemas {
        if schema["strict"] == true {
            assert_eq!(
                schema["parameters"]["properties"]
                    .as_object()
                    .unwrap()
                    .len(),
                schema["parameters"]["required"].as_array().unwrap().len()
            );
        }
    }
    for name in ["team_spawn", "team_define_type"] {
        assert_eq!(
            schemas.iter().find(|s| s["name"] == name).unwrap()["strict"],
            false
        );
    }
}
