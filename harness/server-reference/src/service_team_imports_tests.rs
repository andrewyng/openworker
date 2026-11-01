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

/// A fault-injected private protocol peer. This is deliberately NOT a Git or
/// sandbox fixture: the separate runtime/physical tests check those boundaries.
struct Receiver {
    job: Job,
    calls: Vec<String>,
    marker: bool,
    received: u64,
    checkout: bool,
    lose: Option<&'static str>,
}
impl Receiver {
    fn checkout(&self) -> openworker_runtime::coding_handoff::transfer::Receipt {
        let workspace = receipt(&self.job);
        serde_json::from_value(json!({"transfer_digest":openworker_runtime::bundle::digest(&serde_json::to_vec(&("coding-transfer-v1",&self.job.intent.transfer)).unwrap()),"path":workspace.path,"identity":workspace.identity,"git_identity":workspace.independent_git,"commit":workspace.initial_commit})).unwrap()
    }
    fn status(&self) -> Value {
        json!({"handle":self.job.intent.transfer.handle(),"received":self.received,"size":self.job.intent.transfer.bundle.size,"checkout_dispatched":self.checkout,"receipt":self.checkout.then(||self.checkout())})
    }
}
impl openworker_runtime::environment::Connection for Receiver {
    fn request<'a>(
        &'a mut self,
        method: &'a str,
        params: Value,
    ) -> openworker_runtime::environment::ProviderFuture<'a, Value> {
        Box::pin(async move {
            self.calls.push(method.into());
            let result = match method {
                "handoff/prepare" => {
                    assert_eq!(params, json!(self.job.intent.transfer));
                    self.marker = true;
                    self.status()
                }
                "handoff/inspect" => {
                    anyhow::ensure!(self.marker, "Original marker absent");
                    self.status()
                }
                "handoff/write" => {
                    use base64::Engine;
                    assert_eq!(params["offset"].as_u64(), Some(self.received));
                    let bytes = base64::engine::general_purpose::STANDARD
                        .decode(params["data"].as_str().unwrap())?;
                    self.received += bytes.len() as u64;
                    self.status()
                }
                "handoff/finish" => {
                    assert_eq!(self.received, self.job.intent.transfer.bundle.size);
                    assert!(!self.checkout);
                    self.checkout = true;
                    json!(self.checkout())
                }
                "handoff/inspect_checkout" => {
                    anyhow::ensure!(self.checkout, "Original checkout absent");
                    json!(self.checkout())
                }
                "handoff/workspace" => {
                    assert!(self.checkout);
                    assert_eq!(params["request"], json!(self.job.intent.workspace));
                    json!(receipt(&self.job))
                }
                "workspace/verify" => {
                    assert_eq!(params, json!(receipt(&self.job)));
                    params
                }
                _ => anyhow::bail!("Unexpected private method"),
            };
            if self.lose == Some(method) {
                self.lose = None;
                anyhow::bail!("Injected delivery loss after original effect")
            }
            Ok(result)
        })
    }
    fn close(
        self: Box<Self>,
    ) -> openworker_runtime::environment::ProviderFuture<
        'static,
        openworker_runtime::environment::CloseOutcome,
    > {
        Box::pin(async { Ok(openworker_runtime::environment::CloseOutcome::Exited) })
    }
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn controller_recovers_lost_chunk_and_checkout_replies_without_replaying_mutations() {
    for lost in ["handoff/write", "handoff/finish"] {
        let (f, backend) = staged().await;
        let installed = management::installation(&f.config).unwrap();
        let mut job = f.store.claim_team_import(&installed).unwrap().unwrap();
        let mut peer = Receiver {
            job: job.clone(),
            calls: vec![],
            marker: false,
            received: 0,
            checkout: false,
            lose: Some(lost),
        };
        let stop = tokio_util::sync::CancellationToken::new();
        assert!(crate::service_team_imports::import(
            &f.store, &mut job, &backend, &mut peer, &stop
        )
        .await
        .is_err());
        assert!(!f
            .store
            .provision_agent_team_child_for(Some(&installed))
            .unwrap());
        f.store.defer_team_import(&job).unwrap();
        expire(&f);
        let replacement = PostgresStore::open(f.db.clone()).unwrap();
        let mut resumed = replacement.claim_team_import(&installed).unwrap().unwrap();
        assert!(!resumed.prepare);
        assert_eq!(resumed.checkout_dispatched, lost == "handoff/finish");
        let outcome = crate::service_team_imports::import(
            &replacement,
            &mut resumed,
            &backend,
            &mut peer,
            &stop,
        )
        .await
        .unwrap();
        replacement.complete_team_import(&resumed, outcome).unwrap();
        for method in ["handoff/prepare", "handoff/write", "handoff/finish"] {
            assert_eq!(
                peer.calls.iter().filter(|m| m.as_str() == method).count(),
                1,
                "{lost}: {method} replayed: {:?}",
                peer.calls
            );
        }
        assert_eq!(
            peer.calls
                .iter()
                .filter(|m| m.as_str()
                    == if lost == "handoff/finish" {
                        "handoff/inspect_checkout"
                    } else {
                        "handoff/inspect"
                    })
                .count(),
            1
        );
        assert!(replacement
            .provision_agent_team_child_for(Some(&installed))
            .unwrap());
    }
}

