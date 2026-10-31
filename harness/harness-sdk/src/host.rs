//! Multi-coworker host. Storage and process placement are independent choices.
use crate::{ProviderKind, RestartPolicy, Sandbox, ToolRegistry};
use anyhow::{bail, ensure, Context, Result};
use openworker_runtime::{
    model::Model,
    runtime::Runtime,
    store::{terminal, ScopedStore, SharedStore, StateStore, Store},
    team_service,
};
use openworker_store_postgres::{Config as PgConfig, PostgresStore, Tls};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::{Arc, Mutex, RwLock},
    time::Duration,
};
use tokio::sync::broadcast;
use tokio_util::sync::CancellationToken;
#[path = "host_catalog.rs"]
mod catalog;
#[path = "host_named_types.rs"]
mod named_types;
#[path = "host_runs.rs"]
mod runs;

#[derive(Default)]
struct Registry {
    profiles: BTreeMap<String, Arc<Runtime>>,
    agents: BTreeMap<String, crate::agents::compile::AgentProfile>,
    definitions: BTreeMap<String, crate::agents::Agent>,
    private_profiles: std::collections::BTreeSet<String>,
}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Storage {
    #[default]
    Sqlite,
    Postgres {
        connection_env: String,
        schema: String,
        ca_file: Option<PathBuf>,
        #[serde(default)]
        loopback_plaintext: bool,
    },
}
impl Storage {
    fn postgres(&self) -> Result<Option<PgConfig>> {
        let Self::Postgres {
            connection_env,
            schema,
            ca_file,
            loopback_plaintext,
        } = self
        else {
            return Ok(None);
        };
        ensure!(
            !connection_env.is_empty()
                && connection_env.len() <= 100
                && connection_env
                    .bytes()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
            "Invalid database credential reference"
        );
        let mut c = PgConfig::new(
            std::env::var(connection_env)
                .context("Database credential environment variable missing")?,
            schema.clone(),
        );
        c.tls = if *loopback_plaintext {
            Tls::LoopbackPlaintext
        } else {
            Tls::Verified {
                ca_file: ca_file.clone(),
            }
        };
        Ok(Some(c))
    }
    /// Explicit operator setup; never performed implicitly by host startup.
    pub fn migrate(&self) -> Result<()> {
        if let Some(c) = self.postgres()? {
            PostgresStore::migrate(&c)?;
        }
        Ok(())
    }
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoworkerConfig {
    pub bundle: PathBuf,
    pub model: String,
    #[serde(default)]
    pub provider: ProviderKind,
    #[serde(default)]
    pub servers: Option<PathBuf>,
    #[serde(default)]
    pub tool_policy: Option<PathBuf>,
    #[serde(default)]
    pub runtime_policy: Option<PathBuf>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostConfig {
    #[serde(default)]
    pub desktop: crate::DesktopOptions,
    pub state_dir: PathBuf,
    pub runner: PathBuf,
    #[serde(default)]
    pub storage: Storage,
    pub coworkers: Vec<CoworkerConfig>,
    pub sandbox: Sandbox,
    #[serde(default)]
    pub scripted: bool,
    /// Opt-in OTLP/HTTP export (content-free spans, metrics and logs). Point
    /// it at an OpenTelemetry Collector that forwards to your backend.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub telemetry: Option<openworker_telemetry::otlp::Config>,
    /// Agent definitions compiled by the host into coworker bundles under
    /// `state_dir/agents` (see `agents::Agent` for the JSON shape). May be
    /// used instead of, or alongside, hand-written `coworkers`.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub agents: Vec<crate::agents::Agent>,
}
#[derive(Clone)]
pub struct HostProcessConfig {
    pub program: PathBuf,
    pub host: HostConfig,
    pub restart: RestartPolicy,
}
pub(crate) struct Host {
    pub events: broadcast::Sender<openworker_runtime::store::Event>,
    pub gaps: broadcast::Sender<()>,
    pub failed: CancellationToken,
    store: SharedStore,
    registry: RwLock<Registry>,
    config: HostConfig,
    tools: RwLock<ToolRegistry>,
    pub callbacks: Arc<crate::callbacks::Bridge>,
    pub provider_callbacks: Arc<crate::provider_callbacks::Bridge>,
    catalog: catalog::Catalog,
    connections: Arc<openworker_runtime::connections::Manager>,
    instrumentation: openworker_telemetry::Telemetry,
    postgres: Option<PostgresStore>,
    stop: CancellationToken,
    admission: tokio::sync::Mutex<()>,
    registration: tokio::sync::Mutex<()>,
    task: Mutex<Option<tokio::task::JoinHandle<Result<()>>>>,
    telemetry: Mutex<Option<openworker_telemetry::Controller>>,
    /// Serialize run admission; waiting never holds this or the scheduler gate.
    run_admission: tokio::sync::Mutex<()>,
    /// Transient failures to surface to the UI; approval provenance is durable.
    auto_approval_failures: Mutex<std::collections::HashSet<String>>,
    _state_lock: Option<std::fs::File>,
}
impl HostConfig {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.scripted || cfg!(feature = "test-support"),
            "Scripted models require a development SDK build with test-support"
        );
        self.desktop.compaction.validate()?;
        ensure!(
            [&self.desktop.session_dir, &self.desktop.scratch_dir]
                .into_iter()
                .flatten()
                .all(|p| p.is_absolute()),
            "Desktop directories must be absolute"
        );
        ensure!(
            self.desktop.persist_sessions || matches!(self.storage, Storage::Sqlite),
            "Non-persistent sessions cannot select PostgreSQL"
        );
        ensure!(
            self.state_dir.is_absolute() && self.runner.is_absolute(),
            "Host paths must be absolute"
        );
        let agents = crate::agents::compile::flatten(&self.agents)
            .map_err(|e| anyhow::anyhow!(e.to_string()))?;
        ensure!(
            self.coworkers.len() + agents.len() <= 16,
            "Host supports at most 16 trusted coworkers or agents (subagents count)"
        );
        for c in &self.coworkers {
            ensure!(
                c.bundle.is_absolute()
                    && [&c.servers, &c.tool_policy, &c.runtime_policy]
                        .iter()
                        .all(|p| p.as_ref().is_none_or(|v| v.is_absolute())),
                "Coworker paths must be absolute"
            );
        }
        Ok(())
    }
}
impl Host {
    pub fn open(config: &HostConfig, mut registry: ToolRegistry) -> Result<Arc<Self>> {
        config.validate()?;
        // Selection remains per-agent. The implementation always runs in the
        // session's execution backend, never as an SDK-host callback.
        openworker_runtime::coding::register(&mut registry)?;
        openworker_runtime::platform::private_directory(&config.state_dir)?;
        // Own the directory before compiling or replacing any generated bundle.
        let session_dir = config
            .desktop
            .session_dir
            .as_ref()
            .unwrap_or(&config.state_dir);
        let state_lock = if !config.desktop.persist_sessions
            || session_dir != &config.state_dir
            || matches!(config.storage, Storage::Postgres { .. })
        {
            use fs2::FileExt;
            let path = config.state_dir.join("runtime.lock");
            if let Ok(meta) = std::fs::symlink_metadata(&path) {
                ensure!(
                    !openworker_runtime::platform::linked(&meta),
                    "State lock cannot be a link"
                );
            }
            let file = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(path)?;
            file.try_lock_exclusive()
                .context("Another host owns this state directory")?;
            Some(file)
        } else {
            None
        };
        let sqlite = if matches!(config.storage, Storage::Sqlite) {
            Some(if config.desktop.persist_sessions {
                Store::open(session_dir)?
            } else {
                Store::memory()?
            })
        } else {
            None
        };
        let catalog = catalog::Catalog::open(&config.state_dir)?;
        let callbacks = crate::callbacks::Bridge::new();
        let provider_callbacks = crate::provider_callbacks::Bridge::new(callbacks.events.clone());
        let mut servers = catalog.servers()?;
        for server in &config.desktop.mcp_servers {
            if let Some(saved) = servers.iter().find(|s| s.name() == server.name()) {
                ensure!(
                    saved == server,
                    "Startup MCP configuration conflicts with saved server"
                );
            } else {
                servers.push(server.clone());
            }
        }
        ensure!(servers.len() <= 16, "MCP server capacity exceeded");
        for server in servers {
            server.register(&mut registry)?;
        }
        for server in &config.desktop.mcp_servers {
            if !catalog.servers()?.iter().any(|s| s.name() == server.name()) {
                catalog.insert_server(server)?;
            }
        }
        for descriptor in catalog.tools()? {
            registry.register(callbacks.proxy(descriptor))?;
        }
        let connections = Arc::new(openworker_runtime::connections::Manager::open(
            &config.state_dir,
        )?);
        let mut definitions = catalog::merge(&config.agents, catalog.load()?)?;
        let flat = crate::agents::compile::flatten(&definitions)?;
        ensure!(
            flat.keys().all(|id| !id.starts_with("tt_")),
            "The tt_ namespace is reserved for session-scoped type revisions"
        );
        ensure!(
            flat.len() + config.coworkers.len() <= 16,
            "Saved agents exceed host capacity"
        );
        let (compiled, mut agents) =
            crate::agents::compile::compile(&config.state_dir, &definitions)
                .map_err(|e| anyhow::anyhow!(e.to_string()))?;
        let mut prepared = vec![];
        let mut ids = std::collections::BTreeSet::new();
        for c in config.coworkers.iter().chain(&compiled) {
            let mut registry = registry.clone();
            if let Some(p) = &c.servers {
                openworker_runtime::servers::load(p, &c.bundle, &mut registry)?;
            }
            let bundle =
                openworker_runtime::bundle::Bundle::load_with_registry(&c.bundle, &registry)?;
            ensure!(
                !bundle.id.starts_with("tt_"),
                "The tt_ namespace is reserved for session-scoped type revisions"
            );
            ensure!(ids.insert(bundle.id.clone()), "Duplicate coworker identity");
            agents.entry(bundle.id.clone()).or_insert_with(|| {
                crate::agents::compile::AgentProfile::plain(&bundle.id, &bundle.name)
            });
            prepared.push((c.clone(), registry, bundle));
        }
        let pg = config
            .storage
            .postgres()?
            .map(PostgresStore::open)
            .transpose()?;
        // This SDK host owns its schema's execution. Distributed server worker
        // scheduling remains a separate host mode, not a property of SQL syntax.
        let mut restored = if let Some(db) = &pg {
            db.claim_sdk_host(ids.iter().cloned().collect())?
        } else {
            vec![]
        };
        let mut storage: Box<dyn StateStore> = match &pg {
            Some(db) => Box::new(db.clone()),
            None => Box::new(sqlite.context("SQLite store missing")?),
        };
        if pg.is_none() {
            for s in storage
                .list_metadata()?
                .into_iter()
                .filter(|s| s.agent_team.is_some())
            {
                let doc: openworker_runtime::agent_team::Document = serde_json::from_value(
                    storage.agent_team(&s.id, "snapshot", &json!({}), None)?,
                )?;
                if doc
                    .roster
                    .instance(&doc.lead)
                    .is_some_and(|i| i.session_id == s.id)
                {
                    restored.extend(openworker_runtime::team_type_catalog::restore(
                        &doc, &s, &ids,
                    )?);
                }
            }
        }
        let mut private_profiles = std::collections::BTreeSet::new();
        for (lead, record) in restored {
            let source = definitions
                .iter()
                .find(|a| a.id == lead.coworker)
                .context("Scoped type's parent definition is missing")?;
            let agent = named_types::scoped_agent(&lead, &record, source)?;
            ensure!(
                private_profiles.len() < 128 && !ids.contains(&agent.id),
                "Scoped runtime capacity or identity conflict"
            );
            private_profiles.insert(agent.id.clone());
            let (configs, extra) =
                crate::agents::compile::compile(&config.state_dir, std::slice::from_ref(&agent))?;
            for (id, mut profile) in extra {
                profile.worker_only = true;
                agents.insert(id, profile);
            }
            for c in configs {
                let mut registry = registry.clone();
                if let Some(p) = &c.servers {
                    openworker_runtime::servers::load(p, &c.bundle, &mut registry)?;
                }
                let bundle =
                    openworker_runtime::bundle::Bundle::load_with_registry(&c.bundle, &registry)?;
                prepared.push((c, registry, bundle));
            }
            definitions.push(agent);
        }
        storage.recover()?;
        let store = Arc::new(Mutex::new(storage));
        // The exporter builds a blocking HTTP client, which must not happen on
        // a Tokio worker; a short-lived plain thread works on any runtime flavor.
        let (telemetry, controller) = match config.telemetry.clone() {
            Some(c) => {
                let (t, ctl) = std::thread::spawn(move || c.start("sdk-host"))
                    .join()
                    .map_err(|_| anyhow::anyhow!("Telemetry startup panicked"))?
                    .context("Telemetry configuration failed; check the explicit endpoint")?;
                (t, Some(ctl))
            }
            None => (openworker_telemetry::Telemetry::default(), None),
        };
        let mut profiles = BTreeMap::new();
        for (c, registry, bundle) in prepared {
            let mut rt = Runtime::open_instrumented(
                &config.state_dir.join("profiles").join(&bundle.id),
                &c.bundle,
                &config.runner,
                Model::new(c.model.clone(), config.scripted).with_provider(c.provider),
                config.sandbox,
                c.tool_policy.as_deref(),
                c.runtime_policy.as_deref(),
                registry,
                Some(Box::new(ScopedStore {
                    inner: store.clone(),
                    coworker: bundle.id.clone(),
                })),
                telemetry.clone(),
            )?;
            Arc::get_mut(&mut rt)
                .context("Runtime published before provider binding")?
                .connections = connections.clone();
            profiles.insert(bundle.id, rt);
            if let Some(db) = &pg {
                for s in store.lock().unwrap().list_metadata()? {
                    db.renew(&s.id)?;
                }
            }
        }
        for s in store.lock().unwrap().list_metadata()? {
            ensure!(
                profiles.contains_key(&s.coworker) && s.environment.is_none(),
                "Host configuration does not cover saved session; use its original execution host"
            );
        }
        let (events, _) = broadcast::channel(256);
        let (gaps, _) = broadcast::channel(16);
        let host = Arc::new(Self {
            events,
            gaps,
            failed: CancellationToken::new(),
            store,
            registry: RwLock::new(Registry {
                profiles,
                agents,
                definitions: definitions.into_iter().map(|a| (a.id.clone(), a)).collect(),
                private_profiles,
            }),
            config: config.clone(),
            tools: RwLock::new(registry),
            callbacks,
            provider_callbacks,
            catalog,
            connections,
            instrumentation: telemetry,
            postgres: pg,
            stop: CancellationToken::new(),
            admission: tokio::sync::Mutex::new(()),
            registration: tokio::sync::Mutex::new(()),
            task: Mutex::new(None),
            telemetry: Mutex::new(controller),
            run_admission: Default::default(),
            auto_approval_failures: Default::default(),
            _state_lock: state_lock,
        });
        let weak = Arc::downgrade(&host);
        let stop = host.stop.clone();
        let task = tokio::spawn(async move {
            loop {
                tokio::select! {_=stop.cancelled()=>return Ok(()),_=tokio::time::sleep(Duration::from_millis(100))=>{}}
                let Some(host) = weak.upgrade() else {
                    return Ok(());
                };
                if let Err(error) = host.tick().await {
                    host.failed.cancel();
                    for rt in host.runtimes() {
                        rt.request_shutdown();
                    }
                    return Err(error);
                }
            }
        });
        *host.task.lock().unwrap() = Some(task);
        runs::spawn_auto_approver(&host);
        for rt in host.runtimes() {
            host.forward_events(&rt);
        }
        Ok(host)
    }
    fn runtimes(&self) -> Vec<Arc<Runtime>> {
        self.registry
            .read()
            .unwrap()
            .profiles
            .values()
            .cloned()
            .collect()
    }
    fn runtime(&self, id: &str) -> Result<Arc<Runtime>> {
        self.registry
            .read()
            .unwrap()
            .profiles
            .get(id)
            .cloned()
            .context("Unknown trusted coworker")
    }
    fn forward_events(&self, rt: &Arc<Runtime>) {
        let mut rx = rt.events.subscribe();
        let tx = self.events.clone();
        let gaps = self.gaps.clone();
        let stop = self.stop.clone();
        tokio::spawn(async move {
            loop {
                tokio::select! {_=stop.cancelled()=>break,e=rx.recv()=>match e{Ok(e)=>{let _=tx.send(e);},Err(broadcast::error::RecvError::Lagged(_))=>{let _=gaps.send(());},Err(_)=>break}}
            }
        });
    }
    /// Prepare off the async executor and outside the scheduler's admission gate.
    /// Existing sessions and PostgreSQL lease renewal must continue while hashing
    /// executables/validating a new profile. Publish under admission only at the end.
    async fn register_agent(&self, p: Value) -> Result<Value> {
        let _registration = self.registration.lock().await;
        ensure!(
            !self.stop.is_cancelled() && !self.failed.is_cancelled(),
            "Host unavailable"
        );
        ensure!(
            p.is_object() && p.as_object().unwrap().len() == 1 && p.get("agent").is_some(),
            "Expected an agent definition"
        );
        let agent: crate::agents::Agent = serde_json::from_value(p["agent"].clone())?;
        let flat = crate::agents::compile::flatten(std::slice::from_ref(&agent))?;
        ensure!(
            flat.keys().all(|id| !id.starts_with("tt_")),
            "The tt_ namespace is reserved for session-scoped type revisions"
        );
        {
            let registry = self.registry.read().unwrap();
            if let Some(existing) = registry.definitions.get(&agent.id) {
                ensure!(
                    serde_json::to_value(existing)? == serde_json::to_value(&agent)?,
                    "Agent identity already has a different definition; use a new name"
                );
                // Also persist a definition originally supplied in HostConfig when
                // the application explicitly registers it through this API.
                self.catalog.insert(&agent)?;
                return Ok(json!({"agent":registry.agents[&agent.id],"duplicate":true}));
            }
            ensure!(
                registry.profiles.len() + flat.len() <= 16,
                "Host agent capacity exceeded"
            );
            ensure!(
                flat.keys().all(|id| !registry.profiles.contains_key(id)),
                "Agent or subagent identity is already registered"
            );
        }
        let config = self.config.clone();
        let tools = self.tools.read().unwrap().clone();
        let store = self.store.clone();
        let telemetry = self.instrumentation.clone();
        let connections = self.connections.clone();
        let definition = agent.clone();
        let (profiles, agents) = tokio::task::spawn_blocking(move || -> Result<_> {
            let (configs, agents) =
                crate::agents::compile::compile(&config.state_dir, &[definition])?;
            let mut profiles = BTreeMap::new();
            for c in configs {
                let mut registry = tools.clone();
                if let Some(path) = &c.servers {
                    openworker_runtime::servers::load(path, &c.bundle, &mut registry)?;
                }
                let bundle =
                    openworker_runtime::bundle::Bundle::load_with_registry(&c.bundle, &registry)?;
                let mut runtime = Runtime::open_instrumented(
                    &config.state_dir.join("profiles").join(&bundle.id),
                    &c.bundle,
                    &config.runner,
                    Model::new(c.model, config.scripted).with_provider(c.provider),
                    config.sandbox,
                    c.tool_policy.as_deref(),
                    c.runtime_policy.as_deref(),
                    registry,
                    Some(Box::new(ScopedStore {
                        inner: store.clone(),
                        coworker: bundle.id.clone(),
                    })),
                    telemetry.clone(),
                )?;
                Arc::get_mut(&mut runtime)
                    .context("Runtime published before provider binding")?
                    .connections = connections.clone();
                profiles.insert(bundle.id, runtime);
            }
            Ok((profiles, agents))
        })
        .await
        .context("Agent preparation task failed")??;
        let _admission = self.admission.lock().await;
        ensure!(
            !self.stop.is_cancelled() && !self.failed.is_cancelled(),
            "Host unavailable"
        );
        // Registration is durable before it can create a session or return success.
        self.catalog.insert(&agent)?;
        for rt in profiles.values() {
            self.forward_events(rt);
        }
        let response = json!({"agent":agents[&agent.id],"duplicate":false});
        let mut registry = self.registry.write().unwrap();
        registry.profiles.extend(profiles);
        registry.agents.extend(agents);
        registry.definitions.insert(agent.id.clone(), agent);
        Ok(response)
    }
    async fn tick(&self) -> Result<()> {
        let _admission = self.admission.lock().await;
        let sessions = self.store.lock().unwrap().list_metadata()?;
        self.refresh_named_types(&sessions).await?;
        if let Some(db) = &self.postgres {
            for s in &sessions {
                db.renew(&s.id)?;
            }
        }
        self.store.lock().unwrap().team_tick()?;
        let cancellations = self.store.lock().unwrap().team_cancellations()?;
        for (sid, tid) in cancellations {
            let s = self.store.lock().unwrap().metadata(&sid)?;
            if s.turn
                .as_ref()
                .is_some_and(|t| t.id == tid && !terminal(&t.status))
            {
                self.runtime(&s.coworker)?
                    .dispatch("turn/cancel", json!({"session_id":sid,"turn_id":tid}))
                    .await?;
            }
            self.store.lock().unwrap().team_cancel_settled(&sid, &tid)?;
        }
        for s in sessions {
            if s.closed {
                continue;
            }
            let rt = self.runtime(&s.coworker)?;
            if let Some(member) = &s.agent_team {
                if self
                    .store
                    .lock()
                    .unwrap()
                    .agent_team(&s.id, "snapshot", &json!({}), None)?["stopped"]
                    == true
                {
                    // The durable team stop is an outbox, not just an admission
                    // flag. Reconcile every active member after response loss or
                    // host restart, including parked children. Cancelling only
                    // the lead would leave a running child behind.
                    if let Some(turn) = &s.turn {
                        if !terminal(&turn.status) && turn.status != "cancelling" {
                            rt.dispatch(
                                "turn/cancel",
                                json!({"session_id":s.id,"turn_id":turn.id}),
                            )
                            .await?;
                        }
                    }
                    continue;
                }

                if !rt.is_busy(&s.id) && rt.has_capacity() && rt.model_credentials_ready(&s) {
                    if s.turn
                        .as_ref()
                        .is_some_and(|t| t.status == "resuming_agents")
                    {
                        rt.dispatch("team/resume", json!({"session_id":s.id}))
                            .await?;
                        continue;
                    }
                    let wake =
                        self.store
                            .lock()
                            .unwrap()
                            .agent_team(&s.id, "wake", &json!({}), None)?;
                    if wake["resume"] == true {
                        rt.dispatch("team/resume", json!({"session_id":s.id}))
                            .await?;
                        continue;
                    }
                    if wake["wake"] == true {
                        rt.dispatch("turn/start",json!({"session_id":s.id,"submission_id":format!("inbox-{}",member.inbox_cursor),"text":"Continue from the agent inbox. Collaborator messages are not human approval."})).await?;
                        continue;
                    }
                }
                let pending =
                    self.store
                        .lock()
                        .unwrap()
                        .agent_team(&s.id, "pending", &json!({}), None)?;
                // Only the lead's host reconciles reservations. Stored canonical
                // IDs make restart reconciliation idempotent, never spawn twice.
                for entry in pending.as_array().context("Invalid pending instances")? {
                    let instance: openworker_runtime::team_coordination::Instance =
                        serde_json::from_value(entry["instance"].clone())?;
                    let doc: openworker_runtime::agent_team::Document =
                        serde_json::from_value(self.store.lock().unwrap().agent_team(
                            &s.id,
                            "snapshot",
                            &json!({}),
                            None,
                        )?)?;
                    if member.agent_id != doc.lead {
                        break;
                    }
                    let runtime_type =
                        openworker_runtime::agent_team::runtime_type(&doc, &instance)?;
                    let child_rt = self.runtime(&runtime_type)?;
                    let exists = self
                        .store
                        .lock()
                        .unwrap()
                        .list_metadata()?
                        .iter()
                        .any(|v| v.id == instance.session_id);
                    if !exists {
                        let title = doc
                            .roster
                            .request(&instance.agent_id)
                            .and_then(|r| r.title.as_deref())
                            .unwrap_or(&instance.agent_id);
                        let mut parameters = json!({"workspace":s.workspace,"name":title,"coworker":runtime_type,"permissions":s.permissions,"compaction":s.compaction_config});
                        let definition = self
                            .registry
                            .read()
                            .unwrap()
                            .definitions
                            .get(&runtime_type)
                            .cloned()
                            .context("Registered team type missing")?;
                        parameters["permissions"] = json!({"mode":definition.approvals.mode(),"auto_skip_questions":definition.auto_skip_questions});
                        if let Some(model) = definition.model {
                            parameters["model"] = json!(model);
                        }
                        if let crate::agents::Approvals::Auto(model) = definition.approvals {
                            parameters["reviewer_model"] = json!(model);
                        }
                        if let Some(binding) = &s.environment {
                            parameters["environment_id"] = json!(binding.environment_id);
                        }
                        let mut child = child_rt.prepare_session(parameters)?;
                        child.id = instance.session_id.clone();
                        child.agent_team = Some(openworker_runtime::agent_team::Membership {
                            team_id: doc.id.clone(),
                            agent_id: instance.agent_id.clone(),
                            inbox_cursor: 0,
                            last_output: None,
                            notices: vec![],
                            waiting: None,
                        });
                        self.store.lock().unwrap().save(
                            &child,
                            "team_instance_created",
                            json!({"parent_session_id":s.id,"agent_id":instance.agent_id}),
                        )?;
                    }
                    self.store.lock().unwrap().agent_team(
                        &s.id,
                        "ready",
                        &json!({"agent_id":instance.agent_id}),
                        None,
                    )?;
                }
                // Start each reserved task exactly once through normal turn dedup.
                let doc: openworker_runtime::agent_team::Document = serde_json::from_value(
                    self.store
                        .lock()
                        .unwrap()
                        .agent_team(&s.id, "snapshot", &json!({}), None)?,
                )?;
                if s.turn.is_none()
                    && member.agent_id != doc.lead
                    && rt.has_capacity()
                    && rt.model_credentials_ready(&s)
                {
                    if let Some(message) = doc.tasks.get(&member.agent_id) {
                        rt.dispatch("turn/start",json!({"session_id":s.id,"submission_id":format!("initial-{}",member.agent_id),"text":openworker_runtime::agent_team::delegated_task(message)})).await?;
                        continue;
                    }
                }
            }

            if let Some(db) = &self.postgres {
                if let Some(c) = db.next_control(&s.id)? {
                    rt.apply_control(&s.id, &c.command.request_id).await?;
                    continue;
                }
            }
            if s.closed
                || s.provider_blocked
                || rt.is_busy(&s.id)
                || !rt.has_capacity()
                || !rt.model_credentials_ready(&s)
                || s.turn.as_ref().is_some_and(|t| !terminal(&t.status))
            {
                continue;
            }
            let next = self.store.lock().unwrap().team_next(&s.id)?;
            if let Some((key, text)) = next {
                rt.dispatch(
                    "turn/start",
                    json!({"session_id":s.id,"submission_id":key,"text":text}),
                )
                .await?;
            } else if let Some((key, text)) = openworker_runtime::assistant_tools::due(
                &s,
                openworker_runtime::assistant_tools::now(),
            ) {
                rt.dispatch(
                    "turn/start",
                    json!({"session_id":s.id,"submission_id":key,"text":text}),
                )
                .await?;
            }
        }
        Ok(())
    }
    pub async fn dispatch(&self, method: &str, p: Value) -> Result<Value> {
        if method == "provider/test" {
            ensure!(
                !self.stop.is_cancelled() && !self.failed.is_cancelled(),
                "Host unavailable"
            );
            let choice: openworker_runtime::connections::Choice = serde_json::from_value(p)
                .map_err(|_| anyhow::anyhow!("Invalid provider test configuration"))?;
            let selection = self.connections.resolve(&choice)?;
            let mut model = openworker_runtime::model::Model::new(choice.model, false);
            model.connection = Some((self.connections.clone(), selection));
            model.probe().await?;
            return Ok(json!({"connected":true}));
        }
        if method == "provider/auth/result" {
            return self.provider_callbacks.respond(p);
        }
        if method == "mcp/register" {
            let _registration = self.registration.lock().await;
            ensure!(
                !self.stop.is_cancelled() && !self.failed.is_cancelled(),
                "Host unavailable"
            );
            let server: crate::McpServer = serde_json::from_value(p)?;
            let saved = self.catalog.servers()?;
            if let Some(existing) = saved.iter().find(|s| s.name() == server.name()) {
                ensure!(
                    existing == &server,
                    "MCP server identity is immutable; use a new name"
                );
                return Ok(json!({"registered":true,"duplicate":true}));
            }
            ensure!(saved.len() < 16, "MCP server capacity exceeded");
            let mut registry = self.tools.read().unwrap().clone();
            server.register(&mut registry)?;
            self.catalog.insert_server(&server)?;
            *self.tools.write().unwrap() = registry;
            return Ok(json!({"registered":true}));
        }
        if method == "application/result" || method == "application/progress" {
            return self
                .callbacks
                .respond(p, method == "application/progress")
                .await;
        }
        if method == "application/register" {
            let _registration = self.registration.lock().await;
            ensure!(
                !self.stop.is_cancelled() && !self.failed.is_cancelled(),
                "Host unavailable"
            );
            let owner = openworker_runtime::runtime::text(&p, "owner", 100)?;
            let descriptors: Vec<openworker_tools::ToolDescriptor> =
                serde_json::from_value(p["tools"].clone())?;
            ensure!(
                !descriptors.is_empty() && descriptors.len() <= 128,
                "Invalid application tool count"
            );
            let saved = self.catalog.tools()?;
            let mut registry = self.tools.read().unwrap().clone();
            for descriptor in &descriptors {
                self.callbacks
                    .check_owner(&descriptor.qualified_name, owner)?;
                ensure!(
                    serde_json::to_vec(descriptor)?.len() <= 64000,
                    "Tool definition exceeds bounds"
                );
                if let Some(existing) = saved
                    .iter()
                    .find(|d| d.qualified_name == descriptor.qualified_name)
                {
                    ensure!(
                        existing == descriptor,
                        "Application tool definition changed"
                    );
                } else {
                    registry.register(self.callbacks.proxy(descriptor.clone()))?;
                }
            }
            self.catalog.insert_tools(&descriptors)?;
            *self.tools.write().unwrap() = registry;
            for descriptor in descriptors {
                self.callbacks.bind(&descriptor.qualified_name, owner);
            }
            return Ok(json!({"registered":true}));
        }
        if method == "agent/register" {
            return self.register_agent(p).await;
        }
        // Agent/run methods wait and compose other calls; they take the
        // admission lock per internal call, never across a wait.
        if runs::METHODS.contains(&method) {
            return Box::pin(self.agent_dispatch(method, p)).await;
        }
        let mut result = Box::pin(self.dispatch_locked(method, p)).await?;
        if method == "initialize" {
            if let Some(c) = result
                .get_mut("capabilities")
                .and_then(Value::as_object_mut)
            {
                c.insert("turn_limits".into(), json!(true));
                c.insert("agent_runs".into(), json!(true));
                c.insert(
                    "agent_teams".into(),
                    json!(self.store.lock().unwrap().supports_agent_teams()),
                );
                c.insert("dynamic_agents".into(), json!(true));
                c.insert("session_model_override".into(), json!(true));
                c.insert("sdk_question_policy".into(), json!(true));
                c.insert("memory_tools".into(), json!(true));
                c.insert("scheduled_tasks".into(), json!(true));
                c.insert("coding_tools".into(), json!(true));
                c.insert("coding_processes".into(), json!(cfg!(target_os = "linux")));
                c.insert("model_providers".into(), json!(true));
                c.insert("provider_probe".into(), json!(true));
                c.insert("session_send".into(), json!(true));
                c.insert("session_state".into(), json!(true));
                c.insert("application_callbacks".into(), json!(true));
                c.insert("application_tool_images".into(), json!(true));
                c.insert("provider_access_tokens".into(), json!(true));
            }
        }
        Ok(result)
    }
    async fn dispatch_locked(&self, method: &str, mut p: Value) -> Result<Value> {
        let _admission = self.admission.lock().await;
        ensure!(
            !self.stop.is_cancelled() && !self.failed.is_cancelled(),
            "Host stopped or unavailable; reconcile durable state"
        );
        if self
            .task
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|t| t.is_finished())
        {
            bail!("Host execution unavailable; shut down and reconcile durable sessions");
        }
        match method {
            "provider/auth/register" => {
                return self.provider_callbacks.register(&self.connections, p)
            }
            "provider/auth/update" => {
                let name = openworker_runtime::runtime::text(&p, "name", 64)?;
                self.connections
                    .update_access_token(name, crate::provider_callbacks::parse_token(&p)?)?;
                return Ok(json!({"updated":true}));
            }
            "provider/set" => return self.connections.set_provider(p),
            "provider/list" | "connection/list" => return self.connections.list(),
            "connection/set" => {
                if let Some(object) = p.as_object_mut() {
                    object.remove("coworker");
                }
                return self.connections.set(p);
            }
            "provider/remove" | "connection/remove" => {
                return self
                    .connections
                    .remove(openworker_runtime::runtime::text(&p, "id", 64)?)
            }
            "model/defaults/get" => return Ok(json!({"defaults":self.connections.defaults()?})),
            "model/defaults/set" => {
                if let Some(object) = p.as_object_mut() {
                    object.remove("coworker");
                }
                return self.connections.set_defaults(p);
            }
            "initialize" if self.runtimes().is_empty() => {
                ensure!(
                    p["protocol_version"] == openworker_runtime::PROTOCOL_VERSION,
                    "Unsupported protocol version"
                );
                return Ok(
                    json!({"protocol_version":openworker_runtime::PROTOCOL_VERSION,
                    "implementation":"native-rust","host_os":std::env::consts::OS,
                    "execution":{"sandbox":self.config.sandbox},
                    "capabilities":{"push":true,"event_replay":true,"durable_teams":true}}),
                );
            }
            "agent-team/create" => {
                let sid = openworker_runtime::runtime::text(&p, "session_id", 80)?;
                ensure!(
                    p.as_object().is_some_and(|p| p.keys().all(|k| [
                        "session_id",
                        "agent_id",
                        "agent_types",
                        "policy",
                        "limits",
                        "delegation"
                    ]
                    .contains(&k.as_str()))),
                    "Unknown team configuration"
                );
                let lead = self.store.lock().unwrap().metadata(sid)?;
                ensure!(
                    lead.package.team_role.is_none(),
                    "Use standalone agent definitions for named teams"
                );
                ensure!(lead.execution_backend==openworker_runtime::execution::Backend::Off || lead.environment.as_ref().is_some_and(|e|e.spec.sharing==openworker_runtime::environment::Sharing::Shared),"Named OpenShell teams require a shared service environment; connect to the service with a shared RuntimeProfile");
                let types: Vec<String> = serde_json::from_value(p["agent_types"].clone())?;
                self.validate_named_delegation(&lead, &p)?;
                {
                    let registry = self.registry.read().unwrap();
                    for kind in &types {
                        ensure!(
                            registry.definitions.contains_key(kind),
                            "Unknown registered agent type"
                        );
                    }
                }
                return self
                    .store
                    .lock()
                    .unwrap()
                    .agent_team(sid, "create", &p, None)
                    .map(openworker_runtime::agent_team::public_snapshot);
            }
            "agent-team/command" => {
                let sid = openworker_runtime::runtime::text(&p, "session_id", 80)?;
                let operation = openworker_runtime::runtime::text(&p, "operation", 64)?;
                ensure!(
                    matches!(operation, "snapshot" | "command" | "team_read_board"),
                    "Unsupported application team command"
                );
                let result =
                    self.store
                        .lock()
                        .unwrap()
                        .agent_team(sid, operation, &p["arguments"], None)?;
                return Ok(if operation == "snapshot" {
                    openworker_runtime::agent_team::public_snapshot(result)
                } else {
                    result
                });
            }
            "team/create" => {
                return self
                    .store
                    .lock()
                    .unwrap()
                    .team_create(serde_json::from_value(p)?)
            }
            "team/snapshot" => {
                return self
                    .store
                    .lock()
                    .unwrap()
                    .team_snapshot(openworker_runtime::runtime::text(&p, "team_id", 100)?)
            }
            "team/command" => {
                let id = openworker_runtime::runtime::text(&p, "team_id", 100)?.to_owned();
                let command: team_service::Command = serde_json::from_value(p["command"].clone())?;
                return self.store.lock().unwrap().team_command(&id, command);
            }
            "team/events" => {
                return self.store.lock().unwrap().team_events(
                    openworker_runtime::runtime::text(&p, "team_id", 100)?,
                    p["after"].as_i64().context("Cursor required")?,
                )
            }
            "coworker/list" => {
                let mut all = vec![];
                for rt in self.runtimes() {
                    all.extend(
                        rt.dispatch(method, json!({})).await?["coworkers"]
                            .as_array()
                            .unwrap()
                            .clone(),
                    );
                }
                return Ok(json!({"coworkers":all}));
            }
            "session/list" => {
                let mut all = vec![];
                for rt in self.runtimes() {
                    all.extend(
                        rt.dispatch(method, json!({})).await?["sessions"]
                            .as_array()
                            .unwrap()
                            .clone(),
                    );
                }
                return Ok(json!({"sessions":all}));
            }
            _ => {}
        }
        let coworker = if let Some(sid) = p["session_id"].as_str() {
            self.store.lock().unwrap().metadata(sid)?.coworker
        } else {
            p["coworker"]
                .as_str()
                .map(str::to_owned)
                .or_else(|| {
                    self.registry
                        .read()
                        .unwrap()
                        .profiles
                        .keys()
                        .next()
                        .cloned()
                })
                .context("No agent registered; create an agent first")?
        };
        let rt = self.runtime(&coworker)?;
        if method == "session/create" {
            ensure!(
                !self
                    .registry
                    .read()
                    .unwrap()
                    .private_profiles
                    .contains(&coworker),
                "Scoped types can only be spawned by their owning team"
            );
            let workspace =
                std::path::Path::new(openworker_runtime::runtime::text(&p, "workspace", 4096)?)
                    .canonicalize()?;
            for protected in std::iter::once(&self.config.state_dir)
                .chain(self.config.desktop.session_dir.iter())
            {
                if let Ok(protected) = protected.canonicalize() {
                    ensure!(
                        !workspace.starts_with(&protected) && !protected.starts_with(&workspace),
                        "Workspace must not overlap harness state or session storage"
                    );
                }
            }
            if p.get("compaction").is_none() {
                p["compaction"] = json!(self.config.desktop.compaction);
            }
        }
        // Both creation and policy preview validate the requested coworker identity.
        if !matches!(method, "session/create" | "policy/resolve") {
            p.as_object_mut()
                .context("Parameters must be an object")?
                .remove("coworker");
        }
        let sid = p["session_id"].as_str().map(str::to_owned);
        let mut result = Box::pin(rt.dispatch(method, p)).await?;
        if method == "session/attach" {
            if let Some(sid) = sid {
                let s = self.store.lock().unwrap().metadata(&sid)?;
                result["session"]["model_credentials_ready"] =
                    json!(rt.model_credentials_ready(&s));
            }
        }
        Ok(result)
    }
    pub async fn shutdown(&self) -> Result<()> {
        self.stop.cancel();
        // Fence dynamic registrations before shutting down their runtimes.
        let _admission = self.admission.lock().await;
        drop(_admission);
        let task = self.task.lock().unwrap().take();
        if let Some(t) = task {
            let _ = t.await;
        }
        for rt in self.runtimes() {
            rt.request_shutdown();
        }
        for rt in self.runtimes() {
            rt.shutdown().await?;
        }
        if let Some(db) = &self.postgres {
            for s in self.store.lock().unwrap().list_metadata()? {
                db.release(&s.id)?;
            }
        }
        // Bounded flush (at most 5 s); export problems never fail shutdown.
        let controller = self.telemetry.lock().unwrap().take();
        if let Some(controller) = controller {
            let _ =
                tokio::task::spawn_blocking(move || controller.shutdown(Duration::from_secs(5)))
                    .await;
        }
        Ok(())
    }
}
impl Drop for Host {
    fn drop(&mut self) {
        self.stop.cancel();
        for rt in self.runtimes() {
            rt.request_shutdown();
        }
    }
}
