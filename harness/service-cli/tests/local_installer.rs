#![cfg(unix)]
use serde_json::{json, Value};
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    process::{Command, Output},
};
const BIN: &str = env!("CARGO_BIN_EXE_ows");
struct Fixture {
    dir: tempfile::TempDir,
    state: PathBuf,
    digest: String,
    id: String,
    values: PathBuf,
}
impl Fixture {
    fn call(home: &Path, args: &[&str], extra: &[(&str, &str)]) -> Output {
        let mut c = Command::new(BIN);
        c.args(args).env_clear().env("HOME", home).env("PATH", home);
        for (k, v) in extra {
            c.env(k, v);
        }
        c.output().unwrap()
    }
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        for tool in ["helm", "kubectl"] {
            let p = dir.path().join(tool);
            fs::write(&p, include_str!("fixtures/kind-installer.py")).unwrap();
            fs::set_permissions(p, fs::Permissions::from_mode(0o700)).unwrap();
        }
        fs::write(dir.path().join("remote.json"),json!({"namespaces":{},"releases":{"existing-shell":{"shell":{"version":1,"info":{"status":"deployed","description":"external"},"uid":"external-release"}}}}).to_string()).unwrap();
        let kube = dir.path().join("kubeconfig");
        fs::write(&kube, "fake").unwrap();
        let chart = dir.path().join("chart");
        fs::create_dir(&chart).unwrap();
        fs::write(chart.join("Chart.yaml"), "name: service\nversion: 0.1.0\n").unwrap();
        let values = dir.path().join("values.json");
        fs::write(&values,json!({"image":format!("image@sha256:{}","a".repeat(64)),"config":{"service":{"id":"ows-0123456789ab"},"schema":"ow_service","openshell":{"endpoint":"https://gateway.existing-shell.svc.cluster.local:8080"}}}).to_string()).unwrap();
        let config = dir.path().join("config.json");
        fs::write(&config,json!({"schemaVersion":1,"kubeconfig":kube,"context":"kind-test","helm":dir.path().join("helm"),"service":{"namespace":"ow-service-m4-installer","release":"ows","chart":chart,"valuesFile":values,"fixtureDatabase":true},"openshell":{"mode":"existing","namespace":"existing-shell","release":"shell","fullname":"gateway"}}).to_string()).unwrap();
        let state = dir.path().join("state");
        let r = Self::call(
            dir.path(),
            &[
                "deploy",
                "local",
                "plan",
                "--config",
                config.to_str().unwrap(),
                "--out",
                state.to_str().unwrap(),
            ],
            &[],
        );
        let p = parsed(&r);
        Self {
            dir,
            state,
            digest: p["planDigest"].as_str().unwrap().into(),
            id: p["deploymentId"].as_str().unwrap().into(),
            values,
        }
    }
    fn apply(&self, extra: &[(&str, &str)], confirm: bool) -> Output {
        let mut a = vec![
            "deploy",
            "local",
            "apply",
            "--state",
            self.state.to_str().unwrap(),
            "--accept-plan",
            &self.digest,
        ];
        if confirm {
            a.extend(["--confirm-no-effect", "service-release"]);
        }
        Self::call(self.dir.path(), &a, extra)
    }
    fn destroy(&self) -> Output {
        Self::call(
            self.dir.path(),
            &[
                "deploy",
                "local",
                "destroy",
                "--state",
                self.state.to_str().unwrap(),
                "--deployment-id",
                &self.id,
            ],
            &[],
        )
    }
    fn remote(&self) -> Value {
        serde_json::from_slice(&fs::read(self.dir.path().join("remote.json")).unwrap()).unwrap()
    }
    fn set_remote(&self, v: &Value) {
        fs::write(self.dir.path().join("remote.json"), v.to_string()).unwrap();
    }
    fn installs(&self) -> usize {
        fs::read_to_string(self.dir.path().join("calls"))
            .unwrap()
            .lines()
            .filter(|l| {
                let v: Value = serde_json::from_str(l).unwrap();
                v[0] == "helm" && v[1][0] == "install"
            })
            .count()
    }
}
fn parsed(r: &Output) -> Value {
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    serde_json::from_slice(&r.stdout).unwrap()
}
#[test]
fn lost_response_reconciles_the_original_release_without_reinstalling() {
    let f = Fixture::new();
    assert!(!f.apply(&[("FAIL_AFTER", "yes")], false).status.success());
    assert_eq!(f.installs(), 1);
    assert_eq!(parsed(&f.apply(&[], false))["phase"], "installed");
    assert_eq!(f.installs(), 1);
    assert_eq!(parsed(&f.destroy())["phase"], "destroyed");
    assert!(f.remote()["releases"]["existing-shell"]
        .get("shell")
        .is_some());
    assert_eq!(parsed(&f.destroy())["phase"], "destroyed");
}
#[test]
fn unknown_absent_release_requires_explicit_no_effect_confirmation() {
    let f = Fixture::new();
    assert!(!f.apply(&[("FAIL_BEFORE", "yes")], false).status.success());
    assert!(!f.apply(&[], false).status.success());
    assert_eq!(f.installs(), 1);
    assert_eq!(parsed(&f.apply(&[], true))["phase"], "installed");
    assert_eq!(f.installs(), 2);
    let j: Value =
        serde_json::from_slice(&fs::read(f.state.join("journal.json")).unwrap()).unwrap();
    assert_eq!(j["confirmedNoEffect"].as_array().unwrap().len(), 1);
    parsed(&f.destroy());
}
#[test]
fn ownership_active_work_cluster_drift_and_input_drift_refuse_mutation() {
    let f = Fixture::new();
    parsed(&f.apply(&[], false));
    let mut r = f.remote();
    r["active"] = json!(true);
    f.set_remote(&r);
    assert!(!f.destroy().status.success());
    assert!(f.remote()["namespaces"]
        .get("ow-service-m4-installer")
        .is_some());
    r["active"] = json!(false);
    r["namespaces"]["ow-service-m4-installer"]["metadata"]["uid"] = json!("replaced");
    f.set_remote(&r);
    assert!(!f.destroy().status.success());
    r["namespaces"]["ow-service-m4-installer"]["metadata"]["uid"] =
        json!("uid-ow-service-m4-installer");
    r["clusterUid"] = json!("different-cluster");
    f.set_remote(&r);
    assert!(!f.apply(&[], false).status.success());
    r["clusterUid"] = json!("cluster-uid");
    f.set_remote(&r);
    fs::write(&f.values, "{}").unwrap();
    assert!(!f.apply(&[], false).status.success());
    assert_eq!(f.installs(), 1);
}
#[test]
fn os_lock_and_read_only_status_preserve_remote_state() {
    let f = Fixture::new();
    let lock = fs::OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(f.state.join("installer.lock"))
        .unwrap();
    lock.lock().unwrap();
    assert!(!f.apply(&[], false).status.success());
    drop(lock);
    parsed(&f.apply(&[], false));
    let before = f.remote();
    let r = Fixture::call(
        f.dir.path(),
        &[
            "deploy",
            "local",
            "status",
            "--state",
            f.state.to_str().unwrap(),
        ],
        &[],
    );
    let v = parsed(&r);
    assert_eq!(v["readOnly"], true);
    assert_eq!(v["observedService"]["status"], "deployed");
    assert_eq!(f.remote(), before);
    parsed(&f.destroy());
}

#[test]
fn unavailable_controller_blocks_before_any_namespace_or_release_is_created() {
    let f = Fixture::new();
    let mut remote = f.remote();
    remote["controllerUnavailable"] = json!(true);
    f.set_remote(&remote);
    assert!(!f.apply(&[], false).status.success());
    assert_eq!(f.installs(), 0);
    assert!(f.remote()["namespaces"].as_object().unwrap().is_empty());
}

#[test]
fn oversized_chart_directory_is_rejected_instead_of_silently_truncated() {
    let f = Fixture::new();
    for index in 0..201 {
        fs::create_dir(f.dir.path().join("chart").join(format!("entry-{index}"))).unwrap();
    }
    assert!(!f.apply(&[], false).status.success());
    assert_eq!(f.installs(), 0);
    assert!(f.remote()["namespaces"].as_object().unwrap().is_empty());
}
