//! Session-local type definition contract. No registration, approval or sandbox
//! side effects live here. A validated definition is NOT an approved definition.
//! Trusted adapters must derive the ceiling from the actual lead session, resolve
//! named grants, and persist approval receipts before exposing these types.
use crate::connections::Choice;
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

fn reference(value: &str, max: usize) -> Result<()> {
    ensure!(
        !value.is_empty()
            && value.len() <= max
            && value
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"-_.:@".contains(&c)),
        "Invalid named policy reference"
    );
    Ok(())
}
fn type_name(value: &str) -> Result<()> {
    instance_name(value)?;
    ensure!(
        !matches!(value, "lead" | "user" | "board"),
        "Reserved custom type name"
    );
    Ok(())
}
fn instance_name(value: &str) -> Result<()> {
    reference(value, 64)?;
    ensure!(
        value.as_bytes()[0].is_ascii_alphabetic()
            && value
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"-_".contains(&c))
            && value != "all",
        "Invalid or reserved instance/type name"
    );
    Ok(())
}
fn references(values: &BTreeSet<String>, max: usize) -> Result<()> {
    ensure!(values.len() <= max, "Too many named grants");
    for v in values {
        reference(v, 128)?;
    }
    Ok(())
}
fn digest(value: &str) -> Result<()> {
    ensure!(
        value.len() == 64
            && value
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
        "Expected a resolved policy digest"
    );
    Ok(())
}
fn cpu() -> u16 {
    1
}
fn memory() -> u32 {
    512
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grants {
    pub files: BTreeSet<String>,
    pub network: BTreeSet<String>,
    pub credentials: BTreeSet<String>,
    pub cpu: u16,
    pub memory_mb: u32,
}
impl Grants {
    fn validate(&self) -> Result<()> {
        references(&self.files, 64)?;
        references(&self.network, 64)?;
        references(&self.credentials, 32)?;
        ensure!(
            (1..=256).contains(&self.cpu) && (256..=1_048_576).contains(&self.memory_mb),
            "Invalid resource bounds"
        );
        Ok(())
    }
    pub fn require_subset_of(&self, maximum: &Self) -> Result<()> {
        self.validate()?;
        maximum.validate()?;
        ensure!(
            self.files.is_subset(&maximum.files),
            "File grants exceed delegation ceiling"
        );
        ensure!(
            self.network.is_subset(&maximum.network),
            "Network grants exceed delegation ceiling"
        );
        ensure!(
            self.credentials.is_subset(&maximum.credentials),
            "Credential grants exceed delegation ceiling"
        );
        ensure!(
            self.cpu <= maximum.cpu && self.memory_mb <= maximum.memory_mb,
            "Resources exceed delegation ceiling"
        );
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Placement {
    Team,
    Member,
    New,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub enum Sandbox {
    Team {},
    Member {
        agent_id: String,
    },
    New {
        profile: String,
        #[serde(default)]
        files: BTreeSet<String>,
        #[serde(default)]
        network: BTreeSet<String>,
        #[serde(default)]
        credentials: BTreeSet<String>,
        #[serde(default = "cpu")]
        cpu: u16,
        #[serde(default = "memory")]
        memory_mb: u32,
    },
}
impl Default for Sandbox {
    fn default() -> Self {
        Self::Team {}
    }
}
impl Sandbox {
    fn validate(&self, ceiling: &Ceiling) -> Result<()> {
        let placement = match self {
            Self::Team {} => Placement::Team,
            Self::Member { agent_id } => {
                instance_name(agent_id)?;
                Placement::Member
            }
            Self::New {
                profile,
                files,
                network,
                credentials,
                cpu,
                memory_mb,
            } => {
                reference(profile, 128)?;
                let maximum = ceiling
                    .profiles
                    .get(profile)
                    .ok_or_else(|| anyhow::anyhow!("Sandbox profile is not delegated"))?;
                Grants {
                    files: files.clone(),
                    network: network.clone(),
                    credentials: credentials.clone(),
                    cpu: *cpu,
                    memory_mb: *memory_mb,
                }
                .require_subset_of(maximum)?;
                Placement::New
            }
        };
        ensure!(
            ceiling.placements.contains(&placement),
            "Sandbox placement is not delegated"
        );
        Ok(())
    }
}

/// Host-owned policy input, never accepted from the model. The digest binds the
/// resolved authority (including grant identities/versions), not just grant names.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Ceiling {
    pub lead_session_id: String,
    pub policy_digest: String,
    pub tools: BTreeSet<String>,
    pub models: Vec<Choice>,
    pub placements: BTreeSet<Placement>,
    pub profiles: BTreeMap<String, Grants>,
}
impl Ceiling {
    pub fn validate(&self) -> Result<()> {
        reference(&self.lead_session_id, 200)?;
        digest(&self.policy_digest)?;
        references(&self.tools, 128)?;
        ensure!(
            !self.placements.is_empty() && self.profiles.len() <= 32 && self.models.len() <= 32,
            "Invalid delegation catalog bounds"
        );
        for (name, grants) in &self.profiles {
            reference(name, 128)?;
            grants.validate()?;
        }
        for model in &self.models {
            model.validate()?;
        }
        Ok(())
    }
}

/// Application-owned delegation configuration. The session identity and policy
/// digest are derived by the host; neither is a model-controlled argument.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Delegation {
    pub tools: BTreeSet<String>,
    #[serde(default)]
    pub models: Vec<Choice>,
    pub placements: BTreeSet<Placement>,
    #[serde(default)]
    pub profiles: BTreeMap<String, Grants>,
}
impl Delegation {
    pub fn bind(&self, lead: &crate::store::Session) -> Result<Ceiling> {
        let policy_digest = crate::bundle::digest(&serde_json::to_vec(&(
            "team-delegation-v1",
            self,
            &lead.bundle_digest,
            &lead.resource_policy.digest,
            &lead.tool_policy.digest,
            &lead.permissions,
        ))?);
        let ceiling = Ceiling {
            lead_session_id: lead.id.clone(),
            policy_digest,
            tools: self.tools.clone(),
            models: self.models.clone(),
            placements: self.placements.clone(),
            profiles: self.profiles.clone(),
        };
        ceiling.validate()?;
        Ok(ceiling)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Definition {
    pub name: String,
    pub instructions: String,
    pub tools: BTreeSet<String>,
    #[serde(default)]
    pub sandbox: Sandbox,
    /// None means the lead's persisted resolved model, not a moving global default.
    #[serde(default)]
    pub model: Option<Choice>,
}
impl Definition {
    pub fn validate(&self, ceiling: &Ceiling, prebuilt_names: &BTreeSet<String>) -> Result<()> {
        ceiling.validate()?;
        type_name(&self.name)?;
        ensure!(
            !prebuilt_names.contains(&self.name),
            "Custom type cannot shadow a pre-built type"
        );
        ensure!(
            !self.instructions.trim().is_empty()
                && self.instructions.len() <= 32000
                && !self.instructions.contains('\0'),
            "Invalid custom instructions"
        );
        references(&self.tools, 64)?;
        ensure!(
            self.tools.is_subset(&ceiling.tools),
            "Tools exceed delegation ceiling"
        );
        self.sandbox.validate(ceiling)?;
        if let Some(model) = &self.model {
            model.validate()?;
            ensure!(ceiling.models.contains(model), "Model is not delegated");
        }
        ensure!(
            serde_json::to_vec(self)?.len() <= 128_000,
            "Custom definition exceeds storage bound"
        );
        Ok(())
    }
}

/// Binding material for a future durable approval receipt. Possessing this value
/// does not approve the type or create any runtime. Receipt ownership remains in
/// the trusted storage/approval adapter, not in model-supplied JSON.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScopedDefinition {
    lead_session_id: String,
    policy_digest: String,
    revision: u32,
    definition: Definition,
    digest: String,
}
impl ScopedDefinition {
    pub fn propose(
        definition: Definition,
        revision: u32,
        ceiling: &Ceiling,
        prebuilt_names: &BTreeSet<String>,
    ) -> Result<Self> {
        definition.validate(ceiling, prebuilt_names)?;
        ensure!(
            (1..=65535).contains(&revision),
            "Invalid custom type revision"
        );
        let mut record = Self {
            lead_session_id: ceiling.lead_session_id.clone(),
            policy_digest: ceiling.policy_digest.clone(),
            revision,
            definition,
            digest: String::new(),
        };
        record.digest = record.binding_digest()?;
        Ok(record)
    }
    fn binding_digest(&self) -> Result<String> {
        Ok(crate::bundle::digest(&serde_json::to_vec(&(
            "team-type-definition-v1",
            &self.lead_session_id,
            &self.policy_digest,
            self.revision,
            &self.definition,
        ))?))
    }
    pub fn digest(&self) -> &str {
        &self.digest
    }
    pub fn definition(&self) -> &Definition {
        &self.definition
    }
    pub fn revision(&self) -> u32 {
        self.revision
    }
    pub fn revalidate(&self, ceiling: &Ceiling, prebuilt_names: &BTreeSet<String>) -> Result<()> {
        ensure!(
            self.lead_session_id == ceiling.lead_session_id,
            "Custom type belongs to another lead session"
        );
        ensure!(
            self.policy_digest == ceiling.policy_digest,
            "Delegation policy changed; review a new revision"
        );
        ensure!(
            (1..=65535).contains(&self.revision) && self.digest == self.binding_digest()?,
            "Custom type binding was modified"
        );
        self.definition.validate(ceiling, prebuilt_names)
    }
}

/// Only trusted adapters may supply these fingerprints, computed from resolved
/// enforced policy, mounts and credential identities. Matching profile labels or
/// tool lists are not evidence that two execution contexts have the same access.
pub fn require_shared_scope(requested_fingerprint: &str, actual_fingerprint: &str) -> Result<()> {
    digest(requested_fingerprint)?;
    digest(actual_fingerprint)?;
    ensure!(
        requested_fingerprint == actual_fingerprint,
        "Shared sandbox has different enforced access; use a separate sandbox"
    );
    Ok(())
}
