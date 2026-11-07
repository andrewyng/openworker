//! Private, bounded ToolRunner transport. These methods are NOT model tools.
//! The trusted controller must authorize the original source, artifact and
//! destination before dispatch. A valid transfer label grants no such authority.
use super::{
    git::{self, Bundle},
    Import, Manifest,
};
use crate::team_workspace::{identity, Identity};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    io::Write,
    path::{Path, PathBuf},
};
use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};

/// Keeps base64 chunks and metadata below the private 64 KiB JSON frame limit.
pub const CHUNK: usize = 32 * 1024;
pub const MAX_CHECKOUT_BYTES: u64 = 128 * 1024 * 1024;
pub const MAX_TREE_ENTRIES: usize = 10000;
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Transfer {
    pub request: Import,
    pub manifest: Manifest,
    pub bundle: Bundle,
}
impl Transfer {
    pub fn validate(&self) -> Result<()> {
        self.request.require_manifest(&self.manifest)?;
        self.bundle.validate()?;
        let e = &self.bundle.request;
        let m = &self.manifest;
        ensure!(
            e.repository == m.repository
                && e.object_format == m.object_format
                && e.base_commit == m.base_commit
                && e.submitted_commit == m.submitted_commit
                && self.bundle.size == m.artifact.size
                && self.bundle.sha256 == m.artifact.sha256,
            "Transfer differs from the immutable handoff"
        );
        ensure!(
            serde_json::to_vec(self)?.len() <= 48000,
            "Transfer header too large"
        );
        Ok(())
    }
    pub fn handle(&self) -> Handle {
        Handle {
            operation_id: self.request.operation_id.clone(),
            scope_digest: self.request.scope_digest.clone(),
        }
    }
    pub fn digest(&self) -> Result<String> {
        self.validate()?;
        Ok(crate::bundle::digest(&serde_json::to_vec(&(
            "coding-transfer-v1",
            self,
        ))?))
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Handle {
    pub operation_id: String,
    pub scope_digest: String,
}
impl Handle {
    fn validate(&self) -> Result<()> {
        super::component(&self.operation_id, 80)?;
        super::hex(&self.scope_digest, 64)
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    transfer: Transfer,
    root: PathBuf,
    root_identity: Identity,
    spool_identity: Identity,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub transfer_digest: String,
    pub path: PathBuf,
    pub identity: Identity,
    pub git_identity: Identity,
    pub commit: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Status {
    pub handle: Handle,
    pub received: u64,
    pub size: u64,
    pub checkout_dispatched: bool,
    pub receipt: Option<Receipt>,
}
fn read<T: serde::de::DeserializeOwned>(path: &Path, limit: usize) -> Result<T> {
    git::checked(path)?;
    Ok(serde_json::from_slice(&crate::bundle::read_limited(
        path, limit,
    )?)?)
}
fn root(root: &Path) -> Result<PathBuf> {
    let root = root.canonicalize()?;
    git::checked(&root)?;
    Ok(root)
}
fn spool(root: &Path, h: &Handle) -> Result<PathBuf> {
    h.validate()?;
    crate::runner::path(root, &format!(".openworker/imports/{}", h.operation_id))
}
fn load(root_path: &Path, h: &Handle) -> Result<(PathBuf, Intent)> {
    let root = root(root_path)?;
    let dir = spool(&root, h)?;
    git::checked(&dir)?;
    let i: Intent = read(&dir.join("request.json"), 56000)?;
    i.transfer.validate()?;
    ensure!(
        i.transfer.handle() == *h
            && i.root == root
            && i.root_identity == identity(&root)?
            && i.spool_identity == identity(&dir)?,
        "Original import scope or marker changed"
    );
    Ok((dir, i))
}
fn chunk_path(dir: &Path, offset: u64) -> PathBuf {
    dir.join(format!("chunk-{:08}", offset / CHUNK as u64))
}
fn chunk_length(size: u64, offset: u64) -> Result<usize> {
    ensure!(
        offset < size && offset.is_multiple_of(CHUNK as u64),
        "Invalid chunk offset"
    );
    Ok((size - offset).min(CHUNK as u64) as usize)
}
fn received(dir: &Path, size: u64) -> Result<u64> {
    let mut offset = 0;
    while offset < size {
        let path = chunk_path(dir, offset);
        git::checked(&path)?;
        match std::fs::symlink_metadata(&path) {
            Ok(m) => {
                ensure!(
                    m.is_file() && m.len() == chunk_length(size, offset)? as u64,
                    "Original chunk changed"
                );
                offset += m.len();
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => break,
            Err(e) => return Err(e.into()),
        }
    }
    Ok(offset)
}
async fn verify_receipt(i: &Intent, receipt: &Receipt) -> Result<()> {
    ensure!(
        receipt.transfer_digest == i.transfer.digest()?
            && receipt.commit == i.transfer.manifest.submitted_commit
            && receipt.path == i.root.join(&i.transfer.request.directory),
        "Checkout receipt differs from original intent"
    );
    git::checked(&receipt.path)?;
    let metadata = receipt.path.join(".git");
    git::checked(&metadata)?;
    ensure!(
        identity(&receipt.path)? == receipt.identity
            && identity(&metadata)? == receipt.git_identity,
        "Checkout or Git metadata was replaced; inspection cannot adopt it"
    );
    let common = crate::team_workspace::repository(&receipt.path, &receipt.path).await?;
    ensure!(
        common == metadata,
        "Independent checkout acquired external Git metadata"
    );
    ensure!(
        git::git(&receipt.path, &["rev-parse", "HEAD"])
            .await?
            .trim()
            == receipt.commit,
        "Checkout changed before import acknowledgement; do not reset or replay"
    );
    Ok(())
}
async fn status(dir: &Path, i: &Intent) -> Result<Status> {
    let receipt = dir.join("receipt.json");
    git::checked(&receipt)?;
    let receipt = if receipt.exists() {
        let r: Receipt = read(&receipt, 16000)?;
        verify_receipt(i, &r).await?;
        Some(r)
    } else {
        None
    };
    git::checked(&dir.join("checkout.json"))?;
    Ok(Status {
        handle: i.transfer.handle(),
        received: received(dir, i.transfer.bundle.size)?,
        size: i.transfer.bundle.size,
        checkout_dispatched: dir.join("checkout.json").exists(),
        receipt,
    })
}
/// Record original import identity before accepting bytes. Recovery only reads
/// the same marker. An absent marker is never recreated by inspection.
pub async fn prepare(root_path: &Path, transfer: Transfer, inspect_only: bool) -> Result<Status> {
    transfer.validate()?;
    ensure!(
        cfg!(unix),
        "Independent Git handoff is qualified on Unix only"
    );
    let root = root(root_path)?;
    let dir = spool(&root, &transfer.handle())?;
    if !dir.exists() {
        ensure!(
            !inspect_only,
            "Original import marker absent; outcome unknown"
        );
        std::fs::create_dir_all(&dir)?;
    }
    git::checked(&dir)?;
    let intent = Intent {
        transfer: transfer.clone(),
        root: root.clone(),
        root_identity: identity(&root)?,
        spool_identity: identity(&dir)?,
    };
    let marker = dir.join("request.json");
    git::checked(&marker)?;
    if !marker.exists() {
        ensure!(!inspect_only, "Inspection cannot create an import marker");
        git::write_new(&marker, &intent)?;
    }
    let (_, saved) = load(&root, &transfer.handle())?;
    ensure!(saved == intent, "Original import request changed");
    status(&dir, &saved).await
}
pub async fn inspect(root: &Path, h: &Handle) -> Result<Status> {
    let (dir, i) = load(root, h)?;
    status(&dir, &i).await
}
/// Immutable chunk publication is safe to repeat only with the same exact
/// offset and bytes. It never authorizes retrying checkout or a tool action.
pub async fn write_chunk(root: &Path, h: &Handle, offset: u64, bytes: &[u8]) -> Result<Status> {
    let (dir, i) = load(root, h)?;
    ensure!(
        bytes.len() == chunk_length(i.transfer.bundle.size, offset)?,
        "Incorrect bounded chunk length"
    );
    ensure!(
        offset <= received(&dir, i.transfer.bundle.size)?,
        "Chunk would leave a transfer hole"
    );
    let path = chunk_path(&dir, offset);
    git::checked(&path)?;
    if path.exists() {
        ensure!(
            crate::bundle::read_limited(&path, CHUNK)? == bytes,
            "Original chunk differs"
        );
    } else {
        ensure!(
            !dir.join("checkout.json").exists(),
            "Checkout already dispatched"
        );
        // A private temporary name avoids exposing a truncated chunk after a
        // killed writer. Hard-link publication never overwrites another writer.
        let tmp = dir.join(crate::id("chunk"));
        let result = (|| -> Result<()> {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&tmp)?;
            file.write_all(bytes)?;
            file.sync_all()?;
            match std::fs::hard_link(&tmp, &path) {
                Ok(()) => Ok(()),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    git::checked(&path)?;
                    ensure!(
                        crate::bundle::read_limited(&path, CHUNK)? == bytes,
                        "Concurrent chunk differs"
                    );
                    Ok(())
                }
                Err(e) => Err(e.into()),
            }
        })();
        let _ = std::fs::remove_file(&tmp);
        result?;
    }
    status(&dir, &i).await
}
/// Read a previously completed export; never export automatically on read.
/// The artifact backend verifies the digest of all bytes actually transferred.
pub async fn read_chunk(root_path: &Path, bundle: &Bundle, offset: u64) -> Result<Vec<u8>> {
    bundle.validate()?;
    let root = root(root_path)?;
    git::check_export_receipt(&root, bundle)?;
    let path = crate::runner::path(&root, &bundle.path)?;
    git::checked(&path)?;
    let mut file = tokio::fs::File::open(&path).await?;
    ensure!(
        file.metadata().await?.len() == bundle.size,
        "Original export size changed"
    );
    let length = chunk_length(bundle.size, offset)?;
    file.seek(std::io::SeekFrom::Start(offset)).await?;
    let mut bytes = vec![0; length];
    file.read_exact(&mut bytes).await?;
    Ok(bytes)
}
/// Checkout is a non-repeatable mutation. Commit the dispatch marker before
/// mkdir/Git. A lost reply means inspect the original receipt; absent receipt is
/// unknown and must reach the agent, never automatically rerun/reset/overwrite.
pub async fn finish(root: &Path, h: &Handle, inspect_only: bool) -> Result<Receipt> {
    let (dir, i) = load(root, h)?;
    let current = status(&dir, &i).await?;
    if let Some(r) = current.receipt {
        return Ok(r);
    }
    ensure!(
        !inspect_only && !current.checkout_dispatched,
        "Original checkout outcome unknown; inspect before any new mutation"
    );
    ensure!(current.received == current.size, "Import bytes incomplete");
    let path = dir.join("source.bundle");
    git::checked(&path)?;
    ensure!(!path.exists(), "Original assembly outcome unknown");
    let mut file = tokio::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .await?;
    let mut offset = 0;
    while offset < current.size {
        let bytes = crate::bundle::read_limited(&chunk_path(&dir, offset), CHUNK)?;
        file.write_all(&bytes).await?;
        offset += bytes.len() as u64;
    }
    file.sync_all().await?;
    drop(file);
    ensure!(
        git::fingerprint(&path).await?
            == (i.transfer.bundle.size, i.transfer.bundle.sha256.clone()),
        "Imported bytes differ from the published artifact"
    );
    git::validate(
        &path,
        &i.transfer.bundle.request,
        &i.transfer.bundle.advertised_ref,
    )
    .await?;
    ensure!(
        git::fingerprint(&path).await?
            == (i.transfer.bundle.size, i.transfer.bundle.sha256.clone()),
        "Imported bytes changed during validation"
    );
    let destination = crate::runner::path(&i.root, &i.transfer.request.directory)?;
    git::checked(&destination)?;
    ensure!(
        !destination.exists(),
        "Refusing to replace an existing checkout"
    );
    std::fs::create_dir_all(destination.parent().context("Missing checkout parent")?)?;
    git::checked(&destination)?;
    git::write_new(
        &dir.join("checkout.json"),
        &(i.transfer.digest()?, &destination),
    )?;
    std::fs::create_dir(&destination)?;
    let destination_identity = identity(&destination)?;
    let object_format = format!(
        "--object-format={}",
        i.transfer.manifest.object_format.git_name()
    );
    git::git(
        &destination,
        &["init", "--quiet", "--template=", &object_format, "."],
    )
    .await?;
    let git_identity = identity(&destination.join(".git"))?;
    git::write_new(
        &dir.join("destination.json"),
        &(&destination_identity, &git_identity),
    )?;
    let bundle_path = path.to_str().context("Non UTF-8 bundle path")?;
    git::git(
        &destination,
        &[
            "-c",
            "fetch.fsckObjects=true",
            "-c",
            "transfer.fsckObjects=true",
            "bundle",
            "unbundle",
            bundle_path,
        ],
    )
    .await?;
    check_tree(&destination, &i.transfer.manifest.submitted_commit).await?;
    // Fresh local metadata, no filters/helpers or remote/submodule checkout.
    // An ordinary deterministic local branch lets an application use the same
    // import for its lead source and explicit expected-head integration. A
    // detached lead checkout cannot participate in that CAS protocol.
    git::git(
        &destination,
        &[
            "-c",
            "core.protectNTFS=true",
            "-c",
            "core.protectHFS=true",
            "checkout",
            "--quiet",
            "-b",
            "openworker/import",
            &i.transfer.manifest.submitted_commit,
        ],
    )
    .await?;
    let receipt = Receipt {
        transfer_digest: i.transfer.digest()?,
        path: destination,
        identity: destination_identity,
        git_identity,
        commit: i.transfer.manifest.submitted_commit.clone(),
    };
    verify_receipt(&i, &receipt).await?;
    git::write_new(&dir.join("receipt.json"), &receipt)?;
    Ok(receipt)
}
async fn check_tree(root: &Path, commit: &str) -> Result<()> {
    let args = ["ls-tree", "--long", "-r", "-z", commit].map(str::to_owned);
    let listing = crate::coding::git::run_bounded(root, &args, 1024 * 1024).await?;
    let mut total = 0u64;
    let mut count = 0;
    for entry in listing.split('\0').filter(|s| !s.is_empty()) {
        count += 1;
        ensure!(count <= MAX_TREE_ENTRIES, "Checkout has too many files");
        let fields: Vec<_> = entry
            .split_once('\t')
            .context("Invalid Git tree entry")?
            .0
            .split_ascii_whitespace()
            .collect();
        ensure!(
            fields.len() == 4 && fields[1] == "blob",
            "Submodule content needs an explicit complete source snapshot"
        );
        let size: u64 = fields[3].parse()?;
        total = total.checked_add(size).context("Checkout size overflow")?;
        ensure!(
            total <= MAX_CHECKOUT_BYTES,
            "Expanded checkout exceeds 128 MiB limit"
        );
    }
    Ok(())
}

/// Convert only the original acknowledged import into the ordinary workspace
/// receipt consumed by admission/tool launch. Later builder commits are allowed;
/// replacing its Git metadata or switching to linked metadata is not.
pub async fn workspace_receipt(
    root: &Path,
    handle: &Handle,
    request: crate::team_workspace::Request,
) -> Result<crate::team_workspace::Receipt> {
    let (_, i) = load(root, handle)?;
    request.validate()?;
    ensure!(
        request.operation_id == i.transfer.request.operation_id
            && request.scope_digest == i.transfer.request.scope_digest
            && request.selection
                == (crate::team_workspace::Selection::Directory {
                    path: i.transfer.request.directory.clone()
                }),
        "Workspace admission intent differs from original import"
    );
    let imported = finish(root, handle, true).await?;
    let receipt = crate::team_workspace::Receipt {
        request,
        path: imported.path,
        project_root: i.root,
        identity: imported.identity,
        repository: None,
        git_dir: None,
        git_identity: None,
        worktree_git_dir: None,
        worktree_git_identity: None,
        pointer_digest: None,
        branch: None,
        initial_commit: Some(imported.commit),
        independent_git: Some(imported.git_identity),
    };
    receipt.verify()?;
    Ok(receipt)
}

/// Only called by the trusted workspace-stdio channel. Discovery never lists
/// these methods as agent tools, and params cannot select a host path/URL/key.
pub async fn dispatch(
    root: &Path,
    method: &str,
    params: serde_json::Value,
) -> Result<serde_json::Value> {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use serde_json::{from_value, json, to_value};
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Export {
        request: git::Export,
        #[serde(default)]
        workspace: Option<crate::team_workspace::Receipt>,
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Read {
        bundle: Bundle,
        offset: u64,
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Write {
        handle: Handle,
        offset: u64,
        data: String,
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Workspace {
        handle: Handle,
        request: crate::team_workspace::Request,
    }
    Ok(match method {
        "handoff/integrate" | "handoff/inspect_integration" => to_value(
            super::integrate::rpc(
                root,
                from_value(params)?,
                method == "handoff/inspect_integration",
            )
            .await?,
        )?,
        "handoff/export" | "handoff/inspect_export" => {
            let p: Export = from_value(params)?;
            to_value(
                git::export(
                    root,
                    p.request,
                    method == "handoff/inspect_export",
                    p.workspace.as_ref(),
                )
                .await?,
            )?
        }
        "handoff/read" => {
            let p: Read = from_value(params)?;
            let bytes = read_chunk(root, &p.bundle, p.offset).await?;
            json!({"offset":p.offset,"data":STANDARD.encode(bytes)})
        }
        "handoff/prepare" => to_value(prepare(root, from_value(params)?, false).await?)?,
        "handoff/inspect" => to_value(inspect(root, &from_value(params)?).await?)?,
        "handoff/write" => {
            let p: Write = from_value(params)?;
            ensure!(
                p.data.len() <= CHUNK.div_ceil(3) * 4,
                "Transfer frame too large"
            );
            to_value(write_chunk(root, &p.handle, p.offset, &STANDARD.decode(p.data)?).await?)?
        }
        "handoff/finish" | "handoff/inspect_checkout" => to_value(
            finish(
                root,
                &from_value(params)?,
                method == "handoff/inspect_checkout",
            )
            .await?,
        )?,
        "handoff/workspace" => {
            let p: Workspace = from_value(params)?;
            to_value(workspace_receipt(root, &p.handle, p.request).await?)?
        }
        _ => anyhow::bail!("Unknown private handoff method"),
    })
}
