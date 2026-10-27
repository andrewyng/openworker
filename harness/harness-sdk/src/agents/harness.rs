//! One builder for both backends. Agent definitions travel to the host as
//! data (`HostConfig.agents`); the host compiles and runs them.
use super::session::{Session, SessionRef};
use super::Agent;
use crate::{
    ClientOptions, Error, HarnessClient, HostConfig, HostProcessConfig, RestartPolicy, Result,
    Sandbox, Storage, TelemetryConfig, ToolRegistry,
};
use serde_json::json;
use std::path::{Path, PathBuf};

pub struct HarnessBuilder {
    providers: Vec<crate::ModelProvider>,
    state_dir: Option<PathBuf>,
    runner: Option<PathBuf>,
    desktop: crate::DesktopOptions,
    agents: Vec<Agent>,
    registry: ToolRegistry,
    storage: Storage,
    sandbox: Sandbox,
    scripted: bool,
    telemetry: Option<TelemetryConfig>,
    options: ClientOptions,
}

impl HarnessBuilder {
    pub fn model_provider(mut self, provider: crate::ModelProvider) -> Self {
        self.providers.push(provider);
        self
    }
    pub fn mcp_server(mut self, server: crate::McpServer) -> Self {
        self.desktop.mcp_servers.push(server);
        self
    }
    pub fn state_dir(mut self, path: impl Into<PathBuf>) -> Self {
        self.state_dir = Some(path.into());
        self
    }
    pub fn executable(mut self, path: impl Into<PathBuf>) -> Self {
        self.runner = Some(path.into());
        self
    }
    pub fn persist_sessions(mut self, value: bool) -> Self {
        self.desktop.persist_sessions = value;
        self
    }
    pub fn session_dir(mut self, path: impl Into<PathBuf>) -> Self {
        self.desktop.session_dir = Some(path.into());
        self
    }
    pub fn scratch_dir(mut self, path: impl Into<PathBuf>) -> Self {
        self.desktop.scratch_dir = Some(path.into());
        self
    }
    pub fn compact_at_context_fraction(mut self, value: f64) -> Self {
        self.desktop.compaction.threshold_pct = value;
        self
    }
    pub async fn start(self) -> Result<Harness> {
        self.embedded().await
    }
    /// Register an agent (and its subagents).
    pub fn agent(mut self, agent: Agent) -> Self {
        self.agents.push(agent);
        self
    }
    /// Native tools, MCP and HTTP providers that agents' tool names resolve to.
    /// Embedded mode only: a managed child resolves tools linked into its own
    /// executable.
    pub fn tools(mut self, registry: ToolRegistry) -> Self {
        self.registry = registry;
        self
    }
    /// SQLite (default) or Postgres.
    pub fn storage(mut self, storage: Storage) -> Self {
        self.storage = storage;
        self
    }
    /// Execution sandbox. Defaults to the engine default (OpenShell).
    pub fn sandbox(mut self, sandbox: Sandbox) -> Self {
        self.sandbox = sandbox;
        self
    }
    /// Scripted test models instead of live providers.
    pub fn scripted(mut self, scripted: bool) -> Self {
        self.scripted = scripted;
        self
    }
    /// Export content-free traces, metrics and logs over OTLP/HTTP to
    /// `endpoint`, an OpenTelemetry Collector origin such as
    /// `http://127.0.0.1:4318` (HTTPS required unless loopback). No auth
    /// headers are sent: let the collector add credentials and forward to
    /// Datadog, Grafana, Langfuse or any OTLP backend.
    pub fn otlp(self, endpoint: impl Into<String>) -> Self {
        self.telemetry(TelemetryConfig {
            endpoint: endpoint.into(),
            ..TelemetryConfig::default()
        })
    }
    /// Full telemetry settings: deployment label, authentication headers
    /// (secrets read from environment variables), signal selection and opt-in
    /// content capture. Example, direct to Langfuse:
    ///
    /// ```rust,no_run
    /// # use openworker_sdk::agents::*;
    /// # fn example(builder: HarnessBuilder) {
    /// let builder = builder.telemetry(TelemetryConfig {
    ///     endpoint: "https://cloud.langfuse.com/api/public/otel".into(),
    ///     headers: vec![
    ///         TelemetryHeader::secret("Authorization", "LANGFUSE_OTLP_AUTH"), // "Basic <b64 pk:sk>"
    ///         TelemetryHeader::literal("x-langfuse-ingestion-version", "4"),
    ///     ],
    ///     signals: vec![TelemetrySignal::Traces],
    ///     content: ContentPolicy { capture: Capture::Full, ..ContentPolicy::default() },
    ///     ..TelemetryConfig::default()
    /// });
    /// # }
    /// ```
    pub fn telemetry(mut self, config: TelemetryConfig) -> Self {
        self.telemetry = Some(config);
        self
    }
    pub fn options(mut self, options: ClientOptions) -> Self {
        self.options = options;
        self
    }

