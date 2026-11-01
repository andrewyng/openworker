//! PostgreSQL outbox contracts, not provider qualification. Only these isolated
//! fixtures use the normal trusted service configuration, with synthetic
//! provider observations. Physical isolation is qualified by service_v2.
use super::*;
use openworker_runtime::environment::{Phase, Spec};
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
    f.create_with_delegation(json!({"tools":["coding.read_file"],"models":[Choice::new("other","delegated")],"placements":["team","member","new"],"profiles":{"team@1":{"files":[],"network":[],"credentials":[],"cpu":1,"memory_mb":512}}}));
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
    assert!(!recovered
        .provision_agent_team_child_for(Some(&installation(&f)))
        .unwrap());
    assert!(recovered.bind_team_placement(&f.lead.id, "one").unwrap());
    assert!(recovered
        .provision_agent_team_child_for(Some(&installation(&f)))
        .unwrap());
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

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_binding_member_reuse_retirement_and_original_cleanup() {
    let (f, target, spec) = fixture().await;
    let child = spawn(&f, "one");
    let sid = child["instance"]["session_id"].as_str().unwrap();
    let intent = f
        .store
        .reserve_team_environment(&f.lead.id, "one", target, spec)
        .unwrap();
    let job = f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .unwrap();
    f.store
        .observe_team_environment(&job, Some(binding(&job)))
        .unwrap();
    // A Ready provider with a still-reserved child is a lifecycle reference.
    assert!(f
        .store
        .begin_environment_delete(&intent.environment_id, "delete", "alice")
        .is_err());
    assert!(f.store.bind_team_placement(&f.lead.id, "one").unwrap());
    assert!(f
        .store
        .provision_agent_team_child_for(Some(&installation(&f)))
        .unwrap());
    let actual = f.store.inspect(sid).unwrap();
    assert_eq!(actual.environment, Some(binding(&job)));
    assert_eq!(actual.workspace, "/sandbox");
    assert_ne!(actual.environment, f.lead.environment);
    let mut owner = f.store.clone();
    owner.claim(sid).unwrap();
    let mut widened = owner.metadata(sid).unwrap();
    widened.environment = f.lead.environment.clone();
    widened.resource_policy.digest = widened.environment.as_ref().unwrap().fingerprint().unwrap();
    assert!(owner
        .save(&widened, "invalid-placement", json!({}))
        .is_err());
    f.user.agent_team_api(&f.lead.id,"command",json!({"request_id":"member","operation":"team_spawn","arguments":{"agent_type":"builder","agent_id":"two","message":"Verify in existing sandbox","sandbox":{"mode":"member","agent_id":"one"}}})).unwrap();
    assert!(f.store.bind_team_placement(&f.lead.id, "two").unwrap());
    // Retire the original user of this resource; its already-bound verifier
    // still owns a reservation reference. Nothing is implicitly deleted.
    f.user.agent_team_api(&f.lead.id,"command",json!({"request_id":"retire-one","operation":"team_manage","arguments":{"action":"retire","agent_id":"one","mode":"cancel"}})).unwrap();
    f.store.reconcile_stopped_agent_team().unwrap();
    let mut state = f.store.clone();
    let mut actual = state.metadata(sid).unwrap();
    actual.closed = true;
    state
        .save(&actual, "fixture_cleanup_complete", json!({}))
        .unwrap();
    // Simulate process loss after session closure but before roster settlement.
    assert!(state.reconcile_unstarted_retirement().unwrap());
    assert!(f
        .store
        .begin_environment_delete(&intent.environment_id, "delete", "alice")
        .is_err());
    // A pending member never resurrects a retired source; this one already has
    // its pinned binding and survives the source's retirement unchanged.
    assert!(f
        .store
        .provision_agent_team_child_for(Some(&installation(&f)))
        .unwrap());
    let doc = f
        .user
        .agent_team_api(&f.lead.id, "snapshot", json!({}))
        .unwrap();
    let second = f
        .store
        .inspect(
            doc["roster"]["instances"]["two"]["session_id"]
                .as_str()
                .unwrap(),
        )
        .unwrap();
    assert_eq!(second.environment, Some(binding(&job)));
    assert!(f
        .store
        .begin_environment_delete(&intent.environment_id, "delete", "alice")
        .is_err());
    assert_eq!(
        f.store.environment(&intent.environment_id).unwrap().phase,
        Phase::Ready
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_admission_cannot_deadlock_turn_submission_on_security_audit() {
    let f = Fixture::new().await;
    let mut config = f.db.clone();
    config.timeout = std::time::Duration::from_secs(10);
    let caller = PostgresStore::open(config)
        .unwrap()
        .authorized(&"test-user-".repeat(6))
        .unwrap();
    let db = f.db.clone();
    let sid = f.lead.id.clone();
    tokio::task::spawn_blocking(move||{
        let mut pg=postgres::Client::connect(&db.connection,postgres::NoTls).unwrap();
        pg.batch_execute(&format!("SET search_path TO {}",db.schema)).unwrap();
        let mut tx=pg.transaction().unwrap();
        tx.query_one("SELECT id FROM sessions WHERE id=$1 FOR UPDATE",&[&sid]).unwrap();
        let submitted=std::thread::spawn(move||caller.submit_service_turn(openworker_store_postgres::TurnSubmission{session_id:sid,submission_id:"audit-order".into(),text:"No execution in this fixture".into(),limits:None}));
        let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);
        loop {
            tx.simple_query("SELECT pg_stat_clear_snapshot()").unwrap();
            let waiting:bool=tx.query_one("SELECT EXISTS(SELECT 1 FROM pg_stat_activity a WHERE pg_backend_pid()=ANY(pg_blocking_pids(a.pid)) AND a.wait_event_type='Lock')",&[]).unwrap().get(0);
            if waiting{break}
            assert!(std::time::Instant::now()<deadline,"Submission never reached the held session lock");
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        // Emulate trusted child admission: lead is already locked, append audit
        // next. The API waiting for that lead must NOT own the audit lock.
        let free:bool=tx.query_one("SELECT pg_try_advisory_xact_lock(hashtext(current_schema()),hashtext('security-audit-append-v1'))",&[]).unwrap().get(0);
        tx.commit().unwrap();
        let result=submitted.join().unwrap();
        assert!(free,"Audit/session lock order would deadlock child admission");
        assert!(result.is_ok(),"{result:?}");
    }).await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_controller_distinguishes_sanitized_storage_outage_from_denial() {
    let f = Fixture::new().await;
    // Destroy only the fixture's own environment table, never a shared schema.
    sql(&f, "DROP TABLE environments CASCADE").await;
    let error = f.store.environment("missing").unwrap_err();
    assert!(error
        .downcast_ref::<openworker_store_postgres::StorageUnavailable>()
        .is_some());
    assert_eq!(
        error.to_string(),
        "PostgreSQL operation failed; outcome may be unknown. Reconnect and reconcile durable IDs."
    );
    let poisoned = f.store.environment("missing").unwrap_err();
    assert!(poisoned
        .downcast_ref::<openworker_store_postgres::StorageUnavailable>()
        .is_some());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway/model traffic"]
async fn placement_revoked_after_binding_never_admits_child_or_destroys_retained_work() {
    let (f, target, spec) = fixture().await;
    let child = spawn(&f, "one");
    let sid = child["instance"]["session_id"].as_str().unwrap();
    let intent = f
        .store
        .reserve_team_environment(&f.lead.id, "one", target, spec)
        .unwrap();
    let job = f
        .store
        .claim_team_environment(&installation(&f))
        .unwrap()
        .unwrap();
    f.store
        .observe_team_environment(&job, Some(binding(&job)))
        .unwrap();
    f.store.bind_team_placement(&f.lead.id, "one").unwrap();
    sql(
        &f,
        "UPDATE api_credentials SET revoked=TRUE WHERE spec->>'principal'='alice'",
    )
    .await;
    assert!(f
        .store
        .provision_agent_team_child_for(Some(&installation(&f)))
        .unwrap());
    assert!(f.store.inspect(sid).is_err());
    let roster = f
        .admin
        .agent_team_api(&f.lead.id, "snapshot", json!({}))
        .unwrap();
    assert_eq!(roster["roster"]["instances"]["one"]["phase"], "closed");
    assert_eq!(
        f.store.environment(&intent.environment_id).unwrap().phase,
        Phase::Ready
    );
    // Explicit cleanup, after all references are closed, uses the original
    // deletion queue/profile and can never authorize creation as a side effect.
    f.store
        .begin_environment_delete(&intent.environment_id, "cleanup", "alice")
        .unwrap();
    f.admin
        .service_queue_delete(&intent.environment_id, "cleanup")
        .unwrap();
    assert!(f
        .store
        .service_claim_delete("other-installation")
        .unwrap()
        .is_none());
    let (record, key, profile, dispatch) = f
        .store
        .service_claim_delete(&installation(&f))
        .unwrap()
        .unwrap();
    assert_eq!(record.id, intent.environment_id);
    assert_eq!(key, "cleanup");
    assert_eq!(profile, "team@1");
    assert!(dispatch);
    f.store
        .complete_environment_delete(&record.id, &key, record.revision)
        .unwrap();
    assert_eq!(
        f.store.environment(&record.id).unwrap().phase,
        Phase::Deleted
    );
}
