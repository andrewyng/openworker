use openworker_runtime::{
    bundle::{self, Bundle},
    permissions::{self, Category},
    runner,
    tool_policy::ToolPolicy,
};
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::Path};

fn manifest() -> Value {
    serde_json::from_str(include_str!("../../examples/multi/tools.json")).unwrap()
}
fn install(root: &Path, tools: &Value) -> anyhow::Result<Bundle> {
    std::fs::create_dir_all(root.join("coworker"))?;
    std::fs::write(
        root.join("coworker/manifest.md"),
        include_str!("../../examples/multi/coworker/manifest.md"),
    )?;
    std::fs::write(root.join("tools.json"), tools.to_string())?;
    Bundle::load(root)
}
fn catalogs() -> BTreeMap<String, Value> {
    ["north", "south"]
        .into_iter()
        .map(|id| (id.into(), json!({"tools":runner::schemas()})))
        .collect()
}

#[test]
fn manifests_are_strict_and_legacy_still_works() {
    let tmp = tempfile::tempdir().unwrap();
    let b = install(tmp.path(), &manifest()).unwrap();
    assert_eq!(b.tools.servers.len(), 2);
    let serialized = serde_json::to_vec(&b.tools).unwrap();
    let roundtrip: bundle::Tools = serde_json::from_slice(&serialized).unwrap();
    assert_eq!(roundtrip.servers.len(), 2);
    assert_eq!(roundtrip.bindings["south_echo"].server, "south");
    for invalid in [
        {
            let mut v = manifest();
            v["servers"][1]["id"] = json!("north");
            v
        },
        {
            let mut v = manifest();
            v["bindings"]["north_echo"]["server"] = json!("absent");
            v
        },
        {
            let mut v = manifest();
            v["bindings"]["ask_user"] = v["bindings"]["north_echo"].clone();
            v
        },
        {
            let mut v = manifest();
            v["servers"][0]["program"] = json!("../escape");
            v
        },
        {
            let mut v = manifest();
            v["servers"][0]["trusted"] = json!(true);
            v
        },
        {
            let mut v = manifest();
            v["bindings"]["north_echo"]["category"] = json!("read");
            v
        },
        {
            let mut v = manifest();
            v["server"] = v["servers"][0].clone();
            v
        },
        {
            let mut v = manifest();
            v["version"] = json!(99);
            v
        },
        {
            let mut v = manifest();
            v["servers"] = json!([]);
            v
        },
        {
            let mut v = manifest();
            v["bindings"].as_object_mut().unwrap().remove("south_echo");
            v
        },
    ] {
        assert!(install(tmp.path(), &invalid).is_err(), "accepted {invalid}");
    }
    let raw = r#"{"server":{"id":"one","program":"$OPENWORKER"},"bindings":{"a":{"server":"one","tool":"x"},"a":{"server":"one","tool":"y"}}}"#;
    assert!(serde_json::from_str::<bundle::Tools>(raw).is_err());
    let legacy = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    assert_eq!(Bundle::load(&legacy).unwrap().tools.servers.len(), 1);
}

#[test]
fn catalogs_require_exact_server_routes_and_ignore_trust_hints() {
    let tmp = tempfile::tempdir().unwrap();
    let b = install(tmp.path(), &manifest()).unwrap();
    let mut c = catalogs();
    c.get_mut("north").unwrap()["tools"][3]["description"] = json!("north description");
    c.get_mut("south").unwrap()["tools"][3]["description"] = json!("south description");
    let bound = bundle::bound_tools(&b, &c).unwrap();
    assert_eq!(
        bound.iter().find(|t| t["name"] == "north_echo").unwrap()["description"],
        "north description"
    );
    assert_eq!(
        bound.iter().find(|t| t["name"] == "south_echo").unwrap()["description"],
        "south description"
    );
    c.get_mut("north").unwrap()["tools"]
        .as_array_mut()
        .unwrap()
        .pop();
    assert!(bundle::bound_tools(&b, &c).is_err()); // south cannot satisfy north's route
    let mut c = catalogs();
    let duplicate = c["north"]["tools"][0].clone();
    c.get_mut("north").unwrap()["tools"]
        .as_array_mut()
        .unwrap()
        .push(duplicate);
    assert!(bundle::bound_tools(&b, &c).is_err());
    for (field, value) in [
        (
            "inputSchema",
            json!({"$ref":"https://invalid.example/schema"}),
        ),
        ("execution", json!({"taskSupport":"required"})),
        ("name", json!("bad/name")),
    ] {
        let mut c = catalogs();
        c.get_mut("north").unwrap()["tools"][3][field] = value;
        assert!(bundle::bound_tools(&b, &c).is_err());
    }
    let mut c = catalogs();
    c.get_mut("north").unwrap()["nextCursor"] = json!("more");
    assert!(bundle::bound_tools(&b, &c).is_err());
}

#[test]
fn host_overrides_are_scoped_pinned_and_cannot_remove_hard_checks() {
    let tmp = tempfile::tempdir().unwrap();
    let mut b = install(&tmp.path().join("bundle"), &manifest()).unwrap();
    // Simulate an unfamiliar binary; descriptions/annotations cannot grant trust.
    b.tools.servers.get_mut("south").unwrap().program = "third-party".into();
    let base = ToolPolicy::load(None, &b).unwrap().resolved(&b);
    assert_eq!(base.tools["south_echo"].category, Category::External);
    assert_eq!(base.tools["south_echo"].source, "unknown_external");
    let path = tmp.path().join("operator-policy.json");
    let policy = json!({"version":1,"bundle_digest":b.digest,"overrides":[
        {"server":"north","tool":"write_file","category":"read"},
        {"server":"south","tool":"example_echo","category":"egress"}
    ]});
    std::fs::write(&path, policy.to_string()).unwrap();
    let p = ToolPolicy::load(Some(&path), &b).unwrap();
    let r = p.resolved(&b);
    assert_eq!(r.tools["north_write"].category, Category::Read);
    assert_eq!(r.tools["north_write"].base_category, Category::WriteLocal);
    assert_eq!(r.tools["south_echo"].source, "host_policy");
    assert_eq!(r.tools["north_echo"].source, "native_builtin");
    assert!(permissions::hard_check(
        &b,
        tmp.path().to_str().unwrap(),
        "north_write",
        &json!({"path":"../outside"})
    )
    .is_err());
    assert!(p.check_workspace(tmp.path()).is_err());
    p.verify().unwrap();
    std::fs::write(&path, "{}").unwrap();
    assert!(p.verify().is_err());
    for bad in [
        {
            let mut v = policy.clone();
            v["version"] = json!(2);
            v
        },
        {
            let mut v = policy.clone();
            v["bundle_digest"] = json!("stale");
            v
        },
        {
            let mut v = policy.clone();
            v["overrides"][0]["server"] = json!("*");
            v
        },
        {
            let mut v = policy.clone();
            v["overrides"][0]["category"] = json!("safe");
            v
        },
        {
            let mut v = policy.clone();
            v["overrides"][0]["always_allow"] = json!(true);
            v
        },
        {
            let mut v = policy.clone();
            let x = v["overrides"][0].clone();
            v["overrides"].as_array_mut().unwrap().push(x);
            v
        },
    ] {
        std::fs::write(&path, bad.to_string()).unwrap();
        assert!(ToolPolicy::load(Some(&path), &b).is_err());
    }
    let inside = b.root.join("policy.json");
    std::fs::write(&inside, policy.to_string()).unwrap();
    assert!(ToolPolicy::load(Some(&inside), &b).is_err());
}
