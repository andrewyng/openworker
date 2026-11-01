//! Application-owned isolated-coding defaults. These are configuration/data,
//! never provider authority or permission to read arbitrary source artifacts.
use super::{component, git::Export, Import, ObjectFormat};
use crate::{team_definition::Sandbox, team_workspace::Selection};
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub repository: String,
    pub directory: String,
    pub object_format: ObjectFormat,
    pub commit: String,
}
impl Source {
    pub fn validate(&self) -> Result<()> {
        Export {
            operation_id: "source_validation".into(),
            scope_digest: "a".repeat(64),
            repository: self.repository.clone(),
            directory: self.directory.clone(),
            object_format: self.object_format,
            base_commit: self.commit.clone(),
            submitted_commit: self.commit.clone(),
        }
        .validate()
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Choice {
    Initial,
    Submission { id: String },
}
impl Choice {
    pub fn validate(&self) -> Result<()> {
        if let Self::Submission { id } = self {
            component(id, 80)?;
        }
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DefaultSource {
    Initial,
    /// A verifier must name a submitted handoff, never silently verify the base.
    RequireSubmission,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TypeDefault {
    pub sandbox: Sandbox,
    pub source: DefaultSource,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TeamConfig {
    pub source: Source,
    pub agent_types: BTreeMap<String, TypeDefault>,
}
impl TeamConfig {
    pub fn validate(&self, prebuilt: &BTreeSet<String>) -> Result<()> {
        self.source.validate()?;
        ensure!(
            !self.agent_types.is_empty() && self.agent_types.len() <= 32,
            "Register 1..32 isolated coding defaults"
        );
        for kind in self.agent_types.keys() {
            component(kind, 64)?;
            ensure!(
                kind.as_bytes()[0].is_ascii_alphabetic()
                    && kind != "all"
                    && prebuilt.contains(kind),
                "Coding defaults must name registered prebuilt types"
            );
        }
        Ok(())
    }
    /// Resolve before reservation; the original concrete directory/source are
    /// idempotency inputs. Dynamic definitions do not inherit a prebuilt default.
    /// Sandbox selection must separately revalidate the original ceiling/profile.
    pub fn resolve(
        &self,
        team: &str,
        kind: &str,
        instance: &str,
        workspace: Selection,
        source: Option<Choice>,
    ) -> Result<(Selection, Option<Choice>)> {
        component(team, 80)?;
        component(instance, 64)?;
        let source = match (source, self.agent_types.get(kind)) {
            (
                Some(c),
                Some(TypeDefault {
                    source: DefaultSource::RequireSubmission,
                    ..
                }),
            ) => {
                ensure!(
                    matches!(c, Choice::Submission { .. }),
                    "Verifier requires a submitted handoff"
                );
                Some(c)
            }
            (Some(c), _) => Some(c),
            (
                None,
                Some(TypeDefault {
                    source: DefaultSource::Initial,
                    ..
                }),
            ) => Some(Choice::Initial),
            (
                None,
                Some(TypeDefault {
                    source: DefaultSource::RequireSubmission,
                    ..
                }),
            ) => anyhow::bail!("Verifier requires a submitted handoff"),
            (None, None) => None,
        };
        let Some(source) = source else {
            return Ok((workspace, None));
        };
        source.validate()?;
        let selected = match workspace {
            Selection::Team {} => Selection::Directory {
                path: format!("checkouts/{team}/{instance}"),
            },
            Selection::Directory { path } => Selection::Directory { path },
            Selection::Worktree { .. } => anyhow::bail!(
                "An isolated source needs independent Git metadata, not a linked worktree"
            ),
        };
        let Selection::Directory { path } = &selected else {
            unreachable!()
        };
        // Reuse the portable destination/root-bookkeeping checks, without
        // pretending this placeholder digest is a published artifact reference.
        Import {
            operation_id: "validate_destination".into(),
            scope_digest: "a".repeat(64),
            manifest_digest: "b".repeat(64),
            directory: path.clone(),
        }
        .validate()?;
        ensure!(
            instance.as_bytes()[0].is_ascii_alphabetic() && instance != "all",
            "Invalid coding instance"
        );
        Ok((selected, Some(source)))
    }
    pub fn sandbox_default(&self, kind: &str) -> Option<&Sandbox> {
        self.agent_types.get(kind).map(|t| &t.sandbox)
    }
}
