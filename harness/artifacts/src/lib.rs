//! Immutable, bounded-memory artifacts. No model-selected paths/endpoints and
//! no automatic workspace upload. Metadata authority lives in the host catalog.
use anyhow::{ensure, Result};
use bytes::Bytes;
use futures_util::{Stream, StreamExt};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{pin::Pin, sync::Arc, time::Duration};
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio_util::sync::CancellationToken;
#[cfg(feature = "azure")]
pub mod azure;
pub mod filesystem;
#[cfg(feature = "s3")]
pub mod s3;
pub const MAX_SIZE: u64 = 64 * 1024 * 1024;
pub const CHUNK: usize = 1024 * 1024;
pub type ByteStream = Pin<Box<dyn Stream<Item = Result<Bytes>> + Send>>;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    Invalid,
    NotFound,
    Conflict,
    Unavailable,
    Cancelled,
    TooLarge,
    NotReady,
    Unconfigured,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Invalid => "artifact_invalid",
            Self::NotFound => "artifact_not_found",
            Self::Conflict => "artifact_conflict",
            Self::Unavailable => "artifact_storage_unavailable",
            Self::Cancelled => "artifact_cancelled",
            Self::TooLarge => "artifact_limit",
            Self::NotReady => "artifact_not_ready",
            Self::Unconfigured => "artifact_store_unconfigured",
        })
    }
}
impl std::error::Error for Error {}
pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn component(s: &str, max: usize) -> Result<()> {
    ensure!(
        !s.is_empty()
            && s.len() <= max
            && s.bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-'),
        Error::Invalid
    );
    Ok(())
}
pub fn checksum(s: &str) -> Result<()> {
    ensure!(
        s.len() == 64
            && s.bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
        Error::Invalid
    );
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Expected {
    pub size: u64,
    pub sha256: String,
}
impl Expected {
    pub fn validate(&self) -> Result<()> {
        ensure!(self.size <= MAX_SIZE, Error::TooLarge);
        checksum(&self.sha256)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Proof {
    pub expected: Expected,
    pub manifest_sha256: String,
}
/// Source setup has its own provenance; it must never fabricate an LLM turn.
/// This metadata is not permission to publish: the catalog verifies the original
/// trusted setup intent. Public turn-artifact APIs cannot select setup origin.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Origin {
    #[default]
    Turn,
    TeamSetup {
        team_id: String,
        setup_id: String,
    },
}
impl Origin {
    pub fn is_turn(&self) -> bool {
        matches!(self, Self::Turn)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Registration {
    #[serde(default, skip_serializing_if = "Origin::is_turn")]
    pub origin: Origin,
    pub request_id: String,
    pub session_id: String,
    pub turn_id: String,
    pub name: String,
    pub content_type: String,
    pub expected: Expected,
}
impl Registration {
    pub fn validate(&self) -> Result<()> {
        component(&self.request_id, 100)?;
        component(&self.session_id, 80)?;
        match &self.origin {
            Origin::Turn => component(&self.turn_id, 80)?,
            Origin::TeamSetup { team_id, setup_id } => {
                ensure!(self.turn_id.is_empty(), Error::Invalid);
                component(team_id, 80)?;
                component(setup_id, 80)?;
            }
        }
        self.expected.validate()?;
        ensure!(
            !self.name.is_empty()
                && self.name.len() <= 160
                && !self
                    .name
                    .chars()
                    .any(|c| c.is_control() || matches!(c, '/' | '\\' | ':')),
            Error::Invalid
        );
        ensure!(
            [
                "application/octet-stream",
                "application/x-git-bundle",
                "text/plain",
                "text/markdown",
                "text/html",
                "text/css",
                "application/javascript",
                "application/json",
                "image/png",
                "image/jpeg",
                "application/pdf",
                "application/zip"
            ]
            .contains(&self.content_type.as_str()),
            Error::Invalid
        );
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub deployment: String,
    pub identity: String,
}
impl Binding {
    pub fn validate(&self) -> Result<()> {
        component(&self.deployment, 64)?;
        checksum(&self.identity)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pending,
    Ready,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Artifact {
    pub id: String,
    pub registration: Registration,
    pub binding: Binding,
    pub key: String,
    pub status: Status,
    pub proof: Option<Proof>,
}
impl Artifact {
    pub fn validate(&self) -> Result<()> {
        component(&self.id, 40)?;
        self.registration.validate()?;
        self.binding.validate()?;
        ensure!(
            self.key
                == format!(
                    "{}/{}/{}",
                    self.binding.deployment, self.registration.session_id, self.id
                ),
            Error::Conflict
        );
        match (&self.status, &self.proof) {
            (Status::Pending, None) => {}
            (Status::Ready, Some(p)) => {
                ensure!(p.expected == self.registration.expected, Error::Conflict);
                checksum(&p.manifest_sha256)?;
            }
            _ => return Err(Error::Conflict.into()),
        };
        Ok(())
    }
}
/// Private immutable object primitives. Backend must implement real atomic
/// create-if-absent, not check-then-overwrite. Bytes are bounded by CHUNK/manifest.
#[async_trait::async_trait]
pub trait Backend: Send + Sync {
    async fn create(&self, key: &str, bytes: Bytes) -> Result<()>;
    async fn read(&self, key: &str, limit: usize) -> Result<Bytes>;
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Part {
    sha256: String,
    size: usize,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    version: u8,
    expected: Expected,
    parts: Vec<Part>,
}
#[derive(Clone)]
pub struct Store {
    backend: Arc<dyn Backend>,
    binding: Binding,
}
impl Store {
    pub fn new(backend: Arc<dyn Backend>, binding: Binding) -> Result<Self> {
        binding.validate()?;
        Ok(Self { backend, binding })
    }
    pub fn binding(&self) -> &Binding {
        &self.binding
    }
    fn validate(&self, a: &Artifact) -> Result<()> {
        a.validate()?;
        ensure!(a.binding == self.binding, Error::Conflict);
        Ok(())
    }
    async fn read_object(
        &self,
        key: &str,
        limit: usize,
        cancel: &CancellationToken,
    ) -> Result<Bytes> {
        tokio::select! {biased;_=cancel.cancelled()=>Err(Error::Cancelled.into()),r=tokio::time::timeout(Duration::from_secs(20),self.backend.read(key,limit))=>r.map_err(|_|Error::Unavailable)?}
    }
    async fn create(&self, key: &str, bytes: Bytes, cancel: &CancellationToken) -> Result<()> {
        let result = tokio::select! {biased;_=cancel.cancelled()=>return Err(Error::Cancelled.into()),r=tokio::time::timeout(Duration::from_secs(20),self.backend.create(key,bytes.clone()))=>r.map_err(|_|Error::Unavailable)?};
        match result {
            Ok(()) => Ok(()),
            Err(e) if e.downcast_ref::<Error>() == Some(&Error::Conflict) => {
                let old = self.read_object(key, bytes.len(), cancel).await?;
                ensure!(old == bytes, Error::Conflict);
                Ok(())
            }
            Err(e) => Err(e),
        }
    }
    async fn manifest(
        &self,
        a: &Artifact,
        cancel: &CancellationToken,
    ) -> Result<(Manifest, Proof)> {
        self.validate(a)?;
        let bytes = self
            .read_object(&format!("{}/manifest", a.key), 16384, cancel)
            .await?;
        let manifest: Manifest = serde_json::from_slice(&bytes).map_err(|_| Error::Conflict)?;
        ensure!(
            manifest.version == 1
                && manifest.expected == a.registration.expected
                && manifest.parts.len() <= 64,
            Error::Conflict
        );
        let mut size = 0u64;
        for (n, p) in manifest.parts.iter().enumerate() {
            checksum(&p.sha256)?;
            ensure!(
                p.size > 0 && p.size <= CHUNK && (n + 1 == manifest.parts.len() || p.size == CHUNK),
                Error::Conflict
            );
            size += p.size as u64;
        }
        ensure!(size == manifest.expected.size, Error::Conflict);
        let proof = Proof {
            expected: manifest.expected.clone(),
            manifest_sha256: hash(&bytes),
        };
        if let Some(saved) = &a.proof {
            ensure!(*saved == proof, Error::Conflict);
        }
        Ok((manifest, proof))
    }
    pub async fn head(&self, a: &Artifact, cancel: &CancellationToken) -> Result<Proof> {
        Ok(self.manifest(a, cancel).await?.1)
    }
    /// Explicit byte publication. Resubmission reconciles immutable chunks;
    /// conflicting bytes never overwrite. Whole input is never buffered.
    pub async fn put<R: AsyncRead + Unpin + Send>(
        &self,
        a: &Artifact,
        mut input: R,
        cancel: &CancellationToken,
    ) -> Result<Proof> {
        self.validate(a)?;
        let expected = &a.registration.expected;
        let mut parts = vec![];
        let mut sha = Sha256::new();
        let mut total = 0u64;
        loop {
            let mut bytes = vec![0; CHUNK.min((expected.size.saturating_sub(total) + 1) as usize)];
            let mut used = 0;
            while used < bytes.len() {
                let n = tokio::select! {biased;_=cancel.cancelled()=>return Err(Error::Cancelled.into()),r=tokio::time::timeout(Duration::from_secs(20),input.read(&mut bytes[used..]))=>r.map_err(|_|Error::Unavailable)?.map_err(|_|Error::Unavailable)?};
                if n == 0 {
                    break;
                }
                used += n;
            }
            if used == 0 {
                break;
            }
            ensure!(
                used == CHUNK.min((expected.size.saturating_sub(total)) as usize),
                Error::Conflict
            );
            bytes.truncate(used);
            total += used as u64;
            ensure!(total <= expected.size, Error::Conflict);
            sha.update(&bytes);
            let p = Part {
                sha256: hash(&bytes),
                size: used,
            };
            let key = format!("{}/part-{:02}", a.key, parts.len());
            self.create(&key, bytes.into(), cancel).await?;
            parts.push(p);
        }
        ensure!(
            total == expected.size && format!("{:x}", sha.finalize()) == expected.sha256,
            Error::Conflict
        );
        let manifest = Manifest {
            version: 1,
            expected: expected.clone(),
            parts,
        };
        let bytes = serde_json::to_vec(&manifest)?;
        self.create(&format!("{}/manifest", a.key), bytes.into(), cancel)
            .await?;
        self.verify(a, cancel).await
    }
    /// Read-only reconciliation: confirms the SAME stored ID/key by reading and
    /// hashing its bytes, never repeating a generating tool or changing metadata.
    pub async fn verify(&self, a: &Artifact, cancel: &CancellationToken) -> Result<Proof> {
        let proof = self.head(a, cancel).await?;
        let mut stream = self.get(a, &proof, cancel.clone()).await?;
        while let Some(bytes) = stream.next().await {
            bytes?;
        }
        Ok(proof)
    }
    pub async fn get(
        &self,
        a: &Artifact,
        proof: &Proof,
        cancel: CancellationToken,
    ) -> Result<ByteStream> {
        let (manifest, found) = self.manifest(a, &cancel).await?;
        ensure!(&found == proof, Error::Conflict);
        let store = self.clone();
        let key = a.key.clone();
        Ok(Box::pin(async_stream::try_stream! {
            let mut sha=Sha256::new();
            for (n,p) in manifest.parts.iter().enumerate(){
                let bytes=store.read_object(&format!("{key}/part-{n:02}"),p.size,&cancel).await?;
                if bytes.len()!=p.size||hash(&bytes)!=p.sha256 {Err(Error::Conflict)?;}sha.update(&bytes);yield bytes;
            }
            if format!("{:x}",sha.finalize())!=manifest.expected.sha256 {Err(Error::Conflict)?;}
        }))
    }
}
