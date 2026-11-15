//! Safe registration diagnostics contain identities and field names, never values.
use crate::agents::Agent;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentDefinitionConflict {
    pub agent_id: String,
    pub conflicting_fields: Vec<String>,
}
impl AgentDefinitionConflict {
    pub(crate) fn between(existing: &Agent, requested: &Agent) -> anyhow::Result<Option<Self>> {
        let old = serde_json::to_value(existing)?;
        let new = serde_json::to_value(requested)?;
        let fields = FIELDS
            .iter()
            .filter(|field| old[**field] != new[**field])
            .map(|field| (*field).to_owned())
            .collect::<Vec<_>>();
        Ok((!fields.is_empty()).then(|| Self {
            agent_id: requested.id.clone(),
            conflicting_fields: fields,
        }))
    }
    pub(crate) fn valid(&self) -> bool {
        !self.agent_id.is_empty()
            && self.agent_id.len() <= 64
            && self
                .agent_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
            && !self.conflicting_fields.is_empty()
            && self.conflicting_fields.len() <= FIELDS.len()
            && self
                .conflicting_fields
                .iter()
                .all(|f| FIELDS.contains(&f.as_str()))
    }
}
const FIELDS: &[&str] = &[
    "name",
    "description",
    "instructions",
    "limits",
    "builtin_tools",
    "tools",
    "model",
    "provider",
    "fallback_model",
    "planning",
    "memory",
    "memory_scope",
    "scheduling",
    "approvals",
    "auto_skip_questions",
    "auto_approver_context",
    "compaction",
    "subagents",
    "standalone_subagents",
    "servers",
    "tool_policy",
    "runtime_policy",
];
impl std::fmt::Display for AgentDefinitionConflict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Agent '{}' already has a different definition ({}); use a new agent name",
            self.agent_id,
            self.conflicting_fields.join(", ")
        )
    }
}
impl std::error::Error for AgentDefinitionConflict {}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reports_field_names_without_definition_values() {
        let first = Agent::new("guide").instructions("SECRET_ORIGINAL");
        assert!(AgentDefinitionConflict::between(&first, &first)
            .unwrap()
            .is_none());
        let error =
            AgentDefinitionConflict::between(&first, &first.clone().instructions("SECRET_NEW"))
                .unwrap()
                .unwrap();
        assert!(error.valid());
        assert_eq!(error.conflicting_fields, ["instructions"]);
        assert!(!format!("{error:?} {error}").contains("SECRET"));
        let mut malformed = error;
        malformed.conflicting_fields = vec!["SECRET_VALUE".into()];
        assert!(!malformed.valid());
    }
}
