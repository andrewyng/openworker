//! Release/type scoping must retain qualified v3 provider registration.
use openworker_runtime::bundle::Bundle;
use openworker_tools::{
    Tool, ToolContext, ToolDescriptor, ToolError, ToolFuture, ToolOutput, ToolRegistry,
};
use serde_json::{json, Value};
struct Native(&'static str);
impl Tool for Native {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: "partner.publish".into(),
            description: "Fixture only".into(),
            version: self.0.into(),
            input_schema: json!({"type":"object","properties":{},"additionalProperties":false}),
        }
    }
    fn call(&self, _: ToolContext, _: Value) -> ToolFuture<'_> {
        Box::pin(async {
            Err::<ToolOutput, _>(ToolError::new("fixture", "No execution in selection test"))
        })
    }
}
fn registry(version: &'static str) -> ToolRegistry {
    let mut r = ToolRegistry::default();
    r.register(Native(version)).unwrap();
    r.register_mcp(openworker_tools::McpServer {
        namespace: "coding".into(),
        program: "$OPENWORKER".into(),
        args: vec![
            "mcp-coding".into(),
            "--workspace".into(),
            "$WORKSPACE".into(),
        ],
    })
    .unwrap();
    r
}
#[test]
fn scoped_registered_native_and_mcp_tools_retain_v3_and_exact_names() {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir(root.path().join("coworker")).unwrap();
    std::fs::write(
        root.path().join("coworker/manifest.md"),
        "---\nid: fixture\nname: Fixture\n---\nFixture instructions",
    )
    .unwrap();
    std::fs::write(root.path().join("tools.json"),serde_json::to_vec(&json!({"version":3,"tools":["partner.publish","coding.read_file","coding.write_file"]})).unwrap()).unwrap();
    let b = Bundle::load_with_registry(root.path(), &registry("1")).unwrap();
    assert_eq!(
        b.tools.alias("partner.publish").unwrap(),
        "partner__publish"
    );
    assert!(b.tools.alias("partner.partner.publish").is_err());
    use openworker_runtime::resource_policy::{remote_placements, Placement};
    let placements = remote_placements(&b, false).unwrap();
    assert_eq!(placements["partner"], Placement::Host);
    assert_eq!(placements["coding"], Placement::Sandbox);
    assert!(remote_placements(&b, true).is_err());
    let mut forged = b.clone();
    forged.registry_digest = None;
    assert!(remote_placements(&forged, false).is_err());
    forged = b.clone();
    forged
        .tools
        .bindings
        .get_mut("partner__publish")
        .unwrap()
        .tool = "partner.missing".into();
    assert!(remote_placements(&forged, false).is_err());
    for program in ["$HTTP", "bin/other-mcp"] {
        let mut external = b.clone();
        external.tools.servers.get_mut("partner").unwrap().program = program.into();
        assert!(remote_placements(&external, false).is_err());
    }
    let selected = b
        .tools
        .select(["partner.publish".into(), "coding.read_file".into()].iter())
        .unwrap();
    let wire = b.selection_text(&selected).unwrap();
    let v: Value = serde_json::from_str(&wire).unwrap();
    assert_eq!(v["version"], 3);
    assert_eq!(v["tools"], json!(["coding.read_file", "partner.publish"]));
    std::fs::write(root.path().join("tools.json"), wire).unwrap();
    let subset = Bundle::load_with_registry(root.path(), &registry("1")).unwrap();
    assert_eq!(subset.tools.bindings.len(), 2);
    assert!(subset.tools.alias("coding.write_file").is_err());
    assert!(subset.registry_digest.is_some());
    assert!(Bundle::load(root.path()).is_err());
    let changed = Bundle::load_with_registry(root.path(), &registry("2")).unwrap();
    assert_eq!(subset.digest, changed.digest);
    assert_ne!(subset.registry_digest, changed.registry_digest);
}
#[test]
fn legacy_tool_scoping_remains_v2() {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir(root.path().join("coworker")).unwrap();
    std::fs::write(
        root.path().join("coworker/manifest.md"),
        "---\nid: legacy\nname: Legacy\n---\nFixture",
    )
    .unwrap();
    std::fs::write(root.path().join("tools.json"),r#"{"version":2,"servers":[{"id":"coding","program":"$OPENWORKER","args":["mcp-coding","--workspace","$WORKSPACE"]}],"bindings":{"read_file":{"server":"coding","tool":"read_file"}}}"#).unwrap();
    let b = Bundle::load(root.path()).unwrap();
    let selected = b.tools.select(["coding.read_file".into()].iter()).unwrap();
    let v: Value = serde_json::from_str(&b.selection_text(&selected).unwrap()).unwrap();
    assert_eq!(v["version"], 2);
    assert_eq!(v["bindings"]["read_file"]["tool"], "read_file");
    assert!(b.registry_digest.is_none());
    assert_eq!(
        openworker_runtime::resource_policy::remote_placements(&b, false).unwrap()["coding"],
        openworker_runtime::resource_policy::Placement::Sandbox
    );
}
