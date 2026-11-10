//! Versioned coding definitions over the ordinary runtime; nothing starts here.
use super::{Agent, TeamOptions};
use crate::{Error, Result};
pub use openworker_runtime::team_workspace_defaults::WorktreeDefaults;
use serde::Deserialize;
use std::collections::BTreeSet;
pub const VERSION: &str = "coding-v1";
#[derive(Deserialize)]
struct Role {
    instructions: String,
    tools: Vec<String>,
}
#[derive(Deserialize)]
struct Presets {
    version: String,
    single: Role,
    lead: Role,
    builder: Role,
    verifier: Role,
    isolated_lead: Role,
    isolated_builder: Role,
    isolated_verifier: Role,
}
fn presets() -> Presets {
    let data: Presets = serde_json::from_str(include_str!("presets/coding-v1.json"))
        .expect("bundled coding preset");
    assert_eq!(data.version, VERSION);
    data
}
fn role(id: impl Into<String>, role: Role) -> Agent {
    Agent::new(id)
        .instructions(role.instructions)
        .tools(role.tools)
        .planning(true)
}
/// Starts as one agent, with no implicit children, delegation or GitHub access.
/// Customize using the standard Agent methods. Default approvals remain Ask.
pub fn single(id: impl Into<String>) -> Agent {
    role(id, presets().single)
}
pub struct SharedPreset {
    pub lead: Agent,
    pub builder: Agent,
    pub verifier: Agent,
    pub team: TeamOptions,
}
impl SharedPreset {
    pub fn agents(&self) -> [Agent; 3] {
        [
            self.lead.clone(),
            self.builder.clone(),
            self.verifier.clone(),
        ]
    }
    pub fn service_team(&self, version: &str) -> crate::service::AgentTeamOptions {
        crate::service::AgentTeamOptions {
            coding: None,
            agent_id: self.team.agent_id.clone(),
            agent_types: [self.builder.id(), self.verifier.id()]
                .into_iter()
                .map(|id| (id.into(), format!("{id}@{version}")))
                .collect(),
            policy: self.team.policy.clone(),
            limits: self.team.limits.clone(),
            delegation: self.team.delegation.clone(),
            workspace_defaults: self.team.workspace_defaults.clone(),
        }
    }
}
/// Convert a coding definition to a release for an operator-installed coding
/// bundle. The RuntimeProfile controls service approval/sandbox settings; reject
/// local-only policies rather than silently dropping them during conversion.
pub fn service_release(
    agent: &Agent,
    version: &str,
    profile: &crate::service::RuntimeProfile,
) -> Result<crate::service::AgentRelease> {
    agent.validate(false)?;
    if agent.servers.is_some()
        || agent.tool_policy.is_some()
        || agent.runtime_policy.is_some()
        || !agent.subagents.is_empty()
        || !agent.planning
        || agent.memory
        || agent.memory_scope.is_some()
        || agent.scheduling
        || matches!(agent.approvals, super::Approvals::Only(_))
        || agent.approvals.mode() != profile.approval_mode
        || agent.auto_skip_questions != profile.auto_skip_questions
    {
        return Err(Error::InvalidInput("Service releases use the installed coding bundle and RuntimeProfile; local policies, nested agents and unsupported capabilities cannot be silently converted".into()));
    }
    let reviewer = match &agent.approvals {
        super::Approvals::Auto(m) => Some(m.clone()),
        _ => None,
    };
    let release:crate::service::AgentRelease=serde_json::from_value(serde_json::json!({
        "id":format!("{}@{version}",agent.id()),"instructions":agent.instructions,"tools":agent.tools,
        "runtimeProfiles":[profile.id],"defaultRuntimeProfile":profile.id,"allowedPrincipals":profile.allowed_principals,
        "defaultModel":agent.model,"reviewerModel":reviewer,"modelOverrides":[],"limits":agent.limits
    })).map_err(|e|Error::InvalidInput(e.to_string()))?;
    release
        .validate()
        .map_err(|e| Error::InvalidInput(e.to_string()))?;
    Ok(release)
}
/// `repository` is relative to the shared project root (`.` is supported).
/// `base` is a pinned commit SHA already present in that repository. The lead
/// integrates in its existing main worktree; no automatic branch switch occurs.
pub fn shared(prefix: &str, repository: &str, base: &str) -> Result<SharedPreset> {
    if !openworker_runtime::bundle::safe_name(prefix)
        || prefix.len() > 48
        || !prefix
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphabetic)
    {
        return Err(Error::InvalidInput(
            "Coding team prefix must be 1–48 safe ASCII characters".into(),
        ));
    }
    let p = presets();
    let lead = role(format!("{prefix}-lead"), p.lead);
    let builder = role(format!("{prefix}-builder"), p.builder);
    let verifier = role(format!("{prefix}-verifier"), p.verifier);
    let defaults = WorktreeDefaults {
        repository: repository.into(),
        base: base.into(),
        agent_types: BTreeSet::from([builder.id().into()]),
    };
    defaults
        .validate()
        .map_err(|e| Error::InvalidInput(e.to_string()))?;
    let mut team = TeamOptions::new([builder.id(), verifier.id()]);
    team.workspace_defaults = Some(defaults);
    Ok(SharedPreset {
        lead,
        builder,
        verifier,
        team,
    })
}

/// Artifact-first service preset. Profiles, artifact storage and OpenShell are
/// installed by the operator. Definitions alone never allocate a sandbox.
pub struct IsolatedServicePreset {
    pub lead: Agent,
    pub builder: Agent,
    pub verifier: Agent,
    pub team: crate::service::AgentTeamOptions,
}
impl IsolatedServicePreset {
    pub fn agents(&self) -> [Agent; 3] {
        [
            self.lead.clone(),
            self.builder.clone(),
            self.verifier.clone(),
        ]
    }
}
pub fn isolated_service(
    prefix: &str,
    source: openworker_runtime::coding_handoff::config::Source,
    sandbox: openworker_runtime::team_definition::Sandbox,
    delegation: openworker_runtime::team_definition::Delegation,
    version: &str,
) -> Result<IsolatedServicePreset> {
    source
        .validate()
        .map_err(|e| Error::InvalidInput(e.to_string()))?;
    let baseline = shared(prefix, &source.directory, &source.commit)?;
    let openworker_runtime::team_definition::Sandbox::New { profile, .. } = &sandbox else {
        return Err(Error::InvalidInput(
            "Isolated preset needs an explicit New sandbox profile".into(),
        ));
    };
    if !delegation.profiles.contains_key(profile) {
        return Err(Error::InvalidInput(
            "Register the selected profile in the delegation ceiling".into(),
        ));
    }
    let p = presets();
    let lead = role(baseline.lead.id(), p.isolated_lead);
    let builder = role(baseline.builder.id(), p.isolated_builder);
    let verifier = role(baseline.verifier.id(), p.isolated_verifier);
    let mut team = baseline.service_team(version);
    team.workspace_defaults = None;
    team.delegation = Some(delegation);
    use openworker_runtime::coding_handoff::config::{DefaultSource, TeamConfig, TypeDefault};
    team.coding = Some(TeamConfig {
        source,
        agent_types: [
            (
                builder.id().into(),
                TypeDefault {
                    sandbox: sandbox.clone(),
                    source: DefaultSource::Initial,
                },
            ),
            (
                verifier.id().into(),
                TypeDefault {
                    sandbox,
                    source: DefaultSource::RequireSubmission,
                },
            ),
        ]
        .into(),
    });
    Ok(IsolatedServicePreset {
        lead,
        builder,
        verifier,
        team,
    })
}
