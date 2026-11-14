//! Fault-injected provider: an effect happens, its response is lost, and the
//! same live turn reads state next. The original effect must never be replayed.
#![cfg(unix)]
use anyhow::{bail, Result};
use openworker_runtime::{environment::*, execution::Backend, model::Model, runtime::Runtime};
use openworker_store_postgres::{Config, PostgresStore, Tls};
use serde_json::{json, Value};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};
#[derive(Clone)]
struct Fake {
    spec: Spec,
    effects: Arc<AtomicUsize>,
}
struct Channel(Arc<AtomicUsize>);
impl Connection for Channel {
    fn request<'a>(&'a mut self, method: &'a str, args: Value) -> ProviderFuture<'a, Value> {
        Box::pin(async move {
            if method == "tools/list" {
                return Ok(
                    json!({"tools":[{"name":"write_file","description":"write","inputSchema":{"type":"object"}},{"name":"read_file","description":"read","inputSchema":{"type":"object"}}]}),
                );
            }
            if args["name"] == "write_file" {
                self.0.fetch_add(1, Ordering::SeqCst);
                bail!("simulated connection loss after effect")
            }
            Ok(
                json!({"content":[{"type":"text","text":format!("observed-effects={}",self.0.load(Ordering::SeqCst))}]}),
            )
        })
    }
    fn close(self: Box<Self>) -> ProviderFuture<'static, CloseOutcome> {
        Box::pin(async { Ok(CloseOutcome::Unknown) })
    }
}
impl Provider for Fake {
    fn key(&self) -> &str {
        &self.spec.provider
    }
    fn capabilities(&self) -> Capabilities {
        Capabilities {
            create: true,
            shared: true,
            persistent_workspace: true,
            disconnect_terminates_effects: false,
        }
    }
    fn validate_spec(&self, s: &Spec) -> Result<()> {
        anyhow::ensure!(*s == self.spec, "spec mismatch");
        Ok(())
    }
    fn create(&self, _: Record) -> ProviderFuture<'_, Binding> {
        Box::pin(async { bail!("unused") })
    }
    fn inspect(&self, b: Binding) -> ProviderFuture<'_, ()> {
        Box::pin(async move { self.validate_spec(&b.spec) })
    }
    fn delete(&self, _: Binding, _: String) -> ProviderFuture<'_, ()> {
        Box::pin(async { Ok(()) })
    }
    fn attach(
        &self,
        _: Binding,
        _: String,
        _: Vec<String>,
    ) -> ProviderFuture<'_, Box<dyn Connection>> {
        let effects = self.effects.clone();
        Box::pin(async move { Ok(Box::new(Channel(effects)) as Box<dyn Connection>) })
    }
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires disposable OW_PG_TEST_URL and OW_PROBE_RUNNER Linux binary"]
async fn live_uncertain_effect_reaches_model_then_read_runs_without_replay() {
    let root = tempfile::tempdir().unwrap();
    let bundle = root.path().join("bundle");
    std::fs::create_dir_all(bundle.join("coworker")).unwrap();
    std::fs::write(
        bundle.join("coworker/manifest.md"),
        "---\nid: uncertainty\nname: Uncertainty\n---\nUse the tools.",
    )
    .unwrap();
    std::fs::write(bundle.join("tools.json"),json!({"server":{"id":"tools","program":"$OPENWORKER","args":["mcp-example","--workspace","$WORKSPACE"]},"bindings":{"write_file":{"server":"tools","tool":"write_file"},"read_file":{"server":"tools","tool":"read_file"}}}).to_string()).unwrap();
    let mut config = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_unknown_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&config).unwrap();
    let store = PostgresStore::open(config.clone()).unwrap();
    let spec = Spec {
        provider: "fake".into(),
        provider_identity: "fault-injection".into(),
        profile_digest: "test".into(),
        image: format!("fake@sha256:{}", "a".repeat(64)),
        workspace_root: "/sandbox".into(),
        sharing: Sharing::Shared,
        management: Management::Harness,
    };
    let effects = Arc::new(AtomicUsize::new(0));
    let provider = Arc::new(Fake {
        spec: spec.clone(),
        effects: effects.clone(),
    });
    let (env, _) = store
        .begin_environment("env", "test", spec.clone())
        .unwrap();
    let binding = Binding {
        environment_id: env.id.clone(),
        spec,
        remote_id: "uid".into(),
        remote_name: "name".into(),
        workspace_scope: "test".into(),
        policy_hash: "test".into(),
        policy_version: 1,
    };
    store
        .complete_environment(&env.id, env.revision, Some(binding))
        .unwrap();
    let mut rt = Runtime::open_instrumented(
        &root.path().join("state"),
        &bundle,
        std::path::Path::new(&std::env::var("OW_PROBE_RUNNER").unwrap()),
        Model::new("fixture".into(), true),
        Backend::Openshell,
        None,
        None,
        Default::default(),
        Some(Box::new(store.clone())),
        Default::default(),
    )
    .unwrap();
    let mut providers = Registry::default();
    providers.register(provider).unwrap();
    Runtime::configure_environments(&mut rt, Arc::new(providers)).unwrap();
    let s=rt.dispatch("session/create",json!({"name":"unknown","coworker":"uncertainty","workspace":"/sandbox","environment_id":env.id,"permissions":{"mode":"bypass"}})).await.unwrap();
    let sid = s["session"]["id"].as_str().unwrap();
    rt.dispatch("turn/start",json!({"session_id":sid,"submission_id":"uncertain","text":"TEST_RESOURCE_GRANTS [{\"name\":\"write_file\",\"arguments\":{\"path\":\"proof.txt\",\"content\":\"effect\"}},{\"name\":\"read_file\",\"arguments\":{\"path\":\"proof.txt\"}}]"})).await.unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(15), async {
        loop {
            if store.read_snapshot(sid).unwrap()["turn"]["status"] == "completed" {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let state = store.inspect(sid).unwrap();
    let history = json!(state.history).to_string();
    assert!(history.contains("tool_outcome_unknown"), "{history}");
    assert!(history.contains("observed-effects=1"));
    assert_eq!(effects.load(Ordering::SeqCst), 1);
    assert!(store
        .read_events(sid, 0)
        .unwrap()
        .iter()
        .any(|e| e.kind == "attachment_settled" && e.data["outcome_unknown"] == true));
    rt.shutdown().await.unwrap();
    drop(rt);
    drop(store);
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&config.connection, postgres::NoTls).unwrap();
        pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", config.schema))
            .unwrap();
    })
    .await
    .unwrap();
}
