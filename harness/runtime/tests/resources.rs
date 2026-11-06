use openworker_runtime::{
    bundle::Bundle,
    execution::Backend,
    model::Model,
    resource_policy::{read_secret, Placement, Policy, Selection},
    runtime::Runtime,
    sandbox::{policy_and_mounts, Sandbox},
};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
};
struct Fixture {
    tmp: tempfile::TempDir,
    root: PathBuf,
    bundle: Bundle,
    path: PathBuf,
    document: Value,
    workspace: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let bundle =
            Bundle::load(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/multi")).unwrap();
        for p in [
            "projects",
            "projects/work",
            "notes",
            "exports",
            "state",
            "secrets",
        ] {
            fs::create_dir_all(root.join(p)).unwrap();
        }
        fs::write(
            root.join("secrets/token"),
            "SYNTHETIC_NOT_A_REAL_CREDENTIAL",
        )
        .unwrap();
        let document = json!({"version":1,"bundle_digest":bundle.digest,"profiles":{"daily":{
            "sandbox":"openshell","workspace_roots":[root.join("projects")],
            "servers":{"north":{"placement":"sandbox"},"south":{"placement":"sandbox"}},
            "files":{"notes":{"path":root.join("notes"),"access":"read"},"exports":{"path":root.join("exports"),"access":"read_write"}},
            "network":{"api":{"host":"api.example.com","port":443}},
            "credentials":{"token":{"version":"v1","source_file":root.join("secrets/token"),"delivery":"sandbox_file","target":"/run/openworker/credentials/token","requires_network":["api"]}}
        }}});
        let path = root.join("policy.json");
        let workspace = root.join("projects/work");
        let s = Self {
            tmp,
            root,
            bundle,
            path,
            document,
            workspace,
        };
        s.write();
        s
    }
    fn write(&self) {
        fs::write(&self.path, serde_json::to_vec(&self.document).unwrap()).unwrap();
    }
    fn policy(&self) -> Policy {
        Policy::load(Some(&self.path), &self.bundle).unwrap()
    }
    fn resolve(
        &self,
        p: &Policy,
        selection: Selection,
    ) -> anyhow::Result<openworker_runtime::resource_policy::Resolved> {
        p.resolve(
            &self.bundle,
            Backend::Openshell,
            &self.workspace,
            Some(selection),
            &[self.root.join("state"), self.bundle.root.clone()],
        )
    }
}
fn selected() -> Selection {
    Selection {
        profile: "daily".into(),
        files: vec!["notes".into(), "exports".into()],
        network: vec!["api".into()],
        credentials: vec!["token".into()],
    }
}

