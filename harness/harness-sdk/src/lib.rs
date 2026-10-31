//! Development Rust API for the same engine embedded or over managed private stdio.
//! No request is automatically resubmitted after transport failure.
pub mod agents;
mod application_tools;
mod callbacks;
mod provider_callbacks;
pub use application_tools::{Tool, ToolContext};
pub mod cli;
mod desktop;
mod mcp;
pub use agents::Approvals;
pub use mcp::McpServer;
mod driver;
pub mod host;
mod providers;
pub use agents::{Agent, AgentHandle, Harness, HarnessBuilder};
pub use agents::{Run, Session, SessionInput, TurnLimits};
pub use desktop::{DesktopOptions, SessionOptions};
mod transport;
pub use host::{CoworkerConfig, HostConfig, HostProcessConfig, Storage};
pub use openworker_runtime::provider_auth::{AccessToken, Reason as TokenRefreshReason};
pub use openworker_runtime::team_service::{Command as TeamCommand, Create as TeamCreate};
pub use openworker_runtime::teams::{Member as TeamMember, Role as TeamRole};
/// OTLP/HTTP export settings: `endpoint` (collector origin, HTTPS or loopback
/// HTTP) and `deployment` (label, default `"default"`).
pub use openworker_telemetry::otlp::{
    Config as TelemetryConfig, Header as TelemetryHeader, Signal as TelemetrySignal,
};
/// Opt-in prompt/tool content in telemetry (off by default).
pub use openworker_telemetry::{Capture, ContentPolicy};
pub use providers::{Credentials, Model, ModelApi, ModelProvider};

pub enum EmbeddedConfig {
    Local(RuntimeConfig),
    Host(Box<HostConfig>),
}
impl From<RuntimeConfig> for EmbeddedConfig {
    fn from(c: RuntimeConfig) -> Self {
        Self::Local(c)
    }
}
impl From<HostConfig> for EmbeddedConfig {
    fn from(c: HostConfig) -> Self {
        Self::Host(Box::new(c))
    }
}
pub enum StdioConfig {
    Local(Box<ProcessConfig>),
    Host(Box<HostProcessConfig>),
}
impl From<ProcessConfig> for StdioConfig {
    fn from(c: ProcessConfig) -> Self {
        Self::Local(Box::new(c))
    }
}
impl From<HostProcessConfig> for StdioConfig {
    fn from(c: HostProcessConfig) -> Self {
        Self::Host(Box::new(c))
    }
}

pub use openworker_runtime::connections::Choice as ModelChoice;
pub use openworker_runtime::provider::Kind as ProviderKind;
pub use openworker_runtime::{execution::Backend as Sandbox, store::Event};
pub use openworker_tools::ToolRegistry;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::sync::{broadcast, mpsc, oneshot, watch};
use tokio_util::sync::CancellationToken;

pub type Result<T> = std::result::Result<T, Error>;
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    InvalidInput(String),
    Remote(String),
    Setup(String),
    Unavailable,
    /// May have committed or started. Reconcile durable IDs; do not blindly retry.
    OutcomeUnknown,
    Closed,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidInput(s) | Self::Remote(s) | Self::Setup(s) => f.write_str(s),
            Self::Unavailable => f.write_str("Harness unavailable or recovering; request not sent"),
            Self::OutcomeUnknown => f.write_str("Harness response lost; execution outcome unknown. Reconcile saved session before retrying."),
            Self::Closed => f.write_str("Harness client closed"),
        }
    }
}
impl std::error::Error for Error {}

