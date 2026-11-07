//! Host-side agent compilation: `HostConfig.agents` → validated, deterministic
//! coworker bundles plus the run policy the host enforces for each profile.
//! Runs inside the `Host` (embedded or child process), so every SDK language
//! gets identical behaviour from the same JSON definitions.
use super::{bundle, Agent, Approvals};
use crate::{CoworkerConfig, Error, Result};
use openworker_runtime::teams::Role;
use serde::Serialize;
use std::{collections::BTreeMap, path::Path};

/// What the host needs to know about each agent at run time.
#[derive(Clone, Debug, Serialize)]
pub(crate) struct AgentProfile {
    pub id: String,
    pub name: String,
    pub description: String,
    pub approvals: Approvals,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub auto_skip_questions: bool,
    pub subagents: Vec<String>,
    /// Defined only as a subagent: sessions come from its lead.
    pub worker_only: bool,
}
impl AgentProfile {
    /// Profile for a hand-written coworker bundle (no agent definition).
    pub fn plain(id: &str, name: &str) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            description: String::new(),
            approvals: Approvals::Always,
            auto_skip_questions: false,
            subagents: vec![],
            worker_only: false,
        }
    }
}

/// Validate and flatten leads and their subagents. Shared by client-side
/// fast validation and host startup, so both reject the same definitions.
pub(crate) fn flatten(agents: &[Agent]) -> Result<BTreeMap<String, (Agent, Option<Role>)>> {
    let mut flat: BTreeMap<String, (Agent, Option<Role>)> = BTreeMap::new();
    let mut add = |agent: &Agent, role: Option<Role>| -> Result<()> {
        // Also enforces the manifest size limit and parser compatibility.
        bundle::manifest(agent, role)?;
        let Some((existing, existing_role)) = flat.get(&agent.id).cloned() else {
            flat.insert(agent.id.clone(), (agent.clone(), role));
            return Ok(());
        };
        if existing_role != role || role != Some(Role::Worker) {
            return Err(Error::InvalidInput(format!(
                "Agent ID {} is registered twice (or as both lead and worker)",
                agent.id
            )));
        }
        if serde_json::to_value(agent).map_err(|e| Error::InvalidInput(e.to_string()))?
            == serde_json::to_value(&existing).map_err(|e| Error::InvalidInput(e.to_string()))?
        {
            Ok(())
        } else {
            Err(Error::InvalidInput(format!(
                "Two different definitions share agent ID {}",
                agent.id
            )))
        }
    };
    for agent in agents {
        agent.validate(false)?;
        let role = (!agent.subagents.is_empty()).then_some(Role::Lead);
        add(agent, role)?;
        for sub in &agent.subagents {
            add(sub, Some(Role::Worker))?;
        }
    }
    Ok(flat)
}

/// Materialize bundles under `state_dir/agents` and return the coworker
/// configurations to load plus each agent's run profile.
pub(crate) fn compile(
    state_dir: &Path,
    agents: &[Agent],
) -> Result<(Vec<CoworkerConfig>, BTreeMap<String, AgentProfile>)> {
    let flat = flatten(agents)?;
    let root = state_dir.join("agents");
    let mut coworkers = vec![];
    let mut profiles = BTreeMap::new();
    for (id, (agent, role)) in &flat {
        let dir = bundle::materialize(&root, agent, *role)?;
        coworkers.push(CoworkerConfig {
            bundle: dir,
            model: agent.fallback_model.clone(),
            provider: agent.provider,
            servers: agent.servers.clone(),
            tool_policy: agent.tool_policy.clone(),
            runtime_policy: agent.runtime_policy.clone(),
        });
        profiles.insert(
            id.clone(),
            AgentProfile {
                id: id.clone(),
                name: agent.display_name().to_owned(),
                description: agent.description.clone(),
                approvals: agent.approvals.clone(),
                auto_skip_questions: agent.auto_skip_questions,
                subagents: agent.subagents.iter().map(|s| s.id.clone()).collect(),
                worker_only: *role == Some(Role::Worker),
            },
        );
    }
    Ok((coworkers, profiles))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_workers_must_match_every_configuration_field() {
        let worker = Agent::new("worker").instructions("Work.").tool("sdk.probe");
        let lead = |id: &str, w| {
            Agent::new(id)
                .instructions("Lead.")
                .tool("sdk.probe")
                .subagent(w)
        };
        assert!(flatten(&[lead("one", worker.clone()), lead("two", worker.clone())]).is_ok());
        for different in [
            worker
                .clone()
                .fallback_model(crate::ProviderKind::Anthropic, "different"),
            worker.clone().servers("/required/servers.json"),
            worker.clone().tool_policy("/required/tool-policy.json"),
            worker
                .clone()
                .runtime_policy("/required/runtime-policy.json"),
            worker.clone().approvals(Approvals::Never),
            worker
                .clone()
                .model("different-connection", "different-model"),
        ] {
            assert!(flatten(&[lead("one", worker.clone()), lead("two", different)]).is_err());
        }
    }
}
