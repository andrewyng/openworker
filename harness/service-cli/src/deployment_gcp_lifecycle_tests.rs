//! Isolated protocol/transport fixtures. Never use cloud credentials or endpoints.
use super::{
    control::*,
    engine::{self, Cloud, Mode},
    *,
};
use std::collections::BTreeMap;

pub(super) fn control() -> Control {
    let mut p:Plan=serde_json::from_value(json!({"schemaVersion":2,"name":"gcp-test","deploymentId":"ows-0123456789abcdef0123456789abcdef","target":{"provider":"gcp","project":"schema-only-project","location":"us-central1"},"createdAtUnix":1793373000u64,"expiresAtUnix":1793376600u64,"budget":{"authorizedUsd":100,"hardCap":false,"estimate":null},"topology":{"clusterMode":"new","nodeCount":2,"maxWorkerReplicas":2,"architecture":"amd64","access":"private","nodeAutoscaling":false}})).unwrap();
    p.gcp_bootstrap = Some(Identity {
        project_number: "123456789012".into(),
        operator_member: "user:operator@example.com".into(),
    });
    p.gcp_infrastructure=Some(serde_json::from_value(json!({"zone":"us-central1-a","kubernetesVersion":"1.33.5-gke.1162000","terraformVersion":"1.5.7","googleProviderVersion":"6.44.0","nodeMachineType":"e2-standard-2","nodeDiskGb":40,"postgresVersion":"POSTGRES_16","postgresTier":"db-f1-micro","postgresDiskGb":10,"subnetCidr":"10.70.0.0/16","podCidr":"10.72.0.0/14","serviceCidr":"10.76.0.0/20","controlPlaneCidr":"172.16.0.0/28","sqlCidr":"10.80.0.0/16"})).unwrap());
    p.validate().unwrap();
    let hashes: BTreeMap<_, _> = super::super::artifacts(&p)
        .unwrap()
        .iter()
        .map(|(n, b)| (n.clone(), hash(b)))
        .collect();
    let c=Control{schema_version:1,source_sha256:hash(&source_zip(&p).unwrap()),plan:p,bundle_digest:hash(&serde_json::to_vec(&hashes).unwrap()),source_generation:"123".into(),request_id:"11111111-1111-4111-8111-111111111111".into(),guard_image:format!("us-central1-docker.pkg.dev/schema-only-project/ow-0123456789ab-control-images/ows@sha256:{}","a".repeat(64))};
    c.validate().unwrap();
    c
}
fn timestamp(at: u64) -> String {
    chrono::DateTime::<chrono::Utc>::from_timestamp(at as i64, 0)
        .unwrap()
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}
fn operation(c: &Control, verb: &str, at: u64) -> Value {
    json!({"name":format!("{}/operations/{verb}-original",c.parent()),"metadata":{"target":c.deployment(),"verb":verb,"createTime":timestamp(at)},"done":false})
}
struct Fake {
    c: Control,
    j: Checkpoint,
    g: u64,
    at: u64,
    d: Option<Value>,
    ops: BTreeMap<String, Value>,
    creates: u32,
    deletes: u32,
    saves: u32,
    save_fail: Option<(u32, bool)>,
    lost_create: bool,
    lost_delete: bool,
    source_ok: bool,
    uid: String,
    absent: bool,
    revision_drift: bool,
    allowed: bool,
    paused: bool,
    lost_pause: bool,
    dependent_gone: bool,
}
impl Fake {
    fn new() -> Self {
        let c = control();
        let at = c.plan.created_at_unix + 1;
        let mut j = Checkpoint::new(&c).unwrap();
        let uid = uuid::Uuid::new_v4().to_string();
        j.guard_uid = Some(uid.clone());
        j.heartbeat_at = Some(at);
        Self {
            c,
            j,
            g: 1,
            at,
            d: None,
            ops: BTreeMap::new(),
            creates: 0,
            deletes: 0,
            saves: 0,
            save_fail: None,
            lost_create: false,
            lost_delete: false,
            source_ok: true,
            uid,
            absent: false,
            revision_drift: false,
            allowed: true,
            paused: false,
            lost_pause: false,
            dependent_gone: true,
        }
    }
    async fn step(&mut self, mode: Mode) -> Result<Value> {
        let c = self.c.clone();
        let mut j = self.j.clone();
        let mut g = self.g.to_string();
        engine::drive(self, &c, &mut j, &mut g, mode, self.at).await
    }
    fn complete_create(&mut self, error: bool) {
        self.ops.get_mut("create").unwrap()["done"] = json!(true);
        if error {
            self.ops.get_mut("create").unwrap()["error"] =
                json!({"code":7,"message":"DO_NOT_EXPORT_PROVIDER_ERROR"});
        }
        self.d.as_mut().unwrap()["state"] = json!(if error { "FAILED" } else { "ACTIVE" });
    }
    async fn active(&mut self) {
        self.step(Mode::Apply).await.unwrap();
        self.complete_create(false);
        self.step(Mode::Reconcile).await.unwrap();
        assert_eq!(self.j.phase, Phase::Active);
    }
    fn complete_delete(&mut self, error: bool) {
        self.ops.get_mut("delete").unwrap()["done"] = json!(true);
        if error {
            self.ops.get_mut("delete").unwrap()["error"] = json!({"code":7});
        } else {
            self.d = None;
        }
    }
}
impl Cloud for Fake {
    async fn deployment(&mut self) -> Result<Option<Value>> {
        Ok(self.d.clone())
    }
    async fn operation(&mut self, name: &str) -> Result<Value> {
        self.ops
            .values()
            .find(|v| v["name"] == name)
            .cloned()
            .context("missing original operation")
    }
    async fn revision(&mut self, name: &str) -> Result<Value> {
        Ok(
            json!({"name":name,"serviceAccount":self.c.executor(),"terraformBlueprint":if self.revision_drift{json!({"gcsSource":"gs://other/source"})}else{self.c.request()["terraformBlueprint"].clone()}}),
        )
    }
    async fn operations(&mut self) -> Result<Vec<Value>> {
        Ok(self.ops.values().cloned().collect())
    }
    async fn source(&mut self) -> Result<()> {
        ensure!(self.source_ok, "source unavailable");
        Ok(())
    }
    async fn guard(&mut self) -> Result<String> {
        Ok(self.uid.clone())
    }
    async fn authorize_create(&mut self) -> Result<()> {
        ensure!(self.allowed, "not qualified");
        Ok(())
    }
    async fn create(&mut self) -> Result<Value> {
        assert_eq!(self.j.phase, Phase::CreateIntent);
        assert!(self.j.create_intent_at.is_some());
        self.creates += 1;
        let op = operation(&self.c, "create", self.at);
        self.ops.insert("create".into(), op.clone());
        let mut d = self.c.request();
        d["name"] = json!(self.c.deployment());
        d["createTime"] = json!(timestamp(self.at));
        d["latestRevision"] = json!(format!("{}/revisions/original", self.c.deployment()));
        d["state"] = json!("CREATING");
        self.d = Some(d);
        ensure!(!self.lost_create, "create reply lost");
        Ok(op)
    }
    async fn delete(&mut self, request: &str) -> Result<Value> {
        assert_eq!(self.j.phase, Phase::DeleteIntent);
        assert_eq!(request, self.j.delete_request_id);
        self.deletes += 1;
        let op = operation(&self.c, "delete", self.at);
        self.ops.insert("delete".into(), op.clone());
        self.d.as_mut().unwrap()["state"] = json!("DELETING");
        ensure!(!self.lost_delete, "delete reply lost");
        Ok(op)
    }
    async fn save(&mut self, g: &str, j: &Checkpoint) -> Result<String> {
        self.saves += 1;
        ensure!(g == self.g.to_string(), "generation conflict");
        j.validate(&self.c)?;
        let fail = self.save_fail.filter(|(n, _)| *n == self.saves);
        if fail.is_none_or(|(_, after)| after) {
            self.g += 1;
            self.j = j.clone();
        }
        ensure!(fail.is_none(), "CAS reply lost");
        Ok(self.g.to_string())
    }
    async fn dependent_assets(&mut self) -> Result<Vec<Asset>> {
        Ok(vec![Asset {
            path: format!(
                "/compute/v1/projects/{}/zones/us-central1-a/disks/owned",
                project(&self.c.plan)
            ),
            id: "1234".into(),
        }])
    }
    async fn assets_absent(&mut self, _: &[Asset]) -> Result<bool> {
        Ok(self.dependent_gone)
    }
    async fn scheduler_paused(&mut self) -> Result<bool> {
        Ok(self.paused)
    }
    async fn pause_scheduler(&mut self) -> Result<()> {
        self.paused = true;
        ensure!(!self.lost_pause, "pause reply lost");
        Ok(())
    }
    async fn named_resources_absent(&mut self) -> Result<bool> {
        Ok(self.absent)
    }
}

