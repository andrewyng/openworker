//! End-to-end SDK/HTTP/PostgreSQL/worker qualification. The existing execution
//! environment admission is seeded with a fixture binding; no gateway is needed.
use super::*;
use openworker_runtime::environment::Binding;
use openworker_store_postgres::worker::{Factory, Options, Worker};
use std::path::PathBuf;
use tokio_util::sync::CancellationToken;

use openworker_runtime::{
    environment::ProviderFuture,
    workspaces::{
        reference::ReferenceProvider, ProviderOptions, WorkspaceClient, WorkspaceProvider,
    },
};
use std::sync::atomic::{AtomicBool, Ordering};

const TEST_SECRET: &str = "qualification-only-workspace-secret";

/// A separately registered implementation consumes private startup options and
/// simulates one lost detach acknowledgement after the provider commits it.
struct ProbeProvider {
    inner: ReferenceProvider,
    lose_detach_ack: AtomicBool,
}
impl WorkspaceProvider for ProbeProvider {
    fn start(&self, mut options: ProviderOptions) -> ProviderFuture<'_, Arc<dyn WorkspaceClient>> {
        Box::pin(async move {
            ensure!(
                options
                    .0
                    .remove("accessToken")
                    .as_ref()
                    .and_then(Value::as_str)
                    == Some(TEST_SECRET),
                "Fixture credential unavailable"
            );
            self.inner.start(options).await
        })
    }
    fn recover(&self, id: String) -> ProviderFuture<'_, Arc<dyn WorkspaceClient>> {
        self.inner.recover(id)
    }
    fn detach(&self, workspace: String, session: String) -> ProviderFuture<'_, ()> {
        Box::pin(async move {
            self.inner.detach(workspace, session).await?;
            if self.lose_detach_ack.swap(false, Ordering::SeqCst) {
                anyhow::bail!("Simulated lost acknowledgement");
            }
            Ok(())
        })
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "requires disposable OW_PG_TEST_URL, Node and built TypeScript SDK"]
async fn builder_and_reviewer_sessions() -> Result<()> {
    qualify(None, Scenario::Recovery).await
}

#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "requires two deployed Kubernetes workspace services and Spark qualification environment"]
async fn kubernetes_workspace_recovery() -> Result<()> {
    qualify(
        Some(std::env::var("OW_K8S_WORKSPACES")?),
        Scenario::Recovery,
    )
    .await
}

#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "requires a disposable websites registration, PostgreSQL, and physical verifier"]
async fn website_builder_app() -> Result<()> {
    qualify(
        Some(
            std::env::var("OW_WORKSPACE_SERVICES")
                .or_else(|_| std::env::var("OW_K8S_WORKSPACES"))?,
        ),
        Scenario::WebsiteBuilder,
    )
    .await
}

#[derive(Clone, Copy)]
enum Scenario {
    Recovery,
    WebsiteBuilder,
}

