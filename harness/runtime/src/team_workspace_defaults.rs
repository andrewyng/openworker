//! Application-owned worktree defaults. Resolution precedes reservation, so the
//! generated path/base are durable idempotency inputs, not prompt conventions.
use crate::team_workspace::Selection;
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorktreeDefaults {
    pub repository: String,
    /// Immutable local commit; HEAD is intentionally not a cross-builder default.
    pub base: String,
    pub agent_types: BTreeSet<String>,
}
impl WorktreeDefaults {
    pub fn validate(&self) -> Result<()> {
        if self.repository != "." {
            crate::runner::relative_path(&self.repository)?;
        }
        ensure!(
            [40, 64].contains(&self.base.len()) && self.base.bytes().all(|c| c.is_ascii_hexdigit()),
            "Worktree defaults require a pinned commit SHA"
        );
        ensure!(
            !self.agent_types.is_empty()
                && self.agent_types.len() <= 32
                && self
                    .agent_types
                    .iter()
                    .all(|s| crate::bundle::safe_name(s) && s.len() <= 64),
            "Invalid default-worktree agent types"
        );
        Ok(())
    }
    pub fn resolve(
        &self,
        team: &str,
        kind: &str,
        instance: &str,
        requested: Selection,
    ) -> Result<Selection> {
        self.validate()?;
        if requested != Selection::default() || !self.agent_types.contains(kind) {
            return Ok(requested);
        }
        ensure!(
            crate::bundle::safe_name(team) && crate::bundle::safe_name(instance),
            "Invalid worktree identity"
        );
        // Names are bounded even when app/agent identifiers use their full limit.
        let key = crate::bundle::digest(format!("{team}/{instance}").as_bytes());
        let selected = Selection::Worktree {
            path: format!(".openworker/teams/{team}/workers/{instance}"),
            repository: self.repository.clone(),
            base: self.base.clone(),
            branch: format!("ow/{team}/{}", &key[..24]),
        };
        selected.validate()?;
        Ok(selected)
    }
}
