//! Friendly agent API: agents are values, runs return results.
//!
//! Shape follows the OpenAI Agents SDK (`Agent` + run + typed results and
//! interruptions); built-ins follow LangChain Deep Agents (declarative
//! subagents, a todo/plan tool, a per-tool `interrupt_on` approval map).
//! Everything is a thin layer over [`crate::HarnessClient`], so durability,
//! recovery and permission checks are unchanged.
//!
//! ```rust,no_run
//! use openworker_sdk::agents::*;
//! # async fn example(state_dir: std::path::PathBuf, runner: std::path::PathBuf,
//! # registry: ToolRegistry, workspace: std::path::PathBuf) -> Result<(), Box<dyn std::error::Error>> {
//!
//! let researcher = Agent::new("researcher")
//!     .description("Finds and verifies facts")
//!     .instructions("Research the assigned item and report evidence.")
//!     .tool("web.search");
//!
//! let assistant = Agent::new("assistant")
//!     .instructions("Help the user with their workspace.")
//!     .tools(["files.read", "files.write"])
//!     .planning(true)                  // built-in todo list (update_plan)
//!     .interrupt_on(["files.write"])   // only writes wait for a human
//!     .subagent(researcher);           // lead/worker team, created per session
//!
//! let harness = Harness::builder().state_dir(state_dir).executable(runner)
//!     .agent(assistant)
//!     .tools(registry)
//!     .embedded()
//!     .await?;
//!
//! let session = harness.session("assistant", workspace).await?;
//! let mut result = session.run("Tidy the docs folder").await?;
//! while let RunStatus::Interrupted = result.status {
//!     for i in &result.interruptions {
//!         session.respond(i, Response::Approve(ApprovalDecision::Once)).await?;
//!     }
//!     result = session.resume().await?;
//! }
//! println!("{}", result.final_output.unwrap_or_default());
//! # Ok(())
//! # }
//! ```
mod agent_team;
mod bundle;
pub mod coding;
pub(crate) mod compile;
mod harness;
mod session;
mod team;

pub use crate::{
    AccessToken, ApprovalDecision, Capture, ContentPolicy, Credentials, Model, ModelChoice,
    ModelProvider, ProviderKind, Sandbox, Storage, TelemetryConfig, TelemetryHeader,
    TelemetrySignal, TokenRefreshReason, ToolRegistry,
};
pub use agent_team::{
    AgentTeam, AgentTeamRef, ChildContext, ChildOptions, ChildWorkspace, Communication,
    HistoryPage, HistoryQuery, Instance, RetirementMode, TeamDelegation, TeamOptions, TeamPolicy,
};
pub use harness::{AgentHandle, Harness, HarnessBuilder};
pub use session::{
    Interruption, InterruptionKind, Response, Run, RunEvent, RunResult, RunStatus, RunStream,
    Session, SessionInput, SessionRef, TeamRef, WorkerRef,
};
pub use team::{ItemSpec, ItemStatus, Team};

use crate::{Error, Result};
pub use openworker_runtime::budget::Limits as TurnLimits;
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, path::PathBuf};

/// Who decides whether a tool call may run.
///
/// Engine hard checks (path containment, pinned identities) always apply first;
/// none of these modes can relax them.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "mode", content = "tools", rename_all = "snake_case")]
pub enum Approvals {
    /// Every tool call waits for your decision (engine `ask` mode). Default.
    #[default]
    Always,
    /// Deep Agents-style `interrupt_on`: only the listed qualified tools wait
    /// for a human. The SDK approves all other calls once, on the application's
    /// behalf, through the normal durable interaction record.
    Only(BTreeSet<String>),
    /// The engine's reviewer model decides; unsure verdicts come back to you.
    Reviewer,
    Auto(ModelChoice),
    /// No approvals (engine `bypass`). Use only for trusted, sandboxed work.
    Never,
}
impl Approvals {
    pub fn auto(model: ModelChoice) -> Self {
        Self::Auto(model)
    }
    pub(crate) fn mode(&self) -> openworker_runtime::permissions::Mode {
        use openworker_runtime::permissions::Mode;
        match self {
            Self::Always | Self::Only(_) => Mode::Ask,
            Self::Reviewer | Self::Auto(_) => Mode::AutoApprove,
            Self::Never => Mode::Bypass,
        }
    }
    /// True when the SDK may approve this call without asking. `alias` is the
    /// model-facing name in an interaction (`provider__tool`).
    pub(crate) fn auto_approves(&self, alias: &str) -> bool {
        // A reusable custom type needs its own exact-definition decision. An
        // interrupt_on exclusion must not silently approve arbitrary new roles.
        if alias == openworker_runtime::team_type_catalog::TOOL {
            return false;
        }
        match self {
            Self::Only(ask) => !ask
                .iter()
                .any(|t| t == alias || openworker_tools::model_alias(t).is_ok_and(|a| a == alias)),
            _ => false,
        }
    }
}

