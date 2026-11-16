//! Durable server control messages. Applications authenticate/authorize actors;
//! identity pins bind decisions, they do not authenticate their senders.
use crate::store::Session;
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub bundle_digest: String,
    pub schema_digest: String,
    pub policy_digest: String,
    pub resource_policy_digest: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    Response {
        interaction_id: String,
        response: String,
        binding: Binding,
    },
    Cancel,
    ResumeSafety,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Command {
    pub session_id: String,
    pub request_id: String,
    pub turn_id: String,
    /// Opaque authenticated principal ID supplied by the trusted application.
    pub actor: String,
    pub action: Action,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pending,
    Applied,
    Rejected,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record {
    pub command: Command,
    pub status: Status,
    pub reason: Option<String>,
}
impl Command {
    pub fn validate(&self) -> Result<()> {
        for (s, limit) in [
            (&self.session_id, 80),
            (&self.request_id, 100),
            (&self.turn_id, 80),
            (&self.actor, 128),
        ] {
            if s.trim().is_empty() || s.len() > limit || s.contains('\0') {
                bail!("Invalid control identity");
            }
        }
        if let Action::Response {
            interaction_id,
            response,
            binding,
        } = &self.action
        {
            if interaction_id.is_empty()
                || interaction_id.len() > 80
                || response.trim().is_empty()
                || response.len() > 4000
                || response.contains('\0')
            {
                bail!("Invalid control response");
            }
            if [
                &binding.bundle_digest,
                &binding.schema_digest,
                &binding.policy_digest,
                &binding.resource_policy_digest,
            ]
            .iter()
            .any(|s| s.len() > 256 || s.contains('\0'))
            {
                bail!("Invalid control binding");
            }
        }
        Ok(())
    }
    pub fn target(&self) -> String {
        match &self.action {
            Action::Response { interaction_id, .. } => format!("response:{interaction_id}"),
            Action::Cancel => format!("cancel:{}", self.turn_id),
            // A later unavailable check in the same turn may need another retry.
            // The request key deduplicates one retry, not every future pause.
            Action::ResumeSafety => format!("safety_resume:{}", self.request_id),
        }
    }
    /// Saved-state validation only. The owner must additionally use the runtime's
    /// current bundle, hard guards and grant construction before executing tools.
    pub fn check_saved(&self, s: &Session) -> Result<()> {
        self.validate()?;
        if s.id != self.session_id {
            bail!("Control session mismatch");
        }
        let t = s.turn.as_ref().context("Control turn missing")?;
        if t.id != self.turn_id {
            bail!("Stale control turn");
        }
        if matches!(self.action, Action::ResumeSafety) && (s.closed || t.status != "waiting_safety")
        {
            bail!("No paused safety check");
        }
        if let Action::Response {
            interaction_id,
            response,
            binding,
        } = &self.action
        {
            if s.closed || !matches!(t.status.as_str(), "waiting" | "recoverable") {
                bail!("Interaction is not waiting");
            }
            let i = s
                .interactions
                .iter()
                .find(|i| i.id == *interaction_id && i.turn_id == t.id)
                .context("Interaction missing")?;
            if i.response.is_some() {
                bail!("Interaction already answered");
            }
            if binding.bundle_digest != i.bundle_digest
                || binding.schema_digest != i.schema_digest
                || binding.policy_digest != i.policy_digest
                || binding.resource_policy_digest != i.resource_policy_digest
                || i.bundle_digest != s.bundle_digest
                || i.schema_digest != s.schema_digest.as_deref().unwrap_or("")
                || i.policy_digest != s.tool_policy.digest
                || i.resource_policy_digest != s.resource_policy.digest
            {
                bail!("Control binding mismatch");
            }
            if i.kind == "approval" {
                if !["once", "session", "deny"].contains(&response.as_str()) {
                    bail!("Invalid approval decision");
                }
            } else if matches!(i.kind.as_str(), "question" | "compaction") {
                let args: Value = serde_json::from_str(
                    i.call["arguments"].as_str().context("Invalid question")?,
                )?;
                if args["options"]
                    .as_array()
                    .is_some_and(|a| !a.is_empty() && !a.iter().any(|v| v == response))
                {
                    bail!("Answer must match an offered option");
                }
            } else {
                bail!("Unsupported interaction kind");
            }
        }
        Ok(())
    }
}
