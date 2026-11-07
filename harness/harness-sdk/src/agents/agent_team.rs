//! First-class named team instances. No second scheduler or agent loop.
use super::{Harness, Session, SessionRef, TurnLimits};
use crate::{Error, HarnessClient, Result};
pub use openworker_runtime::team_briefing::{Context as ChildContext, Options as ChildOptions};
pub use openworker_runtime::team_coordination::{
    Communication, HistoryPage, HistoryQuery, Instance, Policy as TeamPolicy,
};
pub use openworker_runtime::team_definition::Delegation as TeamDelegation;
pub use openworker_runtime::team_lifecycle::Mode as RetirementMode;
pub use openworker_runtime::team_workspace::Selection as ChildWorkspace;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::Path;
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TeamOptions {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace_defaults: Option<openworker_runtime::team_workspace_defaults::WorktreeDefaults>,
    pub agent_id: String,
    pub agent_types: Vec<String>,
    #[serde(default)]
    pub policy: TeamPolicy,
    #[serde(default)]
    pub limits: Option<TurnLimits>,
    /// App-owned ceiling for custom roles. Omit to disable custom definitions.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delegation: Option<TeamDelegation>,
}
impl TeamOptions {
    pub fn new(types: impl IntoIterator<Item = impl Into<String>>) -> Self {
        Self {
            workspace_defaults: None,
            agent_id: "lead".into(),
            agent_types: types.into_iter().map(Into::into).collect(),
            policy: TeamPolicy::default(),
            limits: None,
            delegation: None,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AgentTeamRef {
    pub lead: SessionRef,
    pub team_id: String,
}
#[derive(Clone)]
pub struct AgentTeam {
    client: HarnessClient,
    lead: Session,
    id: String,
}
impl Harness {
    /// Register each type with the harness builder, then create one team lead.
    /// Types may have many instances. Use standalone definitions, not legacy
    /// `.subagent()` work-item definitions, for this interface.
    pub async fn new_team(
        &self,
        lead: &str,
        workspace: impl AsRef<Path>,
        options: TeamOptions,
    ) -> Result<AgentTeam> {
        let session = self.session(lead, workspace).await?;
        self.attach_team(session, options).await
    }
    /// Idempotent for the same lead session and configuration after response loss.
    pub async fn attach_team(&self, lead: Session, options: TeamOptions) -> Result<AgentTeam> {
        let mut args =
            serde_json::to_value(options).map_err(|e| Error::InvalidInput(e.to_string()))?;
        args["session_id"] = json!(lead.id());
        let doc = self.client().request("agent-team/create", args).await?;
        Ok(AgentTeam {
            client: self.client().clone(),
            lead,
            id: doc["id"]
                .as_str()
                .ok_or_else(|| Error::Remote("Missing team ID".into()))?
                .into(),
        })
    }
    pub async fn resume_team(&self, reference: AgentTeamRef) -> Result<AgentTeam> {
        let lead = self.resume(reference.lead).await?;
        let team = AgentTeam {
            client: self.client().clone(),
            lead,
            id: reference.team_id,
        };
        if team.snapshot().await?["id"] != team.id {
            return Err(Error::InvalidInput("Team identity mismatch".into()));
        }
        Ok(team)
    }
}
impl AgentTeam {
    pub fn id(&self) -> &str {
        &self.id
    }
    pub fn lead(&self) -> &Session {
        &self.lead
    }
    pub fn reference(&self) -> AgentTeamRef {
        AgentTeamRef {
            lead: self.lead.reference().clone(),
            team_id: self.id.clone(),
        }
    }
    pub async fn snapshot(&self) -> Result<Value> {
        self.query("snapshot", json!({})).await
    }
    pub async fn history(&self, query: HistoryQuery) -> Result<HistoryPage> {
        serde_json::from_value(self.query("team_read_board", json!(query)).await?)
            .map_err(|e| Error::Remote(e.to_string()))
    }
    pub async fn spawn(
        &self,
        request_id: &str,
        agent_type: &str,
        agent_id: &str,
        message: &str,
    ) -> Result<Value> {
        self.spawn_with_options(
            request_id,
            agent_type,
            agent_id,
            message,
            ChildOptions::default(),
        )
        .await
    }
    pub async fn spawn_with_options(
        &self,
        request_id: &str,
        agent_type: &str,
        agent_id: &str,
        message: &str,
        options: ChildOptions,
    ) -> Result<Value> {
        let mut args =
            serde_json::to_value(options).map_err(|e| Error::InvalidInput(e.to_string()))?;
        args["agent_type"] = json!(agent_type);
        args["agent_id"] = json!(agent_id);
        args["message"] = json!(message);
        self.command(request_id, "team_spawn", args).await
    }
    pub async fn members(&self, after: Option<&str>, limit: usize) -> Result<Value> {
        let mut args = json!({"action":"list","limit":limit});
        if let Some(after) = after {
            args["after"] = json!(after);
        }
        self.query("team_manage", args).await
    }
    pub async fn retire(
        &self,
        request_id: &str,
        agent_id: &str,
        mode: RetirementMode,
    ) -> Result<Value> {
        self.command(
            request_id,
            "team_manage",
            json!({"action":"retire","agent_id":agent_id,"mode":mode}),
        )
        .await
    }
    pub async fn post(&self, request_id: &str, message: &str) -> Result<Value> {
        self.command(request_id, "team_post_board", json!({"message":message}))
            .await
    }
    pub async fn send_message(
        &self,
        request_id: &str,
        agent_id: &str,
        message: &str,
    ) -> Result<Value> {
        self.command(
            request_id,
            "team_send_message",
            json!({"agent_id":agent_id,"message":message}),
        )
        .await
    }
    pub async fn stop(&self, request_id: &str) -> Result<Value> {
        // The host reconciles this durable stop for every member, even when
        // the lead is idle or this client disconnects before cancellation.
        self.command(request_id, "stop", json!({})).await
    }
    async fn command(&self, id: &str, operation: &str, args: Value) -> Result<Value> {
        self.query(
            "command",
            json!({"request_id":id,"operation":operation,"arguments":args}),
        )
        .await
    }
    async fn query(&self, operation: &str, args: Value) -> Result<Value> {
        self.client
            .request(
                "agent-team/command",
                json!({"session_id":self.lead.id(),"operation":operation,"arguments":args}),
            )
            .await
    }
}