#[tokio::test]
async fn full_lifecycle_persists_original_receipts_and_does_not_claim_unproved_cleanup() {
    let mut f = Fake::new();
    f.active().await;
    assert_eq!(f.creates, 1);
    f.step(Mode::Destroy).await.unwrap();
    assert_eq!(f.deletes, 1);
    f.complete_delete(false);
    let r = f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(r["providerDeletionConfirmed"], true);
    f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(f.j.phase, Phase::ProviderDeleted);
    f.absent = true;
    let r = f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(f.j.phase, Phase::NamedResourcesAbsent);
    assert_eq!(r["allCleanupComplete"], false);
    assert_eq!(r["managedDependentAbsenceQualified"], false);
    f.step(Mode::Destroy).await.unwrap();
    assert_eq!((f.creates, f.deletes), (1, 1));
}
#[tokio::test]
async fn lost_create_reply_is_read_reconciled_after_restart_never_replayed() {
    let mut f = Fake::new();
    f.lost_create = true;
    assert!(f.step(Mode::Apply).await.is_err());
    assert_eq!(f.j.phase, Phase::CreateIntent);
    f.complete_create(false);
    f.step(Mode::Apply).await.unwrap();
    assert_eq!(f.j.phase, Phase::Active);
    assert_eq!(f.creates, 1);
    // No operation/absence proof cannot be turned into permission to recreate.
    let mut f = Fake::new();
    f.lost_create = true;
    assert!(f.step(Mode::Apply).await.is_err());
    f.ops.clear();
    f.d = None;
    assert!(f.step(Mode::Apply).await.is_err());
    assert_eq!(f.creates, 1);
}
#[tokio::test]
async fn lost_delete_reply_recovers_original_operation_without_another_delete() {
    let mut f = Fake::new();
    f.active().await;
    f.lost_delete = true;
    assert!(f.step(Mode::Destroy).await.is_err());
    assert_eq!(f.j.phase, Phase::DeleteIntent);
    f.complete_delete(false);
    f.step(Mode::Destroy).await.unwrap();
    assert_eq!(f.deletes, 1);
    let mut f = Fake::new();
    f.active().await;
    f.lost_delete = true;
    assert!(f.step(Mode::Destroy).await.is_err());
    f.ops.remove("delete");
    f.d = None;
    assert!(f.step(Mode::Destroy).await.is_err());
    assert_eq!(f.deletes, 1);
}
#[tokio::test]
async fn checkpoint_failure_before_or_after_write_never_replays_an_unknown_effect() {
    for after in [false, true] {
        let mut f = Fake::new();
        f.save_fail = Some((1, after));
        assert!(f.step(Mode::Apply).await.is_err());
        assert_eq!(f.creates, 0);
        f.save_fail = None;
        if after {
            assert!(f.step(Mode::Apply).await.is_err());
            assert_eq!(f.creates, 0);
        } else {
            f.step(Mode::Apply).await.unwrap();
            assert_eq!(f.creates, 1);
        }
    }
    let mut f = Fake::new();
    f.save_fail = Some((2, false));
    assert!(f.step(Mode::Apply).await.is_err());
    assert_eq!(f.creates, 1);
    f.save_fail = None;
    f.complete_create(false);
    f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(f.creates, 1);
}
#[tokio::test]
async fn independent_tick_enforces_expiry_and_runs_without_local_journal_or_cluster() {
    let mut f = Fake::new();
    f.active().await;
    f.step(Mode::Tick).await.unwrap();
    assert_eq!(f.deletes, 0);
    f.at = f.c.plan.expires_at_unix + 1;
    f.step(Mode::Tick).await.unwrap();
    assert_eq!(f.deletes, 1);
    assert_eq!(f.j.heartbeat_at, Some(f.at));
    f.complete_delete(false);
    f.step(Mode::Tick).await.unwrap();
    let mut f = Fake::new();
    f.at = f.c.plan.expires_at_unix;
    f.step(Mode::Tick).await.unwrap();
    assert_eq!(f.j.phase, Phase::Unused);
    assert_eq!((f.creates, f.deletes), (0, 0));
}
#[tokio::test]
async fn concurrent_checkpoint_writers_are_fenced_before_provider_mutation() {
    let mut f = Fake::new();
    let mut stale = f.j.clone();
    let mut g = f.g.to_string();
    let c = f.c.clone();
    f.step(Mode::Apply).await.unwrap();
    assert!(engine::drive(
        &mut f,
        &c,
        &mut stale,
        &mut g,
        Mode::Apply,
        c.plan.created_at_unix + 1
    )
    .await
    .is_err());
    assert_eq!(f.creates, 1);
}
#[tokio::test]
async fn ownership_revision_source_and_guard_drift_block_destructive_actions() {
    let mut missing = Fake::new();
    missing.active().await;
    missing.ops.clear();
    assert!(missing.step(Mode::Destroy).await.is_err());
    assert_eq!(missing.deletes, 0);
    let mut drift = Fake::new();
    drift.active().await;
    drift.revision_drift = true;
    assert!(drift.step(Mode::Destroy).await.is_err());
    assert_eq!(drift.deletes, 0);
    for scenario in ["time", "revision", "source", "executor", "request"] {
        let mut f = Fake::new();
        f.active().await;
        let d = f.d.as_mut().unwrap();
        match scenario {
            "time" => d["createTime"] = json!(timestamp(f.at + 1)),
            "revision" => {
                d["latestRevision"] = json!(format!("{}/revisions/changed", f.c.deployment()))
            }
            "source" => d["terraformBlueprint"]["gcsSource"] = json!("gs://other/source"),
            "executor" => d["serviceAccount"] = json!("foreign"),
            _ => d["annotations"]["openworker.dev/request"] = json!("foreign"),
        };
        assert!(f.step(Mode::Destroy).await.is_err(), "{scenario}");
        assert_eq!(f.deletes, 0);
    }
    let mut f = Fake::new();
    f.source_ok = false;
    assert!(f.step(Mode::Apply).await.is_err());
    assert_eq!(f.creates, 0);
    let mut f = Fake::new();
    f.uid = uuid::Uuid::new_v4().to_string();
    assert!(f.step(Mode::Tick).await.is_err());
    assert_eq!(f.saves, 0);
    let mut f = Fake::new();
    f.j.heartbeat_at = Some(f.at - 181);
    assert!(f.step(Mode::Apply).await.is_err());
    assert_eq!(f.creates, 0);
}
#[tokio::test]
async fn operation_ambiguity_failure_and_permission_gates_keep_receipts() {
    let mut f = Fake::new();
    f.lost_create = true;
    assert!(f.step(Mode::Apply).await.is_err());
    let mut duplicate = f.ops["create"].clone();
    duplicate["name"] = json!(format!("{}/operations/another", f.c.parent()));
    f.ops.insert("duplicate".into(), duplicate);
    assert!(f.step(Mode::Reconcile).await.is_err());
    assert_eq!(f.creates, 1);
    let mut f = Fake::new();
    f.allowed = false;
    assert!(f.step(Mode::Apply).await.is_err());
    assert_eq!(f.creates, 0);
    assert_eq!(f.j.phase, Phase::Prepared);
    let mut f = Fake::new();
    f.step(Mode::Apply).await.unwrap();
    f.complete_create(true);
    f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(f.j.phase, Phase::CreateFailed);
    f.step(Mode::Destroy).await.unwrap();
    f.complete_delete(true);
    f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(f.j.phase, Phase::DeleteFailed);
    assert!(f.step(Mode::Destroy).await.is_err());
    assert_eq!(f.deletes, 1);
}
#[tokio::test]
async fn missing_or_changed_provider_receipts_never_mean_successful_deletion() {
    let mut f = Fake::new();
    f.active().await;
    f.step(Mode::Destroy).await.unwrap();
    f.d = None;
    assert!(f.step(Mode::Reconcile).await.is_err());
    assert_eq!(f.j.phase, Phase::Deleting);
    f.ops.get_mut("delete").unwrap()["metadata"]["target"] =
        json!("projects/other/locations/r/deployments/foreign");
    assert!(f.step(Mode::Reconcile).await.is_err());
    assert_eq!(f.deletes, 1);
}
#[test]
fn source_archive_has_fixed_names_and_control_identity_is_content_bound() {
    let c = control();
    let a = source_zip(&c.plan).unwrap();
    assert_eq!(a, source_zip(&c.plan).unwrap());
    assert_eq!(&a[..4], b"PK\x03\x04");
    assert_eq!(&a[a.len() - 22..a.len() - 18], b"PK\x05\x06");
    for scenario in ["generation", "source", "image", "digest", "project"] {
        let mut bad = c.clone();
        match scenario {
            "generation" => bad.source_generation = "0".into(),
            "source" => bad.source_sha256 = "b".repeat(64),
            "image" => bad.guard_image = "ghcr.io/other:latest".into(),
            "digest" => bad.bundle_digest = "b".repeat(64),
            _ => {
                if let Target::Gcp { project, .. } = &mut bad.plan.target {
                    *project = "another-project".into()
                }
            }
        };
        assert!(bad.validate().is_err(), "{scenario}");
    }
}

