use openworker_runtime::{bundle::Bundle, http_mcp, servers};
use openworker_tools::ToolRegistry;
use serde_json::json;
use std::{fs, path::Path};
fn fixture(root: &Path) {
    fs::create_dir_all(root.join("bundle/coworker")).unwrap();
    fs::write(
        root.join("bundle/coworker/manifest.md"),
        "---\nid: test\nname: Test\n---\nUse selected tools.",
    )
    .unwrap();
    fs::write(
        root.join("bundle/tools.json"),
        json!({"version":3,"tools":["acer.status"]}).to_string(),
    )
    .unwrap();
}
#[test]
fn local_endpoints_are_explicit_not_arbitrary_network_access() {
    for url in ["http://127.0.0.1:1234/mcp", "http://[::1]:1234/mcp"] {
        http_mcp::validate_url(url).unwrap();
    }
    for url in [
        "https://example.com/mcp",
        "http://localhost:123/mcp",
        "http://127.0.0.1:123/mcp?token=secret",
        "http://user:secret@127.0.0.1/mcp",
        "http://127.0.0.1/mcp#x",
        "http://0.0.0.0/mcp",
        "http://127.0.0.1:0/mcp",
    ] {
        assert!(http_mcp::validate_url(url).is_err(), "{url}");
    }
}
#[test]
fn app_configuration_resolves_only_selected_names_and_rejects_ambiguity() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    fixture(&root);
    let path = root.join("servers.json");
    let good = json!({"version":1,"servers":[{"namespace":"acer","transport":"streamable_http","url":"http://127.0.0.1:9876/mcp"}]});
    fs::write(&path, good.to_string()).unwrap();
    let mut registry = ToolRegistry::default();
    servers::load(&path, &root.join("bundle"), &mut registry).unwrap();
    let b = Bundle::load_with_registry(&root.join("bundle"), &registry).unwrap();
    assert_eq!(b.tools.bindings["acer__status"].tool, "status");
    assert_eq!(b.tools.servers["acer"].program, "$HTTP");
    assert_eq!(registry.config_sources(), std::slice::from_ref(&path));
    assert!(servers::load(&path, &root.join("bundle"), &mut registry).is_err());
    for bad in [
        json!({"version":1,"servers":[good["servers"][0].clone(),good["servers"][0].clone()]}),
        json!({"version":1,"servers":[{"namespace":"acer","transport":"streamable_http","url":"http://example.com/mcp"}]}),
        json!({"version":1,"servers":[{"namespace":"acer","transport":"streamable_http","url":"http://127.0.0.1/mcp","authorization":"secret"}]}),
        json!({"version":1,"servers":[],"extra":true}),
    ] {
        fs::write(&path, bad.to_string()).unwrap();
        assert!(servers::load(&path, &root.join("bundle"), &mut ToolRegistry::default()).is_err());
    }
    let bundled = root.join("bundle/servers.json");
    fs::write(&bundled, good.to_string()).unwrap();
    assert!(servers::load(&bundled, &root.join("bundle"), &mut ToolRegistry::default()).is_err());
}
