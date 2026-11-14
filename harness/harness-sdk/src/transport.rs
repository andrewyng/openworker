use crate::*;
use openworker_runtime::{model::Model, runtime::Runtime};
use std::{process::Stdio, sync::Arc};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    process::{Child, ChildStdin},
};

fn provider_error(error: anyhow::Error) -> Error {
    if let Some(diagnostic) = error.downcast_ref::<crate::AgentDefinitionConflict>() {
        return Error::AgentDefinitionConflict(diagnostic.clone());
    }
    match error.downcast_ref::<openworker_runtime::provider::Error>() {
        Some(error) => Error::Provider(error.diagnostic()),
        None => Error::Remote(error.to_string()),
    }
}

pub(crate) enum Backend {
    Host {
        host: Arc<crate::host::Host>,
        events: broadcast::Receiver<Event>,
        gaps: broadcast::Receiver<()>,
        application: broadcast::Receiver<Value>,
    },
    Embedded {
        runtime: Arc<Runtime>,
        events: broadcast::Receiver<Event>,
    },
    Process(Box<Process>),
}
pub(crate) struct Process {
    child: Child,
    stdin: Option<Arc<tokio::sync::Mutex<ChildStdin>>>,
    frames: mpsc::Receiver<Result<Value>>,
    reader: tokio::task::JoinHandle<()>,
    next_id: u64,
}
#[derive(Clone)]
pub(crate) enum CallbackTransport {
    Host(std::sync::Weak<crate::host::Host>),
    Stdio(Arc<tokio::sync::Mutex<ChildStdin>>),
}
impl CallbackTransport {
    pub async fn send(&self, method: &str, params: Value) -> Result<()> {
        if !matches!(
            method,
            "application/result"
                | "application/progress"
                | "provider/auth/result"
                | "safety/result"
        ) {
            return Err(Error::InvalidInput("Unsupported callback control".into()));
        }
        match self {
            Self::Host(host) => {
                if method == "safety/result" {
                    host.upgrade()
                        .ok_or(Error::Closed)?
                        .safety_callbacks
                        .respond(params)
                        .map_err(|_| Error::Remote("Safety callback rejected".into()))?;
                    return Ok(());
                }
                if method == "provider/auth/result" {
                    host.upgrade()
                        .ok_or(Error::Closed)?
                        .provider_callbacks
                        .respond(params)
                        .map_err(|_| Error::Remote("Provider callback rejected".into()))?;
                    return Ok(());
                }
                host.upgrade()
                    .ok_or(Error::Closed)?
                    .callbacks
                    .respond(params, method == "application/progress")
                    .await
                    .map_err(|_| Error::Remote("Callback control rejected".into()))?;
                Ok(())
            }
            Self::Stdio(input) => {
                let frame = format!(
                    "{}\n",
                    json!({"jsonrpc":"2.0","method":method,"params":params})
                );
                if frame.len() > openworker_runtime::MAX_FRAME {
                    return Err(Error::InvalidInput("Callback frame exceeds limit".into()));
                }
                tokio::time::timeout(Duration::from_secs(10), async {
                    input.lock().await.write_all(frame.as_bytes()).await
                })
                .await
                .map_err(|_| Error::OutcomeUnknown)?
                .map_err(|_| Error::OutcomeUnknown)
            }
        }
    }
}
impl Drop for Process {
    fn drop(&mut self) {
        self.reader.abort();
    }
}
impl Backend {
    pub fn callback_transport(&self) -> Option<CallbackTransport> {
        match self {
            Self::Host { host, .. } => Some(CallbackTransport::Host(Arc::downgrade(host))),
            Self::Process(process) => process
                .stdin
                .as_ref()
                .map(|input| CallbackTransport::Stdio(input.clone())),
            _ => None,
        }
    }
    pub(crate) fn host(config: &HostConfig, registry: ToolRegistry) -> Result<Self> {
        let host =
            crate::host::Host::open(config, registry).map_err(|e| Error::Setup(e.to_string()))?;
        let events = host.events.subscribe();
        let gaps = host.gaps.subscribe();
        let application = host.callbacks.events.subscribe();
        Ok(Self::Host {
            host,
            events,
            gaps,
            application,
        })
    }
    pub(crate) fn host_process(config: &HostProcessConfig) -> Result<Self> {
        let mut command = tokio::process::Command::new(&config.program);
        let serialized = serde_json::to_string(&config.host)
            .map_err(|_| Error::InvalidInput("Invalid host configuration".into()))?;
        // Agent instructions can exceed what argv allows; larger configs go
        // through a private file in the state directory instead.
        let argument = if serialized.len() < 32000 {
            serialized
        } else {
            openworker_runtime::platform::private_directory(&config.host.state_dir)
                .map_err(|e| Error::Setup(e.to_string()))?;
            let path = config.host.state_dir.join("sdk-host-config.json");
            std::fs::write(&path, serialized).map_err(|e| Error::Setup(e.to_string()))?;
            format!("@{}", path.display())
        };
        command.args(["harness-stdio", &argument]);
        command.env_clear();
        for key in [
            "PATH",
            "SystemRoot",
            "WINDIR",
            "USERPROFILE",
            "HOME",
            "TMPDIR",
            "TEMP",
            "TMP",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "LANG",
        ] {
            if let Some(v) = std::env::var_os(key) {
                command.env(key, v);
            }
        }
        if let Storage::Postgres { connection_env, .. } = &config.host.storage {
            let value = std::env::var_os(connection_env).ok_or_else(|| {
                Error::Setup("Database credential environment variable missing".into())
            })?;
            command.env(connection_env, value);
        }
        for name in config.host.desktop.inherit_env.iter().chain(
            config
                .host
                .desktop
                .compliance_sinks
                .iter()
                .map(|s| &s.credential_env),
        ) {
            let value = std::env::var_os(name).ok_or_else(|| {
                Error::Setup("Declared host environment reference unavailable".into())
            })?;
            command.env(name, value);
        }
        // Telemetry credentials are referenced by variable name only.
        for header in config.host.telemetry.iter().flat_map(|t| &t.headers) {
            if let Some(name) = &header.value_env {
                let valid = !name.is_empty()
                    && name.len() <= 100
                    && name
                        .bytes()
                        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_');
                if !valid {
                    return Err(Error::InvalidInput(
                        "Invalid telemetry credential reference".into(),
                    ));
                }
                let value = std::env::var_os(name).ok_or_else(|| {
                    Error::Setup(format!("Telemetry credential variable {name} is not set"))
                })?;
                command.env(name, value);
            }
        }
        Self::launch(command)
    }
    pub(crate) fn embedded(config: &RuntimeConfig, mut registry: ToolRegistry) -> Result<Self> {
        if let Some(path) = &config.servers {
            openworker_runtime::servers::load(path, &config.bundle, &mut registry)
                .map_err(|e| Error::Setup(e.to_string()))?;
        }
        let runtime = Runtime::open_with_registry(
            &config.state_dir,
            &config.bundle,
            &config.runner,
            Model::new(config.model.clone(), config.scripted).with_provider(config.provider),
            config.sandbox,
            config.tool_policy.as_deref(),
            config.runtime_policy.as_deref(),
            registry,
        )
        .map_err(|e| Error::Setup(e.to_string()))?;
        let events = runtime.events.subscribe();
        Ok(Self::Embedded { runtime, events })
    }
    pub(crate) fn process(config: &ProcessConfig) -> Result<Self> {
        let c = &config.runtime;
        let mut command = tokio::process::Command::new(&config.program);
        command
            .args(["serve", "--stdio", "--state-dir"])
            .arg(&c.state_dir)
            .arg("--bundle")
            .arg(&c.bundle)
            .arg("--runner")
            .arg(&c.runner)
            .arg("--model")
            .arg(&c.model)
            .arg("--provider")
            .arg(c.provider.name())
            .arg("--sandbox")
            .arg(c.sandbox.name());
        for (flag, path) in [
            ("--servers", &c.servers),
            ("--tool-policy", &c.tool_policy),
            ("--runtime-policy", &c.runtime_policy),
        ] {
            if let Some(path) = path {
                command.arg(flag).arg(path);
            }
        }
        if c.scripted {
            command.arg("--scripted");
        }
        // No inherited provider keys or arbitrary application secrets. Configure
        // credentials through connection/set after readiness (or secure storage).
        command.env_clear();
        for key in [
            "PATH",
            "SystemRoot",
            "WINDIR",
            "USERPROFILE",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_RUNTIME_DIR",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "OPENSHELL_GATEWAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "LANG",
        ] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        Self::launch(command)
    }
    fn launch(mut command: tokio::process::Command) -> Result<Self> {
        let diagnostics =
            cfg!(feature = "test-support") && std::env::var_os("OW_SDK_TEST_STDERR").is_some();
        #[cfg(windows)]
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW, no console focus stealing.
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(if diagnostics {
                Stdio::inherit()
            } else {
                Stdio::null()
            })
            .kill_on_drop(true)
            .spawn()
            .map_err(|_| Error::Setup("Could not launch harness executable".into()))?;
        let stdin = child
            .stdin
            .take()
            .map(|input| Arc::new(tokio::sync::Mutex::new(input)));
        let mut stdout = child.stdout.take().expect("piped stdout");
        let (tx, frames) = mpsc::channel(64);
        let reader = tokio::spawn(async move {
            let mut pending = Vec::new();
            let mut bytes = [0u8; 8192];
            loop {
                let n = match stdout.read(&mut bytes).await {
                    Ok(0) | Err(_) => break,
                    Ok(n) => n,
                };
                for b in &bytes[..n] {
                    if pending.len() >= openworker_runtime::MAX_FRAME {
                        let _ = tx.send(Err(Error::OutcomeUnknown)).await;
                        return;
                    }
                    if *b == b'\n' {
                        let frame =
                            serde_json::from_slice(&pending).map_err(|_| Error::OutcomeUnknown);
                        let bad = frame.is_err();
                        if tx.send(frame).await.is_err() || bad {
                            return;
                        }
                        pending.clear();
                    } else {
                        pending.push(*b);
                    }
                }
            }
            let _ = tx.send(Err(Error::OutcomeUnknown)).await;
        });
        Ok(Self::Process(Box::new(Process {
            child,
            stdin,
            frames,
            reader,
            next_id: 0,
        })))
    }
    pub(crate) fn pid(&self) -> Option<u32> {
        match self {
            Self::Process(p) => p.child.id(),
            _ => None,
        }
    }
    pub(crate) async fn request(
        &mut self,
        method: &str,
        params: Value,
        sink: &broadcast::Sender<Notification>,
    ) -> Result<Value> {
        match self {
            Self::Host {
                host, application, ..
            } => {
                let request = host.dispatch(method, params);
                tokio::pin!(request);
                loop {
                    tokio::select! {
                        result=&mut request=>break result.map_err(provider_error),
                        value=application.recv()=>{let value=value.map_err(|_|Error::OutcomeUnknown)?;let _=sink.send(Notification::Application(value));},
                    }
                }
            }
            Self::Embedded { runtime, .. } => runtime
                .dispatch(method, params)
                .await
                .map_err(provider_error),
            Self::Process(p) => {
                p.next_id += 1;
                let id = p.next_id;
                let frame = format!(
                    "{}\n",
                    json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})
                );
                p.stdin
                    .as_mut()
                    .ok_or(Error::Closed)?
                    .lock()
                    .await
                    .write_all(frame.as_bytes())
                    .await
                    .map_err(|_| Error::OutcomeUnknown)?;
                loop {
                    let v = tokio::select! {
                        biased;
                        frame=p.frames.recv()=>frame.ok_or(Error::OutcomeUnknown)??,
                        _=p.child.wait()=>return Err(Error::OutcomeUnknown),
                    };
                    if v["jsonrpc"] != "2.0" {
                        return Err(Error::OutcomeUnknown);
                    }
                    if v.get("id").is_some() {
                        if v["id"] != id {
                            return Err(Error::OutcomeUnknown);
                        }
                        if let Some(message) = v["error"]["message"].as_str() {
                            if let Ok(diagnostic) =
                                serde_json::from_value::<crate::AgentDefinitionConflict>(
                                    v["error"]["agent_definition_conflict"].clone(),
                                )
                            {
                                if diagnostic.valid() {
                                    return Err(Error::AgentDefinitionConflict(diagnostic));
                                }
                            }
                            if let Ok(diagnostic) =
                                serde_json::from_value::<openworker_runtime::provider::Diagnostic>(
                                    v["error"]["provider_error"].clone(),
                                )
                            {
                                return Err(Error::Provider(diagnostic));
                            }
                            return Err(Error::Remote(message.into()));
                        }
                        return v.get("result").cloned().ok_or(Error::OutcomeUnknown);
                    }
                    let _ = sink.send(notification(v)?);
                }
            }
        }
    }
    pub(crate) async fn receive(&mut self) -> Result<Notification> {
        match self {
            Self::Host {
                host,
                events,
                gaps,
                application,
            } => tokio::select! {
                value=application.recv()=>value.map(Notification::Application).map_err(|_|Error::OutcomeUnknown),
                _=host.failed.cancelled()=>Err(Error::OutcomeUnknown),
                _=gaps.recv()=>Ok(Notification::Gap),
                e=events.recv()=>match e{Ok(e)=>Ok(Notification::Event(e)),Err(broadcast::error::RecvError::Lagged(_))=>Ok(Notification::Gap),Err(_)=>Err(Error::OutcomeUnknown)},
            },
            Self::Embedded { events, .. } => match events.recv().await {
                Ok(e) => Ok(Notification::Event(e)),
                Err(broadcast::error::RecvError::Lagged(_)) => Ok(Notification::Gap),
                Err(_) => Err(Error::OutcomeUnknown),
            },
            Self::Process(p) => tokio::select! {
                biased;
                frame=p.frames.recv()=>notification(frame.ok_or(Error::OutcomeUnknown)??),
                _=p.child.wait()=>Err(Error::OutcomeUnknown),
            },
        }
    }
    pub(crate) async fn close(&mut self, timeout: Duration) -> Result<()> {
        match self {
            Self::Host { host, .. } => tokio::time::timeout(timeout, host.shutdown())
                .await
                .map_err(|_| Error::Setup("Host shutdown timed out".into()))?
                .map_err(|e| Error::Setup(e.to_string())),
            Self::Embedded { runtime, .. } => tokio::time::timeout(timeout, runtime.shutdown())
                .await
                .map_err(|_| {
                    Error::Setup(
                        "Embedded shutdown timed out; keep host runtime alive for cleanup".into(),
                    )
                })?
                .map_err(|e| Error::Setup(e.to_string())),
            Self::Process(p) => {
                if let Some(input) = p.stdin.take() {
                    let _ = tokio::time::timeout(Duration::from_secs(10), async {
                        input.lock().await.shutdown().await
                    })
                    .await;
                } // EOF asks runtime to preserve parked approvals and clean up.
                if tokio::time::timeout(timeout, p.child.wait()).await.is_err() {
                    p.child
                        .kill()
                        .await
                        .map_err(|_| Error::Setup("Unable to stop owned harness process".into()))?;
                }
                p.child
                    .wait()
                    .await
                    .map_err(|_| Error::Setup("Unable to reap owned harness process".into()))?;
                p.reader.abort();
                Ok(())
            }
        }
    }
}
fn notification(v: Value) -> Result<Notification> {
    if v["jsonrpc"] != "2.0" {
        return Err(Error::OutcomeUnknown);
    }
    match v["method"].as_str() {
        Some("application/event") => Ok(Notification::Application(v["params"].clone())),
        Some("session/event") => serde_json::from_value(v["params"].clone())
            .map(Notification::Event)
            .map_err(|_| Error::OutcomeUnknown),
        Some("stream/gap") => Ok(Notification::Gap),
        _ => Err(Error::OutcomeUnknown),
    }
}