#[tokio::test]
async fn captured_dependents_and_scheduler_pause_must_be_confirmed_after_expiry() {
    let mut f = Fake::new();
    f.active().await;
    f.step(Mode::Destroy).await.unwrap();
    assert_eq!(f.j.assets.as_ref().unwrap().len(), 1);
    f.complete_delete(false);
    f.step(Mode::Reconcile).await.unwrap();
    f.absent = true;
    f.dependent_gone = false;
    f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(f.j.phase, Phase::ProviderDeleted);
    assert!(!f.paused);
    f.dependent_gone = true;
    f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(f.j.phase, Phase::NamedResourcesAbsent);
    f.lost_pause = true;
    assert!(f.step(Mode::Reconcile).await.is_err());
    assert_eq!(f.j.phase, Phase::PauseIntent);
    assert!(f.paused);
    let result = f.step(Mode::Reconcile).await.unwrap();
    assert_eq!(f.j.phase, Phase::Complete);
    assert_eq!(result["schedulerPaused"], true);
    assert_eq!(f.deletes, 1);
    let mut f = Fake::new();
    f.at = f.c.plan.expires_at_unix;
    f.step(Mode::Tick).await.unwrap();
    f.step(Mode::Tick).await.unwrap();
    assert_eq!(f.j.phase, Phase::UnusedComplete);
    assert!(f.paused);
    assert_eq!(f.deletes, 0);
}
