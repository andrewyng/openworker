//! Real CLI boundaries against fake Azure only; no account, token or network.
#![cfg(unix)]
use serde_json::Value;
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::PathBuf,
    process::{Command, Output},
    time::Instant,
};
const BIN: &str = env!("CARGO_BIN_EXE_ows");
struct Fixture {
    root: tempfile::TempDir,
}
impl Fixture {
    fn command(&self) -> Command {
        let mut c = Command::new(BIN);
        c.env_clear()
            .env("HOME", self.root.path())
            .env("PATH", self.root.path())
            .env("OW_AZ_TEST_BUNDLE", self.path("bootstrap"));
        c
    }
    fn call(&self, args: &[&str]) -> Output {
        self.command().args(args).output().unwrap()
    }
    fn path(&self, n: &str) -> PathBuf {
        self.root.path().join(n)
    }
    fn new() -> Self {
        let f = Self {
            root: tempfile::tempdir().unwrap(),
        };
        fs::write(f.path("az"), include_str!("fixtures/azure-bootstrap.py")).unwrap();
        fs::set_permissions(f.path("az"), fs::Permissions::from_mode(0o700)).unwrap();
        let r = f.call(&[
            "--config",
            "/nonexistent/service",
            "deploy",
            "azure",
            "plan",
            "--tenant",
            "11111111-2222-4333-8444-555555555555",
            "--subscription",
            "66666666-7777-4888-8999-aaaaaaaaaaaa",
            "--location",
            "westus2",
            "--name",
            "azure-test",
            "--ttl-hours",
            "1",
            "--out",
            f.path("plan").to_str().unwrap(),
        ]);
        assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
        f
    }
    fn export(&self, object: &str, group: &str) -> Output {
        self.call(&[
            "deploy",
            "azure",
            "bootstrap",
            "--plan",
            self.path("plan").to_str().unwrap(),
            "--out",
            self.path("bootstrap").to_str().unwrap(),
            "--resource-group",
            group,
            "--setup-object-id",
            object,
        ])
    }
    fn ready() -> Self {
        let f = Self::new();
        let r = f.export("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff", "customer-ow-test");
        assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
        let receipt: Value = serde_json::from_slice(&r.stdout).unwrap();
        assert_eq!(receipt["identityBootstrap"], "exported_azure_identity_only");
        assert_eq!(receipt["applyReady"], false);
        f
    }
    fn preflight(&self, scenario: &str, out: &str) -> Output {
        self.command()
            .env("OW_AZ_TEST_SCENARIO", scenario)
            .args([
                "deploy",
                "azure",
                "preflight",
                "--plan",
                self.path("bootstrap").to_str().unwrap(),
                "--out",
                self.path(out).to_str().unwrap(),
                "--timeout-seconds",
                "1",
                "--overall-timeout-seconds",
                "20",
            ])
            .output()
            .unwrap()
    }
    fn log(&self) -> Vec<Vec<String>> {
        fs::read_to_string(self.path("az.log"))
            .unwrap_or_default()
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect()
    }
    fn report(&self, n: &str) -> Value {
        serde_json::from_slice(&fs::read(self.path(n).join("preflight.json")).unwrap()).unwrap()
    }
    fn script(&self, scenario: &str, journal: &str) -> Output {
        let ows = self.path("ows");
        if !ows.exists() {
            std::os::unix::fs::symlink(BIN, &ows).unwrap();
        }
        Command::new("/bin/bash")
            .env_clear()
            .env("HOME", self.root.path())
            .env(
                "PATH",
                format!("{}:/usr/bin:/bin", self.root.path().display()),
            )
            .env("OW_AZ_TEST_BUNDLE", self.path("bootstrap"))
            .env("OW_AZ_TEST_SCENARIO", scenario)
            .arg(self.path("bootstrap/azure-bootstrap.sh"))
            .arg("--apply-identities")
            .arg(self.path(journal))
            .output()
            .unwrap()
    }
}
#[test]
fn export_is_offline_deterministic_scoped_and_integrity_checked() {
    let f = Fixture::ready();
    assert!(f.log().is_empty());
    let role: Value =
        serde_json::from_slice(&fs::read(f.path("bootstrap/azure-setup-role.json")).unwrap())
            .unwrap();
    let p = &role["properties"];
    assert_eq!(p["assignableScopes"].as_array().unwrap().len(), 1);
    assert_eq!(p["permissions"][0]["dataActions"], serde_json::json!([]));
    let actions = p["permissions"][0]["actions"].to_string();
    assert!(!actions.contains('*'));
    assert!(!actions.contains("roleAssignments/write"));
    assert!(!actions.contains("managedClusters/write"));
    let validate = f.call(&[
        "deploy",
        "validate",
        "--plan",
        f.path("bootstrap").to_str().unwrap(),
    ]);
    assert!(validate.status.success());
    assert!(f.log().is_empty());
    assert!(
        fs::metadata(f.path("bootstrap"))
            .unwrap()
            .permissions()
            .mode()
            & 0o077
            == 0
    );
    assert!(
        fs::metadata(f.path("bootstrap/azure-bootstrap.sh"))
            .unwrap()
            .permissions()
            .mode()
            & 0o077
            == 0
    );
    fs::write(f.path("bootstrap/azure-setup-assignment.json"), b"{}").unwrap();
    assert!(!f
        .call(&[
            "deploy",
            "validate",
            "--plan",
            f.path("bootstrap").to_str().unwrap()
        ])
        .status
        .success());
    assert!(f.log().is_empty());
}
#[test]
fn invalid_identity_or_scope_and_duplicate_output_create_no_new_bundle() {
    for (object, group) in [
        ("*", "customer-ow-test"),
        ("00000000-0000-0000-0000-000000000000", "customer-ow-test"),
        ("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff", "group;echo"),
        ("BBBBBBBB-CCCC-4DDD-8EEE-FFFFFFFFFFFF", "customer-ow-test"),
    ] {
        let f = Fixture::new();
        assert!(!f.export(object, group).status.success());
        assert!(!f.path("bootstrap").exists());
        assert!(f.log().is_empty());
    }
    let f = Fixture::ready();
    let before = fs::read(f.path("bootstrap/manifest.json")).unwrap();
    assert!(!f
        .export("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff", "customer-ow-test")
        .status
        .success());
    assert_eq!(before, fs::read(f.path("bootstrap/manifest.json")).unwrap());
}
#[test]
fn read_only_preflight_verifies_exact_grant_without_claiming_deployment_readiness() {
    let f = Fixture::ready();
    let r = f.preflight("ready", "ready-report");
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    let v = f.report("ready-report");
    assert_eq!(v["bootstrapReady"], true);
    assert_eq!(v["applyReady"], false);
    assert_eq!(v["mutations"], 0);
    assert_eq!(v["qualification"]["effectivePermissionsVerified"], false);
    assert_eq!(v["cliCalls"], 13);
    assert!(f
        .log()
        .iter()
        .all(|a| a[0] != "rest" || a.windows(2).any(|w| w == ["--method", "get"])));
    assert!(!fs::read_to_string(f.path("ready-report/preflight.json"))
        .unwrap()
        .contains("sensitive"));
    assert!(!f.preflight("ready", "ready-report").status.success());
    assert_eq!(f.log().len(), 13);
}
#[test]
fn wrong_cloud_scope_principal_or_role_fails_closed_and_errors_are_redacted() {
    for scenario in [
        "custom-endpoint",
        "wrong-tenant",
        "wrong-group",
        "broad-role",
        "wrong-principal",
        "conditional-assignment",
        "missing-role",
        "invalid-json",
        "too-large",
    ] {
        let f = Fixture::ready();
        let r = f.preflight(scenario, "blocked");
        assert!(!r.status.success(), "{scenario}");
        let report = f.report("blocked");
        assert_eq!(report["bootstrapReady"], false, "{scenario}");
        let report = report.to_string();
        assert!(!report.contains("canary"), "{scenario}");
        assert!(!String::from_utf8_lossy(&r.stderr).contains("canary"));
        assert!(f
            .log()
            .iter()
            .all(|a| a[0] != "rest" || a.contains(&"get".to_string())));
    }
}
#[test]
fn provider_registration_is_observed_without_enabling_it_or_widening_bootstrap_guarantee() {
    let f = Fixture::ready();
    let r = f.preflight("missing-provider", "providers");
    assert!(r.status.success());
    let v = f.report("providers");
    assert_eq!(v["bootstrapReady"], true);
    assert_eq!(v["applyReady"], false);
    assert!(v["checks"]
        .as_array()
        .unwrap()
        .iter()
        .any(|c| c["status"] == "blocked" && c["required"] == false));
    assert!(f.log().iter().all(|a| !a.contains(&"register".to_string())));
}
#[test]
fn deadlines_and_endpoint_overrides_are_bounded() {
    let f = Fixture::ready();
    let now = Instant::now();
    let r = f.preflight("timeout", "timeout");
    assert!(!r.status.success());
    assert!(now.elapsed().as_secs() < 5);
    let f = Fixture::ready();
    let r = f
        .command()
        .env("AZURE_CORE_DISABLE_CONNECTION_VERIFICATION", "true")
        .args([
            "deploy",
            "azure",
            "preflight",
            "--plan",
            f.path("bootstrap").to_str().unwrap(),
            "--out",
            f.path("override").to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(!r.status.success());
    assert!(f.log().is_empty());
    assert_eq!(
        f.report("override")["checks"][0]["code"],
        "untrusted_cli_configuration"
    );
}
#[test]
fn exported_script_requires_explicit_confirmation_and_refuses_existing_or_wrong_scope() {
    let f = Fixture::ready();
    let r = Command::new("/bin/bash")
        .arg(f.path("bootstrap/azure-bootstrap.sh"))
        .output()
        .unwrap();
    assert!(!r.status.success());
    assert!(f.log().is_empty());
    let nested = f.script("ready", "bootstrap/nested-journal");
    assert!(!nested.status.success());
    assert!(!f.path("bootstrap/nested-journal").exists());
    assert!(f.log().iter().all(|a| !a.contains(&"put".to_string())));
    for scenario in ["existing-role", "wrong-tenant", "wrong-group"] {
        let f = Fixture::ready();
        let r = f.script(scenario, "journal");
        assert!(!r.status.success(), "{scenario}");
        assert!(f.log().iter().all(|a| !a.contains(&"put".to_string())));
        assert!(!f.path("journal").exists());
    }
}
#[test]
fn exported_script_writes_fixed_scope_receipts_and_never_retries_unknown_mutations() {
    let f = Fixture::ready();
    let r = f.script("ready", "private journal");
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    assert!(f.path("private journal/setup-role.json").exists());
    assert!(f.path("private journal/setup-assignment.json").exists());
    assert_eq!(
        f.log()
            .iter()
            .filter(|a| a.contains(&"put".to_string()))
            .count(),
        2
    );
    let f = Fixture::ready();
    let r = f.script("assignment-fails", "journal");
    assert!(!r.status.success());
    assert_eq!(
        f.log()
            .iter()
            .filter(|a| a.contains(&"put".to_string()))
            .count(),
        2
    );
    assert!(f.path("journal/setup-role.json").exists());
}

fn infrastructure_config() -> Value {
    serde_json::json!({
      "kubernetesVersion":"1.33.4", "nodeVmSize":"Standard_D4s_v5",
      "sshPublicKey":"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
      "adminGroupObjectIds":["11111111-1111-4111-8111-111111111111"],
      "controlResourceGroup":"customer-ow-control",
      "guardImage":format!("ghcr.io/example/ows@sha256:{}", "a".repeat(64)),
      "postgresPassword":{"keyVaultId":"/subscriptions/66666666-7777-4888-8999-aaaaaaaaaaaa/resourceGroups/customer-secrets/providers/Microsoft.KeyVault/vaults/customer-vault","secretName":"postgres-password","secretVersion":"a".repeat(32)}
    })
}
fn infrastructure(f: &Fixture, config: &Value, out: &str) -> Output {
    fs::write(f.path("inputs.json"), serde_json::to_vec(config).unwrap()).unwrap();
    f.call(&[
        "deploy",
        "azure",
        "infrastructure",
        "--plan",
        f.path("bootstrap").to_str().unwrap(),
        "--config",
        f.path("inputs.json").to_str().unwrap(),
        "--out",
        f.path(out).to_str().unwrap(),
    ])
}
#[test]
fn infrastructure_is_offline_private_scoped_and_manifest_checked() {
    let f = Fixture::ready();
    let r = infrastructure(&f, &infrastructure_config(), "graph");
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    assert!(f.log().is_empty());
    let get = |name: &str| -> Value {
        serde_json::from_slice(&fs::read(f.path("graph").join(name)).unwrap()).unwrap()
    };
    let inventory = get("azure-inventory.json");
    assert_eq!(inventory["resourceGroupOwned"], false);
    assert_eq!(inventory["resources"].as_array().unwrap().len(), 11);
    assert_ne!(
        inventory["controlScope"].as_str().unwrap(),
        inventory["resources"][0]["id"]
            .as_str()
            .unwrap()
            .split("/providers/")
            .next()
            .unwrap()
    );
    let foundation = get("azure-foundation.arm.json");
    assert_eq!(
        foundation["parameters"]["postgresPassword"]["type"],
        "secureString"
    );
    let storage = foundation["resources"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["type"] == "Microsoft.Storage/storageAccounts")
        .unwrap();
    assert_eq!(storage["properties"]["allowSharedKeyAccess"], false);
    assert_eq!(storage["properties"]["publicNetworkAccess"], "Disabled");
    let cluster = get("azure-cluster.arm.json");
    let aks = &cluster["resources"][0];
    assert_eq!(aks["properties"]["agentPoolProfiles"][0]["count"], 2);
    assert_eq!(
        aks["properties"]["apiServerAccessProfile"]["enablePrivateCluster"],
        true
    );
    assert_eq!(aks["properties"]["disableLocalAccounts"], true);
    assert_eq!(
        aks["properties"]["securityProfile"]["workloadIdentity"]["enabled"],
        true
    );
    let role = get("azure-executor-role.json").to_string();
    assert!(!role.contains('*'));
    assert!(!role.contains("roleAssignments/write"));
    assert!(!role.contains("listKeys"));
    let fic = get("azure-federation.arm.json");
    assert_eq!(
        fic["resources"][0]["properties"]["subject"],
        "system:serviceaccount:ow-service:ows-artifacts"
    );
    assert!(f
        .call(&[
            "deploy",
            "validate",
            "--plan",
            f.path("graph").to_str().unwrap()
        ])
        .status
        .success());
    fs::write(f.path("graph/azure-cluster.arm.json"), b"{}").unwrap();
    assert!(!f
        .call(&[
            "deploy",
            "validate",
            "--plan",
            f.path("graph").to_str().unwrap()
        ])
        .status
        .success());
}
#[test]
fn infrastructure_rejects_unsafe_or_ambiguous_inputs_without_cloud_calls() {
    let f = Fixture::ready();
    for (field, value) in [
        (
            "controlResourceGroup",
            serde_json::json!("customer-ow-test"),
        ),
        (
            "guardImage",
            serde_json::json!("ghcr.io/example/ows:latest"),
        ),
        ("nodeVmSize", serde_json::json!("Standard_D96s_v5")),
        ("kubernetesVersion", serde_json::json!("latest")),
        ("adminGroupObjectIds", serde_json::json!([])),
        (
            "postgresPassword",
            serde_json::json!({"plaintext":"never-accept-this"}),
        ),
    ] {
        let mut c = infrastructure_config();
        c[field] = value;
        assert!(
            !infrastructure(&f, &c, "rejected").status.success(),
            "{field}"
        );
        assert!(!f.path("rejected").exists());
    }
    assert!(f.log().is_empty());
}

#[test]
fn lifecycle_and_independent_cleanup_prepare_offline_and_require_explicit_remote_confirmation() {
    let f = Fixture::ready();
    assert!(infrastructure(&f, &infrastructure_config(), "graph")
        .status
        .success());
    let r = f.call(&[
        "deploy",
        "azure",
        "lifecycle",
        "prepare",
        "--plan",
        f.path("graph").to_str().unwrap(),
        "--out",
        f.path("state").to_str().unwrap(),
    ]);
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    let state: Value =
        serde_json::from_slice(&fs::read(f.path("state/azure-state.json")).unwrap()).unwrap();
    assert_eq!(state["operations"][0]["phase"], "prepared");
    assert!(state["nonce"].as_str().unwrap().len() == 36);
    assert!(f
        .call(&[
            "deploy",
            "azure",
            "cleanup",
            "package",
            "--state",
            f.path("state").to_str().unwrap(),
            "--out",
            f.path("control").to_str().unwrap()
        ])
        .status
        .success());
    let graph: Value =
        serde_json::from_slice(&fs::read(f.path("control/azure-control.arm.json")).unwrap())
            .unwrap();
    let job = graph["resources"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["type"] == "Microsoft.App/jobs")
        .unwrap();
    assert_eq!(
        job["properties"]["configuration"]["triggerType"],
        "Schedule"
    );
    assert_eq!(job["properties"]["configuration"]["replicaRetryLimit"], 0);
    assert!(job["properties"]["template"]["containers"][0]["image"]
        .as_str()
        .unwrap()
        .contains("@sha256:"));
    let state_path = f.path("state");
    for command in [
        vec!["lifecycle", "apply", "--stage", "foundation"],
        vec!["lifecycle", "destroy"],
        vec!["cleanup", "initialize"],
    ] {
        let mut args = vec!["deploy", "azure"];
        args.extend(command);
        args.extend([
            "--state",
            state_path.to_str().unwrap(),
            "--accept",
            "wrong-digest",
        ]);
        assert!(!f.call(&args).status.success());
    }
    assert!(
        f.log().is_empty(),
        "Offline preparation/refused remote calls must not obtain tokens"
    );
    let mut tampered = state;
    tampered["plan"]["azureInfrastructure"]["nodeVmSize"] = serde_json::json!("Standard_D96s_v5");
    fs::write(
        f.path("state/azure-state.json"),
        serde_json::to_vec(&tampered).unwrap(),
    )
    .unwrap();
    assert!(!f
        .call(&[
            "deploy",
            "azure",
            "cleanup",
            "package",
            "--state",
            f.path("state").to_str().unwrap(),
            "--out",
            f.path("tampered").to_str().unwrap()
        ])
        .status
        .success());
    assert!(f.log().is_empty());
}
