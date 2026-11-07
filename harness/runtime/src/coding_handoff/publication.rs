//! Portable trusted-repository scope and publication receipts. Metadata only;
//! optional host Git execution lives outside the core runtime dependency graph.
use super::{git::Bundle, oid};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Repository {
    /// Exact owner/repository; GitHub origin is fixed by this adapter.
    pub github: String,
    #[serde(default)]
    pub read_refs: BTreeSet<String>,
    #[serde(default)]
    pub write_prefixes: BTreeSet<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grant {
    pub credential_env: String,
    pub repositories: BTreeMap<String, Repository>,
    #[serde(default)]
    pub expires_at_ms: Option<u64>,
    #[serde(default)]
    pub revoked: bool,
}
pub fn branch(value: &str) -> Result<()> {
    ensure!(
        !value.is_empty()
            && value.len() <= 160
            && !value.starts_with('/')
            && !value.ends_with('/')
            && !value.contains("..")
            && !value.contains("//")
            && !value.contains("@{")
            && value.split('/').all(|v| !v.is_empty()
                && !v.starts_with('.')
                && !v.ends_with('.')
                && !v.ends_with(".lock"))
            && value
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"/_-.".contains(&b)),
        "Invalid ordinary branch"
    );
    Ok(())
}
pub fn reference(value: &str) -> Result<()> {
    branch(
        value
            .strip_prefix("refs/heads/")
            .context("Only ordinary branch refs are supported")?,
    )
}
impl Grant {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.credential_env.is_empty()
                && self.credential_env.len() <= 100
                && self
                    .credential_env
                    .bytes()
                    .enumerate()
                    .all(|(i, b)| b == b'_'
                        || b.is_ascii_alphabetic()
                        || (i > 0 && b.is_ascii_digit())),
            "Invalid credential environment reference"
        );
        ensure!(
            !self.repositories.is_empty() && self.repositories.len() <= 32,
            "Register 1..32 repositories"
        );
        for (name, r) in &self.repositories {
            ensure!(
                !name.is_empty()
                    && name.len() <= 200
                    && name
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"/_-.".contains(&b))
                    && !name.contains(".."),
                "Invalid repository identity"
            );
            let parts: Vec<_> = r.github.split('/').collect();
            ensure!(
                parts.len() == 2
                    && parts.iter().all(|p| !p.is_empty()
                        && p.len() <= 100
                        && *p != "."
                        && *p != ".."
                        && p.bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"_-.".contains(&b))),
                "Expected exact GitHub owner/repository"
            );
            ensure!(
                r.read_refs.len() <= 32 && r.write_prefixes.len() <= 16,
                "Repository scope is too large"
            );
            for name in &r.read_refs {
                reference(name)?;
            }
            for prefix in &r.write_prefixes {
                ensure!(prefix.ends_with('/'), "Writable prefix must end with /");
                branch(prefix.trim_end_matches('/'))?;
            }
        }
        Ok(())
    }
    pub fn digest(&self) -> Result<String> {
        self.validate()?;
        Ok(crate::bundle::digest(&serde_json::to_vec(self)?))
    }
    pub fn live(&self, now_ms: u64) -> Result<()> {
        self.validate()?;
        ensure!(
            !self.revoked && self.expires_at_ms.is_none_or(|t| now_ms < t),
            "Repository grant expired or revoked"
        );
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Publication {
    pub id: String,
    pub grant_digest: String,
    pub bundle: Bundle,
    pub branch: String,
    /// None means create-only. An existing branch requires its exact old tip.
    pub expected_head: Option<String>,
}
impl Publication {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.id.is_empty()
                && self.id.len() <= 80
                && self
                    .id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
            "Invalid publication identity"
        );
        ensure!(
            self.grant_digest.len() == 64
                && self
                    .grant_digest
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "Invalid original grant digest"
        );
        self.bundle.validate()?;
        branch(&self.branch)?;
        if let Some(head) = &self.expected_head {
            oid(head, self.bundle.request.object_format)?;
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum Outcome {
    Published {
        request: Publication,
        observed_head: String,
    },
    Rejected {
        request: Publication,
        reason: String,
    },
    Unknown {
        request: Publication,
        observed_head: Option<String>,
    },
}
impl Outcome {
    pub fn require_request(&self, expected: &Publication) -> Result<()> {
        expected.validate()?;
        match self {
            Self::Published {
                request,
                observed_head,
            } => ensure!(
                *request == *expected && *observed_head == expected.bundle.request.submitted_commit,
                "Publication receipt differs from original request"
            ),
            Self::Rejected { request, reason } => ensure!(
                *request == *expected
                    && !reason.is_empty()
                    && reason.len() <= 1024
                    && !reason.contains('\0'),
                "Publication rejection differs from original request"
            ),
            Self::Unknown {
                request,
                observed_head,
            } => {
                ensure!(
                    *request == *expected,
                    "Unknown publication differs from original request"
                );
                if let Some(head) = observed_head {
                    oid(head, expected.bundle.request.object_format)?;
                }
            }
        }
        Ok(())
    }
}

/// Non-secret profile selection, installed only by the qualified service adapter.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Access {
    pub grant: String,
    pub digest: String,
    pub repositories: BTreeMap<String, Repository>,
}
impl Access {
    pub fn from_grant(name: &str, grant: &Grant) -> Result<Self> {
        let (id, version) = name
            .split_once('@')
            .context("Repository grants require a versioned name")?;
        ensure!(
            !id.is_empty()
                && !version.is_empty()
                && name.len() <= 129
                && name
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"@_- .".contains(&c))
                && !name.contains(' ')
                && !version.contains('@'),
            "Invalid repository grant name"
        );
        Ok(Self {
            grant: name.into(),
            digest: grant.digest()?,
            repositories: grant.repositories.clone(),
        })
    }
    pub fn require_grant(&self, name: &str, grant: &Grant) -> Result<()> {
        ensure!(
            *self == Self::from_grant(name, grant)?,
            "Original repository access changed"
        );
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    pub lead_profile: String,
    pub profiles: BTreeMap<String, Access>,
}
pub fn access<'a>(
    doc: &'a crate::agent_team::Document,
    session: &crate::store::Session,
) -> Option<&'a Access> {
    let member = session.agent_team.as_ref()?;
    access_for(doc, &member.agent_id)
}
pub fn access_for<'a>(doc: &'a crate::agent_team::Document, agent_id: &str) -> Option<&'a Access> {
    let catalog = doc.repository_access.as_ref()?;
    let profile = if let Some(binding) = doc.placement_bindings.get(agent_id) {
        &binding.profile_id
    } else {
        if doc
            .sandbox_choices
            .get(agent_id)
            .is_some_and(|s| !matches!(s, crate::team_definition::Sandbox::Team {}))
        {
            return None;
        }
        &catalog.lead_profile
    };
    catalog.profiles.get(profile)
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub id: String,
    pub submission_id: String,
    pub manifest_digest: String,
    pub actor: super::Producer,
    pub origin: super::workflow::Call,
    pub grant: String,
    pub grant_digest: String,
    pub branch: String,
    pub expected_head: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct State {
    pub request: Request,
    pub outcome: Option<Outcome>,
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub uncertain: bool,
}
pub fn request(
    doc: &mut crate::agent_team::Document,
    session: &crate::store::Session,
    submission_id: String,
    branch_name: String,
    expected_head: Option<String>,
    origin: super::workflow::Call,
) -> Result<serde_json::Value> {
    ensure!(
        doc.coding_selection && !doc.stopped,
        "Coding publication unavailable"
    );
    origin.validate()?;
    branch(&branch_name)?;
    let scope = access(doc, session).context("This profile has no trusted repository access")?;
    let submission = doc
        .submissions
        .get(&submission_id)
        .context("Unknown same-team submission")?;
    let manifest = submission
        .manifest
        .as_ref()
        .context("Submission is not published")?;
    let repo = scope
        .repositories
        .get(&manifest.repository)
        .context("Repository publication denied")?;
    ensure!(
        repo.write_prefixes
            .iter()
            .any(|p| branch_name.starts_with(p) && branch_name.len() > p.len()),
        "Branch publication denied"
    );
    if let Some(ref head) = expected_head {
        oid(head, manifest.object_format)?;
    }
    let member = session.agent_team.as_ref().context("Not a team member")?;
    let id = format!(
        "gp_{}",
        crate::bundle::digest(&serde_json::to_vec(&(
            "trusted-publication-v1",
            &doc.id,
            &submission_id,
            &manifest.digest()?,
            &scope.grant,
            &scope.digest,
            &branch_name,
            &expected_head
        ))?)
    );
    if let Some(original) = doc.publications.get(&id) {
        return Ok(view(original));
    }
    ensure!(
        doc.publications.len() < 256,
        "Team publication capacity reached"
    );
    let request = Request {
        id: id.clone(),
        submission_id,
        manifest_digest: manifest.digest()?,
        actor: super::Producer {
            team_id: doc.id.clone(),
            agent_id: member.agent_id.clone(),
            session_id: session.id.clone(),
        },
        origin,
        grant: scope.grant.clone(),
        grant_digest: scope.digest.clone(),
        branch: branch_name,
        expected_head,
    };
    let state = State {
        request,
        outcome: None,
        error: None,
        uncertain: false,
    };
    let result = view(&state);
    doc.publications.insert(id, state);
    Ok(result)
}
pub fn view(state: &State) -> serde_json::Value {
    let status = match state.outcome.as_ref() {
        Some(Outcome::Published { .. }) => "published",
        Some(Outcome::Rejected { .. }) => "rejected",
        Some(Outcome::Unknown { .. }) => "outcome_unknown",
        None if state.error.is_some() => "blocked",
        None if state.uncertain => "outcome_unknown",
        None => "pending",
    };
    serde_json::json!({"id":state.request.id,"submission_id":state.request.submission_id,"branch":state.request.branch,"expected_head":state.request.expected_head,"actor":state.request.actor,"status":status,"outcome":state.outcome,"error":state.error})
}
impl Request {
    pub fn require_manifest(&self, manifest: &super::Manifest) -> Result<()> {
        self.actor.validate()?;
        self.origin.validate()?;
        branch(&self.branch)?;
        manifest.validate()?;
        ensure!(
            manifest.kind == super::Kind::Submission
                && manifest.producer.team_id == self.actor.team_id
                && self.manifest_digest == manifest.digest()?,
            "Publication differs from the same-team submitted revision"
        );
        if let Some(head) = &self.expected_head {
            oid(head, manifest.object_format)?;
        }
        let id = format!(
            "gp_{}",
            crate::bundle::digest(&serde_json::to_vec(&(
                "trusted-publication-v1",
                &self.actor.team_id,
                &self.submission_id,
                &self.manifest_digest,
                &self.grant,
                &self.grant_digest,
                &self.branch,
                &self.expected_head
            ))?)
        );
        ensure!(self.id == id, "Original publication identity changed");
        Ok(())
    }
}

/// A normal registered native tool, selected by fully qualified bundle name.
pub const TOOL: &str = "github_broker.publish_code";
pub const ALIAS: &str = "github_broker__publish_code";
