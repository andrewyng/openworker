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

fn submission() -> (
    openworker_runtime::coding_handoff::workflow::Submission,
    Manifest,
) {
    use openworker_runtime::coding_handoff::workflow::{Call, Submission};
    let mut source = fixture();
    source["kind"] = json!("source");
    source["submitted_commit"] = source["base_commit"].clone();
    let source = manifest(source);
    let request = Submission::new(
        source.producer.clone(),
        Call {
            turn_id: "turn_one".into(),
            call_id: "call-one".into(),
        },
        &source,
        "b".repeat(40),
        "Built changes".into(),
    )
    .unwrap();
    let mut published = manifest(fixture());
    published.producer = request.producer.clone();
    (request, published)
}
#[test]
fn model_submission_deduplicates_revision_across_new_call_ids_and_summary_changes() {
    let (request, m) = submission();
    request.require_manifest(&m).unwrap();
    let mut repeat = request.clone();
    repeat.origin.call_id = "new-call-after-loss".into();
    repeat.summary = "Changed wording".into();
    assert!(request.same_revision(&repeat).unwrap());
    repeat.submitted_commit = "d".repeat(40);
    assert!(repeat.validate().is_err()); // ID cannot be relabeled to another commit.
    let mut forged = request.clone();
    forged.producer.team_id = "other_team".into();
    assert!(forged.validate().is_err());
    let mut moving = request;
    moving.submitted_commit = "HEAD".into();
    assert!(moving.validate().is_err());
}
#[test]
fn later_verification_is_bound_to_exact_manifest_and_never_mutates_publication() {
    use openworker_runtime::coding_handoff::workflow::{Call, Verification};
    let (request, m) = submission();
    let before = m.digest().unwrap();
    let mut report=Verification{id:"report_one".into(),submission_id:request.id.clone(),manifest_digest:before.clone(),submitted_commit:m.submitted_commit.clone(),reporter:serde_json::from_value(json!({"team_id":m.producer.team_id,"agent_id":"verifier","session_id":"verify_session"})).unwrap(),origin:Call{turn_id:"verify_turn".into(),call_id:"verify-call".into()},text:"Agent claims tests passed".into()};
    report.require_submission(&request, &m).unwrap();
    assert_eq!(m.digest().unwrap(), before);
    report.reporter.team_id = "another_team".into();
    assert!(report.require_submission(&request, &m).is_err());
    report.reporter.team_id = m.producer.team_id.clone();
    report.manifest_digest = "f".repeat(64);
    assert!(report.require_submission(&request, &m).is_err());
}
#[test]
fn integration_request_requires_exact_submission_and_pinned_expected_head() {
    use openworker_runtime::coding_handoff::workflow::Integration;
    let (request, m) = submission();
    let mut i = Integration {
        submission_id: request.id.clone(),
        manifest_digest: m.digest().unwrap(),
        expected_head: m.base_commit.clone(),
    };
    i.require_submission(&request, &m).unwrap();
    i.expected_head = "main".into();
    assert!(i.require_submission(&request, &m).is_err());
    i.expected_head = m.base_commit.clone();
    i.submission_id = "other_submission".into();
    assert!(i.require_submission(&request, &m).is_err());
}