    fn prepare(&self) -> Result<HostConfig> {
        let state_dir = self
            .state_dir
            .clone()
            .map(Ok)
            .unwrap_or_else(crate::desktop::default_state_dir)?;
        let runner = self
            .runner
            .clone()
            .map(Ok)
            .unwrap_or_else(crate::desktop::default_executable)?;
        if !state_dir.is_absolute() || !runner.is_absolute() {
            return Err(Error::InvalidInput(
                "State directory and runner paths must be absolute".into(),
            ));
        }
        // Same validation the host runs, so mistakes fail here with a typed
        // error instead of as a startup failure.
        if super::compile::flatten(&self.agents)?.len() > 16 {
            return Err(Error::InvalidInput(
                "At most 16 agents, including subagents, per harness".into(),
            ));
        }
        Ok(HostConfig {
            desktop: self.desktop.clone(),
            state_dir,
            runner,
            storage: self.storage.clone(),
            coworkers: vec![],
            sandbox: self.sandbox,
            scripted: self.scripted,
            telemetry: self.telemetry.clone(),
            agents: self.agents.clone(),
        })
    }

    /// Run the engine inside this process (your Tokio runtime, your tools).
    pub async fn embedded(self) -> Result<Harness> {
        let host = self.prepare()?;
        let client = HarnessClient::embedded_host(host, self.registry, self.options).await?;
        let harness = Harness::connect(client).await?;
        for provider in self.providers {
            if let Err(error) = harness.set_model_provider(provider).await {
                let _ = harness.shutdown().await;
                return Err(error);
            }
        }
        Ok(harness)
    }

    /// Run the engine in an SDK-owned child process with bounded restarts.
    pub async fn managed(self, program: PathBuf, restart: RestartPolicy) -> Result<Harness> {
        let host = self.prepare()?;
        let client = HarnessClient::managed_host(
            HostProcessConfig {
                program,
                host,
                restart,
            },
            self.options,
        )
        .await?;
        let harness = Harness::connect(client).await?;
        for provider in self.providers {
            if let Err(error) = harness.set_model_provider(provider).await {
                let _ = harness.shutdown().await;
                return Err(error);
            }
        }
        Ok(harness)
    }
}

/// Loaded agents plus a connection to the engine. Clone freely.
#[derive(Clone)]
pub struct Harness {
    client: HarnessClient,
    agents: Vec<String>,
    application_tools: std::sync::Arc<crate::application_tools::ApplicationTools>,
    provider_auth: std::sync::Arc<crate::provider_callbacks::ClientAuth>,
}

