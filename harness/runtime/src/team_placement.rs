//! Requested placement is not provisioned authority. A trusted adapter must bind
//! the original provider policy/identity before creating the child session.
use crate::{
    agent_team::Document, store::Session, team_coordination::SpawnRequest, team_definition::Sandbox,
};
use anyhow::{ensure, Context, Result};

pub fn select(doc: &Document, lead: &Session, request: &SpawnRequest) -> Result<Sandbox> {
    let catalog = doc.type_catalog.as_ref();
    if let Some(c) = catalog {
        c.check_scope(lead)?;
    }
    let existing = doc.roster.request(&request.agent_id);
    // Repeated reservations retain the ORIGINAL type revision/default, even if
    // a newer named revision has since been approved. Roster checks all raw inputs.
    let record = if let Some(c) = catalog {
        if let Some(binding) = doc.type_bindings.get(&request.agent_id) {
            Some(
                c.records
                    .get(&request.agent_type)
                    .and_then(|rows| {
                        rows.iter().find(|r| {
                            r.binding.digest() == binding
                                && r.status == crate::team_type_catalog::Status::Approved
                        })
                    })
                    .context("Missing original type revision")?,
            )
        } else if !c.prebuilt.contains(&request.agent_type) {
            Some(c.approved(&request.agent_type)?)
        } else {
            None
        }
    } else {
        None
    };
    if let Some(r) = record {
        let c = catalog.context("Missing original type catalog")?;
        r.binding.revalidate(&c.ceiling, &c.prebuilt)?;
    }
    let selected = request
        .sandbox
        .clone()
        .or_else(|| {
            if existing.is_some() {
                doc.sandbox_choices.get(&request.agent_id).cloned()
            } else {
                None
            }
        })
        .or_else(|| record.map(|r| r.binding.definition().sandbox.clone()))
        .unwrap_or_default();
    ensure!(
        doc.placement_selection || selected == Sandbox::default(),
        "This adapter does not support child sandbox selection"
    );
    if let Some(c) = catalog {
        selected.validate(&c.ceiling)?;
    } else {
        ensure!(
            selected == Sandbox::default(),
            "Sandbox choice is not delegated"
        );
    }
    if let Some(r) = record {
        selected.require_subset_of(&r.binding.definition().sandbox)?;
    }
    if let Sandbox::Member { agent_id } = &selected {
        ensure!(
            agent_id != &request.agent_id,
            "A child cannot select itself as its sandbox source"
        );
        let target = doc
            .roster
            .instance(agent_id)
            .context("Unknown sandbox source member")?;
        ensure!(
            matches!(
                target.phase,
                crate::team_coordination::Phase::Reserved | crate::team_coordination::Phase::Ready
            ),
            "Sandbox source member is retired"
        );
    }
    Ok(selected)
}

/// A selection alone grants nothing; this private receipt pins the provider
/// object and activated profile before workspace preparation/child admission.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub choice: Sandbox,
    pub environment: crate::environment::Binding,
    pub profile_id: String,
    pub profile_digest: String,
}
pub fn workspace_scope(doc: &Document, lead: &Session, agent: &str) -> Result<(String, String)> {
    if doc
        .sandbox_choices
        .get(agent)
        .is_none_or(|s| s == &Sandbox::default())
    {
        return Ok((lead.workspace.clone(), lead.resource_policy.digest.clone()));
    }
    let binding = doc
        .placement_bindings
        .get(agent)
        .context("Selected sandbox is not acknowledged")?;
    ensure!(
        doc.placement_selection && doc.sandbox_choices.get(agent) == Some(&binding.choice),
        "Placement receipt differs from selection"
    );
    Ok((
        binding.environment.spec.workspace_root.clone(),
        binding.environment.fingerprint()?,
    ))
}
pub fn schema(doc: &Document) -> serde_json::Value {
    use serde_json::json;
    let Some(c) = &doc.type_catalog else {
        return json!({"type":"object","properties":{"mode":{"type":"string","enum":["team"]}},"required":["mode"],"additionalProperties":false});
    };
    let modes = c
        .ceiling
        .placements
        .iter()
        .map(|p| serde_json::to_value(p).unwrap())
        .collect::<Vec<_>>();
    json!({"type":"object","description":"Select original team sandbox, an existing same-team member's shareable sandbox, or a new sandbox within the delegated immutable profile. Never supplies gateway credentials.","properties":{"mode":{"type":"string","enum":modes},"agent_id":{"type":"string"},"profile":{"type":"string","enum":c.ceiling.profiles.keys().collect::<Vec<_>>(),"description":format!("Delegated profile resources/grants: {}. The current service adapter requires requested CPU/memory to match a registered profile exactly.",serde_json::to_string(&c.ceiling.profiles).unwrap_or_default())},"files":{"type":"array","items":{"type":"string"}},"network":{"type":"array","items":{"type":"string"}},"credentials":{"type":"array","items":{"type":"string"}},"cpu":{"type":"integer","minimum":1},"memory_mb":{"type":"integer","minimum":256}},"required":["mode"],"additionalProperties":false})
}
