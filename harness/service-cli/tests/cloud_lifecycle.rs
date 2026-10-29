#![cfg(unix)]
use serde_json::{json, Value};
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::Path,
    process::{Command, Output},
};
const BIN: &str = env!("CARGO_BIN_EXE_ows");
struct Fixture {
    home: tempfile::TempDir,
    digest: String,
    id: String,
}
fn parsed(r: &Output) -> Value {
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    serde_json::from_slice(&r.stdout).unwrap()
}
impl Fixture {
    fn handoff(&self, scenario: &str) -> (Output, std::path::PathBuf) {
        let root = self.home.path();
        let plan: Value =
            serde_json::from_slice(&fs::read(root.join("plan/deployment-plan.json")).unwrap())
                .unwrap();
        let prefix = format!("ows-{}-{}", plan["name"].as_str().unwrap(), &self.id[4..16]);
        let registry = format!("123456789012.dkr.ecr.us-west-2.amazonaws.com/{prefix}");
        let chart = root.join("chart");
        if !chart.exists() {
            fs::create_dir(&chart).unwrap();
            fs::write(chart.join("Chart.yaml"), "name: service\nversion: 0.1.0\n").unwrap();
        }
        let values = json!({"image":format!("{registry}/harness@sha256:{}","a".repeat(64)),"config":{"service":{"id":self.id,"legacy_compatibility":false},"database_loopback_plaintext":false,"database_ca":"/run/service/pg-ca.crt","openshell":{"image":format!("{registry}/runner@sha256:{}","b".repeat(64)),"endpoint":"https://gateway.existing-shell.svc.cluster.local:8080"},"artifacts":{"kind":"s3","deployment":self.id,"bucket":format!("{prefix}-123456789012"),"region":"us-west-2"}}});
        fs::write(root.join("service-values.json"), values.to_string()).unwrap();
        let config = json!({"schemaVersion":1,"kubeconfig":"/placeholder","context":"placeholder","helm":"/missing/helm","service":{"namespace":"ow-service","release":"ows","chart":chart,"valuesFile":root.join("service-values.json"),"fixtureDatabase":false},"openshell":{"mode":"existing","namespace":"existing-shell","release":"shell","fullname":"gateway"}});
        fs::write(root.join("install-input.json"), config.to_string()).unwrap();
        let out = root.join(format!("handoff-{}", uuid::Uuid::new_v4().simple()));
        let result = Self::command(root)
            .args(["deploy", "aws", "lifecycle", "installation", "--state"])
            .arg(root.join("state"))
            .args(["--profile", "fixture-setup", "--config"])
            .arg(root.join("install-input.json"))
            .arg("--out")
            .arg(&out)
            .env("SCENARIO", scenario)
            .output()
            .unwrap();
        (result, out)
    }
    fn finish(&self) {
        for _ in 0..3 {
            parsed(&self.apply("", false, true));
        }
    }
    fn destroy(&self, scenario: &str) -> Output {
        Self::command(self.home.path())
            .args(["deploy", "aws", "lifecycle", "destroy", "--state"])
            .arg(self.home.path().join("state"))
            .args([
                "--deployment-id",
                &self.id,
                "--profile",
                "fixture-setup",
                "--allow-experimental-cloud",
            ])
            .env("SCENARIO", scenario)
            .output()
            .unwrap()
    }
    fn new() -> Self {
        let home = tempfile::tempdir().unwrap();
        let root = home.path();
        fs::write(root.join("aws"), include_str!("fixtures/aws-lifecycle.py")).unwrap();
        fs::set_permissions(root.join("aws"), fs::Permissions::from_mode(0o700)).unwrap();
        for tool in ["docker", "tar"] {
            fs::write(
                root.join(tool),
                include_str!("fixtures/cloud-image-tools.py"),
            )
            .unwrap();
            fs::set_permissions(root.join(tool), fs::Permissions::from_mode(0o700)).unwrap();
        }
        let package = parsed(
            &Self::command(root)
                .args(["deploy", "guard", "package", "--out"])
                .arg(root.join("package"))
                .output()
                .unwrap(),
        );
        let mut input: Value =
            serde_json::from_str(include_str!("../examples/aws-infrastructure.json")).unwrap();
        input["guardCode"]["sha256"] = package["archiveSha256"].clone();
        fs::write(root.join("input.json"), input.to_string()).unwrap();
        let p = parsed(
            &Self::command(root)
                .args([
                    "deploy",
                    "aws",
                    "plan",
                    "--account",
                    "123456789012",
                    "--region",
                    "us-west-2",
                    "--name",
                    "lifecycle",
                    "--operator-arn",
                    "arn:aws:iam::123456789012:role/operator",
                    "--infrastructure-config",
                ])
                .arg(root.join("input.json"))
                .arg("--out")
                .arg(root.join("plan"))
                .output()
                .unwrap(),
        );
        let plan: Value =
            serde_json::from_slice(&fs::read(root.join("plan/deployment-plan.json")).unwrap())
                .unwrap();
        let at = plan["createdAtUnix"].as_u64().unwrap();
        let names = [
            "operator_identity",
            "service_linked_roles",
            "availability_zones_and_offering",
            "eks_standard_support",
            "recommended_node_release",
            "addon_vpc-cni",
            "addon_kube-proxy",
            "addon_coredns",
            "addon_eks-pod-identity-agent",
            "addon_aws-ebs-csi-driver",
            "postgres_and_orderability",
            "quota_headroom",
            "published_cost_estimate",
            "guard_artifact_bytes",
            "policy_validation",
            "observation_freshness",
        ];
        fs::write(root.join("preflight.json"),json!({"schemaVersion":1,"deploymentId":p["deploymentId"],"bundleDigest":p["bundleDigest"],"target":plan["target"],"mode":"read_only","checksPassed":true,"observedAtUnix":at,"validUntilUnix":at+900,"checks":names.iter().map(|n|json!({"name":n,"required":true,"status":"passed"})).collect::<Vec<_>>()} ).to_string()).unwrap();
        parsed(
            &Self::command(root)
                .args(["deploy", "aws", "lifecycle", "prepare", "--plan"])
                .arg(root.join("plan"))
                .arg("--out")
                .arg(root.join("state"))
                .output()
                .unwrap(),
        );
        Self {
            home,
            digest: p["bundleDigest"].as_str().unwrap().into(),
            id: p["deploymentId"].as_str().unwrap().into(),
        }
    }
    fn command(root: &Path) -> Command {
        let mut c = Command::new(BIN);
        c.env_clear()
            .env("HOME", root)
            .env("PATH", root)
            .env("TMPDIR", "/tmp")
            .env("KUBE_FIXTURE_FILE", root.join("kubernetes-remote.json"));
        c
    }
    fn apply(&self, scenario: &str, confirm: bool, opt_in: bool) -> Output {
        let mut c = Self::command(self.home.path());
        c.args(["deploy", "aws", "lifecycle", "apply", "--state"])
            .arg(self.home.path().join("state"))
            .args([
                "--accept-plan",
                &self.digest,
                "--profile",
                "fixture-setup",
                "--preflight",
            ])
            .arg(self.home.path().join("preflight.json"))
            .env("SCENARIO", scenario);
        if opt_in {
            c.arg("--allow-experimental-cloud");
        }
        if confirm {
            c.args(["--confirm-no-effect", "guard"]);
        }
        let r = c.output().unwrap();
        let errors =
            fs::read_to_string(self.home.path().join("fake-error.log")).unwrap_or_default();
        assert!(
            errors.is_empty(),
            "fake provider implementation error: {errors}"
        );
        r
    }
    fn calls(&self) -> Vec<Value> {
        fs::read_to_string(self.home.path().join("calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect()
    }
    fn creates(&self) -> usize {
        self.calls()
            .iter()
            .filter(|v| v[1] == "create-stack")
            .count()
    }
    fn status(&self, scenario: &str) -> Output {
        Self::command(self.home.path())
            .args(["deploy", "aws", "lifecycle", "status", "--state"])
            .arg(self.home.path().join("state"))
            .args(["--profile", "fixture-setup"])
            .env("SCENARIO", scenario)
            .output()
            .unwrap()
    }
    fn clean(&self, r: &Output) {
        let text = format!(
            "{}{}{}",
            String::from_utf8_lossy(&r.stdout),
            String::from_utf8_lossy(&r.stderr),
            fs::read_to_string(self.home.path().join("state/cloud-journal.json")).unwrap()
        );
        assert!(!text.contains("secret-sentinel-do-not-persist"));
        assert!(!fs::read_dir(self.home.path().join("state"))
            .unwrap()
            .any(|e| e
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".guard-health-")));
    }
    fn publish(&self, scenario: &str, confirm: bool) -> Output {
        let mut c = Self::command(self.home.path());
        c.args(["deploy", "aws", "lifecycle", "publish-image", "--state"])
            .arg(self.home.path().join("state"))
            .args([
                "--profile",
                "fixture-setup",
                "--role",
                "harness",
                "--source",
                "fixture:service",
                "--allow-experimental-cloud",
            ])
            .env("SCENARIO", scenario);
        if confirm {
            c.arg("--confirm-no-effect");
        }
        c.output().unwrap()
    }
    fn pushes(&self) -> usize {
        fs::read_to_string(self.home.path().join("image-calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|s| serde_json::from_str::<Value>(s).unwrap())
            .filter(|v| v[0] == "docker" && v[1].as_array().unwrap().iter().any(|s| s == "push"))
            .count()
    }
    fn no_publisher_secrets(&self, r: &Output) {
        let j = fs::read_to_string(self.home.path().join("state/cloud-journal.json")).unwrap();
        for secret in [
            "registry-password-do-not-persist",
            "secret-sentinel-do-not-persist",
        ] {
            assert!(!String::from_utf8_lossy(&r.stdout).contains(secret));
            assert!(!String::from_utf8_lossy(&r.stderr).contains(secret));
            assert!(!j.contains(secret));
        }
        assert!(!fs::read_dir(self.home.path()).unwrap().any(|e| e
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".aws-publish-")));
        assert!(!fs::read_dir(self.home.path().join("state"))
            .unwrap()
            .any(|e| e
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".aws-publish-")));
    }
}
#[test]
fn image_push_lost_reply_reconciles_exact_content_without_another_push_or_credential_residue() {
    let f = Fixture::new();
    f.finish();
    let r = f.publish("fail-after-push", false);
    assert!(!r.status.success());
    f.no_publisher_secrets(&r);
    assert_eq!(f.pushes(), 1);
    let r = f.publish("", false);
    assert!(parsed(&r)["image"]
        .as_str()
        .unwrap()
        .contains("/harness@sha256:"));
    f.no_publisher_secrets(&r);
    assert_eq!(f.pushes(), 1);
    assert!(!f.publish("image-changed", false).status.success());
    assert_eq!(f.pushes(), 1);
}
#[test]
fn unknown_absent_image_needs_explicit_confirmation_and_invalid_targets_never_push() {
    let f = Fixture::new();
    f.finish();
    assert!(!f.publish("fail-before-push", false).status.success());
    assert!(!f.publish("", false).status.success());
    assert_eq!(f.pushes(), 1);
    let r = f.publish("", true);
    parsed(&r);
    f.no_publisher_secrets(&r);
    assert_eq!(f.pushes(), 2);
    for scenario in ["mutable-repository", "wrong-image-platform"] {
        let g = Fixture::new();
        g.finish();
        let r = g.publish(scenario, false);
        assert!(!r.status.success());
        g.no_publisher_secrets(&r);
        assert_eq!(g.pushes(), 0);
    }
}
#[test]
fn handoff_binds_original_private_endpoint_ca_repositories_and_teardown_obligation() {
    let f = Fixture::new();
    f.finish();
    let (r, path) = f.handoff("");
    assert_eq!(parsed(&r)["credentialsIncluded"], false);
    let kube: Value =
        serde_json::from_slice(&fs::read(path.join("kubeconfig.json")).unwrap()).unwrap();
    assert_eq!(
        kube["clusters"][0]["cluster"]["server"],
        "https://fixture.us-west-2.eks.amazonaws.com"
    );
    assert!(kube["users"][0]["user"].get("token").is_none());
    let mut wrong = kube;
    wrong["clusters"][0]["cluster"]["server"] = json!("https://wrong.example.test");
    fs::write(path.join("kubeconfig.json"), wrong.to_string()).unwrap();
    let r = Fixture::command(f.home.path())
        .args(["deploy", "cluster", "plan", "--config"])
        .arg(path.join("installer-config.json"))
        .arg("--out")
        .arg(f.home.path().join("install-state"))
        .output()
        .unwrap();
    assert!(!r.status.success());
    assert!(String::from_utf8_lossy(&r.stderr).contains("Kubeconfig must match"));
    assert!(!f.home.path().join("install-state").exists());
    // Merely exporting the read-only handoff cannot strand empty infrastructure.
    parsed(&f.destroy(""));
    let g = Fixture::new();
    g.finish();
    let (r, path) = g.handoff("public-endpoint");
    assert!(!r.status.success());
    assert!(!path.exists());
}
#[test]
fn deletion_reconciles_original_token_after_lost_reply_and_never_replays_unknown_outcome() {
    let f = Fixture::new();
    f.finish();
    let r = f.destroy("fail-after-delete");
    assert!(!r.status.success());
    f.clean(&r);
    assert_eq!(parsed(&f.destroy(""))["phase"], "destroying");
    assert_eq!(
        f.calls().iter().filter(|v| v[1] == "delete-stack").count(),
        1
    );
    let path = f.home.path().join("remote.json");
    let mut r: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    for s in r.as_object_mut().unwrap().values_mut() {
        if s["StackName"].as_str().unwrap().ends_with("-infra") {
            s["StackStatus"] = json!("DELETE_COMPLETE");
        }
    }
    fs::write(path, r.to_string()).unwrap();
    assert_eq!(parsed(&f.destroy(""))["phase"], "infrastructure_deleted");
    let g = Fixture::new();
    g.finish();
    assert!(!g.destroy("fail-before-delete").status.success());
    assert!(!g.destroy("").status.success());
    assert_eq!(
        g.calls().iter().filter(|v| v[1] == "delete-stack").count(),
        1
    );
}
#[test]
fn nonempty_artifacts_block_deletion_and_guard_is_never_removed() {
    let f = Fixture::new();
    f.finish();
    assert!(!f.destroy("nonempty-bucket").status.success());
    assert!(!f.calls().iter().any(|v| v[1] == "delete-stack"));
    parsed(&f.destroy(""));
    assert!(f
        .calls()
        .iter()
        .filter(|v| v[1] == "delete-stack")
        .all(|v| v[2]["StackName"].as_str().unwrap().contains("-infra/")));
}
#[test]
fn prepare_and_missing_opt_in_make_no_provider_calls() {
    let f = Fixture::new();
    assert!(f.calls().is_empty());
    assert!(!f.apply("", false, false).status.success());
    assert!(f.calls().is_empty());
}
#[test]
fn restart_reconciles_lost_create_reply_using_original_events_without_reinstalling() {
    let f = Fixture::new();
    let failed = f.apply("fail-after-create", false, true);
    assert!(!failed.status.success());
    f.clean(&failed);
    assert_eq!(f.creates(), 1);
    parsed(&f.apply("", false, true));
    assert_eq!(f.creates(), 2);
    assert_eq!(
        parsed(&f.apply("", false, true))["phase"],
        "infrastructure_ready"
    );
    assert_eq!(f.creates(), 2);
    let before = fs::read(f.home.path().join("state/cloud-journal.json")).unwrap();
    assert_eq!(parsed(&f.status(""))["readOnly"], true);
    assert_eq!(
        fs::read(f.home.path().join("state/cloud-journal.json")).unwrap(),
        before
    );
}
#[test]
fn unknown_absent_create_is_not_replayed_without_explicit_no_effect_confirmation() {
    let f = Fixture::new();
    assert!(!f.apply("fail-before-create", false, true).status.success());
    assert!(!f.apply("", false, true).status.success());
    assert_eq!(f.creates(), 1);
    parsed(&f.apply("", true, true));
    assert_eq!(f.creates(), 2);
    let j: Value =
        serde_json::from_slice(&fs::read(f.home.path().join("state/cloud-journal.json")).unwrap())
            .unwrap();
    assert_eq!(j["recoveryHistory"].as_array().unwrap().len(), 1);
    assert_eq!(j["deploymentId"], f.id);
}
#[test]
fn foreign_identity_drift_event_ambiguity_and_failed_stack_never_create_infrastructure() {
    for scenario in ["wrong-identity", "access-denied"] {
        let f = Fixture::new();
        let r = f.apply(scenario, false, true);
        assert!(!r.status.success());
        f.clean(&r);
        assert_eq!(f.creates(), 0);
    }
    for scenario in [
        "replaced-stack",
        "template-drift",
        "wrong-event",
        "repeat-events",
        "failed-stack",
    ] {
        let f = Fixture::new();
        parsed(&f.apply("", false, true));
        let r = f.apply(scenario, false, true);
        assert!(!r.status.success(), "{scenario}");
        f.clean(&r);
        assert_eq!(f.creates(), 1);
    }
}
#[test]
fn unhealthy_guard_or_lambda_function_error_blocks_infrastructure_and_removes_payload() {
    for scenario in [
        "wrong-code",
        "guard-disabled",
        "guard-schedule-disabled",
        "lambda-error",
    ] {
        let f = Fixture::new();
        parsed(&f.apply("", false, true));
        let r = f.apply(scenario, false, true);
        assert!(!r.status.success(), "{scenario}");
        f.clean(&r);
        assert_eq!(f.creates(), 1);
    }
}
#[test]
fn stale_wrong_or_incomplete_preflight_and_locked_state_prevent_all_calls() {
    for change in ["stale", "wrong", "incomplete"] {
        let f = Fixture::new();
        let path = f.home.path().join("preflight.json");
        let mut v: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        match change {
            "stale" => v["validUntilUnix"] = json!(1),
            "wrong" => v["bundleDigest"] = json!("wrong"),
            _ => v["checks"] = json!([]),
        }
        fs::write(path, v.to_string()).unwrap();
        assert!(!f.apply("", false, true).status.success());
        assert!(f.calls().is_empty());
    }
    let f = Fixture::new();
    let lock = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .open(f.home.path().join("state/cloud.lock"))
        .unwrap();
    lock.lock().unwrap();
    assert!(!f.apply("", false, true).status.success());
    assert!(f.calls().is_empty());
}

#[test]
fn existing_create_is_reconciled_with_stale_preflight_but_next_creation_is_blocked() {
    let f = Fixture::new();
    assert!(!f.apply("fail-after-create", false, true).status.success());
    fs::remove_file(f.home.path().join("preflight.json")).unwrap();
    let r = f.apply("", false, true);
    assert!(!r.status.success()); // guard was reconciled, next infra create needs a fresh report
    assert_eq!(f.creates(), 1);
    let j: Value =
        serde_json::from_slice(&fs::read(f.home.path().join("state/cloud-journal.json")).unwrap())
            .unwrap();
    assert_eq!(j["operations"]["create:guard"]["status"], "CREATE_COMPLETE");
    assert!(j["operations"].get("create:infra").is_none());
    parsed(&f.status(""));
}
#[test]
fn offline_platform_export_and_lost_cni_reply_reconcile_without_a_second_update() {
    let f = Fixture::new();
    let root = f.home.path();
    let artifact = root.join("platform.json");
    let export = || {
        Fixture::command(root)
            .args(["deploy", "aws", "lifecycle", "platform-artifact", "--plan"])
            .arg(root.join("plan"))
            .arg("--out")
            .arg(&artifact)
            .args([
                "--openshell-namespace",
                "existing-shell",
                "--openshell-release",
                "shell",
            ])
            .output()
            .unwrap()
    };
    let pin = parsed(&export());
    assert_eq!(pin["cloudCalls"], false);
    assert!(f.calls().is_empty());
    assert!(!export().status.success());
    let resources: Value = serde_json::from_slice(&fs::read(&artifact).unwrap()).unwrap();
    let items = resources["items"].as_array().unwrap();
    let controller = items.iter().find(|v| v["kind"] == "Deployment").unwrap();
    assert!(
        controller["spec"]["template"]["spec"]["containers"][0]["image"]
            .as_str()
            .unwrap()
            .contains("@sha256:")
    );
    assert_eq!(items.iter().filter(|v| v["kind"] == "Secret").count(), 0);
    let sc = items.iter().find(|v| v["kind"] == "StorageClass").unwrap();
    assert_eq!(
        sc["parameters"]["tagSpecification_1"],
        format!("ows:deployment-id={}", f.id)
    );
    let hook = items
        .iter()
        .find(|v| v["metadata"]["name"] == "ows-gateway-certgen")
        .unwrap();
    assert_eq!(
        hook["spec"]["podSelector"]["matchLabels"],
        json!({"app.kubernetes.io/name":"openshell-certgen","app.kubernetes.io/instance":"shell","app.kubernetes.io/component":"certgen"})
    );
    assert_eq!(hook["spec"]["ingress"], json!([]));
    assert_eq!(hook["spec"]["egress"][0]["ports"][0]["port"], 443);
    assert!(hook["spec"]["egress"].as_array().unwrap().iter().all(|v| v
        .as_object()
        .unwrap()
        .len()
        > 0));
    let gateway = items
        .iter()
        .find(|v| v["metadata"]["name"] == "ows-trusted-gateway")
        .unwrap();
    assert_eq!(gateway["metadata"]["namespace"], "existing-shell");
    assert_eq!(
        gateway["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/instance"],
        "shell"
    );
    f.finish();
    let (r, handoff) = f.handoff("");
    parsed(&r);
    for program in ["kubectl", "helm"] {
        fs::write(
            root.join(program),
            include_str!("fixtures/kind-installer.py"),
        )
        .unwrap();
        fs::set_permissions(root.join(program), fs::Permissions::from_mode(0o700)).unwrap();
    }
    fs::write(root.join("kubernetes-remote.json"), json!({"bootstrapPlatform":true,"namespaces":{},"releases":{"existing-shell":{"shell":{"version":1,"info":{"status":"deployed"},"uid":"external"}}}}).to_string()).unwrap();
    let file = handoff.join("installer-config.json");
    let mut config: Value = serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
    config["helm"] = json!(root.join("helm"));
    config["platform"] = json!({"file":artifact,"sha256":pin["sha256"]});
    fs::write(&file, config.to_string()).unwrap();
    let native = root.join("native-state");
    let p = parsed(
        &Fixture::command(root)
            .args(["deploy", "cluster", "plan", "--config"])
            .arg(&file)
            .arg("--out")
            .arg(&native)
            .output()
            .unwrap(),
    );
    let apply = |scenario: &str| {
        Fixture::command(root)
            .args(["deploy", "cluster", "platform", "--state"])
            .arg(&native)
            .args([
                "--accept-plan",
                p["planDigest"].as_str().unwrap(),
                "--allow-experimental-cloud",
            ])
            .env("SCENARIO", scenario)
            .output()
            .unwrap()
    };
    assert!(!apply("lost-cni-reply").status.success());
    let r = f.destroy("");
    assert!(!r.status.success());
    assert!(String::from_utf8_lossy(&r.stderr).contains("--installation-state"));
    assert!(!f.calls().iter().any(|v| v[1] == "delete-stack"));
    assert_eq!(parsed(&apply(""))["phase"], "platform_ready");
    assert_eq!(
        f.calls().iter().filter(|v| v[1] == "update-addon").count(),
        1
    );
    let mut remote: Value =
        serde_json::from_slice(&fs::read(root.join("kubernetes-remote.json")).unwrap()).unwrap();
    let mut wrong = sc.clone();
    wrong["parameters"]["tagSpecification_1"] = json!("ows:deployment-id=foreign");
    remote["storageclasses"] = json!([wrong]);
    fs::write(root.join("kubernetes-remote.json"), remote.to_string()).unwrap();
    let r = apply("");
    assert!(!r.status.success());
    assert!(String::from_utf8_lossy(&r.stderr).contains("Cloud default storage"));
    // Platform-only interruption: no service ever ran, so no worker/DB probe is needed.
    parsed(
        &Fixture::command(root)
            .args(["deploy", "cluster", "destroy", "--state"])
            .arg(&native)
            .args(["--deployment-id", p["deploymentId"].as_str().unwrap()])
            .output()
            .unwrap(),
    );
    parsed(
        &Fixture::command(root)
            .args(["deploy", "aws", "lifecycle", "destroy", "--state"])
            .arg(root.join("state"))
            .args([
                "--profile",
                "fixture-setup",
                "--deployment-id",
                &f.id,
                "--allow-experimental-cloud",
                "--installation-state",
            ])
            .arg(&native)
            .output()
            .unwrap(),
    );
    assert_eq!(
        f.calls().iter().filter(|v| v[1] == "delete-stack").count(),
        1
    );
}

#[test]
fn current_aws_cli_absence_wrapper_is_recognized_without_treating_denial_as_absence() {
    let f = Fixture::new();
    parsed(&f.apply("styled-absence", false, true));
    assert_eq!(f.creates(), 1);
    let g = Fixture::new();
    assert!(!g.apply("access-denied", false, true).status.success());
    assert_eq!(g.creates(), 0);
}