fn submission_tool(f: &Fixture, sid: &str, name: &str, arguments: Value, key: &str) -> Value {
    let mut store = f.store.clone();
    let mut s = store.get(sid).unwrap();
    if s.turn.is_none() {
        let d = document(f);
        s = store
            .accept(
                sid,
                "initial-one",
                &openworker_runtime::agent_team::delegated_task(&d.tasks["one"]),
            )
            .unwrap()
            .0;
    }
    let call =
        json!({"type":"function_call","name":name,"call_id":key,"arguments":arguments.to_string()});
    let turn = s.turn.as_mut().unwrap();
    turn.status = "running".into();
    turn.queue = vec![call.clone()];
    let tid = turn.id.clone();
    s.schema_digest = Some("synthetic-handoff-schema".into());
    s.interactions.push(openworker_runtime::store::Interaction {
        id: format!("approval-{key}"),
        turn_id: tid.clone(),
        kind: "approval".into(),
        call: call.clone(),
        bundle_digest: s.bundle_digest.clone(),
        schema_digest: s.schema_digest.clone().unwrap(),
        response: Some("once".into()),
        origin: Some("human".into()),
        server: None,
        remote_tool: None,
        policy_digest: s.tool_policy.digest.clone(),
        resource_policy_digest: s.resource_policy.digest.clone(),
        grant_id: None,
        requested_at_ms: None,
        resolved_at_ms: None,
    });
    s.history.push(call.clone());
    store.save(&s, "assistant_message", json!({})).unwrap();
    store
        .agent_team(sid, name, &arguments, Some((&tid, &call)))
        .unwrap()
}
async fn submission_producer() -> (Fixture, openworker_artifacts::Store, String) {
    let (f, backend) = staged().await;
    let installed = management::installation(&f.config).unwrap();
    let j = f.store.claim_team_import(&installed).unwrap().unwrap();
    let j = f.store.dispatch_team_import_checkout(&j).unwrap();
    f.store.complete_team_import(&j, receipt(&j)).unwrap();
    assert!(f
        .store
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    let d = document(&f);
    let sid = d.roster.instance("one").unwrap().session_id.clone();
    f.store.claim(&sid).unwrap();
    let identity = f.user.service_session_identity(&sid).unwrap().unwrap();
    assert_eq!(identity["releaseId"], "lead@1");
    assert_eq!(identity["runtimeProfile"], "team@1");
    assert_eq!(identity["agentId"], "one");
    (f, backend, sid)
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn submitted_revision_is_durable_deduplicated_and_imported_only_after_artifact_publication() {
    let (f, backend, sid) = submission_producer().await;
    let installed = management::installation(&f.config).unwrap();
    let args = json!({"commit":"b".repeat(40),"summary":"Built requested change"});
    let result = submission_tool(&f, &sid, "team_submit_code", args.clone(), "submit-first");
    assert_eq!(result["status"], "pending");
    let id = result["id"].as_str().unwrap();
    let first_origin = document(&f).submissions[id].request.origin.clone();
    let mut retry = args;
    retry["summary"] = json!("Changed wording after an uncertain result");
    assert_eq!(
        submission_tool(&f, &sid, "team_submit_code", retry, "submit-again")["id"],
        id
    );
    assert_eq!(document(&f).submissions.len(), 1);
    assert_eq!(document(&f).submissions[id].request.origin, first_origin);
    assert!(f.user.claim_team_submission(&installed).is_err());
    let first = f.store.claim_team_submission(&installed).unwrap().unwrap();
    assert!(first.dispatch);
    assert_eq!(first.intent.request.producer.session_id, sid);
    assert!(f.store.claim_team_submission(&installed).unwrap().is_none());
    f.user.agent_team_api(&f.lead.id,"command",json!({"request_id":"verify","operation":"team_spawn","arguments":{"agent_type":"builder","agent_id":"verify","message":"Inspect exact submission","source":{"kind":"submission","id":id}}})).unwrap();
    assert!(f.store.claim_team_import(&installed).unwrap().is_none()); // Pending is not published.
    f.store.defer_team_submission(&first).unwrap();
    sql(
        &f,
        "UPDATE service_team_submissions SET next_check=clock_timestamp()-interval '1 second'",
    );
    let replacement = PostgresStore::open(f.db.clone()).unwrap();
    let recovered = replacement
        .claim_team_submission(&installed)
        .unwrap()
        .unwrap();
    assert!(!recovered.dispatch);
    assert_eq!(recovered.intent, first.intent);
    assert!(f.store.check_team_submission(&first).is_err());
    let mut wrong = recovered.clone();
    wrong.intent.environment.remote_id = "replacement".into();
    assert!(replacement.check_team_submission(&wrong).is_err());
    let bytes = b"synthetic submitted bundle; storage test only";
    let b = openworker_runtime::coding_handoff::git::Bundle {
        request: recovered.intent.export.clone(),
        path: format!(".openworker/handoffs/{id}/source.bundle"),
        size: bytes.len() as u64,
        sha256: openworker_runtime::bundle::digest(bytes),
        advertised_ref: format!("refs/openworker/handoffs/{id}"),
    };
    replacement.observe_team_submission(&recovered, b).unwrap();
    let upload = replacement
        .claim_team_submission(&installed)
        .unwrap()
        .unwrap();
    assert!(!upload.dispatch);
    let a = replacement
        .register_team_submission_artifact(&upload)
        .unwrap();
    assert!(
        matches!(&a.registration.origin,openworker_artifacts::Origin::TeamSubmission{submission_id,..} if submission_id==id)
    );
    assert!(a.registration.turn_id.is_empty());
    assert!(f
        .user
        .register_artifact(a.registration.clone(), a.binding.clone(), "alice")
        .is_err());
    assert!(replacement
        .complete_team_submission(&upload, &a.id)
        .is_err());
    let proof = backend
        .put(&a, &bytes[..], &tokio_util::sync::CancellationToken::new())
        .await
        .unwrap();
    replacement
        .publish_team_submission_artifact(&upload, &a.id, proof)
        .unwrap();
    replacement
        .complete_team_submission(&upload, &a.id)
        .unwrap();
    assert!(replacement
        .claim_team_submission(&installed)
        .unwrap()
        .is_none());
    let state = document(&f).submissions[id].clone();
    assert_eq!(
        state.manifest.as_ref().unwrap().submitted_commit,
        "b".repeat(40)
    );
    let verifier = replacement.claim_team_import(&installed).unwrap().unwrap();
    assert_eq!(verifier.intent.transfer.manifest, state.manifest.unwrap());
    assert_eq!(
        verifier.intent.choice,
        openworker_runtime::coding_handoff::config::Choice::Submission { id: id.into() }
    );
    assert_eq!(verifier.intent.artifact.id, a.id);
    // A separate lead reception cannot change its source workspace. This is a
    // storage/authority test with synthetic bytes; real Git is tested separately.
    let received = submission_tool(
        &f,
        &f.lead.id,
        "team_receive_submission",
        json!({"submission_id":id}),
        "receive",
    );
    assert_eq!(received["status"], "pending");
    let r = replacement
        .claim_team_reception(&installed)
        .unwrap()
        .unwrap();
    assert_eq!(r.intent.reception.as_deref(), Some(id));
    assert!(r.prepare);
    let r = replacement.dispatch_team_import_checkout(&r).unwrap();
    replacement.complete_team_import(&r, receipt(&r)).unwrap();
    assert_eq!(child(&f, &f.lead.id).workspace, "/sandbox");
    let catalog = f
        .user
        .agent_team_api(
            &f.lead.id,
            "team_list_submissions",
            json!({"after":null,"limit":20}),
        )
        .unwrap();
    assert_eq!(catalog[0]["reception"]["status"], "ready");
    let integrated = submission_tool(
        &f,
        &f.lead.id,
        "team_integrate_submission",
        json!({"submission_id":id,"expected_head":coding_source().commit}),
        "integrate",
    );
    assert_eq!(integrated["status"], "pending");
    let initial = replacement
        .claim_team_integration(&installed)
        .unwrap()
        .unwrap();
    assert!(initial.dispatch);
    replacement.defer_team_integration(&initial).unwrap();
    sql(
        &f,
        "UPDATE service_team_integrations SET next_check=clock_timestamp()-interval '1 second'",
    );
    let recovered = replacement
        .claim_team_integration(&installed)
        .unwrap()
        .unwrap();
    assert!(!recovered.dispatch);
    assert_eq!(recovered.intent, initial.intent);
    assert!(replacement.check_team_integration(&initial).is_err());
    let mut wrong = recovered.intent.request.clone();
    wrong.expected_head = "f".repeat(40);
    assert!(replacement
        .complete_team_integration(
            &recovered,
            openworker_runtime::coding_handoff::integrate::Observation::Rejected {
                request: wrong,
                reason: "Wrong original request".into()
            }
        )
        .is_err());
    replacement
        .complete_team_integration(
            &recovered,
            openworker_runtime::coding_handoff::integrate::Observation::Applied {
                receipt: openworker_runtime::coding_handoff::integrate::Receipt {
                    request: recovered.intent.request.clone(),
                    branch: "refs/heads/main".into(),
                    commit: "b".repeat(40),
                    tree: "d".repeat(40),
                },
            },
        )
        .unwrap();
    assert!(replacement
        .claim_team_integration(&installed)
        .unwrap()
        .is_none());
    let catalog = f
        .user
        .agent_team_api(
            &f.lead.id,
            "team_list_submissions",
            json!({"after":null,"limit":20}),
        )
        .unwrap();
    assert_eq!(catalog[0]["integrations"][0]["status"], "applied");

    let mut forged = verifier.clone();
    forged.intent.transfer.manifest.submitted_commit = "c".repeat(40);
    assert!(replacement.check_team_import(&forged).is_err());
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn submission_revocation_blocks_without_replay_and_foreign_ids_reject_before_reservation() {
    let (f, _, sid) = submission_producer().await;
    let installed = management::installation(&f.config).unwrap();
    let result = submission_tool(
        &f,
        &sid,
        "team_submit_code",
        json!({"commit":"b".repeat(40),"summary":"A change"}),
        "submit",
    );
    let id = result["id"].as_str().unwrap();
    let first = f.store.claim_team_submission(&installed).unwrap().unwrap();
    sql(&f,"UPDATE api_credentials SET revoked=TRUE WHERE id=(SELECT credential_id FROM service_operations LIMIT 1)");
    assert!(f.store.check_team_submission(&first).is_err());
    f.store.defer_team_submission(&first).unwrap();
    assert!(document(&f).submissions[id].error.is_some());
    sql(&f,"UPDATE api_credentials SET revoked=FALSE; UPDATE service_team_submissions SET next_check=clock_timestamp()-interval '1 second'");
    assert!(f.store.claim_team_submission(&installed).unwrap().is_none()); // No automatic resurrection.
    let before = document(&f).roster.instances().count();
    assert!(f.user.agent_team_api(&f.lead.id,"command",json!({"request_id":"foreign","operation":"team_spawn","arguments":{"agent_type":"builder","agent_id":"foreign","message":"No","source":{"kind":"submission","id":"foreign_team_submission"}}})).is_err());
    assert_eq!(document(&f).roster.instances().count(), before);
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn undispatched_submission_revocation_is_bounded_and_pending_work_prevents_environment_deletion(
) {
    let (f, _, sid) = submission_producer().await;
    let installed = management::installation(&f.config).unwrap();
    let result = submission_tool(
        &f,
        &sid,
        "team_submit_code",
        json!({"commit":"b".repeat(40),"summary":"Preserve original checkout"}),
        "submit",
    );
    let id = result["id"].as_str().unwrap();
    sql(&f,"UPDATE api_credentials SET revoked=TRUE WHERE id=(SELECT credential_id FROM service_operations LIMIT 1)");
    assert!(f.store.claim_team_submission(&installed).unwrap().is_none());
    assert!(document(&f).submissions[id].error.is_some());
    assert!(f.store.claim_team_submission(&installed).unwrap().is_none()); // No poisoned queue loop.
                                                                           // Simulate retirement without erasing the pending request. Even after all
                                                                           // ordinary attachments are closed, its original environment must remain.
    sql(&f,"UPDATE sessions SET data=jsonb_set(data,'{closed}','true'); UPDATE agent_teams SET data=jsonb_set(jsonb_set(data,'{roster,instances,one,phase}','\"closed\"'),'{roster,instances,lead,phase}','\"closed\"')");
    let env = f.lead.environment.as_ref().unwrap();
    let error = f
        .store
        .begin_environment_delete(&env.environment_id, "delete-with-pending-handoff", "alice")
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("unresolved original coding handoff"),
        "{error}"
    );
}
