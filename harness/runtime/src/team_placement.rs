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
