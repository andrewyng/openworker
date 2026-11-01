//! Real PG fencing/catalog tests. Synthetic bundle bytes and identity receipts
//! test storage admission only, NOT Git integrity or physical sandbox isolation.
use super::*;
use openworker_store_postgres::service_team_imports::Job;

async fn staged() -> (Fixture, openworker_artifacts::Store) {
    let mut f = Fixture::new().await;
    std::fs::create_dir(f._root.path().join("artifacts")).unwrap();
    let backend = openworker_artifacts::filesystem::Filesystem::open(
        &f._root.path().join("artifacts"),
        "import-tests",
        false,
        false,
    )
    .unwrap();
    f.try_create_with_source_binding(
        json!({"tools":["coding.read_file"],"models":[Choice::new("other","delegated")],"placements":["team"],"profiles":{}}),
        Some(json!({"source":coding_source(),"agent_types":{"builder":{"sandbox":{"mode":"team"},"source":"initial"}}})),
        json!(backend.binding()),
    ).unwrap();
    let installed = management::installation(&f.config).unwrap();
    f.spawn("builder", "one", None);
    assert!(f.store.claim_team_import(&installed).unwrap().is_none()); // No unverified source.
    let first = f.store.claim_team_source(&installed).unwrap().unwrap();
    let bytes = b"synthetic bundle; private storage contract only";
    f.store
        .observe_team_source(&first, Some(source_bundle(&first.intent, bytes)))
        .unwrap();
    expire_source(&f);
    let upload = f.store.claim_team_source(&installed).unwrap().unwrap();
    let a = f.store.register_team_source_artifact(&upload).unwrap();
    let proof = backend
        .put(&a, &bytes[..], &tokio_util::sync::CancellationToken::new())
        .await
        .unwrap();
    f.store
        .publish_team_source_artifact(&upload, &a.id, proof)
        .unwrap();
    f.store.complete_team_source(&upload, &a.id).unwrap();
    (f, backend)
}
fn sql(f: &Fixture, statement: &str) {
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&f.db.connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!("SET search_path={},pg_catalog", f.db.schema))
            .unwrap();
        pg.batch_execute(statement).unwrap();
    })
}
fn expire(f: &Fixture) {
    sql(
        f,
        "UPDATE service_team_imports SET next_check=clock_timestamp()-interval '1 second'",
    )
}
fn receipt(job: &Job) -> openworker_runtime::team_workspace::Receipt {
    serde_json::from_value(json!({"request":job.intent.workspace,"path":format!("{}/{}",job.intent.root,job.intent.transfer.request.directory),"project_root":job.intent.root,"identity":{"volume":1,"file":2},"repository":null,"git_dir":null,"git_identity":null,"branch":null,"initial_commit":job.intent.transfer.manifest.submitted_commit,"independent_git":{"volume":1,"file":3}})).unwrap()
}
fn notices(f: &Fixture) -> i64 {
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&f.db.connection, postgres::NoTls).unwrap();
        pg.query_one(
            &format!(
                "SELECT count(*) FROM {}.agent_team_messages WHERE data->>'sender'='harness'",
                f.db.schema
            ),
            &[],
        )
        .unwrap()
        .get(0)
    })
}
fn document(f: &Fixture) -> Document {
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&f.db.connection, postgres::NoTls).unwrap();
        serde_json::from_value(
            pg.query_one(
                &format!("SELECT data FROM {}.agent_teams", f.db.schema),
                &[],
            )
            .unwrap()
            .get(0),
        )
        .unwrap()
    })
}
fn child(f: &Fixture, id: &str) -> Session {
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&f.db.connection, postgres::NoTls).unwrap();
        serde_json::from_value(
            pg.query_one(
                &format!("SELECT data FROM {}.sessions WHERE id=$1", f.db.schema),
                &[&id],
            )
            .unwrap()
            .get(0),
        )
        .unwrap()
    })
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn child_source_import_has_one_prepare_one_checkout_and_atomic_admission_after_recovery() {
    let (f, _) = staged().await;
    let installed = management::installation(&f.config).unwrap();
    assert!(f.user.claim_team_import(&installed).is_err());
    let first = f.store.claim_team_import(&installed).unwrap().unwrap();
    assert!(first.prepare && !first.checkout_dispatched);
    assert!(uuid::Uuid::parse_str(&first.claim).is_ok());
    assert_eq!(
        first.intent.transfer.manifest.submitted_commit,
        coding_source().commit
    );
    assert!(f.store.claim_team_import(&installed).unwrap().is_none());
    assert!(f
        .store
        .complete_team_import(&first, receipt(&first))
        .is_err());
    assert!(!f
        .store
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    f.store.defer_team_import(&first).unwrap();
    assert_eq!(notices(&f), 1);
    expire(&f);
    let replacement = PostgresStore::open(f.db.clone()).unwrap();
    let recovered = replacement.claim_team_import(&installed).unwrap().unwrap();
    assert!(!recovered.prepare && !recovered.checkout_dispatched);
    assert_eq!(recovered.intent, first.intent);
    assert!(f.store.check_team_import(&first).is_err());
    let checkout = replacement
        .dispatch_team_import_checkout(&recovered)
        .unwrap();
    assert!(checkout.checkout_dispatched);
    assert!(replacement
        .dispatch_team_import_checkout(&checkout)
        .is_err());
    assert!(replacement.check_team_import(&recovered).is_err());
    replacement.defer_team_import(&checkout).unwrap();
    assert_eq!(notices(&f), 1); // Same original unknown operation, no inbox spam.
    expire(&f);
    let inspected = f.store.claim_team_import(&installed).unwrap().unwrap();
    assert!(!inspected.prepare && inspected.checkout_dispatched);
    assert_eq!(inspected.intent, first.intent);
    let mut wrong = receipt(&inspected);
    wrong.initial_commit = Some("b".repeat(40));
    assert!(f.store.complete_team_import(&inspected, wrong).is_err());
    assert!(!f
        .store
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    f.store
        .complete_team_import(&inspected, receipt(&inspected))
        .unwrap();
    assert!(f.store.claim_team_import(&installed).unwrap().is_none());
    assert!(f
        .store
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    let d = document(&f);
    let child = child(&f, &d.roster.instance("one").unwrap().session_id);
    assert_eq!(child.managed_workspace, Some(receipt(&inspected)));
    assert_eq!(
        child.workspace,
        format!(
            "{}/{}",
            inspected.intent.root, inspected.intent.transfer.request.directory
        )
    );
    assert!(f
        .store
        .complete_team_import(&inspected, receipt(&inspected))
        .is_err());
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn child_import_revocation_blocks_original_operation_and_never_substitutes_artifacts() {
    let (f, _) = staged().await;
    let installed = management::installation(&f.config).unwrap();
    let first = f.store.claim_team_import(&installed).unwrap().unwrap();
    let mut wrong = first.clone();
    wrong.intent.artifact.binding.identity = "b".repeat(64);
    assert!(f.store.check_team_import(&wrong).is_err());
    wrong = first.clone();
    wrong.intent.environment.remote_id = "replacement".into();
    assert!(f.store.check_team_import(&wrong).is_err());
    sql(&f,"UPDATE api_credentials SET revoked=TRUE WHERE id=(SELECT credential_id FROM service_operations LIMIT 1)");
    assert!(f.store.check_team_import(&first).is_err());
    assert!(f.store.dispatch_team_import_checkout(&first).is_err());
    f.store.defer_team_import(&first).unwrap();
    assert_eq!(notices(&f), 1);
    expire(&f);
    assert!(f.store.claim_team_import(&installed).unwrap().is_none());
    sql(&f, "UPDATE api_credentials SET revoked=FALSE");
    assert!(f.store.claim_team_import(&installed).unwrap().is_none()); // No automatic resurrection.
    assert!(!f
        .store
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    assert!(f
        .store
        .complete_team_import(&first, receipt(&first))
        .is_err());
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn import_ready_failure_rolls_back_source_receipt_and_retirement_rejects_late_results() {
    let (mut f, _) = staged().await;
    let installed = management::installation(&f.config).unwrap();
    let first = f.store.claim_team_import(&installed).unwrap().unwrap();
    let job = f.store.dispatch_team_import_checkout(&first).unwrap();
    sql(&f,"CREATE FUNCTION reject_import() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.status='ready' THEN RAISE EXCEPTION 'synthetic import completion failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER reject_import BEFORE UPDATE ON service_team_imports FOR EACH ROW EXECUTE FUNCTION reject_import();");
    assert!(f.store.complete_team_import(&job, receipt(&job)).is_err());
    f.store = PostgresStore::open(f.db.clone()).unwrap();
    sql(
        &f,
        "DROP TRIGGER reject_import ON service_team_imports; DROP FUNCTION reject_import();",
    );
    let d = document(&f);
    assert!(d.source_imports.is_empty());
    assert!(d.workspaces["one"].receipt.is_none());
    assert!(!f
        .store
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    f.store.authorized(&"test-user-".repeat(6)).unwrap().agent_team_api(&f.lead.id,"command",json!({"request_id":"retire-one","operation":"team_manage","arguments":{"action":"retire","agent_id":"one"}})).unwrap();
    assert!(f.store.complete_team_import(&job, receipt(&job)).is_err());
    f.store.defer_team_import(&job).unwrap();
    expire(&f);
    assert!(f.store.claim_team_import(&installed).unwrap().is_none());
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn source_admission_denial_before_import_dispatch_closes_only_the_unstarted_child() {
    let (f, _) = staged().await;
    let installed = management::installation(&f.config).unwrap();
    sql(&f,"UPDATE api_credentials SET revoked=TRUE WHERE id=(SELECT credential_id FROM service_operations LIMIT 1)");
    assert!(f.store.claim_team_import(&installed).unwrap().is_none());
    assert_eq!(
        document(&f).roster.instance("one").unwrap().phase,
        openworker_runtime::team_coordination::Phase::Closed
    );
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&f.db.connection, postgres::NoTls).unwrap();
        assert_eq!(
            pg.query_one(
                &format!("SELECT count(*) FROM {}.service_team_imports", f.db.schema),
                &[]
            )
            .unwrap()
            .get::<_, i64>(0),
            0
        );
    });
    assert!(f.store.claim_team_import(&installed).unwrap().is_none());
}
