//! Portable data contract for artifact-first coding handoff. Valid metadata is
//! NOT authorization, proof of artifact publication, Git integrity or successful
//! verification. Trusted adapters must check those facts before any I/O/admission.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};

pub mod git;

pub const VERSION: u8 = 1;
pub const MAX_BUNDLE_BYTES: u64 = 64 * 1024 * 1024;
fn component(value: &str, max: usize) -> Result<()> {
    ensure!(
        !value.is_empty()
            && value.len() <= max
            && value
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c)),
        "Invalid handoff identity"
    );
    Ok(())
}
fn hex(value: &str, length: usize) -> Result<()> {
    ensure!(
        value.len() == length
            && value
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
        "Expected a pinned lowercase object/checksum identity"
    );
    Ok(())
}
fn repository(value: &str) -> Result<()> {
    ensure!(
        !value.is_empty()
            && value.len() <= 200
            && !value.starts_with(['/', '-'])
            && !value.contains("..")
            && value
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"_-./:@".contains(&c)),
        "Invalid application repository identity"
    );
    // Treat this as an application reference, never a model-provided endpoint.
    ensure!(
        !value.contains("://"),
        "Repository identity cannot be a download URL"
    );
    Ok(())
}
fn oid(value: &str, format: ObjectFormat) -> Result<()> {
    hex(value, format.oid_length())?;
    ensure!(
        value.bytes().any(|c| c != b'0'),
        "Null Git object ID is not a revision"
    );
    Ok(())
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObjectFormat {
    Sha1,
    Sha256,
}
impl ObjectFormat {
    pub fn oid_length(self) -> usize {
        match self {
            Self::Sha1 => 40,
            Self::Sha256 => 64,
        }
    }
    pub fn git_name(self) -> &'static str {
        match self {
            Self::Sha1 => "sha1",
            Self::Sha256 => "sha256",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Source,
    Submission,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Producer {
    pub team_id: String,
    pub agent_id: String,
    pub session_id: String,
}
impl Producer {
    fn validate(&self) -> Result<()> {
        component(&self.team_id, 80)?;
        component(&self.session_id, 80)?;
        component(&self.agent_id, 64)?;
        ensure!(
            self.agent_id.as_bytes()[0].is_ascii_alphabetic() && self.agent_id != "all",
            "Invalid producer instance"
        );
        Ok(())
    }
}
/// Reference to the existing artifact catalog, never a storage key or URL.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactRef {
    pub session_id: String,
    pub artifact_id: String,
    pub size: u64,
    pub sha256: String,
}
impl ArtifactRef {
    pub fn validate(&self) -> Result<()> {
        component(&self.session_id, 80)?;
        component(&self.artifact_id, 40)?;
        hex(&self.sha256, 64)?;
        ensure!(
            self.size > 0 && self.size <= MAX_BUNDLE_BYTES,
            "Git bundle size outside artifact limit"
        );
        Ok(())
    }
}
/// Collaborator-supplied report. Neither an approval nor a verified/integrated flag.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Report {
    pub submitted_commit: String,
    pub text: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub version: u8,
    pub kind: Kind,
    /// Application-owned repository identity; never interpreted as a remote URL.
    pub repository: String,
    pub producer: Producer,
    pub object_format: ObjectFormat,
    pub base_commit: String,
    pub submitted_commit: String,
    pub artifact: ArtifactRef,
    #[serde(default)]
    pub reports: Vec<Report>,
}
impl Manifest {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.version == VERSION,
            "Unsupported handoff manifest version"
        );
        repository(&self.repository)?;
        self.producer.validate()?;
        self.artifact.validate()?;
        ensure!(
            self.artifact.session_id == self.producer.session_id,
            "Artifact producer differs from handoff producer"
        );
        oid(&self.base_commit, self.object_format)?;
        oid(&self.submitted_commit, self.object_format)?;
        if self.kind == Kind::Source {
            ensure!(
                self.base_commit == self.submitted_commit,
                "A source snapshot pins one exact revision"
            );
        }
        ensure!(self.reports.len() <= 16, "Too many handoff reports");
        for report in &self.reports {
            ensure!(
                report.submitted_commit == self.submitted_commit
                    && !report.text.is_empty()
                    && report.text.len() <= 8000,
                "Report is not bounded to the submitted revision"
            );
        }
        ensure!(
            serde_json::to_vec(self)?.len() <= 32768,
            "Handoff manifest too large"
        );
        Ok(())
    }
    pub fn digest(&self) -> Result<String> {
        self.validate()?;
        Ok(crate::bundle::digest(&serde_json::to_vec(&(
            "coding-handoff-v1",
            self,
        ))?))
    }
    /// Data binding only. The caller must still authorize the original producer,
    /// read the published artifact and inspect the exact Git objects.
    pub fn require_source(&self, repository: &str, base: &str) -> Result<()> {
        self.validate()?;
        ensure!(
            self.repository == repository && self.base_commit == base,
            "Handoff repository/base differs from the expected source"
        );
        Ok(())
    }
}

/// Original import intent. A lost connection permits inspection of this exact
/// operation/destination only, never blindly repeating checkout or applying a
/// patch again. A digest alone is not permission to read someone else's artifact.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Import {
    pub operation_id: String,
    pub scope_digest: String,
    pub manifest_digest: String,
    pub directory: String,
}
impl Import {
    pub fn validate(&self) -> Result<()> {
        component(&self.operation_id, 80)?;
        hex(&self.scope_digest, 64)?;
        hex(&self.manifest_digest, 64)?;
        ensure!(
            !self.directory.contains(['\\', ':']),
            "Checkout directory must be a portable relative path"
        );
        crate::runner::relative_path(&self.directory)?;
        Ok(())
    }
    pub fn require_manifest(&self, manifest: &Manifest) -> Result<()> {
        self.validate()?;
        ensure!(
            self.manifest_digest == manifest.digest()?,
            "Import intent belongs to another immutable handoff"
        );
        Ok(())
    }
}
