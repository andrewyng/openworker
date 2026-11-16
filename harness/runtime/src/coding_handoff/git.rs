//! Bounded local Git bundle mechanics. Call only in the authorized ToolRunner
//! workspace. Provider identity, approval, artifact publication and transfer are
//! the trusted host's duties; these receipts alone grant none of that authority.
use super::{component, hex, ObjectFormat, MAX_BUNDLE_BYTES};
use crate::{
    coding::git::{command, run, stop, GitChild},
    team_workspace::{identity, Identity, Receipt as Workspace},
};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Export {
    pub operation_id: String,
    pub scope_digest: String,
    pub repository: String,
    pub directory: String,
    pub object_format: ObjectFormat,
    pub base_commit: String,
    pub submitted_commit: String,
}
impl Export {
    pub fn validate(&self) -> Result<()> {
        component(&self.operation_id, 80)?;
        hex(&self.scope_digest, 64)?;
        super::repository(&self.repository)?;
        if self.directory != "." {
            crate::runner::relative_path(&self.directory)?;
        }
        ensure!(
            !self.directory.contains(['\\', ':']),
            "Repository directory must be portable and relative"
        );
        super::oid(&self.base_commit, self.object_format)?;
        super::oid(&self.submitted_commit, self.object_format)?;
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Pin {
    pub(super) root: PathBuf,
    pub(super) root_identity: Identity,
    pub(super) repository: PathBuf,
    pub(super) repository_identity: Identity,
    pub(super) common: PathBuf,
    pub(super) common_identity: Identity,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    request: Export,
    pin: Pin,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bundle {
    pub request: Export,
    /// Relative to the authorized root, never an arbitrary host path.
    pub path: String,
    pub size: u64,
    pub sha256: String,
    pub advertised_ref: String,
}
pub(super) fn check_export_receipt(root: &Path, bundle: &Bundle) -> Result<()> {
    bundle.validate()?;
    let dir = crate::runner::path(
        root,
        &format!(".openworker/handoffs/{}", bundle.request.operation_id),
    )?;
    for name in ["request.json", "receipt.json", "source.bundle"] {
        checked(&dir.join(name))?;
    }
    let i: Intent = serde_json::from_slice(&crate::bundle::read_limited(
        &dir.join("request.json"),
        16000,
    )?)?;
    ensure!(
        i.request == bundle.request
            && i.pin.root == root
            && identity(root)? == i.pin.root_identity
            && identity(&i.pin.repository)? == i.pin.repository_identity
            && identity(&i.pin.common)? == i.pin.common_identity,
        "Original export scope changed"
    );
    checked(&i.pin.repository)?;
    checked(&i.pin.common)?;
    let saved: Bundle = serde_json::from_slice(&crate::bundle::read_limited(
        &dir.join("receipt.json"),
        16000,
    )?)?;
    ensure!(saved == *bundle, "Original export receipt differs");
    Ok(())
}
impl Bundle {
    /// Validate remote response shape before treating any path/size as a fact.
    /// Artifact byte verification and original provider authority remain required.
    pub fn validate(&self) -> Result<()> {
        self.request.validate()?;
        hex(&self.sha256, 64)?;
        ensure!(
            self.size > 0 && self.size <= MAX_BUNDLE_BYTES,
            "Invalid bundle size"
        );
        ensure!(
            self.path
                == format!(
                    ".openworker/handoffs/{}/source.bundle",
                    self.request.operation_id
                )
                && self.advertised_ref
                    == format!("refs/openworker/handoffs/{}", self.request.operation_id),
            "Bundle response differs from the original operation"
        );
        Ok(())
    }
}
fn args(args: &[&str]) -> Vec<String> {
    ["-c", "protocol.allow=never"]
        .into_iter()
        .chain(args.iter().copied())
        .map(str::to_owned)
        .collect()
}
pub(super) async fn git(root: &Path, a: &[&str]) -> Result<String> {
    run(root, &args(a)).await
}
fn text(path: &Path) -> Result<String> {
    Ok(path.to_str().context("Git paths must be UTF-8")?.into())
}
struct Scratch {
    path: PathBuf,
    identity: Identity,
}
impl Drop for Scratch {
    fn drop(&mut self) {
        if identity(&self.path).is_ok_and(|i| i == self.identity) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}
pub(super) async fn fingerprint(path: &Path) -> Result<(u64, String)> {
    let mut file = tokio::fs::File::open(path).await?;
    let mut buf = vec![0; 65536];
    let mut size = 0;
    let mut hash = Sha256::new();
    loop {
        let n = file.read(&mut buf).await?;
        if n == 0 {
            break;
        }
        size += n as u64;
        ensure!(
            size <= MAX_BUNDLE_BYTES,
            "Bundle exceeds artifact size limit"
        );
        hash.update(&buf[..n]);
    }
    ensure!(size > 0, "Empty Git bundle");
    Ok((size, format!("{:x}", hash.finalize())))
}
pub(super) fn checked(path: &Path) -> Result<()> {
    let mut p = PathBuf::new();
    for c in path.components() {
        p.push(c);
        if let Ok(m) = std::fs::symlink_metadata(&p) {
            ensure!(
                !crate::platform::linked(&m),
                "Git handoff symlinks are refused"
            );
        }
    }
    Ok(())
}
pub(super) async fn pin(
    root: &Path,
    request: &Export,
    workspace: Option<&Workspace>,
) -> Result<Pin> {
    let root = root.canonicalize()?;
    checked(&root)?;
    let repository = if request.directory == "." {
        root.clone()
    } else {
        crate::runner::path(&root, &request.directory)?.canonicalize()?
    };
    checked(&repository)?;
    let project = if let Some(w) = workspace {
        w.validate_scope(&w.project_root, &root)?;
        w.verify()?;
        w.project_root.as_path()
    } else {
        root.as_path()
    };
    let common = crate::team_workspace::repository(&repository, project).await?;
    ensure!(
        common.starts_with(&repository)
            || workspace
                .is_some_and(|w| w.path == repository && w.git_dir.as_ref() == Some(&common)),
        "Unregistered external Git metadata"
    );
    for child in [
        "objects",
        "objects/info",
        "objects/pack",
        "refs",
        "refs/openworker",
        "logs",
        "logs/refs",
    ] {
        checked(&common.join(child))?;
    }
    ensure!(
        !common.join("objects/info/http-alternates").exists() && !common.join("shallow").exists(),
        "Shallow/external object stores need an explicit complete source snapshot"
    );
    let config = git(
        &repository,
        &["config", "--local", "--no-includes", "--null", "--list"],
    )
    .await?;
    for entry in config.split('\0') {
        let key = entry.split('\n').next().unwrap_or("").to_ascii_lowercase();
        ensure!(
            key != "extensions.partialclone" && !key.ends_with(".promisor"),
            "Partial/promisor repositories are not complete handoff sources"
        );
    }
    ensure!(
        git(
            &repository,
            &["for-each-ref", "--format=%(refname)", "refs/replace/"]
        )
        .await?
        .trim()
        .is_empty(),
        "Replacement objects are not valid handoff sources"
    );
    ensure!(
        git(&repository, &["rev-parse", "--show-object-format"])
            .await?
            .trim()
            == request.object_format.git_name(),
        "Repository object format differs from handoff"
    );
    for sha in [&request.base_commit, &request.submitted_commit] {
        ensure!(
            git(&repository, &["cat-file", "-t", sha]).await?.trim() == "commit",
            "Handoff requires commit objects"
        );
    }
    git(
        &repository,
        &[
            "merge-base",
            "--is-ancestor",
            &request.base_commit,
            &request.submitted_commit,
        ],
    )
    .await?;
    Ok(Pin {
        root_identity: identity(&root)?,
        repository_identity: identity(&repository)?,
        common_identity: identity(&common)?,
        root,
        repository,
        common,
    })
}
pub(super) fn write_new<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    file.write_all(&serde_json::to_vec(value)?)?;
    file.sync_all()?;
    Ok(())
}
async fn stream(root: &Path, reference: &str, path: &Path) -> Result<()> {
    ensure!(
        cfg!(unix),
        "Bounded Git handoff process cleanup is qualified on Unix only"
    );
    let mut child = GitChild(
        command(root, &args(&["bundle", "create", "-", reference]))?
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()?,
    );
    let mut stdout = child.stdout.take().context("No Git bundle output")?;
    let mut stderr = child.stderr.take().unwrap().take(4001);
    let mut err = vec![];
    let mut file = tokio::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .await?;
    let copy = async {
        let mut buf = vec![0; 65536];
        let mut size = 0;
        loop {
            let n = stdout.read(&mut buf).await?;
            if n == 0 {
                break;
            }
            size += n as u64;
            ensure!(
                size <= MAX_BUNDLE_BYTES,
                "Git bundle exceeds artifact limit"
            );
            file.write_all(&buf[..n]).await?;
        }
        file.sync_all().await?;
        Ok::<_, anyhow::Error>(())
    };
    let result = tokio::time::timeout(Duration::from_secs(60), async {
        let (_, _, status) = tokio::try_join!(
            copy,
            async {
                stderr
                    .read_to_end(&mut err)
                    .await
                    .map_err(anyhow::Error::from)
            },
            async { child.wait().await.map_err(anyhow::Error::from) }
        )?;
        ensure!(
            status.success() && err.len() <= 4000,
            "Git bundle export did not complete"
        );
        Ok::<_, anyhow::Error>(())
    })
    .await;
    if !matches!(result, Ok(Ok(()))) {
        stop(&mut child).await;
    }
    result.context("Git bundle export response unknown after timeout")?
}
/// Import into a fresh private bare validation repository. Never execute hooks,
/// checkout project files or contact a remote. Reject extra refs/unreachable data.
pub async fn validate(path: &Path, request: &Export, reference: &str) -> Result<()> {
    request.validate()?;
    ensure!(
        cfg!(unix),
        "Git handoff process cleanup is qualified on Unix only"
    );
    let path = path.canonicalize()?;
    checked(&path)?;
    fingerprint(&path).await?;
    let parent = path.parent().context("Bundle has no parent")?;
    let scratch = parent.join(crate::id("verify"));
    std::fs::create_dir(&scratch)?;
    let _scratch = Scratch {
        identity: identity(&scratch)?,
        path: scratch.clone(),
    };
    let result: Result<()> = async {
        let format = format!("--object-format={}", request.object_format.git_name());
        git(&scratch, &["init", "--bare", "--quiet", &format, "."]).await?;
        let heads = git(&scratch, &["bundle", "list-heads", &text(&path)?]).await?;
        ensure!(
            heads.trim() == format!("{} {}", request.submitted_commit, reference),
            "Bundle advertises unexpected refs or revision"
        );
        git(
            &scratch,
            &[
                "-c",
                "fetch.fsckObjects=true",
                "-c",
                "transfer.fsckObjects=true",
                "bundle",
                "unbundle",
                &text(&path)?,
            ],
        )
        .await?;
        git(
            &scratch,
            &[
                "update-ref",
                "refs/heads/verify",
                &request.submitted_commit,
                &"0".repeat(request.object_format.oid_length()),
            ],
        )
        .await?;
        git(&scratch, &["symbolic-ref", "HEAD", "refs/heads/verify"]).await?;
        let check = git(
            &scratch,
            &[
                "fsck",
                "--full",
                "--strict",
                "--no-reflogs",
                "--unreachable",
                "--no-progress",
            ],
        )
        .await?;
        ensure!(
            check.trim().is_empty(),
            "Bundle contains unrelated/unreachable objects"
        );
        for sha in [&request.base_commit, &request.submitted_commit] {
            ensure!(
                git(&scratch, &["cat-file", "-t", sha]).await?.trim() == "commit",
                "Missing pinned commit"
            );
        }
        git(
            &scratch,
            &[
                "merge-base",
                "--is-ancestor",
                &request.base_commit,
                &request.submitted_commit,
            ],
        )
        .await?;
        Ok(())
    }
    .await;
    // Only the UUID directory created above; no repository/model-selected path.
    result
}
/// First dispatch records an immutable marker before updating its operation-owned
/// ref. Re-entry only inspects the original complete bundle; no export is replayed.
pub async fn export(
    root: &Path,
    request: Export,
    inspect_only: bool,
    workspace: Option<&Workspace>,
) -> Result<Bundle> {
    request.validate()?;
    ensure!(
        cfg!(unix),
        "Git handoff process cleanup is qualified on Unix only"
    );
    let pin = pin(root, &request, workspace).await?;
    let relative = format!(".openworker/handoffs/{}", request.operation_id);
    let directory = crate::runner::path(&pin.root, &relative)?;
    if !directory.exists() {
        ensure!(
            !inspect_only,
            "Original handoff marker is absent; outcome unknown"
        );
        std::fs::create_dir_all(&directory)?;
    }
    checked(&directory)?;
    let intent = Intent {
        request: request.clone(),
        pin: pin.clone(),
    };
    let marker = directory.join("request.json");
    checked(&marker)?;
    ensure!(
        !inspect_only || marker.is_file(),
        "Original handoff marker is absent; inspection cannot create one"
    );
    let fresh = match write_new(&marker, &intent) {
        Ok(()) => true,
        Err(e)
            if e.downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.kind() == std::io::ErrorKind::AlreadyExists) =>
        {
            false
        }
        Err(e) => return Err(e),
    };
    let saved: Intent = serde_json::from_slice(&crate::bundle::read_limited(&marker, 16000)?)?;
    ensure!(saved == intent, "Original handoff scope or request changed");
    let reference = format!("refs/openworker/handoffs/{}", request.operation_id);
    let bundle_path = directory.join("source.bundle");
    if fresh {
        ensure!(!inspect_only, "Inspection cannot create an export marker");
        if pin.repository == pin.root {
            crate::team_workspace::exclude_bookkeeping(&pin.common)?;
        }
        git(
            &pin.repository,
            &[
                "update-ref",
                "--no-deref",
                &reference,
                &request.submitted_commit,
                &"0".repeat(request.object_format.oid_length()),
            ],
        )
        .await?;
        let partial = directory.join("source.part");
        stream(&pin.repository, &reference, &partial).await?;
        // Atomic create-if-absent; never overwrite a previous operation's output.
        std::fs::hard_link(&partial, &bundle_path)?;
        std::fs::remove_file(partial)?;
    }
    ensure!(
        bundle_path.is_file(),
        "Original complete bundle is absent; inspect before another mutation"
    );
    checked(&bundle_path)?;
    ensure!(
        git(&pin.repository, &["rev-parse", "--verify", &reference])
            .await?
            .trim()
            == request.submitted_commit,
        "Original handoff ref changed"
    );
    let before = fingerprint(&bundle_path).await?;
    validate(&bundle_path, &request, &reference).await?;
    ensure!(
        fingerprint(&bundle_path).await? == before,
        "Bundle changed during validation"
    );
    let receipt = Bundle {
        request,
        path: format!("{relative}/source.bundle"),
        size: before.0,
        sha256: before.1,
        advertised_ref: reference,
    };
    receipt.validate()?;
    let proof = directory.join("receipt.json");
    checked(&proof)?;
    match write_new(&proof, &receipt) {
        Ok(()) => {}
        Err(e)
            if e.downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.kind() == std::io::ErrorKind::AlreadyExists) =>
        {
            let old: Bundle = serde_json::from_slice(&crate::bundle::read_limited(&proof, 16000)?)?;
            ensure!(old == receipt, "Original bundle receipt changed");
        }
        Err(e) => return Err(e),
    }
    Ok(receipt)
}
