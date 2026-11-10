use openworker_runtime::{connections::Choice, team_definition::*};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

fn names(values: &[&str]) -> BTreeSet<String> {
    values.iter().map(|v| (*v).into()).collect()
}
fn ceiling() -> Ceiling {
    Ceiling {
        lead_session_id: "s_lead_one".into(),
        policy_digest: "a".repeat(64),
        tools: names(&["read_file", "search_files"]),
        models: vec![Choice::new("primary", "working-model")],
        placements: BTreeSet::from([Placement::Team, Placement::Member, Placement::New]),
        profiles: BTreeMap::from([(
            "review".into(),
            Grants {
                files: names(&["project-read"]),
                network: names(&["docs-read"]),
                credentials: names(&["docs-token"]),
                cpu: 2,
                memory_mb: 2048,
            },
        )]),
    }
}
fn definition() -> Definition {
    serde_json::from_value(json!({"name":"docs-reviewer","instructions":"Review documentation; report evidence.","tools":["read_file"],"sandbox":{"mode":"new","profile":"review","files":["project-read"],"network":[],"credentials":[]}})).unwrap()
}
fn prebuilt() -> BTreeSet<String> {
    names(&["builder", "verifier"])
}

#[test]
fn proposed_shape_accepts_a_real_subset_without_provisioning_or_approval() {
    let c = ceiling();
    let d = definition();
    d.validate(&c, &prebuilt()).unwrap();
    let bound = ScopedDefinition::propose(d.clone(), 1, &c, &prebuilt()).unwrap();
    assert_eq!(bound.definition(), &d);
    assert_eq!(bound.revision(), 1);
    assert_eq!(bound.digest().len(), 64);
    // Binding material has no approval flag, global registration, credentials or
    // environment reference: storing/approving/executing is a later adapter step.
    let value = serde_json::to_value(&bound).unwrap();
    assert!(value.get("approved").is_none());
    assert!(value.get("environment_id").is_none());
    let copy: ScopedDefinition = serde_json::from_value(value).unwrap();
    copy.revalidate(&c, &prebuilt()).unwrap();
}

#[test]
fn extra_file_network_credential_tool_profile_and_resource_authority_rejects() {
    let c = ceiling();
    for (field, value) in [
        ("files", json!(["project-write"])),
        ("network", json!(["arbitrary-web"])),
        ("credentials", json!(["admin-key"])),
        ("profile", json!("host-admin")),
        ("cpu", json!(3)),
        ("memory_mb", json!(2049)),
    ] {
        let mut v = serde_json::to_value(definition()).unwrap();
        v["sandbox"][field] = value;
        let d: Definition = serde_json::from_value(v).unwrap();
        assert!(d.validate(&c, &prebuilt()).is_err(), "{field}");
    }
    let mut d = definition();
    d.tools.insert("run_shell".into());
    assert!(d.validate(&c, &prebuilt()).is_err());
}

#[test]
fn missing_grants_are_empty_not_implicit_inheritance() {
    let d:Definition=serde_json::from_value(json!({"name":"reader","instructions":"Read.","tools":[],"sandbox":{"mode":"new","profile":"review"}})).unwrap();
    match &d.sandbox {
        Sandbox::New {
            files,
            network,
            credentials,
            cpu,
            memory_mb,
            ..
        } => {
            assert!(files.is_empty() && network.is_empty() && credentials.is_empty());
            assert_eq!((*cpu, *memory_mb), (1, 512));
        }
        _ => panic!("expected new sandbox"),
    }
    d.validate(&ceiling(), &prebuilt()).unwrap();
}

#[test]
fn sandbox_placement_is_explicit_and_cannot_smuggle_policy_overrides() {
    let mut c = ceiling();
    c.placements = BTreeSet::from([Placement::Team]);
    assert!(definition().validate(&c, &prebuilt()).is_err());
    for v in [
        json!({"mode":"off"}),
        json!({"mode":"team","files":["project-read"]}),
        json!({"mode":"new","profile":"review","policy":"anything"}),
        json!({"mode":"new","profile":"review","image":"arbitrary-image"}),
        json!({"mode":"new","profile":"review","apiKey":"secret"}),
    ] {
        assert!(serde_json::from_value::<Sandbox>(v.clone()).is_err(), "{v}");
    }
    let mut d = definition();
    d.sandbox = Sandbox::Team {};
    d.validate(&c, &prebuilt()).unwrap();
    d.sandbox = Sandbox::Member {
        agent_id: "builder-ui".into(),
    };
    assert!(d.validate(&c, &prebuilt()).is_err());
    c.placements.insert(Placement::Member);
    d.validate(&c, &prebuilt()).unwrap();
    d.sandbox = Sandbox::Member {
        agent_id: "lead".into(),
    };
    d.validate(&c, &prebuilt()).unwrap();
    d.sandbox = Sandbox::Member {
        agent_id: "../another-team".into(),
    };
    assert!(d.validate(&c, &prebuilt()).is_err());
}

#[test]
fn custom_types_cannot_replace_approvers_modes_or_server_launchers() {
    for (field, value) in [
        ("approvals", json!({"mode":"bypass"})),
        ("reviewer_model", json!("agent-chosen-reviewer")),
        ("servers", json!("arbitrary-launcher")),
        ("lead_session_id", json!("another-lead")),
    ] {
        let mut v = serde_json::to_value(definition()).unwrap();
        v[field] = value;
        assert!(serde_json::from_value::<Definition>(v).is_err(), "{field}");
    }
}

