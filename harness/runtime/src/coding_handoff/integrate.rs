//! Explicit fast-forward integration, with an expected-head CAS and inspection-
//! only recovery. No automatic merge retry, reset, conflict resolution or push.
use super::{
    git::{self, Export},
    oid,
};
use crate::{
    coding::git::{command, GitChild},
    team_workspace::Receipt as Workspace,
};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub export: Export,
    pub expected_head: String,
    pub received: Workspace,
}
impl Request {
    pub fn validate(&self) -> Result<()> {
        self.export.validate()?;
        oid(&self.expected_head, self.export.object_format)?;
        self.received
            .validate_scope(&self.received.project_root, &self.received.path)?;
        ensure!(
            self.received.independent_git.is_some()
                && self.received.initial_commit.as_ref() == Some(&self.export.submitted_commit),
            "Integration needs the exact acknowledged independent checkout"
        );
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub request: Request,
    pub branch: String,
    pub commit: String,
    pub tree: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum Observation {
    Applied { receipt: Receipt },
    Rejected { request: Request, reason: String },
}
#[derive(Debug)]
struct Rejected(Observation);
impl std::fmt::Display for Rejected {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Integration rejected before changing the working tree")
    }
}
impl std::error::Error for Rejected {}
fn reject(directory: &Path, request: &Request, reason: &str) -> Result<()> {
    let result = Observation::Rejected {
        request: request.clone(),
        reason: reason.into(),
    };
    git::write_new(&directory.join("rejected.json"), &result)?;
    Err(Rejected(result).into())
}
pub async fn rpc(root: &Path, request: Request, inspect_only: bool) -> Result<Observation> {
    match integrate(root, request, inspect_only).await {
        Ok(receipt) => Ok(Observation::Applied { receipt }),
        Err(e) => match e.downcast_ref::<Rejected>() {
            Some(rejection) => Ok(rejection.0.clone()),
            None => Err(e),
        },
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    request: Request,
    pin: git::Pin,
    branch: String,
}
async fn branch(repo: &Path) -> Result<String> {
    let b = git::git(repo, &["symbolic-ref", "--quiet", "HEAD"])
        .await?
        .trim()
        .to_owned();
    ensure!(
        b.starts_with("refs/heads/")
            && b.len() <= 200
            && !b.contains("..")
            && b.bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"/_-.".contains(&c)),
        "Integration requires an ordinary pinned local branch"
    );
    Ok(b)
}
async fn clean(repo: &Path) -> Result<()> {
    ensure!(
        git::git(
            repo,
            &[
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
                "--ignore-submodules=none"
            ]
        )
        .await?
        .trim()
        .is_empty(),
        "Integration needs a clean working tree; preserve current work"
    );
    Ok(())
}
async fn receipt(intent: &Intent) -> Result<Receipt> {
    let repo = &intent.pin.repository;
    ensure!(
        branch(repo).await? == intent.branch,
        "Original integration branch changed"
    );
    ensure!(
        git::git(repo, &["rev-parse", "HEAD"]).await?.trim()
            == intent.request.export.submitted_commit,
        "Integration outcome unknown or current HEAD changed; do not retry automatically"
    );
    clean(repo).await?;
    let tree = git::git(repo, &["rev-parse", "HEAD^{tree}"])
        .await?
        .trim()
        .into();
    Ok(Receipt {
        request: intent.request.clone(),
        branch: intent.branch.clone(),
        commit: intent.request.export.submitted_commit.clone(),
        tree,
    })
}
async fn apply(intent: &Intent, directory: &Path) -> Result<()> {
    let r = &intent.request;
    let repo = &intent.pin.repository;
    let received = r.received.path.clone();
    // A local operation-owned bundle avoids any configured remote or credentials.
    let bundle = git::export(
        &received,
        Export {
            directory: ".".into(),
            ..r.export.clone()
        },
        false,
        Some(&r.received),
    )
    .await?;
    let path = received.join(&bundle.path);
    let reference = format!("refs/openworker/integrations/{}", r.export.operation_id);
    git::git(
        repo,
        &[
            "-c",
            "protocol.file.allow=always",
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--no-recurse-submodules",
            path.to_str().context("Nonportable bundle path")?,
            &format!("{}:{reference}", bundle.advertised_ref),
        ],
    )
    .await?;
    // The received revision must descend from the caller's CURRENT expected
    // head, not merely the original team base. Divergence never force-resets.
    let mut ancestor = GitChild(
        command(
            repo,
            &[
                "merge-base".into(),
                "--is-ancestor".into(),
                r.expected_head.clone(),
                r.export.submitted_commit.clone(),
            ],
        )?
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()?,
    );
    let status = tokio::time::timeout(Duration::from_secs(10), ancestor.wait()).await??;
    if status.code() == Some(1) {
        reject(
            directory,
            r,
            "Current expected head diverges from this submitted revision",
        )?;
    }
    ensure!(
        status.success(),
        "Cannot inspect integration ancestry; outcome unknown"
    );
    clean(repo).await?;
    ensure!(
        branch(repo).await? == intent.branch,
        "Integration branch changed before dispatch"
    );
    ensure!(
        git::git(repo, &["rev-parse", "HEAD"]).await?.trim() == r.expected_head,
        "HEAD changed before integration; inspect original operation"
    );
    // Index/files and the branch ref are separate Git operations. CAS below
    // protects the ref; a crash or competing write between them remains an
    // UNKNOWN partial outcome. Never retry read-tree, reset or discard work.
    git::git(
        repo,
        &[
            "read-tree",
            "-u",
            "-m",
            &r.expected_head,
            &r.export.submitted_commit,
        ],
    )
    .await?;
    ensure!(
        branch(repo).await? == intent.branch,
        "Branch switched during integration; original operation requires inspection"
    );
    git::git(
        repo,
        &[
            "update-ref",
            "--no-deref",
            &intent.branch,
            &r.export.submitted_commit,
            &r.expected_head,
        ],
    )
    .await?;
    Ok(())
}
pub async fn integrate(root: &Path, request: Request, inspect_only: bool) -> Result<Receipt> {
    request.validate()?;
    ensure!(
        cfg!(unix),
        "Git integration process cleanup is qualified on Unix only"
    );
    let root = root.canonicalize()?;
    request
        .received
        .validate_scope(&root, &request.received.path)?;
    request.received.verify()?;
    let mut original = request.export.clone();
    original.submitted_commit = original.base_commit.clone();
    let pin = git::pin(&root, &original, None).await?;
    // A configured URL rewrite could redirect even a local bundle fetch. Reject
    // it independently of tool classification and network approval.
    let config = git::git(
        &pin.repository,
        &["config", "--local", "--no-includes", "--null", "--list"],
    )
    .await?;
    for entry in config.split('\0') {
        let key = entry.split('\n').next().unwrap_or("").to_ascii_lowercase();
        ensure!(
            !key.starts_with("url.")
                && !matches!(
                    key.as_str(),
                    "core.sparsecheckout" | "core.sparsecheckoutcone"
                ),
            "Integration refuses URL rewrites and sparse-checkout configuration"
        );
    }
    let intent = Intent {
        branch: branch(&pin.repository).await?,
        pin,
        request,
    };
    let dir = crate::runner::path(
        &root,
        &format!(
            ".openworker/integrations/{}",
            intent.request.export.operation_id
        ),
    )?;
    if !dir.exists() {
        ensure!(
            !inspect_only,
            "Original integration intent absent; outcome unknown"
        );
        std::fs::create_dir_all(&dir)?;
    }
    git::checked(&dir)?;
    let marker = dir.join("request.json");
    git::checked(&marker)?;
    ensure!(
        !inspect_only || marker.is_file(),
        "Original integration intent absent; inspection cannot dispatch"
    );
    let fresh = match git::write_new(&marker, &intent) {
        Ok(()) => true,
        Err(e)
            if e.downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.kind() == std::io::ErrorKind::AlreadyExists) =>
        {
            false
        }
        Err(e) => return Err(e),
    };
    let saved: Intent = serde_json::from_slice(&crate::bundle::read_limited(&marker, 32768)?)?;
    ensure!(
        saved == intent,
        "Original integration request or repository identity changed"
    );
    let output: PathBuf = dir.join("receipt.json");
    git::checked(&output)?;
    let rejection = dir.join("rejected.json");
    git::checked(&rejection)?;
    if rejection.exists() {
        let original: Observation =
            serde_json::from_slice(&crate::bundle::read_limited(&rejection, 32768)?)?;
        ensure!(
            matches!(&original,Observation::Rejected{request,..} if *request==intent.request),
            "Original integration rejection changed"
        );
        return Err(Rejected(original).into());
    }
    if fresh {
        ensure!(!inspect_only, "Inspection cannot dispatch integration");
        if intent.pin.repository == root {
            crate::team_workspace::exclude_bookkeeping(&intent.pin.common)?;
        }
        if git::git(&intent.pin.repository, &["rev-parse", "HEAD"])
            .await?
            .trim()
            != intent.request.expected_head
        {
            reject(
                &dir,
                &intent.request,
                "Expected HEAD is stale; inspect current branch before a new integration",
            )?;
        }
        if !git::git(
            &intent.pin.repository,
            &[
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
                "--ignore-submodules=none",
            ],
        )
        .await?
        .trim()
        .is_empty()
        {
            reject(
                &dir,
                &intent.request,
                "Working tree is dirty; preserve current work",
            )?;
        }
        apply(&intent, &dir).await?;
        let r = receipt(&intent).await?;
        git::write_new(&output, &r)?;
        return Ok(r);
    }
    let observed = receipt(&intent).await?;
    if output.exists() {
        let recorded: Receipt =
            serde_json::from_slice(&crate::bundle::read_limited(&output, 32768)?)?;
        ensure!(recorded == observed, "Original integration receipt changed");
    }
    // Desired original HEAD/tree can be observed after a lost reply. This does
    // not claim causality or rerun Git, and missing/partial state stays unknown.
    Ok(observed)
}

impl Observation {
    pub fn require_request(&self, expected: &Request) -> Result<()> {
        expected.validate()?;
        match self {
            Self::Applied { receipt } => {
                ensure!(
                    receipt.request == *expected
                        && receipt.commit == expected.export.submitted_commit
                        && receipt.branch.starts_with("refs/heads/")
                        && receipt.branch.len() <= 200
                        && receipt
                            .branch
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"/_-.".contains(&b)),
                    "Integration acknowledgement differs from original request"
                );
                oid(&receipt.tree, expected.export.object_format)?;
            }
            Self::Rejected { request, reason } => ensure!(
                *request == *expected
                    && !reason.trim().is_empty()
                    && reason.len() <= 2000
                    && !reason.contains('\0'),
                "Integration rejection differs from original request"
            ),
        }
        Ok(())
    }
}