/// Explicit configuration shared by both backends. Use stable absolute paths.
#[derive(Clone)]
pub struct RuntimeConfig {
    pub state_dir: PathBuf,
    pub bundle: PathBuf,
    /// Existing OpenWorker/partner executable supporting runner/supervisor commands.
    /// Embedded mode still needs this helper for out-of-process tool execution.
    pub runner: PathBuf,
    pub model: String,
    pub provider: ProviderKind,
    pub sandbox: Sandbox,
    pub servers: Option<PathBuf>,
    pub tool_policy: Option<PathBuf>,
    pub runtime_policy: Option<PathBuf>,
    pub scripted: bool,
}
impl RuntimeConfig {
    pub fn new(state_dir: PathBuf, bundle: PathBuf, runner: PathBuf) -> Self {
        Self {
            state_dir,
            bundle,
            runner,
            model: "gpt-6-sol".into(),
            provider: ProviderKind::Openai,
            sandbox: Sandbox::Openshell,
            servers: None,
            tool_policy: None,
            runtime_policy: None,
            scripted: false,
        }
    }
    fn validate(&self) -> Result<()> {
        for path in [&self.state_dir, &self.bundle, &self.runner]
            .into_iter()
            .chain(self.servers.iter())
            .chain(self.tool_policy.iter())
            .chain(self.runtime_policy.iter())
        {
            if !path.is_absolute() {
                return Err(Error::InvalidInput(
                    "SDK configuration paths must be absolute".into(),
                ));
            }
        }
        Ok(())
    }
}
#[derive(Clone)]
pub struct RestartPolicy {
    /// Total retries for the client's lifetime; successful reconnects do not reset it.
    pub max_restarts: u32,
    pub initial_delay: Duration,
    pub max_delay: Duration,
}
impl Default for RestartPolicy {
    fn default() -> Self {
        Self {
            max_restarts: 3,
            initial_delay: Duration::from_millis(250),
            max_delay: Duration::from_secs(5),
        }
    }
}
#[derive(Clone)]
pub struct ProcessConfig {
    pub program: PathBuf,
    pub runtime: RuntimeConfig,
    pub restart: RestartPolicy,
}
#[derive(Clone)]
pub struct ClientOptions {
    pub request_timeout: Duration,
    pub shutdown_timeout: Duration,
}
impl Default for ClientOptions {
    fn default() -> Self {
        Self {
            request_timeout: Duration::from_secs(60),
            shutdown_timeout: Duration::from_secs(310),
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ConnectionState {
    Starting,
    Ready {
        generation: u64,
        process_id: Option<u32>,
    },
    Recovering {
        attempt: u32,
    },
    Failed,
    Closed,
}
#[derive(Clone, Debug)]
pub enum Notification {
    /// Private callback control channel; not a journal record or model instruction.
    Application(Value),
    Event(Event),
    Recovered {
        session_id: String,
        snapshot: Value,
    },
    RecoveryFailed {
        session_id: String,
        message: String,
    },
    /// Consumer must attach/read events from its last processed cursor.
    Gap,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalDecision {
    Once,
    Session,
    Deny,
}
#[derive(Clone, Debug, Serialize)]
pub struct RuntimeSessionOptions {
    pub name: String,
    pub coworker: String,
    pub workspace: PathBuf,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelChoice>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub permissions: Option<openworker_runtime::permissions::Config>,
}
pub(crate) enum Command {
    Request(String, Value, oneshot::Sender<Result<Value>>),
    Attach(String, i64, oneshot::Sender<Result<Value>>),
    Ack(String, i64, oneshot::Sender<Result<()>>),
}
struct Handle {
    callback_transport: Arc<std::sync::Mutex<Option<transport::CallbackTransport>>>,
    registrations: Arc<std::sync::Mutex<Vec<Value>>>,
    tx: mpsc::Sender<(u64, Command)>,
    stop: CancellationToken,
    state: watch::Receiver<ConnectionState>,
    events: broadcast::Sender<Notification>,
    done: watch::Receiver<Option<Result<()>>>,
    info: std::sync::OnceLock<Value>,
}
impl Drop for Handle {
    fn drop(&mut self) {
        self.stop.cancel();
    }
}
#[derive(Clone)]
pub struct HarnessClient {
    inner: Arc<Handle>,
}
impl HarnessClient {
    pub(crate) async fn callback_message(&self, method: &str, params: Value) -> Result<()> {
        if self.inner.stop.is_cancelled() {
            return Err(Error::Closed);
        }
        let transport = self
            .inner
            .callback_transport
            .lock()
            .unwrap()
            .clone()
            .ok_or(Error::Unavailable)?;
        transport.send(method, params).await
    }
    pub async fn embedded_host(
        config: HostConfig,
        registry: ToolRegistry,
        options: ClientOptions,
    ) -> Result<Self> {
        config
            .validate()
            .map_err(|e| Error::InvalidInput(e.to_string()))?;
        driver::start(driver::Source::Host(config, registry), options).await
    }
    pub async fn managed_host(config: HostProcessConfig, options: ClientOptions) -> Result<Self> {
        config
            .host
            .validate()
            .map_err(|e| Error::InvalidInput(e.to_string()))?;
        if !config.program.is_absolute()
            || config.restart.max_restarts > 100
            || config.restart.initial_delay > config.restart.max_delay
            || config.restart.max_delay > Duration::from_secs(60)
        {
            return Err(Error::InvalidInput(
                "Invalid executable path or restart policy".into(),
            ));
        }
        driver::start(driver::Source::HostProcess(config), options).await
    }
    pub async fn create_team(
        &self,
        request: openworker_runtime::team_service::Create,
    ) -> Result<Value> {
        self.request("team/create", json!(request)).await
    }
    pub async fn team_snapshot(&self, id: &str) -> Result<Value> {
        self.request("team/snapshot", json!({"team_id":id})).await
    }
    pub async fn team_command(
        &self,
        id: &str,
        command: openworker_runtime::team_service::Command,
    ) -> Result<Value> {
        self.request("team/command", json!({"team_id":id,"command":command}))
            .await
    }
    pub async fn team_events(&self, id: &str, after: i64) -> Result<Value> {
        self.request("team/events", json!({"team_id":id,"after":after}))
            .await
    }
    pub async fn embedded(
        config: impl Into<EmbeddedConfig>,
        registry: ToolRegistry,
        options: ClientOptions,
    ) -> Result<Self> {
        match config.into() {
            EmbeddedConfig::Local(config) => {
                config.validate()?;
                driver::start(driver::Source::Embedded(config, registry), options).await
            }
            EmbeddedConfig::Host(config) => Self::embedded_host(*config, registry, options).await,
        }
    }
    pub async fn managed_stdio(
        config: impl Into<StdioConfig>,
        options: ClientOptions,
    ) -> Result<Self> {
        let config = match config.into() {
            StdioConfig::Local(c) => *c,
            StdioConfig::Host(c) => return Self::managed_host(*c, options).await,
        };
        config.runtime.validate()?;
        if !config.program.is_absolute()
            || config.restart.max_restarts > 100
            || config.restart.initial_delay > config.restart.max_delay
            || config.restart.max_delay > Duration::from_secs(60)
        {
            return Err(Error::InvalidInput(
                "Invalid executable path or restart policy".into(),
            ));
        }
        driver::start(driver::Source::Process(config), options).await
    }
    pub fn connection(&self) -> watch::Receiver<ConnectionState> {
        self.inner.state.clone()
    }
    /// Initial handshake capabilities. Gate evolving API usage on these flags.
    pub fn info(&self) -> &Value {
        self.inner.info.get().expect("initialized client")
    }
    /// Subscribe before starting work. On RecvError::Lagged, replay from your
    /// persisted processed cursor. Notifications are not an exactly-once queue.
    pub fn subscribe(&self) -> broadcast::Receiver<Notification> {
        self.inner.events.subscribe()
    }
    fn ready(&self) -> Result<()> {
        if self.inner.stop.is_cancelled() {
            return Err(Error::Closed);
        }
        if !matches!(*self.inner.state.borrow(), ConnectionState::Ready { .. }) {
            return Err(Error::Unavailable);
        }
        Ok(())
    }
    fn enqueue(&self, command: Command) -> Result<()> {
        self.ready()?;
        let ConnectionState::Ready { generation, .. } = *self.inner.state.borrow() else {
            return Err(Error::Unavailable);
        };
        self.inner
            .tx
            .try_send((generation, command))
            .map_err(|e| match e {
                mpsc::error::TrySendError::Full(_) => Error::Unavailable,
                mpsc::error::TrySendError::Closed(_) => Error::Closed,
            })
    }
    /// Escape hatch for evolving protocol methods. Secrets must use payloads,
    /// never executable arguments; do not log requests. initialize is SDK-owned.
    pub async fn request(&self, method: &str, params: Value) -> Result<Value> {
        self.ready()?;
        if matches!(method, "initialize" | "session/attach")
            || method.is_empty()
            || method.len() > 100
            || !params.is_object()
            || params.to_string().len() + method.len() + 128 > openworker_runtime::MAX_FRAME
        {
            return Err(Error::InvalidInput(
                "Invalid SDK request; use attach for session subscriptions".into(),
            ));
        }
        let (tx, rx) = oneshot::channel();
        self.enqueue(Command::Request(method.into(), params, tx))?;
        rx.await.unwrap_or(Err(Error::OutcomeUnknown))
    }
    pub async fn create_session(&self, options: RuntimeSessionOptions) -> Result<String> {
        let v = self
            .request(
                "session/create",
                serde_json::to_value(options)
                    .map_err(|_| Error::InvalidInput("Invalid session options".into()))?,
            )
            .await?;
        v["session"]["id"]
            .as_str()
            .map(str::to_owned)
            .ok_or_else(|| Error::Remote("Missing session ID".into()))
    }
    /// Attach and replay from a caller-persisted processed cursor. The snapshot
    /// and replay can overlap live notifications; deduplicate by event_id.
    pub async fn attach(&self, session_id: &str, after: i64) -> Result<Value> {
        self.ready()?;
        if session_id.len() > 80 || after < 0 {
            return Err(Error::InvalidInput("Invalid session/cursor".into()));
        }
        let (tx, rx) = oneshot::channel();
        self.enqueue(Command::Attach(session_id.into(), after, tx))?;
        rx.await.unwrap_or(Err(Error::OutcomeUnknown))
    }
    /// Acknowledge only processed events. Persist your cursor in the application
    /// too if recovery must survive an application (not just child) restart.
    pub async fn acknowledge(&self, session_id: &str, cursor: i64) -> Result<()> {
        self.ready()?;
        if session_id.len() > 80 || cursor < 0 {
            return Err(Error::InvalidInput("Invalid session/cursor".into()));
        }
        let (tx, rx) = oneshot::channel();
        self.enqueue(Command::Ack(session_id.into(), cursor, tx))?;
        rx.await.unwrap_or(Err(Error::Closed))
    }
    /// Read current state without replaying or advancing the processed cursor.
    /// Use attach first if automatic child-restart recovery is wanted.
    pub async fn snapshot(&self, session_id: &str) -> Result<Value> {
        if session_id.len() > 80 {
            return Err(Error::InvalidInput("Invalid session ID".into()));
        }
        let (tx, rx) = oneshot::channel();
        self.enqueue(Command::Request(
            "session/attach".into(),
            json!({"session_id":session_id}),
            tx,
        ))?;
        rx.await.unwrap_or(Err(Error::OutcomeUnknown))
    }
    pub async fn start_turn(
        &self,
        session_id: &str,
        submission_id: &str,
        text: &str,
    ) -> Result<Value> {
        self.request(
            "turn/start",
            json!({"session_id":session_id,"submission_id":submission_id,"text":text}),
        )
        .await
    }
    pub async fn cancel_turn(&self, session_id: &str, turn_id: &str) -> Result<Value> {
        self.request(
            "turn/cancel",
            json!({"session_id":session_id,"turn_id":turn_id}),
        )
        .await
    }
    pub async fn approve(
        &self,
        session_id: &str,
        interaction_id: &str,
        decision: ApprovalDecision,
    ) -> Result<Value> {
        self.request(
            "interaction/respond",
            json!({"session_id":session_id,"interaction_id":interaction_id,"decision":decision}),
        )
        .await
    }
    pub async fn answer(
        &self,
        session_id: &str,
        interaction_id: &str,
        answer: &str,
    ) -> Result<Value> {
        self.request(
            "interaction/respond",
            json!({"session_id":session_id,"interaction_id":interaction_id,"answer":answer}),
        )
        .await
    }
    /// Closes all clones. Keep the host Tokio runtime alive until this completes.
    pub async fn shutdown(&self) -> Result<()> {
        self.inner.stop.cancel();
        let mut done = self.inner.done.clone();
        loop {
            if let Some(result) = done.borrow().clone() {
                return result;
            }
            done.changed().await.map_err(|_| Error::Closed)?;
        }
    }
}

/// Authenticated REST/SSE service transport; independent of managed child lifecycle.
pub mod service;
