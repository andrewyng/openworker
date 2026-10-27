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
