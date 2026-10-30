//! Native command/manifest boundaries with isolated fake Google Cloud tools.
#![cfg(unix)]
use serde_json::Value;
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    process::{Command, Output},
    time::Instant,
};
const BIN: &str = env!("CARGO_BIN_EXE_ows");
struct Fixture {
    home: tempfile::TempDir,
}
impl Fixture {
    fn command(&self) -> Command {
        let mut c = Command::new(BIN);
        c.env_clear()
            .env("HOME", self.home.path())
            .env("PATH", self.home.path());
        c
    }
    fn call(&self, args: &[&str]) -> Output {
        self.command().args(args).output().unwrap()
    }
    fn new() -> Self {
        let f = Self {
            home: tempfile::tempdir().unwrap(),
        };
        let g = f.home.path().join("gcloud");
        fs::write(&g, include_str!("fixtures/gcp-preflight.py")).unwrap();
        fs::set_permissions(g, fs::Permissions::from_mode(0o700)).unwrap();
        let plan = f.home.path().join("plan");
        let r = f.call(&[
            "--config",
            "/missing-service-config",
            "deploy",
            "gcp",
            "plan",
            "--project",
            "my-gcp-project",
            "--location",
            "us-central1",
            "--name",
            "test-service",
            "--ttl-hours",
            "1",
            "--out",
            plan.to_str().unwrap(),
        ]);
        assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
        f
    }
    fn export(&self, member: &str, number: &str) -> Output {
        self.call(&[
            "deploy",
            "gcp",
            "bootstrap",
            "--plan",
            self.home.path().join("plan").to_str().unwrap(),
            "--out",
            self.home.path().join("bootstrap").to_str().unwrap(),
            "--project-number",
            number,
            "--operator-member",
            member,
        ])
    }
    fn ready() -> Self {
        let f = Self::new();
        let r = f.export("user:operator@example.com", "123456789012");
        assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
        f
    }
    fn preflight(&self, scenario: &str, extra: &[&str]) -> (Output, Value) {
        let out = self
            .home
            .path()
            .join(format!("report-{}", uuid::Uuid::new_v4().simple()));
        let r = self
            .command()
            .env("TEST_SCENARIO", scenario)
            .args([
                "deploy",
                "gcp",
                "preflight",
                "--plan",
                self.home.path().join("bootstrap").to_str().unwrap(),
                "--out",
                out.to_str().unwrap(),
                "--timeout-seconds",
                "1",
                "--overall-timeout-seconds",
                "3",
            ])
            .args(extra)
            .output()
            .unwrap();
        let v = serde_json::from_slice(&fs::read(out.join("preflight.json")).unwrap()).unwrap();
        (r, v)
    }
}
#[test]
fn export_is_offline_immutable_scoped_and_does_not_claim_deployment_readiness() {
    let f = Fixture::ready();
    assert!(!f.home.path().join("calls.jsonl").exists());
    let root = f.home.path().join("bootstrap");
    let r = f.call(&["deploy", "validate", "--plan", root.to_str().unwrap()]);
    assert!(r.status.success());
    let v: Value =
        serde_json::from_slice(&fs::read(root.join("gcp-identities.json")).unwrap()).unwrap();
    assert_eq!(v["applyReady"], false);
    assert_eq!(v["infrastructurePermissionsReady"], false);
    assert_eq!(v["accounts"].as_object().unwrap().len(), 5);
    for (_, e) in v["accounts"].as_object().unwrap() {
        let e = e.as_str().unwrap();
        assert!(e.ends_with("@my-gcp-project.iam.gserviceaccount.com"));
        assert!(e.split('@').next().unwrap().len() <= 30);
    }
    for b in v["bindings"].as_array().unwrap() {
        assert_ne!(b["role"], "roles/owner");
        assert_ne!(b["role"], "roles/editor");
        if b["role"] == "roles/iam.serviceAccountTokenCreator"
            || b["role"] == "roles/iam.serviceAccountUser"
        {
            assert!(b["resource"]
                .as_str()
                .unwrap()
                .ends_with(".iam.gserviceaccount.com"));
        }
    }
    let script = fs::read_to_string(root.join("gcp-bootstrap.sh")).unwrap();
    assert!(!script.contains("service-accounts keys"));
    assert!(!script.contains("services enable"));
    assert!(!script.contains("projects create"));
    assert!(Command::new("/bin/bash")
        .args(["-n", root.join("gcp-bootstrap.sh").to_str().unwrap()])
        .status()
        .unwrap()
        .success());
    let denied = Command::new("/bin/bash")
        .arg(root.join("gcp-bootstrap.sh"))
        .output()
        .unwrap();
    assert!(!denied.status.success());
    assert!(!f.home.path().join("calls.jsonl").exists());
    assert!(!f
        .export("user:operator@example.com", "123456789012")
        .status
        .success());
    fs::write(root.join("gcp-bootstrap.sh"), "tampered").unwrap();
    assert!(!f
        .call(&["deploy", "validate", "--plan", root.to_str().unwrap()])
        .status
        .success());
}
#[test]
fn invalid_principals_numbers_and_cross_provider_authority_fail_before_output() {
    for (member, number) in [
        ("allUsers", "123456789012"),
        ("group:team@example.com", "123456789012"),
        ("user:a';bad@example.com", "123456789012"),
        ("user:operator@example.com", "000000000001"),
        ("serviceAccount:bad@example.com", "123456789012"),
    ] {
        let f = Fixture::new();
        assert!(!f.export(member, number).status.success());
        assert!(!f.home.path().join("bootstrap").exists());
        assert!(!f.home.path().join("calls.jsonl").exists());
    }
}
#[test]
fn healthy_bootstrap_observations_never_claim_full_apply_or_verified_cost() {
    let f = Fixture::ready();
    let (r, v) = f.preflight("ok", &[]);
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    assert_eq!(v["bootstrapReady"], true);
    assert_eq!(v["applyReady"], false);
    assert_eq!(v["cloudMutations"], 0);
    assert!(v["budget"]["estimate"].is_null());
    assert!(v["budget"]["verifiedActualSpend"].is_null());
    assert_eq!(v["budget"]["availableForEstimateUsd"], 75);
    let calls = fs::read_to_string(f.home.path().join("calls.jsonl")).unwrap();
    assert!(!calls.contains("print-access-token"));
    assert!(!calls.contains("secret-sentinel"));
}
#[test]
fn blocked_identity_policy_api_and_transport_cases_are_scrubbed_and_bounded() {
    for scenario in [
        "wrong-caller",
        "wrong-project",
        "wrong-number",
        "inactive-project",
        "billing-disabled",
        "missing-apis",
        "missing-identity",
        "missing-service-agent",
        "disabled-identity",
        "conditional-trust",
        "excess-trust",
        "broad-role",
        "endpoint-config",
        "access-denied",
        "malformed",
        "large-output",
        "truncated",
        "timeout",
    ] {
        let f = Fixture::ready();
        let before = Instant::now();
        let (r, v) = f.preflight(scenario, &[]);
        assert!(!r.status.success(), "{scenario}");
        assert_eq!(v["bootstrapReady"], false, "{scenario}");
        assert_eq!(v["applyReady"], false);
        let text = serde_json::to_string(&v).unwrap()
            + &String::from_utf8_lossy(&r.stderr)
            + &String::from_utf8_lossy(&r.stdout);
        assert!(!text.contains("secret-sentinel"), "{scenario}");
        assert!(before.elapsed().as_secs() < 6, "{scenario}");
        if [
            "wrong-project",
            "wrong-number",
            "wrong-caller",
            "endpoint-config",
        ]
        .contains(&scenario)
        {
            let calls = fs::read_to_string(f.home.path().join("calls.jsonl")).unwrap();
            assert!(!calls.contains("get-iam-policy"), "{scenario}");
        }
    }
}
#[test]
fn preflight_rejects_deadlines_budget_paths_and_environment_overrides() {
    let f = Fixture::ready();
    let root = f.home.path().join("bootstrap");
    for extra in [
        vec!["--timeout-seconds", "0"],
        vec!["--spent-usd", "90", "--variable-allowance-usd", "25"],
        vec!["--out", root.to_str().unwrap()],
    ] {
        let out = f.home.path().join("not-created");
        let r = f
            .command()
            .args([
                "deploy",
                "gcp",
                "preflight",
                "--plan",
                root.to_str().unwrap(),
                "--out",
                out.to_str().unwrap(),
            ])
            .args(extra)
            .output()
            .unwrap();
        assert!(!r.status.success());
        assert!(!f.home.path().join("calls.jsonl").exists());
    }
    let out = f.home.path().join("overridden");
    let r = f
        .command()
        .env(
            "CLOUDSDK_API_ENDPOINT_OVERRIDES_IAM",
            "https://untrusted.invalid",
        )
        .args([
            "deploy",
            "gcp",
            "preflight",
            "--plan",
            root.to_str().unwrap(),
            "--out",
            out.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(!r.status.success());
    assert!(!f.home.path().join("calls.jsonl").exists());
    let report = fs::read_to_string(out.join("preflight.json")).unwrap();
    assert!(report.contains("endpoint_override"));
    assert_eq!(
        fs::metadata(out).unwrap().permissions().mode() & 0o777,
        0o700
    );
}

#[test]
fn exported_script_qualifies_only_against_fake_cloud_and_stops_on_unknown_mutation() {
    use std::os::unix::fs::symlink;
    for scenario in [
        "ok",
        "wrong-caller",
        "wrong-number",
        "missing-api",
        "existing",
        "missing-service-agent",
        "endpoint",
        "partial-failure",
    ] {
        let f = Fixture::ready();
        let home = f.home.path();
        fs::write(
            home.join("gcloud"),
            include_str!("fixtures/gcp-bootstrap.py"),
        )
        .unwrap();
        symlink(BIN, home.join("ows")).unwrap();
        let run = || {
            Command::new("/bin/bash")
                .env_clear()
                .env("HOME", home)
                .env("PATH", format!("{}:/usr/bin:/bin", home.display()))
                .env("TEST_SCENARIO", scenario)
                .arg(home.join("bootstrap/gcp-bootstrap.sh"))
                .arg("--apply-identities")
                .arg(home.join("journal"))
                .output()
                .unwrap()
        };
        let r = run();
        let path = home.join("mutations.json");
        let mutations: Vec<Value> = if path.exists() {
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap()
        } else {
            vec![]
        };
        if scenario == "ok" {
            assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
            assert_eq!(mutations.len(), 12);
            assert_eq!(
                fs::metadata(home.join("journal/setup.json"))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        } else {
            assert!(!r.status.success(), "{scenario}");
            assert_eq!(
                mutations.len(),
                if scenario == "partial-failure" {
                    3
                } else if scenario == "missing-service-agent" {
                    1
                } else {
                    0
                },
                "{scenario}"
            );
        }
        if scenario == "partial-failure" {
            assert!(!run().status.success());
            let after: Vec<Value> = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
            assert_eq!(
                after.len(),
                3,
                "rerun must refuse to adopt partial identities"
            );
        }
    }
}

#[test]
fn valid_but_expired_or_future_bundles_stop_before_cloud_reads() {
    use sha2::{Digest, Sha256};
    use std::collections::BTreeMap;
    for future in [false, true] {
        let f = Fixture::ready();
        let root = f.home.path().join("bootstrap");
        let p = root.join("deployment-plan.json");
        let mut text = fs::read_to_string(&p).unwrap();
        let v: Value = serde_json::from_str(&text).unwrap();
        let old_created = v["createdAtUnix"].as_u64().unwrap();
        let old_expiry = v["expiresAtUnix"].as_u64().unwrap();
        let created = if future { old_created + 86400 } else { 1 };
        let expiry = created + 3600;
        text = text
            .replace(&old_created.to_string(), &created.to_string())
            .replace(&old_expiry.to_string(), &expiry.to_string());
        fs::write(&p, text).unwrap();
        let review = root.join("REVIEW.md");
        let text = fs::read_to_string(&review)
            .unwrap()
            .replace(&old_created.to_string(), &created.to_string())
            .replace(&old_expiry.to_string(), &expiry.to_string());
        fs::write(review, text).unwrap();
        let mp = root.join("manifest.json");
        let mut m: Value = serde_json::from_slice(&fs::read(&mp).unwrap()).unwrap();
        let mut hashes = BTreeMap::new();
        for name in m["artifacts"].as_object().unwrap().keys() {
            hashes.insert(
                name.clone(),
                format!("{:x}", Sha256::digest(fs::read(root.join(name)).unwrap())),
            );
        }
        m["artifacts"] = serde_json::to_value(&hashes).unwrap();
        m["bundleDigest"] = serde_json::json!(format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&hashes).unwrap())
        ));
        fs::write(mp, serde_json::to_vec_pretty(&m).unwrap()).unwrap();
        let out = f.home.path().join("no-report");
        let r = f.call(&[
            "deploy",
            "gcp",
            "preflight",
            "--plan",
            root.to_str().unwrap(),
            "--out",
            out.to_str().unwrap(),
        ]);
        assert!(!r.status.success());
        assert!(
            String::from_utf8_lossy(&r.stderr).contains("expired or future-dated"),
            "{}",
            String::from_utf8_lossy(&r.stderr)
        );
        assert!(!out.exists());
        assert!(!f.home.path().join("calls.jsonl").exists());
    }
}
#[test]
fn nested_output_is_rejected_and_inherited_access_tokens_are_not_forwarded() {
    let f = Fixture::ready();
    let root = f.home.path().join("bootstrap");
    let out = root.join("forbidden-report");
    let r = f.call(&[
        "deploy",
        "gcp",
        "preflight",
        "--plan",
        root.to_str().unwrap(),
        "--out",
        out.to_str().unwrap(),
    ]);
    assert!(!r.status.success());
    assert!(!out.exists());
    assert!(!f.home.path().join("calls.jsonl").exists());
    let out = f.home.path().join("clean-environment");
    let r = f
        .command()
        .env(
            "CLOUDSDK_AUTH_ACCESS_TOKEN",
            "secret-inherited-access-token",
        )
        .env(
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
            "/untrusted/credentials",
        )
        .args([
            "deploy",
            "gcp",
            "preflight",
            "--plan",
            root.to_str().unwrap(),
            "--out",
            out.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    let calls = fs::read_to_string(f.home.path().join("calls.jsonl")).unwrap();
    assert!(!calls.contains("secret-inherited"));
    for line in calls.lines() {
        let call: Value = serde_json::from_str(line).unwrap();
        assert_eq!(call["tokenPresent"], false);
    }
}

#[test]
fn recovery_preflight_pins_journal_account_uids_and_refuses_replacements() {
    let f = Fixture::ready();
    let home = f.home.path();
    let journal = home.join("creation-journal");
    fs::create_dir(&journal).unwrap();
    let identities: Value =
        serde_json::from_slice(&fs::read(home.join("bootstrap/gcp-identities.json")).unwrap())
            .unwrap();
    for (role, email) in identities["accounts"].as_object().unwrap() {
        fs::write(journal.join(format!("{role}.json")),serde_json::to_vec(&serde_json::json!({"email":email,"projectId":"my-gcp-project","uniqueId":"10000000000"})).unwrap()).unwrap();
    }
    let (r, v) = f.preflight("ok", &["--bootstrap-journal", journal.to_str().unwrap()]);
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    assert!(v["checks"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| r["name"].as_str().unwrap().starts_with("identity_"))
        .all(|r| r["detail"]["journalReceiptMatched"] == true));
    let p = journal.join("executor.json");
    let mut receipt: Value = serde_json::from_slice(&fs::read(&p).unwrap()).unwrap();
    receipt["uniqueId"] = serde_json::json!("99999999999");
    fs::write(&p, serde_json::to_vec(&receipt).unwrap()).unwrap();
    let (r, v) = f.preflight("ok", &["--bootstrap-journal", journal.to_str().unwrap()]);
    assert!(!r.status.success());
    assert!(v["checks"]
        .as_array()
        .unwrap()
        .iter()
        .any(|r| r["name"] == "identity_executor" && r["status"] == "blocked"));
}
