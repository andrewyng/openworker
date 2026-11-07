//! Durable scoped proposals and exact-definition approval receipts. These records
//! live in the team's existing transactional document on both storage adapters.
//! A proposal or a serialized digest is never sufficient authorization to spawn.
use crate::{
    store::{Interaction, Session},
    team_definition::{Ceiling, Definition, ScopedDefinition},
};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub const TOOL: &str = "team_define_type";
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pending,
    Approved,
    Denied,
    Abandoned,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Record {
    pub binding: ScopedDefinition,
    pub status: Status,
    pub proposal_turn: String,
    pub proposal_call: String,
    pub created_at_ms: u64,
    pub interaction_id: Option<String>,
    pub approval_origin: Option<String>,
    pub resolved_at_ms: Option<u64>,
}
impl Record {
    pub fn runtime_id(&self) -> String {
        format!("tt_{}", &self.binding.digest()[..48])
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Catalog {
    pub ceiling: Ceiling,
    pub prebuilt: BTreeSet<String>,
    pub records: BTreeMap<String, Vec<Record>>,
}
impl Catalog {
    pub fn new(ceiling: Ceiling, prebuilt: BTreeSet<String>) -> Result<Self> {
        ceiling.validate()?;
        Ok(Self {
            ceiling,
            prebuilt,
            records: BTreeMap::new(),
        })
    }
    pub fn check_scope(&self, lead: &Session) -> Result<()> {
        ensure!(
            self.ceiling.lead_session_id == lead.id,
            "Type catalog belongs to another lead"
        );
        let current = crate::team_definition::Delegation {
            tools: self.ceiling.tools.clone(),
            models: self.ceiling.models.clone(),
            placements: self.ceiling.placements.clone(),
            profiles: self.ceiling.profiles.clone(),
        }
        .bind(lead)?;
        ensure!(
            current.policy_digest == self.ceiling.policy_digest,
            "Delegation policy changed; review a new type catalog"
        );
        Ok(())
    }
    pub fn propose(&mut self, lead: &Session, definition: Definition, now: u64) -> Result<Record> {
        self.check_scope(lead)?;
        definition.validate(&self.ceiling, &self.prebuilt)?;
        let turn = lead
            .turn
            .as_ref()
            .context("Definition requires a current turn")?;
        ensure!(turn.status == "running", "Definition turn is not running");
        let call = turn.queue.first().context("Definition is not queued")?;
        ensure!(
            call["name"] == TOOL
                && serde_json::from_str::<Definition>(
                    call["arguments"]
                        .as_str()
                        .context("Missing definition arguments")?
                )? == definition,
            "Definition differs from queued call"
        );
        let call_id = call["call_id"].as_str().context("Missing call identity")?;
        if let Some(old) = self
            .records
            .values()
            .flatten()
            .find(|r| r.proposal_turn == turn.id && r.proposal_call == call_id)
        {
            ensure!(
                old.binding.definition() == &definition,
                "Changed definition proposal retry"
            );
            old.binding.revalidate(&self.ceiling, &self.prebuilt)?;
            return Ok(old.clone());
        }
        ensure!(
            self.records.values().map(Vec::len).sum::<usize>() < 64,
            "Scoped definition history capacity reached"
        );
        let revisions = self.records.entry(definition.name.clone()).or_default();
        for old in revisions
            .iter_mut()
            .filter(|r| r.status == Status::Pending && r.proposal_turn != turn.id)
        {
            // A store may admit this new turn only after the previous one has
            // ended. An abandoned approval cannot become authority for a new call.
            old.status = Status::Abandoned;
            old.resolved_at_ms = Some(now);
            old.approval_origin = Some("prior_turn_ended".into());
        }
        ensure!(
            !revisions.iter().any(|r| r.status == Status::Pending),
            "Another revision awaits a decision"
        );
        let record = Record {
            binding: ScopedDefinition::propose(
                definition,
                revisions.len() as u32 + 1,
                &self.ceiling,
                &self.prebuilt,
            )?,
            status: Status::Pending,
            proposal_turn: turn.id.clone(),
            proposal_call: call_id.into(),
            created_at_ms: now,
            interaction_id: None,
            approval_origin: None,
            resolved_at_ms: None,
        };
        revisions.push(record.clone());
        Ok(record)
    }
    pub fn resolve(&mut self, lead: &Session, name: &str, now: u64) -> Result<Record> {
        self.check_scope(lead)?;
        let turn = lead
            .turn
            .as_ref()
            .context("Definition requires a current turn")?;
        ensure!(turn.status == "running", "Definition turn is not running");
        let record = self
            .records
            .get_mut(name)
            .and_then(|r| r.last_mut())
            .context("Unknown type proposal")?;
        record.binding.revalidate(&self.ceiling, &self.prebuilt)?;
        ensure!(turn.id == record.proposal_turn, "Stale type proposal");
        let call = turn.queue.first().context("Definition is not queued")?;
        ensure!(
            call["call_id"] == record.proposal_call && call["name"] == TOOL,
            "Stale type call"
        );
        let interaction = lead
            .interactions
            .iter()
            .rev()
            .find(|i| i.turn_id == turn.id && i.call == *call)
            .context("No exact definition approval interaction")?;
        check_interaction(lead, interaction, record.binding.definition())?;
        let status = if interaction.response.as_deref() == Some("deny") {
            Status::Denied
        } else {
            Status::Approved
        };
        if record.status != Status::Pending {
            ensure!(
                record.status == status
                    && record.interaction_id.as_deref() == Some(&interaction.id),
                "Conflicting type decision retry"
            );
            return Ok(record.clone());
        }
        record.status = status;
        record.interaction_id = Some(interaction.id.clone());
        record.approval_origin = interaction.origin.clone();
        record.resolved_at_ms = Some(now);
        Ok(record.clone())
    }
    pub fn approved(&self, name: &str) -> Result<&Record> {
        // New pending/denied revisions do not erase an immutable approved revision.
        let record = self
            .records
            .get(name)
            .and_then(|r| r.iter().rev().find(|r| r.status == Status::Approved))
            .context("No approved custom type")?;
        record.binding.revalidate(&self.ceiling, &self.prebuilt)?;
        Ok(record)
    }
}
/// Restore only approved, correctly scoped revisions whose parent is configured
/// by this host. Also verify every persisted private child keeps its roster pin.
pub fn restore(
    doc: &crate::agent_team::Document,
    lead: &Session,
    parents: &BTreeSet<String>,
) -> Result<Vec<(Session, Record)>> {
    let Some(catalog) = &doc.type_catalog else {
        return Ok(vec![]);
    };
    ensure!(
        doc.roster
            .instance(&doc.lead)
            .is_some_and(|i| i.session_id == lead.id),
        "Invalid scoped catalog parent"
    );
    ensure!(
        parents.contains(&lead.coworker),
        "Scoped catalog parent is not configured"
    );
    catalog.check_scope(lead)?;
    catalog
        .records
        .values()
        .flatten()
        .filter(|r| r.status == Status::Approved)
        .map(|r| {
            r.binding.revalidate(&catalog.ceiling, &catalog.prebuilt)?;
            Ok((lead.clone(), r.clone()))
        })
        .collect()
}
fn check_interaction(
    lead: &Session,
    interaction: &Interaction,
    definition: &Definition,
) -> Result<()> {
    ensure!(
        interaction.kind == "approval" && interaction.call["name"] == TOOL,
        "Wrong approval kind"
    );
    let args: Definition = serde_json::from_str(
        interaction.call["arguments"]
            .as_str()
            .context("Missing arguments")?,
    )?;
    ensure!(
        &args == definition,
        "Approval covers a different definition"
    );
    ensure!(
        interaction.bundle_digest == lead.bundle_digest
            && Some(&interaction.schema_digest) == lead.schema_digest.as_ref()
            && interaction.policy_digest == lead.tool_policy.digest
            && interaction.resource_policy_digest == lead.resource_policy.digest,
        "Type approval policy binding changed"
    );
    ensure!(
        matches!(interaction.response.as_deref(), Some("once" | "deny")),
        "Definition requires an exact decision, not a whole-tool grant"
    );
    ensure!(
        interaction.grant_id.is_none()
            && (matches!(
                interaction.origin.as_deref(),
                Some("human" | "reviewer" | "bypass")
            ) || (interaction.response.as_deref() == Some("deny")
                && interaction.origin.as_deref() == Some("hard_policy"))),
        "Invalid type approval provenance"
    );
    Ok(())
}
