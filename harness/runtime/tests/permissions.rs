use openworker_runtime::{
    bundle::Bundle,
    permissions::{self, Category, Config, Mode},
};
use serde_json::json;
use std::path::Path;

fn bundle() -> Bundle {
    Bundle::load(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant")).unwrap()
}

#[test]
fn unknown_and_spoofed_tools_are_external() {
    let mut b = bundle();
    assert_eq!(permissions::classify(&b, "unknown"), Category::External);
    assert_eq!(permissions::classify(&b, "read_file"), Category::Read);
    b.tools.servers.get_mut("example-tools").unwrap().program = "partner-tools".into();
    assert_eq!(permissions::classify(&b, "read_file"), Category::External);
    assert_eq!(permissions::classify(&b, "write_file"), Category::External);
    b.tools.servers.get_mut("example-tools").unwrap().program = "$OPENWORKER".into();
    b.tools
        .servers
        .get_mut("example-tools")
        .unwrap()
        .args
        .push("--extra-unknown".into());
    assert_eq!(permissions::classify(&b, "read_file"), Category::External);
}

#[test]
fn guards_follow_exact_tool_identity_not_display_alias_or_category() {
    let mut b = bundle();
    let binding = b.tools.bindings.remove("write_file").unwrap();
    b.tools.bindings.insert("looks_like_read".into(), binding);
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_str().unwrap();
    assert_eq!(
        permissions::classify(&b, "looks_like_read"),
        Category::WriteLocal
    );
    // Hard-check's interface accepts neither label nor mode: arbitrary future
    // classification overrides cannot change the result.
    for _label in [
        Category::Read,
        Category::Execute,
        Category::Egress,
        Category::External,
    ] {
        assert!(
            permissions::hard_check(&b, root, "looks_like_read", &json!({"path":"../escape"}))
                .is_err()
        );
        assert!(
            permissions::hard_check(&b, root, "looks_like_read", &json!({"path":"src/file"}))
                .is_ok()
        );
    }
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(tmp.path(), tmp.path().join("link")).unwrap();
        assert!(
            permissions::hard_check(&b, root, "looks_like_read", &json!({"path":"link/file"}))
                .is_err()
        );
    }
}

#[test]
fn permission_configuration_is_strict_and_conservative() {
    let default: Config = serde_json::from_value(json!({})).unwrap();
    assert_eq!(default.mode, Mode::Ask);
    assert!(!default.auto_skip_questions);
    for v in [
        json!({"mode":"auto"}),
        json!({"mode":"typo"}),
        json!({"attended":false}),
        json!({"auto_skip_questions":"true"}),
        json!({"mode":"bypass","allow_everything":true}),
    ] {
        assert!(serde_json::from_value::<Config>(v).is_err());
    }
    assert!(
        serde_json::from_value::<openworker_runtime::compaction::Config>(json!({"attended":false}))
            .is_err()
    );
}
