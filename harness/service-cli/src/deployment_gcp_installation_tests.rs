use super::*;
#[derive(Default)]
struct Store {
    objects: BTreeMap<String, (String, Vec<u8>)>,
    creates: usize,
    fail_after_write: bool,
    fail_before_write: bool,
    fail_read: bool,
}
impl ObjectStore for Store {
    async fn get(&mut self, name: &str) -> Result<Option<(String, Vec<u8>)>> {
        ensure!(!self.fail_read, "fixture read failure");
        Ok(self.objects.get(name).cloned())
    }
    async fn create(&mut self, name: &str, b: &[u8], _: &str) -> Result<String> {
        self.creates += 1;
        ensure!(!self.fail_before_write, "fixture rejected before write");
        ensure!(
            !self.objects.contains_key(name),
            "fixture generation conflict"
        );
        let generation = (self.creates + 500).to_string();
        self.objects
            .insert(name.into(), (generation.clone(), b.to_vec()));
        if self.fail_after_write {
            self.fail_after_write = false;
            bail!("fixture lost acknowledgement");
        }
        Ok(generation)
    }
}
fn installation() -> Installation {
    let mut c = super::super::lifecycle_tests::control();
    c.source_generation = "1".into();
    c.plan.created_at_unix = now().unwrap() - 1;
    c.plan.expires_at_unix = c.plan.created_at_unix + 3600;
    c.source_sha256 = hash(&source_zip(&c.plan).unwrap());
    let hashes: BTreeMap<_, _> = super::super::super::artifacts(&c.plan)
        .unwrap()
        .iter()
        .map(|(n, b)| (n.clone(), hash(b)))
        .collect();
    c.bundle_digest = hash(&serde_json::to_vec(&hashes).unwrap());
    c.validate().unwrap();
    Installation {
        schema_version: 1,
        scope: c,
        plan_path: "unused-by-object-protocol-tests".into(),
    }
}
#[tokio::test]
async fn source_control_and_checkpoint_are_uploaded_once_with_actual_generation() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store::default();
    let c = upload(&mut s, d.path(), &i, true).await.unwrap();
    assert_eq!(c.source_generation, "501");
    assert_eq!(s.creates, 3);
    assert_eq!(
        s.objects[&c.source_object()].1,
        source_zip(&c.plan).unwrap()
    );
    let j: Checkpoint = serde_json::from_slice(&s.objects[&c.checkpoint_object()].1).unwrap();
    j.validate(&c).unwrap();
    assert_eq!(
        upload(&mut s, d.path(), &i, true)
            .await
            .unwrap()
            .sha()
            .unwrap(),
        c.sha().unwrap()
    );
    assert_eq!(s.creates, 3);
}
#[tokio::test]
async fn lost_upload_response_is_reconciled_by_read_without_repeating_upload() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store {
        fail_after_write: true,
        ..Default::default()
    };
    assert!(upload(&mut s, d.path(), &i, true).await.is_err());
    assert_eq!(s.creates, 1);
    assert!(d.path().join("source-intent.json").exists());
    assert!(!d.path().join("source-receipt.json").exists());
    upload(&mut s, d.path(), &i, true).await.unwrap();
    assert_eq!(s.creates, 3);
}
#[tokio::test]
async fn absent_after_original_intent_is_unknown_and_never_replayed() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store {
        fail_before_write: true,
        ..Default::default()
    };
    assert!(upload(&mut s, d.path(), &i, true).await.is_err());
    s.fail_before_write = false;
    assert!(upload(&mut s, d.path(), &i, true)
        .await
        .err()
        .unwrap()
        .to_string()
        .contains("No replay"));
    assert_eq!(s.creates, 1);
}
#[tokio::test]
async fn read_failure_before_dispatch_leaves_no_unknown_effect_or_intent() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store {
        fail_read: true,
        ..Default::default()
    };
    assert!(upload(&mut s, d.path(), &i, true).await.is_err());
    assert!(!d.path().join("source-intent.json").exists());
    assert_eq!(s.creates, 0);
    s.fail_read = false;
    upload(&mut s, d.path(), &i, true).await.unwrap();
    assert_eq!(s.creates, 3);
}
#[tokio::test]
async fn conflicting_source_and_same_bytes_replacement_generation_are_refused() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store::default();
    s.objects.insert(
        i.scope.source_object(),
        ("1".into(), b"wrong source".to_vec()),
    );
    assert!(upload(&mut s, d.path(), &i, true).await.is_err());
    assert_eq!(s.creates, 0);
    s.objects.clear();
    let c = upload(&mut s, d.path(), &i, true).await.unwrap();
    s.objects.get_mut(&c.source_object()).unwrap().0 = "999".into();
    assert!(upload(&mut s, d.path(), &i, true)
        .await
        .err()
        .unwrap()
        .to_string()
        .contains("generation changed"));
    assert_eq!(s.creates, 3);
}
#[tokio::test]
async fn restarted_installer_preserves_an_advanced_checkpoint() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store::default();
    let c = upload(&mut s, d.path(), &i, true).await.unwrap();
    let mut j: Checkpoint = serde_json::from_slice(&s.objects[&c.checkpoint_object()].1).unwrap();
    j.guard_uid = Some(uuid::Uuid::new_v4().to_string());
    j.heartbeat_at = Some(c.plan.created_at_unix + 1);
    let advanced = json_bytes(&j).unwrap();
    s.objects
        .insert(c.checkpoint_object(), ("88".into(), advanced.clone()));
    upload(&mut s, d.path(), &i, true).await.unwrap();
    assert_eq!(s.creates, 3);
    assert_eq!(s.objects[&c.checkpoint_object()].1, advanced);
}
#[tokio::test]
async fn checkpoint_from_another_control_is_never_adopted_or_replaced() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store::default();
    let c = upload(&mut s, d.path(), &i, true).await.unwrap();
    let mut j: Checkpoint = serde_json::from_slice(&s.objects[&c.checkpoint_object()].1).unwrap();
    j.control_sha256 = "b".repeat(64);
    s.objects.insert(
        c.checkpoint_object(),
        ("88".into(), json_bytes(&j).unwrap()),
    );
    assert!(upload(&mut s, d.path(), &i, true).await.is_err());
    assert_eq!(s.creates, 3);
}
#[tokio::test]
async fn upload_receipt_and_payload_tampering_are_rejected() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store::default();
    let c = upload(&mut s, d.path(), &i, true).await.unwrap();
    fs::write(d.path().join("control.json"), b"{}").unwrap();
    assert!(upload(&mut s, d.path(), &i, true).await.is_err());
    fs::write(d.path().join("control.json"), json_bytes(&c).unwrap()).unwrap();
    let mut r: Receipt =
        serde_json::from_slice(&fs::read(d.path().join("source-receipt.json")).unwrap()).unwrap();
    r.name = "other-source".into();
    fs::write(
        d.path().join("source-receipt.json"),
        json_bytes(&r).unwrap(),
    )
    .unwrap();
    assert!(upload(&mut s, d.path(), &i, true).await.is_err());
    assert_eq!(s.creates, 3);
}
#[test]
fn package_preserves_initial_checkpoint_and_exact_control_binding() {
    let d = tempfile::tempdir().unwrap();
    let c = installation().scope;
    let j = Checkpoint::new(&c).unwrap();
    let out = d.path().join("package");
    super::super::control_export::package(&out, &c, &j).unwrap();
    assert_eq!(
        fs::read(out.join("control.json")).unwrap(),
        json_bytes(&c).unwrap()
    );
    assert_eq!(
        fs::read(out.join("checkpoint.json")).unwrap(),
        json_bytes(&j).unwrap()
    );
    let tf: Value = serde_json::from_slice(&fs::read(out.join("main.tf.json")).unwrap()).unwrap();
    assert_eq!(
        tf["provider"]["google"]["impersonate_service_account"],
        accounts(&c.plan)["setup"]
    );
}