#[test]
fn names_cannot_shadow_prebuilt_or_reserved_roles() {
    for name in [
        "builder",
        "verifier",
        "lead",
        "all",
        "user",
        "board",
        "../x",
        "session:x",
        "",
    ] {
        let mut d = definition();
        d.name = name.into();
        assert!(d.validate(&ceiling(), &prebuilt()).is_err(), "{name}");
    }
    let mut d = definition();
    d.name = "reader-2".into();
    d.validate(&ceiling(), &prebuilt()).unwrap();
}

#[test]
fn model_selection_is_optional_and_bounded_by_the_leads_delegated_models() {
    let mut d = definition();
    d.model = Some(Choice::new("primary", "working-model"));
    d.validate(&ceiling(), &prebuilt()).unwrap();
    for m in [
        Choice::new("other-connection", "working-model"),
        Choice::new("primary", "other-model"),
    ] {
        d.model = Some(m);
        assert!(d.validate(&ceiling(), &prebuilt()).is_err());
    }
}

#[test]
fn approval_binding_is_session_scoped_and_invalidates_on_policy_revocation() {
    let c = ceiling();
    let bound = ScopedDefinition::propose(definition(), 1, &c, &prebuilt()).unwrap();
    let mut other = c.clone();
    other.lead_session_id = "s_other_lead".into();
    assert!(bound.revalidate(&other, &prebuilt()).is_err());
    other = c.clone();
    other.policy_digest = "b".repeat(64);
    assert!(bound.revalidate(&other, &prebuilt()).is_err());
    // Even if a buggy adapter forgets to change the digest, current ceiling
    // validation refuses a grant that is no longer permitted.
    other = c.clone();
    other.tools.clear();
    assert!(bound.revalidate(&other, &prebuilt()).is_err());
    other = c.clone();
    other.profiles.get_mut("review").unwrap().files.clear();
    assert!(bound.revalidate(&other, &prebuilt()).is_err());
    let mut collisions = prebuilt();
    collisions.insert("docs-reviewer".into());
    assert!(bound.revalidate(&c, &collisions).is_err());
}

#[test]
fn digest_binds_prompt_tools_revision_model_scope_and_policy_not_set_order() {
    let c = ceiling();
    let base = ScopedDefinition::propose(definition(), 1, &c, &prebuilt()).unwrap();
    let mut variants = vec![];
    let mut d = definition();
    d.instructions.push_str(" Different instructions.");
    variants.push(d);
    let mut d = definition();
    d.tools.insert("search_files".into());
    variants.push(d);
    let mut d = definition();
    d.model = Some(Choice::new("primary", "working-model"));
    variants.push(d);
    let mut d = definition();
    d.sandbox = Sandbox::Team {};
    variants.push(d);
    for d in variants {
        assert_ne!(
            base.digest(),
            ScopedDefinition::propose(d, 1, &c, &prebuilt())
                .unwrap()
                .digest()
        );
    }
    assert_ne!(
        base.digest(),
        ScopedDefinition::propose(definition(), 2, &c, &prebuilt())
            .unwrap()
            .digest()
    );
    let mut other = c.clone();
    other.lead_session_id = "s_second".into();
    assert_ne!(
        base.digest(),
        ScopedDefinition::propose(definition(), 1, &other, &prebuilt())
            .unwrap()
            .digest()
    );
    other = c.clone();
    other.policy_digest = "b".repeat(64);
    assert_ne!(
        base.digest(),
        ScopedDefinition::propose(definition(), 1, &other, &prebuilt())
            .unwrap()
            .digest()
    );
    let mut a = definition();
    a.tools = names(&["search_files", "read_file"]);
    let mut b = definition();
    b.tools = names(&["read_file", "search_files"]);
    assert_eq!(
        ScopedDefinition::propose(a, 1, &c, &prebuilt())
            .unwrap()
            .digest(),
        ScopedDefinition::propose(b, 1, &c, &prebuilt())
            .unwrap()
            .digest()
    );
}

#[test]
fn modified_serialized_definitions_cannot_reuse_binding_material() {
    let c = ceiling();
    let bound = ScopedDefinition::propose(definition(), 1, &c, &prebuilt()).unwrap();
    let mut v = serde_json::to_value(&bound).unwrap();
    v["definition"]["instructions"] = json!("Changed after approval");
    assert!(serde_json::from_value::<ScopedDefinition>(v)
        .unwrap()
        .revalidate(&c, &prebuilt())
        .is_err());
    assert!(ScopedDefinition::propose(definition(), 0, &c, &prebuilt()).is_err());
}

#[test]
fn hard_policy_sharing_requires_resolved_fingerprints_not_matching_labels() {
    require_shared_scope(&"a".repeat(64), &"a".repeat(64)).unwrap();
    assert!(require_shared_scope(&"a".repeat(64), &"b".repeat(64)).is_err());
    assert!(require_shared_scope("review", "review").is_err());
    assert!(require_shared_scope("", "").is_err());
}

#[test]
fn oversized_or_malformed_definitions_and_ceilings_are_refused() {
    for text in [
        "".to_owned(),
        " ".into(),
        "x".repeat(32001),
        "hidden\0text".into(),
    ] {
        let mut d = definition();
        d.instructions = text;
        assert!(d.validate(&ceiling(), &prebuilt()).is_err());
    }
    let mut d = definition();
    d.tools = (0..65).map(|i| format!("tool-{i}")).collect();
    assert!(d.validate(&ceiling(), &prebuilt()).is_err());
    let mut c = ceiling();
    c.policy_digest.clear();
    assert!(definition().validate(&c, &prebuilt()).is_err());
    c = ceiling();
    c.profiles.get_mut("review").unwrap().cpu = 0;
    assert!(definition().validate(&c, &prebuilt()).is_err());
}
