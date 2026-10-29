//! End-to-end preflight with an isolated fake AWS CLI and no cloud credentials.
#![cfg(unix)]
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    process::{Command, Output},
    time::Instant,
};
const BIN: &str = env!("CARGO_BIN_EXE_ows");
struct Fixture {
    home: tempfile::TempDir,
    plan: PathBuf,
    digest: String,
}
impl Fixture {
    fn new() -> Self {
        Self::with_access(false)
    }
    fn with_access(access: bool) -> Self {
        let home = tempfile::tempdir().unwrap();
        let aws = home.path().join("aws");
        fs::write(&aws, include_str!("fixtures/aws-preflight.py")).unwrap();
        fs::set_permissions(&aws, fs::Permissions::from_mode(0o700)).unwrap();
        let mut input: Value =
            serde_json::from_str(include_str!("../examples/aws-infrastructure.json")).unwrap();
        input["guardCode"]["sha256"] =
            json!(format!("{:x}", Sha256::digest(b"guard-code-fixture")));
        if access {
            input["operatorAccess"] = json!({"mode":"ssm","amiId":"ami-11111111111111111"});
        }
        let config = home.path().join("input.json");
        fs::write(&config, serde_json::to_vec(&input).unwrap()).unwrap();
        let plan = home.path().join("plan");
        let r = Self::command(home.path())
            .args([
                "--config",
                "/missing-service-config",
                "deploy",
                "aws",
                "plan",
                "--account",
                "123456789012",
                "--region",
                "us-west-2",
                "--name",
                "preflight-test",
                "--operator-arn",
                "arn:aws:iam::123456789012:role/operators/operator",
                "--infrastructure-config",
                config.to_str().unwrap(),
                "--out",
                plan.to_str().unwrap(),
            ])
            .output()
            .unwrap();
        assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
        let digest = serde_json::from_slice::<Value>(&r.stdout).unwrap()["bundleDigest"]
            .as_str()
            .unwrap()
            .into();
        Self { home, plan, digest }
    }
    fn command(home: &Path) -> Command {
        let mut c = Command::new(BIN);
        c.env_clear()
            .env("HOME", home)
            .env("PATH", home)
            .env("AWS_EC2_METADATA_DISABLED", "true");
        c
    }
    fn preflight(&self, scenario: &str, extra: &[&str]) -> (Output, PathBuf) {
        let out = self
            .home
            .path()
            .join(format!("report-{}", uuid::Uuid::new_v4().simple()));
        let r = Self::command(self.home.path())
            .args([
                "--config",
                "/missing-service-config",
                "deploy",
                "aws",
                "preflight",
                "--plan",
                self.plan.to_str().unwrap(),
                "--out",
                out.to_str().unwrap(),
            ])
            .args(extra)
            .env("TEST_SCENARIO", scenario)
            .output()
            .unwrap();
        (r, out)
    }
    fn calls(&self) -> Vec<Value> {
        fs::read_to_string(self.home.path().join("calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect()
    }
}
fn report(r: &Output) -> Value {
    serde_json::from_slice(&r.stdout)
        .unwrap_or_else(|_| panic!("{}", String::from_utf8_lossy(&r.stderr)))
}
fn check<'a>(v: &'a Value, name: &str) -> &'a Value {
    v["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["name"] == name)
        .unwrap()
}
fn clean(r: &Output, out: &Path) {
    let text = format!(
        "{}{}{}",
        String::from_utf8_lossy(&r.stdout),
        String::from_utf8_lossy(&r.stderr),
        fs::read_to_string(out.join("preflight.json")).unwrap_or_default()
    );
    for secret in [
        "secret-sentinel-do-not-persist",
        "fake-access-key",
        "fake-secret-key",
        "fake-session-token",
    ] {
        assert!(!text.contains(secret), "leaked secret into output");
    }
    if out.exists() {
        assert_eq!(
            fs::read_dir(out).unwrap().count(),
            1,
            "scratch artifacts remain"
        );
    }
}
#[test]
fn passes_complete_read_only_preflight_and_binds_fresh_report_to_plan() {
    let f = Fixture::new();
    let (r, out) = f.preflight("ok", &["--profile", "customer-login", "--check-setup-role"]);
    assert!(
        r.status.success(),
        "{} {}",
        String::from_utf8_lossy(&r.stdout),
        String::from_utf8_lossy(&r.stderr)
    );
    let v = report(&r);
    assert_eq!(v["checksPassed"], true);
    assert_eq!(v["applyReady"], false);
    assert_eq!(v["mode"], "read_only");
    assert_eq!(v["bundleDigest"], f.digest);
    assert!(v["validUntilUnix"].as_u64().unwrap() <= v["observedAtUnix"].as_u64().unwrap() + 900);
    assert_eq!(
        check(&v, "guard_artifact_bytes")["detail"]["sha256Verified"],
        true
    );
    assert_eq!(
        check(&v, "setup_role_assumption")["detail"]["credentialsPersisted"],
        false
    );
    let cost = &check(&v, "published_cost_estimate")["detail"];
    assert_eq!(cost["components"].as_object().unwrap().len(), 7);
    assert!(cost["fixedCostEstimateUsd"].as_f64().unwrap() > 7.0);
    assert_eq!(cost["hardCap"], false);
    assert_eq!(
        check(&v, "policy_validation")["detail"]["policiesValidated"]
            .as_array()
            .unwrap()
            .len(),
        9
    );
    let calls = f.calls();
    assert!(calls.len() > 35);
    assert_eq!(
        calls.len(),
        v["awsCliInvocations"].as_u64().unwrap() as usize
    );
    for call in &calls {
        assert_eq!(call["maxAttempts"], "1");
        assert_eq!(call["endpointOverridesIgnored"], "true");
        assert_eq!(call["pager"], "");
    }
    let last = calls.last().unwrap();
    assert_eq!(last["temporaryCredentialPresent"], true);
    assert!(!last["args"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "--profile"));
    assert_eq!(
        fs::metadata(&out).unwrap().permissions().mode() & 0o777,
        0o700
    );
    assert_eq!(
        fs::metadata(out.join("preflight.json"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    clean(&r, &out);
    // The plan is immutable and remains valid; observations live in another directory.
    let inspected = Fixture::command(f.home.path())
        .args(["deploy", "inspect", "--plan", f.plan.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(inspected.status.success());
    assert_eq!(report(&inspected)["bundleDigest"], f.digest);
}
#[test]
fn identity_failures_stop_after_the_first_read_and_do_not_leak_provider_data() {
    for scenario in [
        "wrong-account",
        "wrong-operator",
        "access-denied",
        "malformed",
        "large-output",
    ] {
        let f = Fixture::new();
        let (r, out) = f.preflight(scenario, &[]);
        assert!(!r.status.success(), "accepted {scenario}");
        let v = report(&r);
        assert_eq!(v["checksPassed"], false);
        assert_eq!(v["identityMismatchStoppedFurtherCalls"], true);
        assert_eq!(f.calls().len(), 1);
        clean(&r, &out);
    }
}
#[test]
fn missing_permissions_versions_capacity_and_artifact_mismatches_block() {
    for (scenario, name) in [
        ("missing-role", "service_linked_roles"),
        ("extended-support", "eks_standard_support"),
        ("wrong-architecture", "addon_vpc-cni"),
        ("quota-exhausted", "quota_headroom"),
        ("reserved-capacity", "quota_headroom"),
        ("scheduled-capacity", "quota_headroom"),
        ("guard-tampered", "guard_artifact_bytes"),
        ("guard-too-large", "guard_artifact_bytes"),
        ("guard-wrong-region", "guard_artifact_bytes"),
        ("policy-warning", "policy_validation"),
        ("assume-denied", "setup_role_assumption"),
        ("assume-wrong-identity", "setup_role_assumption"),
    ] {
        let f = Fixture::new();
        let extra = if scenario.starts_with("assume-") {
            vec!["--check-setup-role"]
        } else {
            vec![]
        };
        let (r, out) = f.preflight(scenario, &extra);
        assert!(!r.status.success(), "accepted {scenario}");
        let v = report(&r);
        assert_eq!(check(&v, name)["status"], "blocked", "{scenario}");
        assert_eq!(v["applyReady"], false);
        clean(&r, &out);
        if scenario == "guard-too-large" || scenario == "guard-wrong-region" {
            assert!(!f
                .calls()
                .iter()
                .any(|c| c["args"][0] == "s3api" && c["args"][1] == "get-object"));
        }
    }
}
#[test]
fn missing_or_ambiguous_costs_never_become_zero_and_budget_failure_keeps_estimate() {
    for scenario in [
        "pricing-denied",
        "zero-price",
        "nan-price",
        "ambiguous-price",
        "over-budget",
    ] {
        let f = Fixture::new();
        let (r, out) = f.preflight(scenario, &[]);
        assert!(!r.status.success());
        let v = report(&r);
        let c = check(&v, "published_cost_estimate");
        assert_eq!(c["status"], "blocked");
        if scenario == "over-budget" {
            assert!(c["detail"]["planningEstimateUsd"].as_f64().unwrap() > 100.0);
            assert_eq!(c["detail"]["withinAuthorizedBudget"], false);
        } else {
            assert!(c["detail"].get("fixedCostEstimateUsd").is_none());
        }
        clean(&r, &out);
    }
}
#[test]
fn follows_native_pagination_and_rejects_repeated_tokens() {
    for (scenario, expected) in [
        ("paginated-usage", true),
        ("rds-marker", true),
        ("repeated-token", false),
    ] {
        let f = Fixture::new();
        let (r, out) = f.preflight(scenario, &[]);
        assert_eq!(
            r.status.success(),
            expected,
            "{}",
            String::from_utf8_lossy(&r.stdout)
        );
        let v = report(&r);
        if scenario == "paginated-usage" {
            assert_eq!(
                check(&v, "quota_headroom")["detail"]["conservativeOnDemandVCpuUsage"],
                4.0
            );
        }
        if scenario == "repeated-token" {
            assert_eq!(check(&v, "quota_headroom")["status"], "blocked");
        }
        assert!(f.calls().iter().any(|c| c["args"]
            .as_array()
            .unwrap()
            .iter()
            .any(|x| x == "--cli-input-json")));
        clean(&r, &out);
    }
}
#[test]
fn deadlines_and_invalid_local_inputs_prevent_unbounded_or_unreviewed_calls() {
    let f = Fixture::new();
    let start = Instant::now();
    let (r, out) = f.preflight(
        "timeout",
        &["--timeout-seconds", "1", "--overall-timeout-seconds", "1"],
    );
    assert!(!r.status.success());
    assert!(start.elapsed().as_secs() < 5);
    assert_eq!(
        check(&report(&r), "operator_identity")["detail"]["reason"],
        "timeout"
    );
    clean(&r, &out);
    let f = Fixture::new();
    let nested = f.plan.join("report");
    let r = Fixture::command(f.home.path())
        .args([
            "deploy",
            "aws",
            "preflight",
            "--plan",
            f.plan.to_str().unwrap(),
            "--out",
            nested.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(!r.status.success());
    assert!(f.calls().is_empty());
    assert!(!nested.exists());
    let (r, out) = f.preflight("ok", &["--timeout-seconds", "0"]);
    assert!(!r.status.success());
    assert!(!out.exists());
    assert!(f.calls().is_empty());
    fs::write(f.plan.join("guard.cfn.json"), "{}").unwrap();
    let (r, out) = f.preflight("ok", &[]);
    assert!(!r.status.success());
    assert!(!out.exists());
    assert!(f.calls().is_empty());
}

#[test]
fn operator_access_checks_ami_offering_quota_and_includes_all_extra_fixed_costs() {
    let f = Fixture::with_access(true);
    let (r, _) = f.preflight("", &[]);
    let v = report(&r);
    assert!(r.status.success(), "{v}");
    assert_eq!(check(&v, "operator_access")["status"], "passed");
    assert_eq!(
        check(&v, "quota_headroom")["detail"]["requestedStandardVCpus"],
        6
    );
    let cost = &check(&v, "published_cost_estimate")["detail"]["components"];
    assert_eq!(cost["operatorInstance"]["quantity"], 1.0);
    assert_eq!(cost["operatorGp3Storage"]["quantity"], 8.0);
    assert_eq!(
        check(&v, "policy_validation")["detail"]["policiesValidated"]
            .as_array()
            .unwrap()
            .len(),
        11
    );
    for (scenario, name) in [
        ("operator-ami-foreign", "operator_access"),
        ("operator-quota-small", "quota_headroom"),
    ] {
        let (r, _) = f.preflight(scenario, &[]);
        assert!(!r.status.success());
        assert_eq!(check(&report(&r), name)["status"], "blocked");
    }
}