#[tokio::test]
async fn read_only_reconcile_can_recover_receipts_but_never_create_missing_objects() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store::default();
    assert!(upload(&mut s, d.path(), &i, false).await.is_err());
    assert_eq!(s.creates, 0);
    assert!(!d.path().join("source-intent.json").exists());
    upload(&mut s, d.path(), &i, true).await.unwrap();
    fs::remove_file(d.path().join("source-receipt.json")).unwrap();
    upload(&mut s, d.path(), &i, false).await.unwrap();
    assert_eq!(s.creates, 3);
    assert!(d.path().join("source-receipt.json").exists());
}
#[tokio::test]
async fn expired_plan_refuses_new_uploads_without_recording_a_dispatched_intent() {
    let d = tempfile::tempdir().unwrap();
    let mut i = installation();
    i.scope.plan.expires_at_unix = now().unwrap() - 1;
    // Use install_one directly to isolate the action-time expiry check; graph binding
    // validation independently rejects mutation of a prepared installation.
    let mut s = Store::default();
    let result = install_one(
        &mut s,
        d.path(),
        "fixture",
        ObjectSpec {
            key: "source",
            name: "scoped/source",
            bytes: b"fixture",
            mime: "application/zip",
            checkpoint: None,
            create_until: Some(i.scope.plan.expires_at_unix),
        },
    )
    .await;
    assert!(result.is_err());
    assert_eq!(s.creates, 0);
    assert!(!d.path().join("source-intent.json").exists());
}
#[tokio::test]
async fn original_checkpoint_payload_is_not_regenerated_after_dispatch() {
    let d = tempfile::tempdir().unwrap();
    let i = installation();
    let mut s = Store::default();
    upload(&mut s, d.path(), &i, true).await.unwrap();
    fs::remove_file(d.path().join("initial-checkpoint.json")).unwrap();
    assert!(upload(&mut s, d.path(), &i, true).await.is_err());
    assert_eq!(s.creates, 3);
}