impl Harness {
    pub async fn connect<T: crate::service::ConnectTarget>(
        target: T,
    ) -> std::result::Result<T::Connected, T::Error> {
        target.connect().await
    }
    /// Register an agent after startup. Exact repeats are safe; changes need a new ID.
    pub async fn create_agent(&self, definition: Agent) -> Result<AgentHandle> {
        if self.client.info()["capabilities"]["dynamic_agents"] != true {
            return Err(Error::Setup("Host does not support dynamic agents".into()));
        }
        let id = definition.id().to_owned();
        self.client
            .request("agent/register", json!({"agent":definition}))
            .await?;
        Ok(AgentHandle {
            client: self.client.clone(),
            id,
        })
    }
    /// `state_dir` holds durable state and generated agent bundles. `runner`
    /// is the OpenWorker executable used for out-of-process tool helpers.
    pub async fn start() -> Result<Self> {
        Self::builder().start().await
    }
    pub fn builder() -> HarnessBuilder {
        HarnessBuilder {
            providers: vec![],
            state_dir: None,
            runner: None,
            desktop: Default::default(),
            agents: vec![],
            registry: ToolRegistry::default(),
            storage: Storage::Sqlite,
            sandbox: Sandbox::default(),
            scripted: false,
            telemetry: None,
            options: ClientOptions::default(),
        }
    }
    /// Wrap any connected client whose host supports agent runs, e.g. one
    /// built from a hand-written `HostConfig` with `agents`.
    pub(crate) async fn connect_client(client: HarnessClient) -> Result<Self> {
        if client.info()["capabilities"]["agent_runs"] != true {
            return Err(Error::Setup("Host does not support agent runs".into()));
        }
        let listed = client.request("agent/list", json!({})).await?;
        let agents = listed["agents"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|a| a["id"].as_str().map(str::to_owned))
            .collect();
        let application_tools = crate::application_tools::ApplicationTools::new(&client);
        let provider_auth = crate::provider_callbacks::ClientAuth::new(&client);
        Ok(Self {
            client,
            agents,
            application_tools,
            provider_auth,
        })
    }
    /// The underlying protocol client, for anything this layer does not wrap.
    pub fn client(&self) -> &HarnessClient {
        &self.client
    }
    pub async fn register_tools(
        &self,
        namespace: &str,
        tools: impl IntoIterator<Item = crate::Tool>,
    ) -> Result<()> {
        self.application_tools
            .register(&self.client, namespace, tools)
            .await
    }
    /// Configure or rotate a named provider before or after registering agents.
    /// Environment credentials resolve in this application, not in tool processes.
    pub async fn set_model_provider(&self, mut provider: crate::ModelProvider) -> Result<()> {
        let name = provider.name().to_owned();
        let access_auth = provider.access_auth;
        let refresh = provider.refresh.take();
        if access_auth && self.client.info()["capabilities"]["provider_access_tokens"] != true {
            return Err(Error::Setup(
                "Update the harness for access-token authentication".into(),
            ));
        }
        self.client
            .request("provider/set", provider.wire()?)
            .await?;
        if access_auth {
            self.provider_auth
                .register(&self.client, name, refresh)
                .await?;
        }
        Ok(())
    }
    /// Atomically replace the in-memory bearer access token, not the refresh credential.
    pub async fn update_provider_credentials(
        &self,
        name: &str,
        token: crate::AccessToken,
    ) -> Result<()> {
        self.client.request("provider/auth/update", json!({"name":name,"access_token":token.token.as_str(),"expires_at":token.expires_at})).await?;
        Ok(())
    }
    pub async fn connect_mcp_server(&self, server: crate::McpServer) -> Result<()> {
        if !matches!(server, crate::McpServer::StreamableHttp { .. }) {
            return Err(Error::InvalidInput(
                "Use start_mcp_server for stdio executables".into(),
            ));
        }
        self.client.request("mcp/register", json!(server)).await?;
        Ok(())
    }
    /// Send a short explicit model request without creating a session or tools.
    pub async fn test_model_provider(&self, model: crate::Model) -> Result<()> {
        if self.client.info()["capabilities"]["provider_probe"] != true {
            return Err(Error::Setup(
                "Update the harness for provider connection tests".into(),
            ));
        }
        self.client.request("provider/test", json!(model)).await?;
        Ok(())
    }
    pub async fn start_mcp_server(&self, server: crate::McpServer) -> Result<()> {
        if !matches!(server, crate::McpServer::Stdio { .. }) {
            return Err(Error::InvalidInput(
                "Use connect_mcp_server for external services".into(),
            ));
        }
        self.client.request("mcp/register", json!(server)).await?;
        Ok(())
    }
    /// IDs loaded when this handle connected. Use list_agents() for the live catalog.
    pub fn agents(&self) -> impl Iterator<Item = &str> {
        self.agents.iter().map(String::as_str)
    }
    /// Refresh the catalog, including agents registered after this handle connected.
    pub async fn list_agents(&self) -> Result<Vec<String>> {
        let listed = self.client.request("agent/list", json!({})).await?;
        Ok(listed["agents"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|a| a["id"].as_str().map(str::to_owned))
            .collect())
    }
    /// Start a session of `agent` in `workspace`. An agent with subagents also
    /// gets one worker session per subagent and a team joining them.
    pub async fn session(&self, agent: &str, workspace: impl AsRef<Path>) -> Result<Session> {
        Session::create(self.client.clone(), agent, workspace.as_ref()).await
    }
    /// Reattach to a session from a [`SessionRef`] you persisted. Only the
    /// session ID is needed; the host knows its team.
    pub async fn resume(&self, reference: SessionRef) -> Result<Session> {
        Session::open(self.client.clone(), reference).await
    }
    /// Deterministic cleanup. Await before dropping your Tokio runtime.
    pub async fn shutdown(&self) -> Result<()> {
        self.provider_auth.stop();
        self.application_tools.stop();
        self.client.shutdown().await
    }
}

/// A reusable agent bound to a harness; each new session is independent.
#[derive(Clone)]
pub struct AgentHandle {
    client: HarnessClient,
    id: String,
}
impl AgentHandle {
    pub fn id(&self) -> &str {
        &self.id
    }
    pub async fn new_session(&self) -> Result<Session> {
        Session::create_options(self.client.clone(), json!({"agent":self.id})).await
    }
    pub async fn new_session_in(&self, workspace: impl AsRef<Path>) -> Result<Session> {
        Session::create(self.client.clone(), &self.id, workspace.as_ref()).await
    }
    pub async fn new_session_with(&self, options: crate::SessionOptions) -> Result<Session> {
        let mut value = serde_json::to_value(options)
            .map_err(|_| Error::InvalidInput("Invalid session options".into()))?;
        value["agent"] = json!(self.id);
        Session::create_options(self.client.clone(), value).await
    }
    pub async fn new_session_with_model(
        &self,
        workspace: impl AsRef<Path>,
        model: crate::ModelChoice,
    ) -> Result<Session> {
        Session::create_with_model(
            self.client.clone(),
            &self.id,
            workspace.as_ref(),
            Some(model),
        )
        .await
    }
    pub async fn resume_session(&self, reference: SessionRef) -> Result<Session> {
        if reference.agent != self.id {
            return Err(Error::InvalidInput(
                "Session belongs to a different agent".into(),
            ));
        }
        let snapshot = self.client.snapshot(&reference.session_id).await?;
        if snapshot["session"]["coworker"] != self.id {
            return Err(Error::InvalidInput(
                "Session identity does not match this agent".into(),
            ));
        }
        Session::open(self.client.clone(), reference).await
    }
}