#[test]
fn explicit_subset_default_deny_and_secret_free_preview() {
    let f = Fixture::new();
    let p = f.policy();
    let s = f.resolve(&p, selected()).unwrap();
    assert_eq!(s.digest, f.resolve(&p, selected()).unwrap().digest);
    let mut reordered = selected();
    reordered.files.reverse();
    assert_eq!(s.digest, f.resolve(&p, reordered).unwrap().digest);
    assert!(!serde_json::to_string(&s).unwrap().contains("SYNTHETIC"));
    assert!(!s.context().to_string().contains("source_file"));
    let empty = f
        .resolve(
            &p,
            Selection {
                profile: "daily".into(),
                ..Default::default()
            },
        )
        .unwrap();
    assert!(empty.files.is_empty() && empty.network.is_empty() && empty.credentials.is_empty());
    assert!(p
        .resolve(&f.bundle, Backend::Openshell, &f.workspace, None, &[])
        .is_err());
    assert!(p
        .resolve(&f.bundle, Backend::Off, &f.workspace, Some(selected()), &[])
        .is_err());
    assert_eq!(
        read_secret(&s.credentials["token"]).unwrap(),
        b"SYNTHETIC_NOT_A_REAL_CREDENTIAL"
    );
}
#[test]
fn unknown_duplicate_missing_dependency_and_wrong_workspace_fail() {
    let f = Fixture::new();
    let p = f.policy();
    let mut s = selected();
    s.network.clear();
    assert!(f.resolve(&p, s).is_err());
    let mut s = selected();
    s.files.push("notes".into());
    assert!(f.resolve(&p, s).is_err());
    let mut s = selected();
    s.credentials.push("not-configured".into());
    assert!(f.resolve(&p, s).is_err());
    let mut s = selected();
    s.profile = "unknown".into();
    assert!(f.resolve(&p, s).is_err());
    assert!(p
        .resolve(
            &f.bundle,
            Backend::Openshell,
            &f.root.join("notes"),
            Some(selected()),
            &[]
        )
        .is_err());
}
#[test]
fn policy_and_credential_mutation_change_identity_without_secret_hashes() {
    let mut f = Fixture::new();
    let p = f.policy();
    let s = f.resolve(&p, selected()).unwrap();
    fs::write(f.root.join("secrets/token"), "NEW_SYNTHETIC_TOKEN").unwrap();
    assert!(read_secret(&s.credentials["token"]).is_err());
    assert_ne!(s.digest, f.resolve(&p, selected()).unwrap().digest);
    f.document["profiles"]["daily"]["servers"]["north"]["placement"] = json!("host");
    f.write();
    assert!(p.verify().is_err());
    let p = f.policy();
    let host = f.resolve(&p, selected()).unwrap();
    assert_eq!(host.servers["north"], Placement::Host);
    assert!(host.warnings.iter().any(|s| s.contains("host-account")));
}
#[test]
fn paths_symlinks_protected_sources_and_mount_overlap_fail() {
    use std::os::unix::fs::symlink;
    let mut f = Fixture::new();
    symlink(f.root.join("notes"), f.root.join("linked")).unwrap();
    for bad in [
        f.root.join("linked"),
        f.root.join("secrets"),
        f.root.join("state"),
        f.workspace.clone(),
        PathBuf::from("/etc"),
    ] {
        f.document["profiles"]["daily"]["files"]["notes"]["path"] = json!(bad);
        f.write();
        assert!(f.resolve(&f.policy(), selected()).is_err());
    }
    fs::remove_file(f.root.join("secrets/token")).unwrap();
    symlink(f.root.join("policy.json"), f.root.join("secrets/token")).unwrap();
    assert!(f.resolve(&f.policy(), selected()).is_err());
}
#[test]
fn missing_source_blocks_selection_not_recovery_startup() {
    let f = Fixture::new();
    fs::remove_file(f.root.join("secrets/token")).unwrap();
    let policy = f.policy();
    assert!(f.resolve(&policy, selected()).is_err());
    assert!(f
        .resolve(
            &policy,
            Selection {
                profile: "daily".into(),
                ..Default::default()
            }
        )
        .is_ok());
}
#[test]
fn parser_rejects_ambiguous_or_unsupported_authority() {
    let mut f = Fixture::new();
    let original = f.document.clone();
    for bad in [
        "*",
        "https://api.example.com",
        "api.example.com:443",
        "a..com",
        "API.example.com",
        "127.0.0.1/8",
        "[::1]",
    ] {
        f.document = original.clone();
        f.document["profiles"]["daily"]["network"]["api"]["host"] = json!(bad);
        f.write();
        assert!(Policy::load(Some(&f.path), &f.bundle).is_err());
    }
    f.document = original.clone();
    f.document["profiles"]["daily"]["network"]["api"]["port"] = json!(0);
    f.write();
    assert!(Policy::load(Some(&f.path), &f.bundle).is_err());
    f.document = original.clone();
    f.document["profiles"]["daily"]["credentials"]["token"]["delivery"] = json!("host_env");
    f.write();
    assert!(Policy::load(Some(&f.path), &f.bundle).is_err());
    f.document = original.clone();
    f.document["profiles"]["daily"]["sandbox"] = json!("off");
    f.write();
    assert!(Policy::load(Some(&f.path), &f.bundle).is_err());
    let raw = serde_json::to_string(&original)
        .unwrap()
        .replace("\"version\":1", "\"version\":1,\"version\":1");
    fs::write(&f.path, raw).unwrap();
    assert!(Policy::load(Some(&f.path), &f.bundle).is_err());
    let raw = serde_json::to_string(&original).unwrap().replace(
        "\"network\":{",
        "\"network\":{\"api\":{\"host\":\"evil.example.com\",\"port\":443},",
    );
    fs::write(&f.path, raw).unwrap();
    assert!(Policy::load(Some(&f.path), &f.bundle).is_err());
}
#[test]
fn materialization_is_readonly_private_separate_from_runner_and_cleanable() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fixture::new();
    let s = f.resolve(&f.policy(), selected()).unwrap();
    let sandbox = Sandbox {
        root: f.root.join("state"),
        runner: f.root.join("runner"),
    };
    let dir = sandbox
        .prepare_credentials("owr-synthetic", &s)
        .unwrap()
        .unwrap();
    assert_eq!(
        fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
        0o700
    );
    assert_eq!(
        fs::metadata(dir.join("token"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o400
    );
    assert!(!dir.starts_with(sandbox.root.join("sandbox-files")));
    let (policy, mounts) = policy_and_mounts(
        f.workspace.to_str().unwrap(),
        &f.bundle,
        &f.root.join("runner-files"),
        &s,
        Some(&dir),
    );
    assert_eq!(
        policy["network_policies"]["api"]["endpoints"][0]["host"],
        "api.example.com"
    );
    assert!(mounts["docker"]["mounts"]
        .as_array()
        .unwrap()
        .iter()
        .any(|m| m["target"] == "/run/openworker/credentials" && m["read_only"] == true));
    assert!(!policy.to_string().contains("SYNTHETIC"));
    assert!(sandbox.prepare_credentials("owr-synthetic", &s).is_err());
    sandbox.cleanup_credentials("owr-synthetic").unwrap();
    assert!(!dir.exists());
    let runner_dir = sandbox.root.join("sandbox-files/owr-synthetic");
    fs::create_dir_all(&runner_dir).unwrap();
    fs::write(runner_dir.join("openworker"), "synthetic runner copy").unwrap();
    sandbox.cleanup_runner("owr-synthetic").unwrap();
    assert!(!runner_dir.exists());
    sandbox.cleanup_credentials("owr-synthetic").unwrap();
    assert!(sandbox.cleanup_credentials("../../state").is_err());
    assert_eq!(
        fs::read(f.root.join("secrets/token")).unwrap(),
        b"SYNTHETIC_NOT_A_REAL_CREDENTIAL"
    );
}
#[test]
fn replacing_a_granted_root_invalidates_its_resolved_identity() {
    let f = Fixture::new();
    let p = f.policy();
    let s = f.resolve(&p, selected()).unwrap();
    fs::rename(f.root.join("notes"), f.root.join("old-notes")).unwrap();
    fs::create_dir(f.root.join("notes")).unwrap();
    assert!(s.verify_paths().is_err());
    assert_ne!(s.digest, f.resolve(&p, selected()).unwrap().digest);
}
#[tokio::test]
async fn protocol_preview_pins_creation_and_blocks_changed_credentials() {
    let f = Fixture::new();
    let runner = f.root.join("runner");
    fs::write(
        &runner,
        if cfg!(target_os = "macos") {
            b"\xcf\xfa\xed\xfe-test-no-execution".as_slice()
        } else {
            b"\x7fELF-test-no-execution".as_slice()
        },
    )
    .unwrap();
    let rt = Runtime::open_with_policies(
        &f.root.join("state"),
        &f.bundle.root,
        &runner,
        Model::new("fixture".into(), true),
        Backend::Openshell,
        None,
        Some(&f.path),
    )
    .unwrap();
    let mut request = json!({"coworker":f.bundle.id,"workspace":f.workspace,"policy":selected()});
    let resolved = rt
        .dispatch("policy/resolve", request.clone())
        .await
        .unwrap();
    assert!(!f.root.join("state/credential-files").exists());
    request["name"] = json!("Policy test");
    assert!(rt
        .dispatch("session/create", request.clone())
        .await
        .is_err());
    request["expected_policy_digest"] = resolved["policy"]["digest"].clone();
    let created = rt
        .dispatch("session/create", request.clone())
        .await
        .unwrap();
    let sid = &created["session"]["id"];
    let snapshot = rt
        .dispatch("session/attach", json!({"session_id":sid}))
        .await
        .unwrap();
    assert_eq!(
        snapshot["session"]["resource_policy"]["digest"],
        resolved["policy"]["digest"]
    );
    fs::write(f.root.join("secrets/token"), "rotated").unwrap();
    assert!(rt.dispatch("session/create", request).await.is_err());
    assert!(rt
        .dispatch(
            "turn/start",
            json!({"session_id":sid,"submission_id":"new","text":"Do nothing"})
        )
        .await
        .is_err());
    // Inspection is still available; changed authority cannot run work.
    assert!(rt
        .dispatch("session/attach", json!({"session_id":sid}))
        .await
        .is_ok());
    drop(rt);
    drop(f.tmp);
}
