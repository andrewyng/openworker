use openworker_runtime::{
    environment::{AttachmentState, Binding, Management, Phase, Sharing, Spec},
    store::{Session, StateStore},
};
use openworker_store_postgres::{Config, PostgresStore, Tls};
use serde_json::json;
use std::sync::{Arc, Barrier};

fn config() -> Config {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").expect("Disposable DB required"),
        format!("ow_env_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    c
}
fn spec(sharing: Sharing, management: Management) -> Spec {
    Spec {
        provider: "test-provider".into(),
        provider_identity: "deployment-one".into(),
        profile_digest: "profile-one".into(),
        image: format!("example.invalid/runner@sha256:{}", "a".repeat(64)),
        workspace_root: "/sandbox".into(),
        sharing,
        management,
    }
}
fn ready(store: &PostgresStore, sharing: Sharing, management: Management) -> Binding {
    let (e, fresh) = store
        .begin_environment("create", "operator", spec(sharing, management))
        .unwrap();
    assert!(fresh);
    let b = Binding {
        environment_id: e.id.clone(),
        spec: e.spec.clone(),
        remote_id: "remote-unique-id".into(),
        remote_name: "ow-test".into(),
        workspace_scope: "default".into(),
        policy_hash: "accepted-policy".into(),
        policy_version: 1,
    };
    store
        .complete_environment(&e.id, e.revision, Some(b.clone()))
        .unwrap();
    b
}
fn session(id: &str, b: Binding) -> Session {
    let mut s:Session=serde_json::from_value(json!({"id":id,"name":"Remote session","workspace":"/sandbox","coworker":"test","bundle_digest":"bundle","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
    s.resource_policy.digest = b.fingerprint().unwrap();
    s.resource_policy.workspace = "/sandbox".into();
    s.environment = Some(b);
    s
}
fn close(store: &mut PostgresStore, id: &str) {
    let mut s = store.get(id).unwrap();
    s.closed = true;
    store.save(&s, "session_closed", json!({})).unwrap();
}
fn admin(c: &Config) -> postgres::Client {
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    db
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn concurrent_creation_is_once_unknown_is_not_permission_to_recreate() {
    let c = config();
    let a = PostgresStore::open(c.clone()).unwrap();
    let b = PostgresStore::open(c.clone()).unwrap();
    let gate = Arc::new(Barrier::new(3));
    let ga = gate.clone();
    let gb = gate.clone();
    let ta = std::thread::spawn(move || {
        ga.wait();
        a.begin_environment(
            "create",
            "operator",
            spec(Sharing::Shared, Management::Harness),
        )
        .unwrap()
    });
    let tb = std::thread::spawn(move || {
        gb.wait();
        b.begin_environment(
            "create",
            "operator",
            spec(Sharing::Shared, Management::Harness),
        )
        .unwrap()
    });
    gate.wait();
    let (a, af) = ta.join().unwrap();
    let (b, bf) = tb.join().unwrap();
    assert_eq!(a, b);
    assert_ne!(af, bf);
    let p = PostgresStore::open(c).unwrap();
    let unknown = p.complete_environment(&a.id, a.revision, None).unwrap();
    assert_eq!(unknown.phase, Phase::Unknown);
    let (existing, fresh) = p
        .begin_environment("create", "operator", a.spec.clone())
        .unwrap();
    assert!(!fresh);
    assert_eq!(existing, unknown);
    assert!(p
        .begin_environment("create", "different-actor", a.spec.clone())
        .is_err());
    assert!(p
        .begin_environment_delete(&a.id, "delete", "operator")
        .is_err());
    let binding = Binding {
        environment_id: a.id.clone(),
        spec: a.spec,
        remote_id: "observed-original-resource".into(),
        remote_name: "ow-original".into(),
        workspace_scope: "default".into(),
        policy_hash: "policy".into(),
        policy_version: 1,
    };
    assert!(p
        .complete_environment(&a.id, a.revision, Some(binding.clone()))
        .is_err());
    let known = p
        .complete_environment(&a.id, unknown.revision, Some(binding.clone()))
        .unwrap();
    assert_eq!(known.phase, Phase::Ready);
    assert_eq!(
        p.complete_environment(&a.id, unknown.revision, Some(binding))
            .unwrap(),
        known
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn shared_sessions_and_pending_connections_block_delete_until_explicit_release() {
    let c = config();
    let mut a = PostgresStore::open(c.clone()).unwrap();
    let mut b = PostgresStore::open(c).unwrap();
    let binding = ready(&a, Sharing::Shared, Management::Harness);
    a.save(&session("a", binding.clone()), "session_created", json!({}))
        .unwrap();
    b.save(&session("b", binding.clone()), "session_created", json!({}))
        .unwrap();
    let (x, fresh) = a.reserve_attachment("a", "files").unwrap();
    assert!(fresh);
    assert_eq!(x.state, AttachmentState::Pending);
    assert!(!a.reserve_attachment("a", "files").unwrap().1);
    let (y, _) = b.reserve_attachment("b", "files").unwrap();
    b.settle_attachment("b", &y.id, false, false).unwrap();
    assert!(a
        .begin_environment_delete(&binding.environment_id, "delete", "operator")
        .is_err());
    close(&mut a, "a");
    // Closing A cannot release B or delete the shared environment.
    a.settle_attachment("a", &x.id, true, true).unwrap();
    assert_eq!(
        b.reserve_attachment("b", "files").unwrap().0.state,
        AttachmentState::Active
    );
    assert!(a
        .begin_environment_delete(&binding.environment_id, "delete", "operator")
        .is_err());
    close(&mut b, "b");
    assert!(a
        .begin_environment_delete(&binding.environment_id, "delete", "operator")
        .is_err());
    b.settle_attachment("b", &y.id, true, false).unwrap();
    let (deleting, fresh) = a
        .begin_environment_delete(&binding.environment_id, "delete", "operator")
        .unwrap();
    assert!(fresh);
    assert_eq!(deleting.phase, Phase::Deleting);
    assert!(
        !b.begin_environment_delete(&binding.environment_id, "delete", "operator")
            .unwrap()
            .1
    );
    assert!(b
        .begin_environment_delete(&binding.environment_id, "different", "operator")
        .is_err());
    assert!(a
        .save(
            &session("late", binding.clone()),
            "session_created",
            json!({})
        )
        .is_err());
    let deleted = a
        .complete_environment_delete(&binding.environment_id, "delete", deleting.revision)
        .unwrap();
    assert_eq!(deleted.phase, Phase::Deleted);
    assert_eq!(
        a.complete_environment_delete(&binding.environment_id, "delete", deleting.revision)
            .unwrap(),
        deleted
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn exclusive_and_app_owned_environments_are_not_silently_shared_or_destroyed() {
    let c = config();
    let mut a = PostgresStore::open(c.clone()).unwrap();
    let mut b = PostgresStore::open(c).unwrap();
    let binding = ready(&a, Sharing::Exclusive, Management::Application);
    a.save(&session("a", binding.clone()), "session_created", json!({}))
        .unwrap();
    assert!(b
        .save(&session("b", binding.clone()), "session_created", json!({}))
        .is_err());
    let mut changed = a.get("a").unwrap();
    changed.environment.as_mut().unwrap().remote_id = "replacement".into();
    assert!(a.save(&changed, "rebind", json!({})).is_err());
    close(&mut a, "a");
    b.save(&session("b", binding.clone()), "session_created", json!({}))
        .unwrap();
    close(&mut b, "b");
    assert!(a
        .begin_environment_delete(&binding.environment_id, "delete", "operator")
        .is_err());
    assert_eq!(
        a.environment(&binding.environment_id).unwrap().phase,
        Phase::Ready
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn takeover_keeps_binding_and_fences_old_attachment_writes() {
    let c = config();
    let mut old = PostgresStore::open(c.clone()).unwrap();
    let binding = ready(&old, Sharing::Shared, Management::Harness);
    old.save(&session("s", binding.clone()), "session_created", json!({}))
        .unwrap();
    let (a, _) = old.reserve_attachment("s", "files").unwrap();
    admin(&c)
        .execute(
            "UPDATE sessions SET expires=clock_timestamp()-interval '1 second' WHERE id='s'",
            &[],
        )
        .unwrap();
    let mut new = PostgresStore::open(c).unwrap();
    new.claim("s").unwrap();
    new.recover().unwrap();
    assert_eq!(new.get("s").unwrap().environment, Some(binding.clone()));
    assert!(old.settle_attachment("s", &a.id, false, false).is_err());
    new.abandon_attachments("s").unwrap();
    let (next, fresh) = new.reserve_attachment("s", "files").unwrap();
    assert!(fresh);
    assert_ne!(a.id, next.id);
    assert!(next.owner_epoch > a.owner_epoch);
    // Release/abandon are bookkeeping, not a claim that old remote effects stopped.
    assert!(new
        .events("s", 0)
        .unwrap()
        .iter()
        .any(|e| e.kind == "attachments_abandoned" && e.data["outcome_unknown"] == true));
    assert_eq!(
        new.environment(&binding.environment_id).unwrap().phase,
        Phase::Ready
    );
}

#[cfg(target_os = "linux")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn closing_recovered_session_releases_old_attachments_without_starting_tools() {
    use openworker_runtime::{execution::Backend, model::Model, runtime::Runtime};
    let c = config();
    let mut old = PostgresStore::open(c.clone()).unwrap();
    let binding = ready(&old, Sharing::Shared, Management::Harness);
    old.save(&session("s", binding.clone()), "session_created", json!({}))
        .unwrap();
    let (attachment, _) = old.reserve_attachment("s", "files").unwrap();
    old.settle_attachment("s", &attachment.id, false, false)
        .unwrap();
    tokio::task::block_in_place(|| {
        admin(&c)
            .execute(
                "UPDATE sessions SET expires=clock_timestamp()-interval '1 second' WHERE id='s'",
                &[],
            )
            .unwrap();
    });
    let new = PostgresStore::open(c.clone()).unwrap();
    new.claim("s").unwrap();
    let local = tempfile::tempdir().unwrap();
    let bundle = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let runtime = Runtime::open_with_store(
        local.path(),
        &bundle,
        &std::env::current_exe().unwrap(),
        Model::new("fixture".into(), true),
        Backend::Openshell,
        None,
        None,
        Default::default(),
        Some(Box::new(new.clone())),
    )
    .unwrap();
    // No provider registry or exec endpoint is configured: close must not launch
    // a replacement tool process merely to clear old ownership bookkeeping.
    runtime
        .dispatch("session/close", json!({"session_id":"s"}))
        .await
        .unwrap();
    assert!(new.get("s").unwrap().closed);
    let row = tokio::task::block_in_place(|| {
        admin(&c)
            .query_one(
                "SELECT state,outcome_unknown FROM attachments WHERE id=$1",
                &[&attachment.id],
            )
            .unwrap()
    });
    assert_eq!(row.get::<_, String>(0), "released");
    assert!(row.get::<_, bool>(1));
    assert!(new
        .begin_environment_delete(&binding.environment_id, "delete", "operator")
        .is_ok());
    runtime.shutdown().await.unwrap();
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn delete_racing_session_creation_never_deletes_a_successfully_bound_live_session() {
    let c = config();
    let a = PostgresStore::open(c.clone()).unwrap();
    let binding = ready(&a, Sharing::Shared, Management::Harness);
    let mut creator = PostgresStore::open(c).unwrap();
    let gate = Arc::new(Barrier::new(3));
    let ga = gate.clone();
    let gb = gate.clone();
    let id = binding.environment_id.clone();
    let create = std::thread::spawn(move || {
        ga.wait();
        creator
            .save(&session("s", binding), "session_created", json!({}))
            .is_ok()
    });
    let delete = std::thread::spawn(move || {
        gb.wait();
        a.begin_environment_delete(&id, "delete", "operator")
            .is_ok()
    });
    gate.wait();
    assert_ne!(create.join().unwrap(), delete.join().unwrap());
}

#[cfg(not(windows))]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn resilience_recovered_runtime_shutdown_retires_old_attachments_without_deleting_shared_environment(
) {
    use openworker_runtime::{
        execution::Backend, model::Model, runtime::Runtime, telemetry::Telemetry,
    };
    let c = config();
    let mut old = PostgresStore::open(c.clone()).unwrap();
    let binding = ready(&old, Sharing::Shared, Management::Harness);
    old.save(
        &session("recovered", binding.clone()),
        "session_created",
        json!({}),
    )
    .unwrap();
    let (attachment, _) = old.reserve_attachment("recovered", "files").unwrap();
    old.settle_attachment("recovered", &attachment.id, false, false)
        .unwrap();
    old.release("recovered").unwrap();
    let mut next = PostgresStore::open(c.clone()).unwrap();
    next.claim("recovered").unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(root.path().join("bundle/coworker")).unwrap();
    std::fs::write(
        root.path().join("bundle/coworker/manifest.md"),
        "---\nid: test\n---\nInspect the original environment.",
    )
    .unwrap();
    std::fs::write(
        root.path().join("bundle/tools.json"),
        include_str!("../../runtime/examples/assistant/tools.json"),
    )
    .unwrap();
    std::fs::write(root.path().join("runner"), b"\x7fELF-fixture-not-executed").unwrap();
    let rt = Runtime::open_instrumented(
        &root.path().join("state"),
        &root.path().join("bundle"),
        &root.path().join("runner"),
        Model::new("fixture".into(), true),
        Backend::Openshell,
        None,
        None,
        Default::default(),
        Some(Box::new(next.clone())),
        Telemetry::default(),
    )
    .unwrap();
    // No prepare_tools or provider attachment occurs. The old channel can have
    // an uncertain outcome, but may not strand close/delete bookkeeping forever.
    rt.shutdown().await.unwrap();
    let row = admin(&c)
        .query_one(
            "SELECT state,outcome_unknown FROM attachments WHERE id=$1",
            &[&attachment.id],
        )
        .unwrap();
    assert_eq!(row.get::<_, String>(0), "released");
    assert!(row.get::<_, bool>(1));
    assert_eq!(
        next.environment(&binding.environment_id).unwrap().phase,
        Phase::Ready
    );
    assert!(!next.get("recovered").unwrap().closed);
}
