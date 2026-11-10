use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Default, Clone, Serialize)]
pub struct SessionOptions {
    #[serde(skip_serializing_if = "Option::is_none")]
    initial_history: Option<Vec<crate::HistoryItem>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    safety: Option<crate::guardrails::Config>,
    #[serde(skip_serializing_if = "Option::is_none")]
    compliance: Option<openworker_runtime::compliance::Config>,
    #[serde(skip_serializing_if = "Option::is_none")]
    model: Option<crate::Model>,
    #[serde(skip_serializing_if = "Option::is_none")]
    workspace: Option<PathBuf>,
    #[serde(skip_serializing_if = "Option::is_none")]
    scratch: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    auto_skip_questions: Option<bool>,
}
impl SessionOptions {
    /// Seed a new session with a complete portable transcript. No tool runs.
    pub fn initial_history(mut self, history: Vec<crate::HistoryItem>) -> Self {
        self.initial_history = Some(history);
        self
    }
    pub fn compliance(mut self, config: openworker_runtime::compliance::Config) -> Self {
        self.compliance = Some(config);
        self
    }
    pub fn safety(mut self, config: crate::guardrails::Config) -> Self {
        self.safety = Some(config);
        self
    }
    /// Override this agent's question policy for the new session only.
    pub fn auto_skip_questions(mut self, enabled: bool) -> Self {
        self.auto_skip_questions = Some(enabled);
        self
    }
    pub fn model(mut self, model: crate::Model) -> Self {
        self.model = Some(model);
        self
    }
    pub fn workspace(mut self, path: impl Into<PathBuf>) -> Self {
        self.workspace = Some(path.into());
        self
    }
    pub fn scratch(mut self, enabled: bool) -> Self {
        self.scratch = Some(enabled);
        self
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct DesktopOptions {
    pub inherit_env: Vec<String>,
    pub safety: crate::guardrails::Config,
    pub compliance: Option<openworker_runtime::compliance::Config>,
    pub compliance_sinks: Vec<openworker_runtime::compliance_export::Sink>,
    pub mcp_servers: Vec<crate::McpServer>,
    pub persist_sessions: bool,
    pub session_dir: Option<PathBuf>,
    pub scratch_dir: Option<PathBuf>,
    pub compaction: openworker_runtime::compaction::Config,
}
impl Default for DesktopOptions {
    fn default() -> Self {
        Self {
            inherit_env: vec![],
            safety: Default::default(),
            compliance: None,
            compliance_sinks: vec![],
            mcp_servers: vec![],
            persist_sessions: true,
            session_dir: None,
            scratch_dir: None,
            compaction: Default::default(),
        }
    }
}
pub(crate) fn default_state_dir() -> Result<PathBuf> {
    let exe = std::env::current_exe()
        .map_err(|_| Error::Setup("Application executable is unavailable".into()))?;
    let app = std::env::var("OPENWORKER_APP_ID")
        .ok()
        .or_else(|| exe.file_stem().and_then(|s| s.to_str()).map(str::to_owned))
        .ok_or_else(|| Error::Setup("Specify an application state directory".into()))?;
    if !openworker_runtime::bundle::safe_name(&app) {
        return Err(Error::InvalidInput(
            "Use an alphanumeric app ID or specify state_dir".into(),
        ));
    }
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .ok_or_else(|| Error::Setup("User home unavailable; specify state_dir".into()))?;
    Ok(PathBuf::from(home)
        .join(".openworker")
        .join(app)
        .join("state"))
}
pub(crate) fn default_executable() -> Result<PathBuf> {
    if let Some(value) = std::env::var_os("OPENWORKER_EXECUTABLE") {
        let path = PathBuf::from(value);
        if path.is_absolute() && path.is_file() {
            return Ok(path);
        }
        return Err(Error::Setup(
            "OPENWORKER_EXECUTABLE must name an absolute bundled executable".into(),
        ));
    }
    let exe = std::env::current_exe()
        .map_err(|_| Error::Setup("Application executable unavailable".into()))?;
    let dir = exe
        .parent()
        .ok_or_else(|| Error::Setup("Application executable directory unavailable".into()))?;
    let name = if cfg!(windows) {
        "openworker-harness.exe"
    } else {
        "openworker-harness"
    };
    for path in [
        dir.join(name),
        dir.join("resources").join(name),
        dir.join("../Resources").join(name),
    ] {
        if path.is_file() {
            return path
                .canonicalize()
                .map_err(|_| Error::Setup("Bundled harness is inaccessible".into()));
        }
    }
    Err(Error::Setup(
        "Bundled openworker-harness not found; use a desktop starter or set executable explicitly"
            .into(),
    ))
}

/// Per-message options. Session and application safety requirements remain active.
#[derive(Clone, Default)]
pub struct TurnOptions {
    pub submission_id: Option<String>,
    pub limits: Option<crate::TurnLimits>,
    pub safety: Option<crate::guardrails::Config>,
    /// Trusted application instructions for this turn only, not user text.
    pub instruction_addendum: Option<String>,
    pub output: Option<crate::OutputOptions>,
}
