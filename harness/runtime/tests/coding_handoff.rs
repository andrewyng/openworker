//! Contract tests are not proof of Git integrity, ownership or provider staging.
use openworker_runtime::coding_handoff::{Import, Manifest};
use serde_json::{json, Value};
fn fixture() -> Value {
    json!({"version":1,"kind":"submission","repository":"org/game","producer":{"team_id":"at_one","agent_id":"builder_one","session_id":"s_one"},"object_format":"sha1","base_commit":"a".repeat(40),"submitted_commit":"b".repeat(40),"artifact":{"session_id":"s_one","artifact_id":"art_one","size":4096,"sha256":"c".repeat(64)},"reports":[]})
}
fn manifest(v: Value) -> Manifest {
    serde_json::from_value(v).unwrap()
}
#[test]
fn pinned_manifest_roundtrip_binds_repository_revision_producer_and_artifact() {
    let m = manifest(fixture());
    m.validate().unwrap();
    let restored: Manifest = serde_json::from_slice(&serde_json::to_vec(&m).unwrap()).unwrap();
    assert_eq!(m.digest().unwrap(), restored.digest().unwrap());
    for field in ["repository", "base_commit", "submitted_commit"] {
        let mut v = fixture();
        v[field] = if field == "repository" {
            json!("org/another")
        } else {
            json!("d".repeat(40))
        };
        assert_ne!(m.digest().unwrap(), manifest(v).digest().unwrap());
    }
    let mut v = fixture();
    v["artifact"]["sha256"] = json!("d".repeat(64));
    assert_ne!(m.digest().unwrap(), manifest(v).digest().unwrap());
    m.require_source("org/game", &"a".repeat(40)).unwrap();
    assert!(m.require_source("org/another", &"a".repeat(40)).is_err());
}
#[test]
fn mixed_formats_moving_refs_empty_or_oversized_bundles_and_wrong_producers_reject() {
    for (path, value) in [
        (vec!["base_commit"], json!("HEAD")),
        (vec!["submitted_commit"], json!("0".repeat(40))),
        (vec!["submitted_commit"], json!("b".repeat(64))),
        (vec!["artifact", "size"], json!(0)),
        (vec!["artifact", "size"], json!(64 * 1024 * 1024 + 1)),
        (vec!["artifact", "session_id"], json!("s_other")),
        (vec!["producer", "agent_id"], json!("all")),
        (vec!["repository"], json!("https://example.invalid/repo")),
    ] {
        let mut v = fixture();
        let mut p = &mut v;
        for k in &path[..path.len() - 1] {
            p = &mut p[*k];
        }
        p[path[path.len() - 1]] = value;
        assert!(manifest(v).validate().is_err());
    }
    let mut v = fixture();
    v["object_format"] = json!("sha256");
    v["base_commit"] = json!("a".repeat(64));
    v["submitted_commit"] = json!("b".repeat(64));
    manifest(v).validate().unwrap();
}
#[test]
fn source_snapshot_is_one_commit_and_reports_cannot_claim_another_submission() {
    let mut v = fixture();
    v["kind"] = json!("source");
    assert!(manifest(v.clone()).validate().is_err());
    v["submitted_commit"] = v["base_commit"].clone();
    manifest(v).validate().unwrap();
    let mut v = fixture();
    v["reports"] = json!([{"submitted_commit":"a".repeat(40),"text":"Tests passed"}]);
    assert!(manifest(v.clone()).validate().is_err());
    v["reports"][0]["submitted_commit"] = json!("b".repeat(40));
    let m = manifest(v);
    m.validate().unwrap();
    assert_ne!(m.digest().unwrap(), manifest(fixture()).digest().unwrap());
    let mut bad = fixture();
    bad["verified"] = json!(true);
    assert!(serde_json::from_value::<Manifest>(bad).is_err());
}
#[test]
fn import_identity_refuses_path_escape_and_changed_manifest_without_authorizing_io() {
    let m = manifest(fixture());
    let import = Import {
        operation_id: "import_one".into(),
        scope_digest: "a".repeat(64),
        manifest_digest: m.digest().unwrap(),
        directory: "source".into(),
    };
    import.require_manifest(&m).unwrap();
    let mut changed = m.clone();
    changed.repository = "org/other".into();
    assert!(import.require_manifest(&changed).is_err());
    for path in ["../other", "/host/repo", "C:\\repo", "source/../../escape"] {
        let mut bad = import.clone();
        bad.directory = path.into();
        assert!(bad.validate().is_err());
    }
    let mut v = serde_json::to_value(import).unwrap();
    v["download_url"] = json!("https://example.invalid/source");
    assert!(serde_json::from_value::<Import>(v).is_err());
}