async fn qualify(k8s: Option<String>, scenario: Scenario) -> Result<()> {
    let root = tempfile::tempdir()?;
    let id = uuid::Uuid::new_v4().simple().to_string();
    let token = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let bundle = root.path().join("bundle");
    std::fs::create_dir_all(bundle.join("coworker"))?;
    std::fs::write(bundle.join("coworker/manifest.md"),"---\nid: workspace-example\nname: Workspace test\nversion: '1'\nrequires_folder: true\n---\nUse the selected file tools.")?;
    let names = ["list_files", "read_file", "write_file", "edit_file"];
    let bindings: serde_json::Map<_, _> = names
        .iter()
        .map(|n| (n.to_string(), json!({"server":"coding","tool":n})))
        .collect();
    std::fs::write(
        bundle.join("tools.json"),
        serde_json::to_vec(
            &json!({"version":2,"servers":[{"id":"coding","program":"$OPENWORKER","args":["mcp-coding","--workspace","$WORKSPACE"]}],"bindings":bindings}),
        )?,
    )?;
    let runner = std::env::var_os("OW_WORKSPACE_TEST_RUNNER")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/bin/true"));
    let mut cfg: Config = serde_json::from_value(json!({
        "database_env":"OW_PG_TEST_URL","schema":format!("ow_ws_{}",&id[..20]),"database_loopback_plaintext":true,
        "state_root":root.path().join("state"),"bundle":bundle,"runner":runner,"scripted":true,"model":"fixture",
        "api_token_file":"unused","actor":"tester","openshell":{"identity":"workspace-test","endpoint":"https://127.0.0.1:1","certificates":"unused","workspace":"default","image":format!("example.invalid/runner@sha256:{}","a".repeat(64))},
        "service":{"id":"workspace-test","namespace":"test","agent":"base"},
        "reference_workspaces":{"research":root.path().join("research.db")}
    }))?;
    if let Some(path) = &k8s {
        cfg.reference_workspaces.clear();
        cfg.kubernetes_workspaces = serde_json::from_slice(&std::fs::read(path)?)?;
    } else {
        cfg.workspace_registry.register(
            "websites",
            Arc::new(ProbeProvider {
                inner: ReferenceProvider::open(&root.path().join("websites.db"))?,
                lose_detach_ack: AtomicBool::new(true),
            }),
        )?;
    }
    let db = cfg.database()?;
    PostgresStore::migrate(&db)?;
    let store = PostgresStore::open(db.clone())?;
    store.auth_bootstrap(&token, "tester")?;
    let stop = CancellationToken::new();
    let _stop_on_drop = stop.clone().drop_guard();
    let api = cfg.api_runtime("api", store.clone())?;
    let app = App::new(&cfg, api, store.clone(), stop.clone())?;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let url = format!("http://{}", listener.local_addr()?);
    let http_stop = stop.clone();
    tokio::spawn(async move {
        axum::serve(listener, app.router())
            .with_graceful_shutdown(http_stop.cancelled_owned())
            .await
    });
    let signed = store.authorized(&token)?;
    let restricted = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    signed.auth_issue(
        "issue-restricted",
        serde_json::from_value(
            json!({"principal":"restricted","role":"operator","scope":{"kind":"owned"}}),
        )?,
        &restricted,
    )?;
    let installed = installation(&cfg)?;
    signed.catalog_put("profile","profile",json!({"id":"profile@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared","approvalMode":"bypass","autoSkipQuestions":true}),&installed)?;
    signed.catalog_activate("profile-on", "profile", "profile@1", None)?;
    let profile = signed.catalog_get("profile", "profile@1")?;
    let mut worker_configs = Vec::new();
    for agent in ["builder", "reviewer"] {
        let rid = format!("{agent}@1");
        let tools: &[&str] = match (scenario, agent) {
            (Scenario::WebsiteBuilder, "reviewer") => &["list_files", "read_file"],
            _ => &names,
        };
        signed.catalog_put(&format!("put-{agent}"),"release",json!({"id":rid,"instructions":"Use project tools.","tools":tools,"runtimeProfiles":["profile@1"],"defaultRuntimeProfile":"profile@1"}),&installed)?;
        signed.catalog_activate(
            &format!("activate-{agent}"),
            "release",
            &rid,
            Some(agent.into()),
        )?;
        let c = release_config(&cfg, &signed.catalog_get("release", &rid)?, &profile)?;
        let rt = c.runtime(&format!("template-{agent}"), store.clone())?;
        let select = selector(&c, &rt)?;
        rt.shutdown().await?;
        let factory: Factory =
            Arc::new(move |sid, s| c.runtime(&format!("wk-{}", &digest(sid.as_bytes())[..16]), s));
        worker_configs.push((select, factory));
    }
    let worker_stop = stop.clone();
    let restart_dir = root.path().to_owned();
    let supervisor = tokio::spawn(async move {
        for generation in 0..2 {
            let generation_stop = worker_stop.child_token();
            let mut tasks = Vec::new();
            for (select, factory) in &worker_configs {
                let worker = Worker::new(
                    db.clone(),
                    select.clone(),
                    Options::default(),
                    factory.clone(),
                )?;
                tasks.push(tokio::spawn(worker.run(generation_stop.clone())));
            }
            if generation == 0 {
                while !worker_stop.is_cancelled() && !restart_dir.join("restart").exists() {
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                }
            } else {
                std::fs::write(restart_dir.join("restarted"), "")?;
                worker_stop.cancelled().await;
            }
            generation_stop.cancel();
            for task in tasks {
                task.await??;
            }
            if worker_stop.is_cancelled() {
                break;
            }
        }
        Ok::<(), anyhow::Error>(())
    });
    let controller_stop = stop.clone();
    let controller = tokio::spawn(async move {
        while !controller_stop.is_cancelled() {
            if let Some((op, _)) = store.service_claim_for(Some(&installed))? {
                let e = store.clone().environment(&op.environment_id)?;
                let binding = Binding {
                    environment_id: e.id.clone(),
                    spec: e.spec.clone(),
                    remote_id: format!("fixture-{}", e.id),
                    remote_name: "fixture".into(),
                    workspace_scope: "default".into(),
                    policy_hash: "fixture-hash".into(),
                    policy_version: 1,
                };
                store.complete_environment(&e.id, e.revision, Some(binding))?;
                store.service_update_operation(&op.id, "ready", None)?;
                let release = store.catalog_get("release", &op.release_id)?;
                finish_ready_admission(&cfg, &store, &op, &release, &profile).await?;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        Ok::<(), anyhow::Error>(())
    });
    let mut exec =
        tokio::process::Command::new(std::env::var("OW_NODE").unwrap_or_else(|_| "node".into()));
    exec.arg(match scenario {
        Scenario::Recovery => "../typescript-sdk/test/workspaces-live.mjs",
        Scenario::WebsiteBuilder => "../typescript-sdk/test/website-builder-live.mjs",
    })
    .env("OW_SERVICE_URL", url)
    .env("OW_WS_RESTART_DIR", root.path())
    .env("OW_SERVICE_TOKEN", token)
    .env("OW_WS_RESTRICTED_TOKEN", restricted)
    .env("OW_WS_TEST_SECRET", TEST_SECRET)
    .kill_on_drop(true);
    let mut controller = controller;
    let status = tokio::select! {
        result=tokio::time::timeout(std::time::Duration::from_secs(if k8s.is_some() {420} else {160}),exec.status())=>result??,
        result=&mut controller=>{result??; anyhow::bail!("Controller ended early")}
    };
    stop.cancel();
    controller.await??;
    supervisor.await??;
    let database = std::env::var("OW_PG_TEST_URL")?;
    let schema = format!("ow_ws_{}", &id[..20]);
    tokio::task::spawn_blocking(move || -> Result<()> {
        let mut pg=postgres::Client::connect(&database,postgres::NoTls)?;
        for table in ["sessions","events","service_operations"] {
            let sql=format!("SELECT EXISTS (SELECT 1 FROM {schema}.{table} AS record WHERE row_to_json(record)::text LIKE $1)");
            let leaked:bool=pg.query_one(&sql,&[&format!("%{TEST_SECRET}%")])?.get(0);
            ensure!(!leaked,"Secret found in durable {table}");
        }
        pg.batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))?;
        Ok(())
    }).await??;
    if k8s.is_none() {
        let stored = std::fs::read(root.path().join("websites.db"))?;
        ensure!(
            !stored
                .windows(TEST_SECRET.len())
                .any(|w| w == TEST_SECRET.as_bytes()),
            "Secret found in provider database"
        );
    }
    ensure!(status.success(), "SDK workspace qualification failed");
    Ok(())
}
