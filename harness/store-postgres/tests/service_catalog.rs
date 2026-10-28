use openworker_runtime::environment::{Binding, Management, Sharing, Spec};
use openworker_runtime::store::{Session, StateStore};
use openworker_store_postgres::{service_catalog::*, Config, PostgresStore, Tls};
use serde_json::{json, Value};
fn setup() -> (Config, PostgresStore, PostgresStore, PostgresStore) {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_catalog_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let store = PostgresStore::open(c.clone()).unwrap();
    let admin = "admin-test-key-".repeat(5);
    let user = "user-test-key-".repeat(5);
    store.auth_bootstrap(&admin, "admin").unwrap();
    let a = store.authorized(&admin).unwrap();
    a.auth_issue(
        "user",
        serde_json::from_value(
            json!({"principal":"alice","role":"operator","scope":{"kind":"owned"}}),
        )
        .unwrap(),
        &user,
    )
    .unwrap();
    let u = store.authorized(&user).unwrap();
    (c, store, a, u)
}
#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn installer_status_is_trusted_only_and_counts_pending_environments() {
    let (_config, store, admin, user) = setup();
    assert_eq!(store.service_install_status().unwrap()["openSessions"], 0);
    assert_eq!(
        store.service_install_status().unwrap()["liveEnvironments"],
        0
    );
    assert!(admin.service_install_status().is_err());
    assert!(user.service_install_status().is_err());
    let (r, p) = catalog(&admin);
    user.service_admit("installer-count", admission(), r, p, spec())
        .unwrap();
    let status = store.service_install_status().unwrap();
    assert_eq!(status["liveEnvironments"], 1);
    assert_eq!(status["pendingAdmissions"], 1);
    assert!(status.as_object().unwrap().keys().all(|k| [
        "schemaVersion",
        "openSessions",
        "liveEnvironments",
        "pendingAdmissions",
        "queuedTurns"
    ]
    .contains(&k.as_str())));
}
fn profile() -> Value {
    json!({"id":"shared@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared","allowedPrincipals":["alice"]})
}
fn release() -> Value {
    json!({"id":"coder@1","instructions":"Build games","tools":["read_file","write_file"],"runtimeProfiles":["shared@1"],"defaultRuntimeProfile":"shared@1","allowedPrincipals":["alice"]})
}
fn spec() -> Spec {
    Spec {
        provider: "openshell-k8s".into(),
        provider_identity: "local".into(),
        profile_digest: "accepted".into(),
        image: format!("runner@sha256:{}", "a".repeat(64)),
        workspace_root: "/sandbox".into(),
        sharing: Sharing::Shared,
        management: Management::Harness,
    }
}
fn admission() -> Admission {
    serde_json::from_value(json!({"agent":"coder@1","project":"p"})).unwrap()
}
fn catalog(a: &PostgresStore) -> (Entry, Entry) {
    let p = a.catalog_put("p", "profile", profile(), "install").unwrap();
    a.catalog_activate("pa", "profile", &p.id, None).unwrap();
    let r = a.catalog_put("r", "release", release(), "install").unwrap();
    a.catalog_activate("ra", "release", &r.id, Some("coder".into()))
        .unwrap();
    (r, p)
}
fn session(id: &str, binding: Binding) -> Session {
    let mut s:Session=serde_json::from_value(json!({"id":id,"name":"Game","workspace":"/sandbox","coworker":"test","bundle_digest":"test","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
    s.resource_policy.workspace = "/sandbox".into();
    s.resource_policy.digest = binding.fingerprint().unwrap();
    s.environment = Some(binding);
    s
}
#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn immutable_catalog_admission_ownership_and_one_dispatch() {
    let (c, store, a, u) = setup();
    assert!(u
        .catalog_put("forbidden", "profile", profile(), "install")
        .is_err());
    let (r, p) = catalog(&a);
    assert_eq!(u.catalog_get("release", "coder").unwrap().id, r.id);
    let mut changed = profile();
    changed["cpu"] = json!(2);
    assert!(a.catalog_put("p2", "profile", changed, "install").is_err());
    let mut forbidden_model = admission();
    forbidden_model.model = Some(openworker_runtime::connections::Choice::new(
        "not-allowed",
        "model",
    ));
    assert!(u
        .service_admit("bad-model", forbidden_model, r.clone(), p.clone(), spec())
        .is_err());
    let o = u
        .service_admit("create", admission(), r.clone(), p.clone(), spec())
        .unwrap();
    assert_eq!(o.status, "queued");
    assert_eq!(
        u.service_admit("create", admission(), r.clone(), p.clone(), spec())
            .unwrap()
            .id,
        o.id
    );
    let mut changed = admission();
    changed.project = "different".into();
    assert!(u
        .service_admit("create", changed, r.clone(), p.clone(), spec())
        .is_err());
    assert!(store
        .service_claim_for(Some("incompatible-worker-installation"))
        .unwrap()
        .is_none());
    let (_, first) = store.service_claim_for(Some("install")).unwrap().unwrap();
    assert!(first);
    assert!(store.service_claim().unwrap().is_none());
    let mut pg = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    pg.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    pg.execute(
        "UPDATE service_operations SET next_check=clock_timestamp() WHERE id=$1",
        &[&o.id],
    )
    .unwrap();
    let (reclaimed, again) = store.service_claim().unwrap().unwrap();
    assert_eq!(o.id, reclaimed.id);
    assert!(
        !again,
        "claim after a crash must never authorize another provider create"
    );
    let env = store.clone().environment(&o.environment_id).unwrap();
    let binding = Binding {
        environment_id: env.id.clone(),
        spec: env.spec.clone(),
        remote_id: "uid".into(),
        remote_name: "name".into(),
        workspace_scope: "default".into(),
        policy_hash: "policy".into(),
        policy_version: 1,
    };
    store
        .complete_environment(&env.id, env.revision, Some(binding.clone()))
        .unwrap();
    store
        .service_update_operation(&o.id, "ready", None)
        .unwrap();
    store
        .service_finish(&o.id, session("prepared", binding.clone()), "svc", "test")
        .unwrap();
    store
        .service_finish(&o.id, session("prepared", binding), "svc", "test")
        .unwrap();
    assert_eq!(
        u.read_snapshot(&o.session_id).unwrap()["session"]["id"],
        o.session_id
    );
    assert_eq!(u.service_operation(&o.id).unwrap().status, "complete");
    let bob = "bob-test-key-".repeat(5);
    a.auth_issue(
        "bob",
        serde_json::from_value(
            json!({"principal":"bob","role":"operator","scope":{"kind":"owned"}}),
        )
        .unwrap(),
        &bob,
    )
    .unwrap();
    let bob = store.authorized(&bob).unwrap();
    assert!(bob.service_operation(&o.id).is_err());
    assert!(bob.service_environment(&o.environment_id).is_err());
    assert!(bob.catalog_get("release", &r.id).is_err());
    let mut share = admission();
    share.environment_id = Some(o.environment_id.clone());
    let second = u
        .service_admit("share", share.clone(), r.clone(), p.clone(), spec())
        .unwrap();
    assert_eq!(second.environment_id, o.environment_id);
    assert_ne!(second.session_id, o.session_id);
    share.project = "other-project".into();
    assert!(u.service_admit("share-other", share, r, p, spec()).is_err());
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}
#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn admission_failure_rolls_back_environment_and_receipt() {
    let (c, store, a, u) = setup();
    let (r, p) = catalog(&a);
    let mut pg = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    pg.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    pg.batch_execute("CREATE FUNCTION fail_admission() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fault'; END $$; CREATE TRIGGER fail_admission BEFORE INSERT ON api_security_audit FOR EACH ROW EXECUTE FUNCTION fail_admission()").unwrap();
    assert!(u.service_admit("fail", admission(), r, p, spec()).is_err());
    for table in [
        "service_operations",
        "environments",
        "service_environment_owners",
    ] {
        assert_eq!(
            pg.query_one(&format!("SELECT count(*) FROM {table}"), &[])
                .unwrap()
                .get::<_, i64>(0),
            0
        );
    }
    drop(store);
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn profile_quota_and_alias_updates_do_not_change_existing_admissions() {
    let (c, _store, a, u) = setup();
    let (r, p) = catalog(&a);
    let first = u
        .service_admit("first", admission(), r.clone(), p.clone(), spec())
        .unwrap();
    let mut new = release();
    new["id"] = json!("coder@2");
    new["instructions"] = json!("Updated instructions");
    a.catalog_put("new-release", "release", new, "install")
        .unwrap();
    a.catalog_activate("new-alias", "release", "coder@2", Some("coder".into()))
        .unwrap();
    assert_eq!(u.catalog_get("release", "coder").unwrap().id, "coder@2");
    assert_eq!(
        u.service_operation(&first.id).unwrap().release_id,
        "coder@1"
    );
    for n in 1..16 {
        u.service_admit(
            &format!("quota-{n}"),
            admission(),
            r.clone(),
            p.clone(),
            spec(),
        )
        .unwrap();
    }
    let error = u
        .service_admit("over-limit", admission(), r.clone(), p.clone(), spec())
        .unwrap_err();
    assert_eq!(
        error.downcast_ref::<openworker_store_postgres::SubmissionError>(),
        Some(&openworker_store_postgres::SubmissionError::QueueFull)
    );
    assert_eq!(
        u.service_admit("first", admission(), r, p, spec())
            .unwrap()
            .id,
        first.id
    );
    let mut pg = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn combined_submission_commits_once_and_rolls_back_as_one_unit() {
    let (c, store, a, u) = setup();
    let (r, p) = catalog(&a);
    let mut input = admission();
    input.initial_message = Some("first task".into());
    let o = u
        .service_admit("combined", input.clone(), r.clone(), p.clone(), spec())
        .unwrap();
    assert!(o.run_id.is_some() && o.submission_id.is_some());
    assert!(u.read_snapshot(&o.session_id).is_err());
    let env = store.clone().environment(&o.environment_id).unwrap();
    let binding = Binding {
        environment_id: env.id.clone(),
        spec: env.spec.clone(),
        remote_id: "uid".into(),
        remote_name: "name".into(),
        workspace_scope: "default".into(),
        policy_hash: "policy".into(),
        policy_version: 1,
    };
    store
        .complete_environment(&env.id, env.revision, Some(binding.clone()))
        .unwrap();
    store
        .service_update_operation(&o.id, "ready", None)
        .unwrap();
    let mut pg = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    pg.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    pg.batch_execute("CREATE FUNCTION fail_initial_task() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fault'; END $$; CREATE TRIGGER fail_initial_task BEFORE INSERT ON submissions FOR EACH ROW EXECUTE FUNCTION fail_initial_task()").unwrap();
    assert!(store
        .service_finish(&o.id, session("prepared", binding.clone()), "svc", "test")
        .is_err());
    for table in ["sessions", "submissions", "creations"] {
        assert_eq!(
            pg.query_one(&format!("SELECT count(*) FROM {table}"), &[])
                .unwrap()
                .get::<_, i64>(0),
            0
        );
    }
    pg.batch_execute("DROP TRIGGER fail_initial_task ON submissions")
        .unwrap();
    let store = PostgresStore::open(c.clone()).unwrap();
    store
        .service_finish(&o.id, session("prepared", binding.clone()), "svc", "test")
        .unwrap();
    store
        .service_finish(&o.id, session("prepared", binding), "svc", "test")
        .unwrap();
    let user = store.authorized(&"user-test-key-".repeat(5)).unwrap();
    assert_eq!(
        user.service_run_id(&o.session_id, o.run_id.as_deref().unwrap())
            .unwrap()["status"],
        "running"
    );
    assert!(user.service_close(&o.session_id).is_err());
    assert_eq!(
        pg.query_one("SELECT count(*) FROM submissions", &[])
            .unwrap()
            .get::<_, i64>(0),
        1
    );
    let mut owner = PostgresStore::open(c.clone()).unwrap();
    owner.claim(&o.session_id).unwrap();
    let next = owner.next_turn(&o.session_id).unwrap().unwrap();
    let (mut state, _, _) = owner
        .accept(&o.session_id, &next.submission_id, &next.text)
        .unwrap();
    assert_eq!(state.turn.as_ref().unwrap().id, o.run_id.clone().unwrap());
    state.turn.as_mut().unwrap().status = "completed".into();
    owner
        .save(&state, "turn_completed", json!({"status":"completed"}))
        .unwrap();
    assert!(user.service_close(&o.session_id).is_err());
    owner.release(&o.session_id).unwrap();
    assert_eq!(user.service_close(&o.session_id).unwrap()["closed"], true);
    assert_eq!(user.service_close(&o.session_id).unwrap()["closed"], true);
    let replay = user.service_admit("combined", input, r, p, spec()).unwrap();
    assert_eq!(replay.run_id, o.run_id);
    assert_eq!(replay.receipt.unwrap()["runId"], o.run_id.unwrap());
    let a = store.authorized(&"admin-test-key-".repeat(5)).unwrap();
    let delete_key = "11111111111111111111111111111111";
    let (deleting, fresh) = a
        .begin_environment_delete(&o.environment_id, delete_key, "admin")
        .unwrap();
    assert!(fresh);
    assert!(user
        .service_queue_delete(&o.environment_id, delete_key)
        .is_err());
    a.service_queue_delete(&o.environment_id, delete_key)
        .unwrap();
    a.service_queue_delete(&o.environment_id, delete_key)
        .unwrap();
    assert!(a.service_claim_delete("install").is_err());
    assert!(store
        .service_claim_delete("other-installation")
        .unwrap()
        .is_none());
    let (_, k, _, dispatch) = store.service_claim_delete("install").unwrap().unwrap();
    assert!(dispatch);
    assert_eq!(k, delete_key);
    assert!(store.service_claim_delete("install").unwrap().is_none());
    pg.batch_execute("UPDATE service_deletions SET next_check=clock_timestamp()")
        .unwrap();
    let (_, _, _, dispatch) = store.service_claim_delete("install").unwrap().unwrap();
    assert!(!dispatch, "lost delete must never dispatch twice");
    store
        .complete_environment_delete(&o.environment_id, delete_key, deleting.revision)
        .unwrap();
    assert!(store.service_claim_delete("install").unwrap().is_none());
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn configuration_revisions_pause_and_pin_admission_defaults() {
    use openworker_store_postgres::service_admin::Configuration;
    let (c, store, a, u) = setup();
    let (r, p) = catalog(&a);
    let mut cfg = Configuration::default();
    cfg.compaction.context_window = 64000;
    assert!(u.service_configure("denied", 1, cfg.clone()).is_err());
    assert_eq!(
        a.service_configure("edit-1", 1, cfg.clone())
            .unwrap()
            .revision,
        2
    );
    let op = u
        .service_admit("first", admission(), r.clone(), p.clone(), spec())
        .unwrap();
    assert_eq!(
        store
            .service_operation_configuration(&op.id)
            .unwrap()
            .configuration
            .compaction
            .context_window,
        64000
    );
    cfg.admissions_paused = true;
    cfg.compaction.context_window = 32000;
    assert!(a.service_configure("stale", 1, cfg.clone()).is_err());
    a.service_configure("pause", 2, cfg.clone()).unwrap();
    assert!(u
        .service_admit("new", admission(), r.clone(), p.clone(), spec())
        .is_err());
    assert_eq!(
        u.service_admit("first", admission(), r.clone(), p.clone(), spec())
            .unwrap()
            .id,
        op.id
    );
    assert_eq!(
        store
            .service_operation_configuration(&op.id)
            .unwrap()
            .configuration
            .compaction
            .context_window,
        64000
    );
    cfg.admissions_paused = false;
    a.service_configure("resume", 3, cfg.clone()).unwrap();
    assert_eq!(
        a.service_configure("resume", 3, cfg.clone())
            .unwrap()
            .revision,
        4
    );
    cfg.max_environments = 0;
    assert!(a.service_configure("invalid", 4, cfg).is_err());
    let mut pg = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn pool_ownership_revisions_and_observed_state_do_not_fight_infrastructure() {
    use openworker_store_postgres::service_pools::{Binding, Ownership};
    let (c, store, a, u) = setup();
    let binding = Binding {
        id: "coder".into(),
        namespace: "ow-test".into(),
        deployment: "coder".into(),
        uid: "original".into(),
        ownership: Ownership::Manual,
        max_replicas: 4,
    };
    assert!(a.service_bind_pool(binding.clone(), 1).is_err());
    store.service_bind_pool(binding.clone(), 1).unwrap();
    assert!(u.service_scale_pool("coder", "denied", 1, 2).is_err());
    assert!(a.service_scale_pool("coder", "oversized", 1, 5).is_err());
    let desired = a.service_scale_pool("coder", "scale", 1, 3).unwrap();
    assert_eq!(desired.revision, 2);
    assert_eq!(desired.observed["status"], "pending");
    assert_eq!(
        store
            .service_bind_pool(binding.clone(), 1)
            .unwrap()
            .desired_replicas,
        3
    );
    assert_eq!(
        a.service_scale_pool("coder", "scale", 1, 3)
            .unwrap()
            .revision,
        2
    );
    assert!(a.service_scale_pool("coder", "conflict", 1, 2).is_err());
    store
        .service_observe_pool("coder", 1, json!({"status":"applied"}))
        .unwrap();
    assert_eq!(a.service_pools().unwrap()[0].observed["status"], "pending");
    let mut replacement = binding.clone();
    replacement.uid = "replacement".into();
    assert!(store.service_bind_pool(replacement, 1).is_err());
    store
        .service_observe_pool("coder", 2, json!({"status":"applied"}))
        .unwrap();
    assert_eq!(a.service_pools().unwrap()[0].observed["status"], "applied");
    let mut stale = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    stale.batch_execute(&format!("UPDATE {}.service_pools SET observed=jsonb_set(observed,'{{checkedAt}}','1') WHERE id='coder'",c.schema)).unwrap();
    assert_eq!(a.service_pools().unwrap()[0].observed["status"], "unknown");
    for owner in [Ownership::Autoscaled, Ownership::Gitops] {
        let mut b = binding.clone();
        b.id = format!("owned-{owner:?}");
        b.ownership = owner;
        store.service_bind_pool(b.clone(), 1).unwrap();
        assert!(a.service_scale_pool(&b.id, "override", 1, 2).is_err());
    }
    let mut pg = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn credential_expiring_during_provisioning_cannot_commit_a_session() {
    let (c, store, a, u) = setup();
    let (r, p) = catalog(&a);
    let o = u
        .service_admit("expiry-admission", admission(), r, p, spec())
        .unwrap();
    let e = store.clone().environment(&o.environment_id).unwrap();
    let binding = Binding {
        environment_id: e.id.clone(),
        spec: e.spec.clone(),
        remote_id: "original".into(),
        remote_name: "name".into(),
        workspace_scope: "default".into(),
        policy_hash: "policy".into(),
        policy_version: 1,
    };
    store
        .complete_environment(&e.id, e.revision, Some(binding.clone()))
        .unwrap();
    store
        .service_update_operation(&o.id, "ready", None)
        .unwrap();
    let mut pg = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    pg.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    pg.batch_execute("UPDATE api_credentials SET spec=jsonb_set(spec,'{expires_at}',to_jsonb(1::bigint)) WHERE spec->>'principal'='alice'").unwrap();
    assert!(store
        .service_finish(&o.id, session("prepared", binding), "svc", "test")
        .is_err());
    assert_eq!(
        pg.query_one("SELECT count(*) FROM sessions", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn visible_catalog_matches_individual_read_authority() {
    let (_c, root, a, alice) = setup();
    catalog(&a);
    let mut draft = release();
    draft["id"] = json!("draft@1");
    a.catalog_put("draft", "release", draft, "install").unwrap();
    let mut hidden = release();
    hidden["id"] = json!("hidden@1");
    hidden["allowedPrincipals"] = json!(["bob"]);
    a.catalog_put("hidden", "release", hidden, "install")
        .unwrap();
    a.catalog_activate("hidden-active", "release", "hidden@1", None)
        .unwrap();
    assert_eq!(
        alice
            .catalog_visible("release")
            .unwrap()
            .iter()
            .map(|e| e.id.as_str())
            .collect::<Vec<_>>(),
        vec!["coder@1"]
    );
    assert_eq!(a.catalog_visible("release").unwrap().len(), 3);
    let publisher = "publisher-key-".repeat(5);
    a.auth_issue(
        "publisher",
        serde_json::from_value(
            json!({"principal":"publisher","role":"publisher","scope":{"kind":"all"}}),
        )
        .unwrap(),
        &publisher,
    )
    .unwrap();
    let p = root.authorized(&publisher).unwrap();
    assert_eq!(p.catalog_visible("release").unwrap().len(), 3);
    assert!(
        p.catalog_list().is_err(),
        "Private worker catalog enumeration remains admin-only"
    );
    assert!(alice.catalog_visible("unknown").is_err());
}
