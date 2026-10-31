//! PostgreSQL outbox contracts, not provider qualification. Only these isolated
//! fixtures enable the trusted placement gate; production ingress keeps it off.
use super::*;
use openworker_runtime::{
    environment::{Phase, Spec},
    team_definition::Delegation,
    team_type_catalog::Catalog,
};
use openworker_store_postgres::service_team_placements::{Intent, Job};

async fn sql(f: &Fixture, statement: &str) {
    let db = f.db.clone();
    let statement = statement.to_owned();
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!("SET search_path TO {};{}", db.schema, statement))
            .unwrap();
    })
    .await
    .unwrap();
}
async fn fixture() -> (Fixture, Entry, Spec) {
    let mut f = Fixture::new().await;
    let mut doc = f.create();
    let delegation:Delegation=serde_json::from_value(json!({"tools":["coding.read_file"],"placements":["team","member","new"],"profiles":{"team@1":{"files":[],"network":[],"credentials":[],"cpu":1,"memory_mb":512}}})).unwrap();
    doc.type_catalog =
        Some(Catalog::new(delegation.bind(&f.lead).unwrap(), ["builder".into()].into()).unwrap());
    doc.placement_selection = true;
    let db = f.db.clone();
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        pg.execute(
            &format!("UPDATE {}.agent_teams SET data=$2 WHERE id=$1", db.schema),
            &[&doc.id, &json!(doc)],
        )
        .unwrap();
    })
    .await
    .unwrap();
    let target = f.store.catalog_get("profile", "team@1").unwrap();
    let spec = management::provider(&f.config, &target)
        .unwrap()
        .spec(Sharing::Shared, Management::Harness);
    (f, target, spec)
}
fn spawn(f: &Fixture, id: &str) -> Value {
    f.user.agent_team_api(&f.lead.id,"command",json!({"request_id":format!("spawn-{id}"),"operation":"team_spawn","arguments":{"agent_type":"builder","agent_id":id,"message":"Inspect isolated workspace","sandbox":{"mode":"new","profile":"team@1"}}})).unwrap()
}
fn reserve(f: &Fixture, target: &Entry, spec: &Spec, id: &str) -> Intent {
    spawn(f, id);
    f.store
        .reserve_team_environment(&f.lead.id, id, target.clone(), spec.clone())
        .unwrap()
}
fn binding(job: &Job) -> Binding {
    Binding {
        environment_id: job.environment.id.clone(),
        spec: job.environment.spec.clone(),
        remote_id: "child-original-uid".into(),
        remote_name: "child-original-name".into(),
        workspace_scope: "default".into(),
        policy_hash: "original-policy".into(),
        policy_version: 1,
    }
}
fn installation(f: &Fixture) -> String {
    management::installation(&f.config).unwrap()
}
async fn expire(f: &Fixture) {
    sql(
        f,
        "UPDATE service_team_environments SET next_check=clock_timestamp()-interval '1 second'",
    )
    .await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_outbox_identity_crash_recovery_and_stale_receipts() {
    let (f, target, spec) = fixture().await;
    let intent = reserve(&f, &target, &spec, "one");
    assert_ne!(
        intent.environment_id,
        f.lead.environment.as_ref().unwrap().environment_id
    );
    assert_eq!(
        intent,
        f.store
            .reserve_team_environment(&f.lead.id, "one", target.clone(), spec.clone())
            .unwrap()
    );
    assert!(f
        .user
        .reserve_team_environment(&f.lead.id, "one", target, spec)
        .is_err());
    assert!(f.user.claim_team_environment(&installation(&f)).is_err());
    assert!(f
        .store
        .claim_team_environment("wrong-installation")
        .unwrap()
        .is_none());
    let original = f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .unwrap();
    assert!(original.dispatch);
    assert!(f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .is_none());
    // Crash after dispatch was committed: do not replay create, even when there
    // is no acknowledged provider UID. Recover the original environment only.
    expire(&f).await;
    assert!(f
        .store
        .observe_team_environment(&original, Some(binding(&original)))
        .is_err());
    let recovered = PostgresStore::open(f.db.clone()).unwrap();
    let job = recovered
        .claim_team_environment(&installation(&f))
        .unwrap()
        .unwrap();
    assert!(!job.dispatch);
    assert_eq!(job.intent, intent);
    assert_ne!(job.claim, original.claim);
    assert!(recovered
        .observe_team_environment(&original, Some(binding(&original)))
        .is_err());
    let mut wrong = binding(&job);
    wrong.environment_id = "env-other".into();
    assert!(recovered
        .observe_team_environment(&job, Some(wrong))
        .is_err());
    let unknown = recovered.observe_team_environment(&job, None).unwrap();
    assert_eq!(unknown.phase, Phase::Unknown);
    assert!(recovered
        .observe_team_environment(&job, Some(binding(&job)))
        .is_err());
    expire(&f).await;
    let job = recovered
        .claim_team_environment(&installation(&f))
        .unwrap()
        .unwrap();
    assert!(!job.dispatch);
    assert_eq!(job.environment.id, intent.environment_id);
    let ready = recovered
        .observe_team_environment(&job, Some(binding(&job)))
        .unwrap();
    assert_eq!(ready.phase, Phase::Ready);
    assert_eq!(
        ready.binding.as_ref().unwrap().remote_id,
        "child-original-uid"
    );
    assert!(recovered
        .claim_team_environment(&installation(&f))
        .unwrap()
        .is_none());
    let mut replacement = ready.binding.clone().unwrap();
    replacement.remote_id = "replacement-uid".into();
    assert!(recovered
        .complete_environment(&ready.id, ready.revision, Some(replacement))
        .is_err());
    // Observing a provider does not itself authorize a child model/tool turn.
    assert!(recovered
        .provision_agent_team_child_for(Some(&installation(&f)))
        .is_err());
    assert_eq!(
        f.store.clone().metadata(&f.lead.id).unwrap().environment,
        f.lead.environment
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_outbox_retired_before_dispatch_releases_only_proven_unstarted_quota() {
    let (f, target, spec) = fixture().await;
    let c = f.admin.service_configuration().unwrap();
    let mut config = c.configuration;
    config.max_environments_per_principal = 2;
    f.admin
        .service_configure("quota", c.revision, config)
        .unwrap();
    let one = reserve(&f, &target, &spec, "one");
    spawn(&f, "two");
    assert!(f
        .store
        .reserve_team_environment(&f.lead.id, "two", target.clone(), spec.clone())
        .is_err());
    f.user.agent_team_api(&f.lead.id,"command",json!({"request_id":"retire","operation":"team_manage","arguments":{"action":"retire","agent_id":"one","mode":"cancel"}})).unwrap();
    assert!(f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .is_none());
    assert_eq!(
        f.store.environment(&one.environment_id).unwrap().phase,
        Phase::Deleted
    );
    let second = f
        .store
        .reserve_team_environment(&f.lead.id, "two", target, spec)
        .unwrap();
    assert_ne!(one.environment_id, second.environment_id);
    assert_eq!(
        f.store
            .environment(&f.lead.environment.as_ref().unwrap().environment_id)
            .unwrap()
            .phase,
        Phase::Ready
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_outbox_pause_and_revocation_before_dispatch_but_inspection_after_dispatch() {
    let (f, target, spec) = fixture().await;
    let first = reserve(&f, &target, &spec, "one");
    let c = f.admin.service_configuration().unwrap();
    let mut config = c.configuration;
    config.admissions_paused = true;
    let c = f
        .admin
        .service_configure("pause", c.revision, config)
        .unwrap();
    assert!(f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .is_none());
    assert_eq!(
        f.store.environment(&first.environment_id).unwrap().phase,
        Phase::Provisioning
    );
    let mut config = c.configuration;
    config.admissions_paused = false;
    f.admin
        .service_configure("unpause", c.revision, config)
        .unwrap();
    let original = f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .unwrap();
    assert!(original.dispatch);
    // The same original credential is revoked for both reservations. This is a
    // DB fixture mutation; no real user's token is changed or exposed.
    reserve(&f, &target, &spec, "two");
    sql(
        &f,
        "UPDATE api_credentials SET revoked=TRUE WHERE spec->>'principal'='alice'",
    )
    .await;
    assert!(f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .is_none());
    expire(&f).await;
    let recovered = f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .unwrap();
    assert!(!recovered.dispatch);
    assert_eq!(recovered.intent, first);
    assert_eq!(
        f.store
            .observe_team_environment(&recovered, Some(binding(&recovered)))
            .unwrap()
            .phase,
        Phase::Ready
    );
    assert!(f
        .store
        .reserve_team_environment(&f.lead.id, "one", target, spec)
        .is_err());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_outbox_concurrent_quota_reservation_and_single_dispatch() {
    let (f, target, spec) = fixture().await;
    let c = f.admin.service_configuration().unwrap();
    let mut config = c.configuration;
    config.max_environments_per_principal = 2;
    f.admin
        .service_configure("quota", c.revision, config)
        .unwrap();
    spawn(&f, "one");
    spawn(&f, "two");
    let reserve = |agent: &str| {
        let store = PostgresStore::open(f.db.clone()).unwrap();
        let lead = f.lead.id.clone();
        let agent = agent.to_owned();
        let target = target.clone();
        let spec = spec.clone();
        tokio::task::spawn_blocking(move || {
            store.reserve_team_environment(&lead, &agent, target, spec)
        })
    };
    let (a, b) = tokio::join!(reserve("one"), reserve("two"));
    let (a, b) = (a.unwrap(), b.unwrap());
    assert_ne!(a.is_ok(), b.is_ok());
    let original = a.or(b).unwrap();
    let claim = || {
        let store = PostgresStore::open(f.db.clone()).unwrap();
        let installed = installation(&f);
        tokio::task::spawn_blocking(move || store.claim_team_environment(&installed).unwrap())
    };
    let (a, b) = tokio::join!(claim(), claim());
    let (a, b) = (a.unwrap(), b.unwrap());
    assert_ne!(a.is_some(), b.is_some());
    let job = a.or(b).unwrap();
    assert!(job.dispatch);
    assert_eq!(job.intent, original);
    f.store.observe_team_environment(&job, None).unwrap();
    // Unknown provider work continues to consume quota. Never free a resource
    // because a transport stopped reporting its outcome.
    let other = if original.agent == "one" {
        "two"
    } else {
        "one"
    };
    assert!(f
        .store
        .reserve_team_environment(&f.lead.id, other, target, spec)
        .is_err());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_outbox_rejects_changed_profile_and_foreign_deployment_before_allocation() {
    let (f, target, spec) = fixture().await;
    spawn(&f, "one");
    let mut foreign = spec.clone();
    foreign.provider_identity = "another-gateway".into();
    assert!(f
        .store
        .reserve_team_environment(&f.lead.id, "one", target.clone(), foreign)
        .is_err());
    let mut changed = target.clone();
    changed.digest = "changed".into();
    assert!(f
        .store
        .reserve_team_environment(&f.lead.id, "one", changed, spec.clone())
        .is_err());
    let mut changed = target.clone();
    changed.id = "mutable-alias".into();
    assert!(f
        .store
        .reserve_team_environment(&f.lead.id, "one", changed, spec.clone())
        .is_err());
    let intent = f
        .store
        .reserve_team_environment(&f.lead.id, "one", target, spec)
        .unwrap();
    assert_eq!(
        f.store
            .environment(&intent.environment_id)
            .unwrap()
            .revision,
        1
    );
}
