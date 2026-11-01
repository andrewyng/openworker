use super::*;
use openworker_runtime::{
    agent_team::Document,
    environment::{Binding, Management, Sharing},
    store::{Session, StateStore},
};
use openworker_store_postgres::{
    service_catalog::{Admission, Entry},
    Config as DbConfig, Tls,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
struct Fixture {
    _root: tempfile::TempDir,
    config: Config,
    db: DbConfig,
    store: PostgresStore,
    admin: PostgresStore,
    user: PostgresStore,
    lead: Session,
    release: Entry,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        // Only this test's isolated schema, never a shared/demo database.
        let db = self.db.clone();
        std::thread::spawn(move || {
            if let Ok(mut pg) = postgres::Client::connect(&db.connection, postgres::NoTls) {
                let _ = pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", db.schema));
            }
        })
        .join()
        .unwrap();
    }
}
impl Fixture {
    async fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let bundle = root.path().join("installed");
        std::fs::create_dir_all(bundle.join("coworker")).unwrap();
        std::fs::write(bundle.join("coworker/manifest.md"),"---\nid: installed\nname: Installed\nplanning: true\nmemory: true\nscheduling: true\n---\nTrusted installed prompt").unwrap();
        std::fs::write(bundle.join("tools.json"),r#"{"version":2,"servers":[{"id":"coding","program":"$OPENWORKER","args":["mcp-coding","--workspace","$WORKSPACE"]}],"bindings":{"read_file":{"server":"coding","tool":"read_file"},"write_file":{"server":"coding","tool":"write_file"}}}"#).unwrap();
        let runner = root.path().join("inert-fixture-ELF");
        std::fs::write(&runner, b"\x7fELF-no-execution-in-this-fixture").unwrap();
        let mut v: Value = serde_json::from_str(include_str!("../config.example.json")).unwrap();
        v["bundle"] = json!(bundle);
        v["runner"] = json!(runner);
        v["state_root"] = json!(root.path().join("state"));
        v["openshell"]["image"] = json!(format!("runner@sha256:{}", "a".repeat(64)));
        v["model_connections"] = json!([{"id":"primary","provider":"openai","identity":"stable-primary","key_env":"UNUSED_TEST_KEY"},{"id":"other","provider":"anthropic","identity":"stable-other","key_env":"UNUSED_TEST_KEY"}]);
        let cfg: Config = serde_json::from_value(v).unwrap();
        let installed = management::installation(&cfg).unwrap();
        let mut db = DbConfig::new(
            std::env::var("OW_PG_TEST_URL").unwrap(),
            format!("ow_stypes_{}", uuid::Uuid::new_v4().simple()),
        );
        db.tls = Tls::LoopbackPlaintext;
        PostgresStore::migrate(&db).unwrap();
        let store = PostgresStore::open(db.clone()).unwrap();
        let token = "test-admin-".repeat(6);
        store.auth_bootstrap(&token, "admin").unwrap();
        let admin = store.authorized(&token).unwrap();
        let token = "test-user-".repeat(6);
        admin
            .auth_issue(
                "user",
                serde_json::from_value(
                    json!({"principal":"alice","role":"operator","scope":{"kind":"owned"}}),
                )
                .unwrap(),
                &token,
            )
            .unwrap();
        let user = store.authorized(&token).unwrap();
        let profile=admin.catalog_put("profile","profile",json!({"id":"team@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared","allowedPrincipals":["alice"]}),&installed).unwrap();
        admin
            .catalog_activate("activate-profile", "profile", &profile.id, None)
            .unwrap();
        let release=admin.catalog_put("release","release",json!({"id":"lead@1","instructions":"Trusted application lead.","tools":["read_file","write_file"],"runtimeProfiles":["team@1"],"defaultRuntimeProfile":"team@1","allowedPrincipals":["alice"],"defaultModel":{"connection":"primary","model":"working"},"reviewerModel":{"connection":"other","model":"reviewer"},"modelOverrides":[{"connection":"other","model":"delegated"}]}),&installed).unwrap();
        admin
            .catalog_activate("activate-release", "release", &release.id, None)
            .unwrap();
        let release = store.catalog_get("release", &release.id).unwrap();
        let profile = store.catalog_get("profile", &profile.id).unwrap();
        let provider = management::provider(&cfg, &profile).unwrap();
        let operation = user
            .service_admit(
                "lead",
                serde_json::from_value::<Admission>(json!({"agent":"lead@1","project":"test"}))
                    .unwrap(),
                release.clone(),
                profile.clone(),
                provider.spec(Sharing::Shared, Management::Harness),
            )
            .unwrap();
        let state = store.clone();
        let env = state.environment(&operation.environment_id).unwrap();
        let binding = Binding {
            environment_id: env.id.clone(),
            spec: env.spec.clone(),
            remote_id: "original-test-uid".into(),
            remote_name: "original-test-sandbox".into(),
            workspace_scope: "default".into(),
            policy_hash: "test-policy".into(),
            policy_version: 1,
        };
        store
            .complete_environment(&env.id, env.revision, Some(binding))
            .unwrap();
        store
            .service_update_operation(&operation.id, "ready", None)
            .unwrap();
        let source = management::release_config(&cfg, &release, &profile).unwrap();
        let rt = source.api_runtime("source", store.clone()).unwrap();
        let lead=rt.prepare_session(json!({"coworker":rt.coworker(),"name":"Lead","workspace":"/sandbox","environment_id":env.id,"model":{"connection":"primary","model":"working"},"reviewer_model":{"connection":"other","model":"reviewer"}})).unwrap();
        rt.shutdown().await.unwrap();
        store
            .service_finish(&operation.id, lead, "test", "test")
            .unwrap();
        store.claim(&operation.session_id).unwrap();
        let lead = state.metadata(&operation.session_id).unwrap();
        Self {
            _root: root,
            config: cfg,
            db,
            store,
            admin,
            user,
            lead,
            release,
        }
    }
    fn create(&mut self) -> Document {
        self.create_with_delegation(json!({"tools":["coding.read_file"],"models":[Choice::new("other","delegated")],"placements":["team"],"profiles":{}}))
    }
    fn create_with_delegation(&mut self, delegation: Value) -> Document {
        let rt = self
            .config
            .api_runtime("models", self.store.clone())
            .unwrap();
        let choice = Choice::new("other", "delegated");
        let models = BTreeMap::from([(
            openworker_store_postgres::service_team_types::model_key(&choice).unwrap(),
            rt.connections.resolve(&choice).unwrap(),
        )]);
        let args = json!({"agent_id":"lead","agent_types":["builder"],"delegation":delegation});
        let templates = BTreeMap::from([(
            "builder".into(),
            (self.release.id.clone(), {
                let mut s = self.lead.clone();
                s.id = "throwaway".into();
                s.storage_revision = 0;
                s
            }),
        )]);
        let doc = self
            .user
            .configure_agent_team_models(&self.lead.id, args, templates, models)
            .unwrap();
        self.lead = self.store.clone().metadata(&self.lead.id).unwrap();
        serde_json::from_value(doc).unwrap()
    }
    fn approve(&mut self, name: &str, tools: Value) -> Record {
        let mut state = self.store.clone();
        let args = json!({"name":name,"instructions":"Read evidence, do not write.","tools":tools});
        let (mut s, _, _) = state
            .accept(
                &self.lead.id,
                &format!("define-{name}"),
                "Define a private reader",
            )
            .unwrap();
        s.schema_digest = Some("test-schema".into());
        let call = json!({"type":"function_call","call_id":format!("call-{name}"),"name":"team_define_type","arguments":args.to_string()});
        let turn = s.turn.as_mut().unwrap();
        turn.status = "running".into();
        turn.queue = vec![call.clone()];
        s.history.push(call.clone());
        state.save(&s, "assistant_message", json!({})).unwrap();
        state
            .agent_team(&s.id, "type_prepare", &args, None)
            .unwrap();
        let mut s = state.get(&s.id).unwrap();
        s.interactions.push(serde_json::from_value(json!({"id":format!("approval-{name}"),"turn_id":s.turn.as_ref().unwrap().id,"kind":"approval","call":call,"bundle_digest":s.bundle_digest,"schema_digest":s.schema_digest,"response":"once","origin":"human","server":null,"remote_tool":null,"policy_digest":s.tool_policy.digest,"resource_policy_digest":s.resource_policy.digest})).unwrap());
        state.save(&s, "interaction_resolved", json!({})).unwrap();
        state
            .agent_team(&s.id, "type_resolve", &args, None)
            .unwrap();
        let snapshot: Document = serde_json::from_value(
            state
                .agent_team(&s.id, "snapshot", &json!({}), None)
                .unwrap(),
        )
        .unwrap();
        let record = snapshot
            .type_catalog
            .unwrap()
            .approved(name)
            .unwrap()
            .clone();
        let mut s = state.get(&s.id).unwrap();
        s.turn.as_mut().unwrap().status = "completed".into();
        state.save(&s, "turn_completed", json!({})).unwrap();
        record
    }
    fn team(&self) -> Team {
        self.store
            .service_team_type_page(&management::installation(&self.config).unwrap(), "", 8)
            .unwrap()
            .remove(0)
    }
    fn spawn(&self, name: &str, id: &str, model: Option<Choice>) -> Value {
        self.user.agent_team_api(&self.lead.id,"command",json!({"request_id":format!("spawn-{id}"),"operation":"team_spawn","arguments":{"agent_type":name,"agent_id":id,"message":"Read evidence","model":model}})).unwrap()
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway or model traffic"]
async fn shared_workspace_intent_is_leased_once_and_admission_pins_receipt() {
    let mut f = Fixture::new().await;
    let doc = f.create();
    let installed = management::installation(&f.config).unwrap();
    assert!(f.user.claim_team_workspace(&installed).is_err());
    let spawned=f.user.agent_team_api(&f.lead.id,"command",json!({"request_id":"workspace-child","operation":"team_spawn","arguments":{"agent_type":"builder","agent_id":"ui","workspace":{"mode":"directory","path":"workers/ui"},"message":"Inspect own directory"}})).unwrap();
    let sid = spawned["instance"]["session_id"].as_str().unwrap();
    assert!(!f
        .store
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    assert!(f
        .store
        .claim_team_workspace("wrong-installation")
        .unwrap()
        .is_none());
    let original = f.store.claim_team_workspace(&installed).unwrap().unwrap();
    assert!(original.dispatch);
    assert!(f.store.claim_team_workspace(&installed).unwrap().is_none());
    // Simulate controller loss after committing dispatch, not after a model/tool
    // turn. Only this fixture's lease expires; the next controller gets inspect.
    let db = f.db.clone();
    tokio::task::spawn_blocking(move||{
        let mut pg=postgres::Client::connect(&db.connection,postgres::NoTls).unwrap();
        pg.batch_execute(&format!("UPDATE {}.agent_team_workspace_claims SET expires_at=clock_timestamp()-interval '1 second'",db.schema)).unwrap();
    }).await.unwrap();
    let reopened = PostgresStore::open(f.db.clone()).unwrap();
    let recovered = reopened.claim_team_workspace(&installed).unwrap().unwrap();
    assert!(!recovered.dispatch);
    assert_eq!(recovered.intent.request, original.intent.request);
    let receipt:openworker_runtime::team_workspace::Receipt=serde_json::from_value(json!({"request":original.intent.request,"path":"/sandbox/workers/ui","project_root":"/sandbox","identity":{"volume":1,"file":2},"repository":null,"git_dir":null,"git_identity":null,"branch":null,"initial_commit":null})).unwrap();
    assert!(f
        .store
        .finish_team_workspace(original, Ok(receipt.clone()))
        .is_err());
    let mut forged = receipt.clone();
    forged.path = "/sandbox/other".into();
    assert!(reopened
        .finish_team_workspace(recovered.clone(), Ok(forged))
        .is_err());
    reopened
        .finish_team_workspace(recovered.clone(), Ok(receipt.clone()))
        .unwrap();
    assert!(reopened.claim_team_workspace(&installed).unwrap().is_none());
    assert!(reopened
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    reopened.claim(sid).unwrap();
    let child = reopened.clone().metadata(sid).unwrap();
    assert_eq!(child.workspace, "/sandbox/workers/ui");
    assert_eq!(child.environment, f.lead.environment);
    assert_eq!(child.managed_workspace, Some(receipt));
    let snapshot = f
        .user
        .agent_team_api(&f.lead.id, "snapshot", json!({}))
        .unwrap();
    assert_eq!(snapshot["id"], doc.id);
    assert!(snapshot.get("workspaces").is_none());
    let mut widened = child.clone();
    widened.workspace = "/sandbox/other".into();
    assert!(reopened
        .clone()
        .save(&widened, "invalid-test", json!({}))
        .is_err());
    assert!(!reopened
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway or model traffic"]
async fn unknown_workspace_outcome_closes_only_unstarted_child() {
    let mut f = Fixture::new().await;
    f.create();
    let installed = management::installation(&f.config).unwrap();
    f.user.agent_team_api(&f.lead.id,"command",json!({"request_id":"unknown-child","operation":"team_spawn","arguments":{"agent_type":"builder","agent_id":"unknown","workspace":{"mode":"directory","path":"absent"},"message":"Do not substitute a directory"}})).unwrap();
    let job = f.store.claim_team_workspace(&installed).unwrap().unwrap();
    f.store
        .finish_team_workspace(
            job,
            Err("Outcome unknown; inspect original operation".into()),
        )
        .unwrap();
    let snapshot = f
        .user
        .agent_team_api(&f.lead.id, "snapshot", json!({}))
        .unwrap();
    assert_eq!(
        snapshot["roster"]["instances"]["unknown"]["phase"],
        "closed"
    );
    assert!(f.store.claim_team_workspace(&installed).unwrap().is_none());
    // A failed selected workspace must not starve a normal same-directory child.
    let normal = f.spawn("builder", "normal", None);
    assert!(f
        .store
        .provision_agent_team_child_for(Some(&installed))
        .unwrap());
    f.store
        .claim(normal["instance"]["session_id"].as_str().unwrap())
        .unwrap();
    assert_eq!(
        f.store
            .clone()
            .metadata(normal["instance"]["session_id"].as_str().unwrap())
            .unwrap()
            .workspace,
        "/sandbox"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway or model traffic"]
async fn scoped_service_compilation_admission_and_restart() {
    let mut f = Fixture::new().await;
    let doc = f.create();
    assert!(f.user.service_team_type_page("irrelevant", "", 8).is_err());
    assert!(f.admin.service_team_type_page("irrelevant", "", 8).is_err());
    assert!(f
        .store
        .service_team_type_page("other-installation", "", 8)
        .unwrap()
        .is_empty());
    assert!(f
        .store
        .service_team_type_page(&management::installation(&f.config).unwrap(), &doc.id, 8)
        .unwrap()
        .is_empty());
    let record = f.approve("reader", json!(["coding.read_file"]));
    let team = f.team();
    let c = config(&f.config, &team, &record).unwrap();
    let bundle = Bundle::load(&c.bundle).unwrap();
    assert_eq!(bundle.id, record.runtime_id());
    assert_eq!(
        bundle.tools.bindings.keys().collect::<Vec<_>>(),
        vec!["read_file"]
    );
    assert!(!bundle.manifest.planning && !bundle.manifest.memory && !bundle.manifest.scheduling);
    assert!(f.user.catalog_get("release", &record.runtime_id()).is_err());
    let mut pending = record.clone();
    pending.status = Status::Pending;
    assert!(config(&f.config, &team, &pending).is_err());
    let mut changed = team.clone();
    changed.lead.permissions.mode = openworker_runtime::permissions::Mode::Bypass;
    assert!(config(&f.config, &changed, &record).is_err());
    let session = prepare(&f.config, &f.store, &team, &record).await.unwrap();
    let mut widened = session.clone();
    widened.permissions.mode = openworker_runtime::permissions::Mode::Bypass;
    assert!(f
        .store
        .install_service_team_type(&team, &record, widened)
        .is_err());
    let mut changed = session.clone();
    changed.model_selection.as_mut().unwrap().identity = "replacement".into();
    assert!(f
        .store
        .install_service_team_type(&team, &record, changed)
        .is_err());
    f.store
        .install_service_team_type(&team, &record, session.clone())
        .unwrap();
    let mut duplicate = session.clone();
    duplicate.id = "other-throwaway".into();
    f.store
        .install_service_team_type(&team, &record, duplicate)
        .unwrap();
    f.store
        .verify_service_team_type_runtime(&doc.id, &record.runtime_id(), &session.bundle_digest)
        .unwrap();
    assert!(f
        .store
        .verify_service_team_type_runtime(&doc.id, &record.runtime_id(), "wrong")
        .is_err());
    let child = f.spawn(
        "reader",
        "reader-one",
        Some(Choice::new("other", "delegated")),
    )["instance"]["session_id"]
        .as_str()
        .unwrap()
        .to_owned();
    assert!(f.store.provision_agent_team_child().unwrap());
    assert!(!f.store.provision_agent_team_child().unwrap());
    f.store.claim(&child).unwrap();
    let stored = f.store.clone().metadata(&child).unwrap();
    assert_eq!(stored.coworker, record.runtime_id());
    assert_eq!(stored.environment, f.lead.environment);
    assert_eq!(stored.model, "delegated");
    assert_eq!(stored.model_selection.as_ref().unwrap().connection, "other");
    assert_eq!(
        serde_json::to_value(stored.reviewer_selection).unwrap(),
        serde_json::to_value(f.lead.reviewer_selection.clone()).unwrap()
    );
    let mut fresh = f.config.clone();
    fresh.state_root = f._root.path().join("new-worker");
    let rebuilt = config(&fresh, &f.team(), &record).unwrap();
    let rt = rebuilt.api_runtime("restart", f.store.clone()).unwrap();
    assert_eq!(rt.execution_digest(), session.bundle_digest);
    rt.shutdown().await.unwrap();
    let mut cursor = String::new();
    refresh(
        &f.config,
        &f.store,
        &management::installation(&f.config).unwrap(),
        &mut cursor,
    )
    .await
    .unwrap();
    // A missing configured model never falls back or poisons prebuilt members.
    let empty = f.approve("missing", json!(["coding.read_file"]));
    f.spawn("missing", "missing-one", None);
    let connections = f.config.model_connections.clone();
    f.config.model_connections.retain(|c| c.id != "primary");
    refresh(
        &f.config,
        &f.store,
        &management::installation(&f.config).unwrap(),
        &mut String::new(),
    )
    .await
    .unwrap();
    assert!(f
        .store
        .service_team_type_installed(&doc.id, &empty.runtime_id())
        .unwrap());
    assert!(!f
        .store
        .service_team_type_ready(&doc.id, &empty.runtime_id())
        .unwrap());
    assert!(f.store.provision_agent_team_child().unwrap());
    f.config.model_connections = connections;
    let list = f
        .user
        .agent_team_api(&f.lead.id, "team_manage", json!({"action":"list"}))
        .unwrap();
    assert!(list.to_string().contains("could not be prepared"));
    let next = f.spawn("builder", "builder-one", None)["instance"]["session_id"]
        .as_str()
        .unwrap()
        .to_owned();
    assert!(f.store.provision_agent_team_child().unwrap());
    f.store.claim(&next).unwrap();
    assert!(f.store.clone().metadata(&next).is_ok());
    std::fs::write(c.bundle.join("tools.json"), "{}").unwrap();
    assert!(config(&f.config, &team, &record).is_err());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; no gateway or model traffic"]
async fn service_delegation_cannot_escape_source_release_or_stale_approval() {
    let mut f = Fixture::new().await;
    f.create();
    let record = f.approve("reader", json!(["coding.read_file"]));
    let team = f.team();
    let session = prepare(&f.config, &f.store, &team, &record).await.unwrap();
    f.store
        .install_service_team_type(&team, &record, session)
        .unwrap();
    let child = f.spawn("reader", "stale-reader", None)["instance"]["session_id"]
        .as_str()
        .unwrap()
        .to_owned();
    let db = f.db.clone();
    let release = f.release.id.clone();
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!("SET search_path={},pg_catalog", db.schema))
            .unwrap();
        pg.execute(
            "UPDATE service_catalog SET activated=false WHERE kind='release' AND id=$1",
            &[&release],
        )
        .unwrap();
    })
    .await
    .unwrap();
    assert!(f.store.provision_agent_team_child().unwrap());
    assert!(f.store.claim(&child).is_err());
    assert!(!f.store.provision_agent_team_child().unwrap());
    let list = f
        .user
        .agent_team_api(&f.lead.id, "team_manage", json!({"action":"list"}))
        .unwrap();
    assert!(list.to_string().contains("Delegation denied"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and loopback HTTP; no tools/models"]
async fn service_team_http_authorizes_and_bounds_delegation_before_creation() {
    let f = Fixture::new().await;
    let mut cfg = f.config.clone();
    cfg.service = Some(
        serde_json::from_value(json!({"id":"types-test","namespace":"test","agent":"legacy"}))
            .unwrap(),
    );
    let rt = cfg.api_runtime("api", f.store.clone()).unwrap();
    let stop = tokio_util::sync::CancellationToken::new();
    let app = crate::http::App::new(&cfg, rt.clone(), f.store.clone(), stop.clone()).unwrap();
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = socket.local_addr().unwrap();
    let done = stop.clone();
    let server = tokio::spawn(async move {
        axum::serve(socket, app.router())
            .with_graceful_shutdown(done.cancelled_owned())
            .await
            .unwrap()
    });
    let client = reqwest::Client::new();
    let url = format!("http://{address}/v1/sessions/{}/team", f.lead.id);
    let token = "test-user-".repeat(6);
    let args = json!({"agentTypes":{"builder":"lead@1"},"delegation":{"tools":["coding.read_file"],"models":[{"connection":"other","model":"delegated"}],"placements":["team"],"profiles":{}}});
    for bad in [
        json!({"tools":["coding.run_shell"],"models":[],"placements":["team"]}),
        json!({"tools":["coding.read_file"],"models":[{"connection":"primary","model":"not-delegated"}],"placements":["team"]}),
        json!({"tools":["coding.read_file"],"models":[],"placements":["new"]}),
    ] {
        let mut body = args.clone();
        body["delegation"] = bad;
        let response = client
            .post(&url)
            .header("x-openworker-namespace", "test")
            .bearer_auth(&token)
            .json(&body)
            .send()
            .await
            .unwrap();
        assert!(!response.status().is_success());
        assert!(f
            .store
            .clone()
            .metadata(&f.lead.id)
            .unwrap()
            .agent_team
            .is_none());
    }
    let observer = "observer-test-".repeat(6);
    f.admin
        .auth_issue(
            "observer",
            serde_json::from_value(
                json!({"principal":"observer","role":"observer","scope":{"kind":"all"}}),
            )
            .unwrap(),
            &observer,
        )
        .unwrap();
    let response = client
        .post(&url)
        .header("x-openworker-namespace", "test")
        .bearer_auth(observer)
        .json(&args)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status().as_u16(), 403);
    let response = client
        .post(&url)
        .header("x-openworker-namespace", "test")
        .bearer_auth(&token)
        .json(&args)
        .send()
        .await
        .unwrap();
    let status = response.status();
    let body: Value = response.json().await.unwrap();
    assert!(status.is_success(), "{status}: {body}");
    assert!(body.get("type_catalog").is_none());
    let team = f.team();
    assert!(team.document.type_catalog.is_some());
    let repeated = client
        .post(&url)
        .header("x-openworker-namespace", "test")
        .bearer_auth(&token)
        .json(&args)
        .send()
        .await
        .unwrap();
    assert!(repeated.status().is_success());
    stop.cancel();
    server.await.unwrap();
    rt.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL; legacy receipt read compatibility only"]
async fn unchanged_prebuilt_team_retry_does_not_require_rewriting_old_receipts() {
    let f = Fixture::new().await;
    let mut template = f.lead.clone();
    template.id = "throwaway".into();
    template.storage_revision = 0;
    let args = json!({"agent_id":"lead","agent_types":["builder"]});
    let templates = BTreeMap::from([("builder".into(), (f.release.id.clone(), template))]);
    let created = f
        .user
        .configure_agent_team(&f.lead.id, args.clone(), templates.clone())
        .unwrap();
    let team = created["id"].as_str().unwrap().to_owned();
    let db = f.db.clone();
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!("SET search_path={},pg_catalog", db.schema))
            .unwrap();
        pg.execute(
            "UPDATE agent_team_templates SET data=data-'models' WHERE team=$1",
            &[&team],
        )
        .unwrap();
    })
    .await
    .unwrap();
    let again = f
        .user
        .configure_agent_team(&f.lead.id, args, templates)
        .unwrap();
    assert_eq!(again["id"], created["id"]);
}

#[path = "service_team_placements_tests.rs"]
mod placements;