/// An agent definition. Cheap to clone; nothing runs until a [`Harness`] loads it.
///
/// Also the wire format of `HostConfig.agents`: other-language SDKs send the
/// same JSON (see `docs/harness/AGENT-RUN-PROTOCOL.md`).
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Agent {
    pub(crate) id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) name: Option<String>,
    #[serde(default)]
    pub(crate) description: String,
    pub(crate) instructions: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) limits: Option<TurnLimits>,
    #[serde(default)]
    pub(crate) tools: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) model: Option<ModelChoice>,
    #[serde(default)]
    pub(crate) provider: ProviderKind,
    #[serde(default = "default_fallback_model")]
    pub(crate) fallback_model: String,
    #[serde(default)]
    pub(crate) planning: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub(crate) memory: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) memory_scope: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub(crate) scheduling: bool,
    #[serde(default)]
    pub(crate) approvals: Approvals,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub(crate) auto_skip_questions: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(crate) subagents: Vec<Agent>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) servers: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) tool_policy: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) runtime_policy: Option<PathBuf>,
}
fn default_fallback_model() -> String {
    "gpt-6-sol".into()
}

impl Agent {
    /// `id` is the stable identifier sessions are pinned to: ASCII letters,
    /// digits, `-` or `_`, at most 64 characters.
    pub fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            name: None,
            description: String::new(),
            instructions: String::new(),
            limits: None,
            tools: vec![],
            model: None,
            provider: ProviderKind::Openai,
            fallback_model: default_fallback_model(),
            planning: false,
            memory: false,
            memory_scope: None,
            scheduling: false,
            approvals: Approvals::default(),
            auto_skip_questions: false,
            subagents: vec![],
            servers: None,
            tool_policy: None,
            runtime_policy: None,
        }
    }
    pub fn limits(mut self, limits: TurnLimits) -> Self {
        self.limits = Some(limits);
        self
    }
    pub fn id(&self) -> &str {
        &self.id
    }
    /// Display name. Defaults to the ID.
    pub fn name(mut self, name: impl Into<String>) -> Self {
        self.name = Some(name.into());
        self
    }
    /// When to use this agent. Shown to a lead agent when this is a subagent.
    pub fn description(mut self, description: impl Into<String>) -> Self {
        self.description = description.into();
        self
    }
    /// System prompt. Required.
    pub fn instructions(mut self, instructions: impl Into<String>) -> Self {
        self.instructions = instructions.into();
        self
    }
    /// Add one qualified tool (`provider.tool`) from the harness tool registry.
    pub fn tool(mut self, name: impl Into<String>) -> Self {
        self.tools.push(name.into());
        self
    }
    pub fn tools<I, S>(mut self, names: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.tools.extend(names.into_iter().map(Into::into));
        self
    }
    /// Default model through a named model connection (see `connection/set`).
    pub fn model(mut self, connection: impl Into<String>, model: impl Into<String>) -> Self {
        self.model = Some(ModelChoice {
            connection: connection.into(),
            model: model.into(),
        });
        self
    }
    /// Default model, overridable when creating an independent session.
    pub fn default_model(mut self, model: ModelChoice) -> Self {
        self.model = Some(model);
        self
    }
    /// Engine fallback provider/model used when no named connection is selected.
    pub fn fallback_model(mut self, provider: ProviderKind, model: impl Into<String>) -> Self {
        self.provider = provider;
        self.fallback_model = model.into();
        self
    }
    /// Built-in durable todo list (`update_plan`). Bookkeeping only; it never
    /// grants permission.
    pub fn planning(mut self, enabled: bool) -> Self {
        self.planning = enabled;
        self
    }
    /// Only these qualified tools wait for a human; others are approved by the
    /// SDK. Shorthand for `.approvals(Approvals::Only(..))`.
    pub fn interrupt_on<I, S>(mut self, tools: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.approvals = Approvals::Only(tools.into_iter().map(Into::into).collect());
        self
    }
    pub fn approvals(mut self, approvals: Approvals) -> Self {
        self.approvals = approvals;
        self
    }
    /// Enable durable memory tools; shared scope IDs are assigned by the app.
    pub fn memory(mut self, enabled: bool) -> Self {
        self.memory = enabled;
        self
    }
    pub fn memory_scope(mut self, scope: impl Into<String>) -> Self {
        self.memory = true;
        self.memory_scope = Some(scope.into());
        self
    }
    /// Enable bounded schedules driven by the SDK host while it runs.
    pub fn scheduling(mut self, enabled: bool) -> Self {
        self.scheduling = enabled;
        self
    }
    /// Return a recorded "no human answer available" result instead of parking
    /// ask_user. This does not approve tools or invent an answer.
    pub fn auto_skip_questions(mut self, enabled: bool) -> Self {
        self.auto_skip_questions = enabled;
        self
    }
    /// Add a subagent. A session of this agent becomes a team lead with one
    /// worker session per subagent; delegation runs over the durable team board.
    pub fn subagent(mut self, agent: Agent) -> Self {
        self.subagents.push(agent);
        self
    }
    /// App-owned `servers.json` for this agent's MCP providers.
    pub fn servers(mut self, path: impl Into<PathBuf>) -> Self {
        self.servers = Some(path.into());
        self
    }
    /// Operator tool-classification policy file.
    pub fn tool_policy(mut self, path: impl Into<PathBuf>) -> Self {
        self.tool_policy = Some(path.into());
        self
    }
    /// Operator runtime policy file.
    pub fn runtime_policy(mut self, path: impl Into<PathBuf>) -> Self {
        self.runtime_policy = Some(path.into());
        self
    }

    pub(crate) fn display_name(&self) -> &str {
        self.name.as_deref().unwrap_or(&self.id)
    }

    pub(crate) fn validate(&self, nested: bool) -> Result<()> {
        let bad = |m: String| Err(Error::InvalidInput(m));
        if !openworker_runtime::bundle::safe_name(&self.id) || self.id.len() > 64 {
            return bad(format!(
                "Agent ID {:?} must be 1–64 ASCII letters, digits, '-' or '_'",
                self.id
            ));
        }
        if self.instructions.trim().is_empty() {
            return bad(format!("Agent {} needs instructions", self.id));
        }
        if (self.tools.is_empty() && !self.memory && !self.scheduling && !self.planning)
            || self.tools.len() > 64
        {
            return bad(format!(
                "Agent {} needs at most 64 tools and at least one tool or an enabled planning, memory or scheduling capability",
                self.id
            ));
        }
        let mut seen = BTreeSet::new();
        for tool in &self.tools {
            openworker_tools::qualified_parts(tool)
                .map_err(|e| Error::InvalidInput(format!("Agent {}: {tool}: {e}", self.id)))?;
            if !seen.insert(tool) {
                return bad(format!("Agent {} lists tool {tool} twice", self.id));
            }
        }
        if let Approvals::Only(ask) = &self.approvals {
            if let Some(t) = ask.iter().find(|t| !seen.contains(t)) {
                return bad(format!(
                    "Agent {}: interrupt_on names {t}, which is not one of its tools",
                    self.id
                ));
            }
        }
        if let Some(m) = &self.model {
            m.validate()
                .map_err(|e| Error::InvalidInput(format!("Agent {}: {e}", self.id)))?;
        }
        for path in [&self.servers, &self.tool_policy, &self.runtime_policy]
            .into_iter()
            .flatten()
        {
            if !path.is_absolute() {
                return bad(format!("Agent {}: policy paths must be absolute", self.id));
            }
        }
        if nested && !self.subagents.is_empty() {
            return bad(format!(
                "Subagent {} cannot have its own subagents (one team level)",
                self.id
            ));
        }
        if self.subagents.len() > 15 {
            return bad(format!("Agent {} has more than 15 subagents", self.id));
        }
        if !self.subagents.is_empty() {
            // Team member display names must be unique (case-insensitive) and short.
            let mut names = BTreeSet::new();
            for a in std::iter::once(self).chain(&self.subagents) {
                let name = a.display_name().trim().to_lowercase();
                if name.is_empty() || a.display_name().len() > 100 || !names.insert(name) {
                    return bad(format!(
                        "Agent {}: team display names must be unique and at most 100 bytes",
                        self.id
                    ));
                }
            }
        }
        let mut ids = BTreeSet::new();
        for sub in &self.subagents {
            sub.validate(true)?;
            if sub.id == self.id || !ids.insert(&sub.id) {
                return bad(format!("Agent {}: duplicate subagent {}", self.id, sub.id));
            }
        }
        Ok(())
    }
}
