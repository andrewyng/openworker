//! Workspace selection is not an isolation boundary. The trusted host records
//! an intent before calling prepare; after delivery loss it calls inspect only.
//! This module executes inside ToolRunner for remote environments.
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub enum Selection {
    Team {},
    Directory {
        path: String,
    },
    Worktree {
        path: String,
        repository: String,
        branch: String,
        #[serde(default = "head")]
        base: String,
    },
}
fn head() -> String {
    "HEAD".into()
}
impl Default for Selection {
    fn default() -> Self {
        Self::Team {}
    }
}
impl Selection {
    pub fn validate(&self) -> Result<()> {
        match self {
            Self::Team {} => {}
            Self::Directory { path } => {
                crate::runner::relative_path(path)?;
            }
            Self::Worktree {
                path,
                repository,
                branch,
                base,
            } => {
                crate::runner::relative_path(path)?;
                if repository != "." {
                    crate::runner::relative_path(repository)?;
                }
                for value in [branch, base] {
                    ensure!(
                        !value.is_empty()
                            && value.len() <= 128
                            && !value.starts_with(['-', '/'])
                            && !value.contains("..")
                            && !value.contains("@{")
                            && value
                                .bytes()
                                .all(|b| b.is_ascii_alphanumeric() || b"/_-.".contains(&b)),
                        "Invalid branch/base reference"
                    );
                }
            }
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Identity {
    volume: u64,
    file: u64,
}
pub fn identity(path: &Path) -> Result<Identity> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let m = fs::metadata(path)?;
        Ok(Identity {
            volume: m.dev(),
            file: m.ino(),
        })
    }
    #[cfg(windows)]
    {
        let (volume, file, _, _, _) = crate::platform::windows::file_identity(path)?;
        Ok(Identity { volume, file })
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub operation_id: String,
    pub scope_digest: String,
    pub selection: Selection,
}
impl Request {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            crate::bundle::safe_name(&self.operation_id) && self.operation_id.len() <= 80,
            "Invalid workspace operation ID"
        );
        ensure!(
            self.scope_digest.len() == 64
                && self.scope_digest.bytes().all(|b| b.is_ascii_hexdigit()),
            "Invalid workspace scope digest"
        );
        self.selection.validate()
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub request: Request,
    pub path: PathBuf,
    pub project_root: PathBuf,
    pub identity: Identity,
    pub repository: Option<PathBuf>,
    pub git_dir: Option<PathBuf>,
    pub git_identity: Option<Identity>,
    #[serde(default)]
    pub worktree_git_dir: Option<PathBuf>,
    #[serde(default)]
    pub worktree_git_identity: Option<Identity>,
    #[serde(default)]
    pub pointer_digest: Option<String>,
    pub branch: Option<String>,
    pub initial_commit: Option<String>,
}
/// Saved in the team transaction before filesystem dispatch. A recovered
/// dispatched intent authorizes inspection only, never another mutation.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Intent {
    pub request: Request,
    pub receipt: Option<Receipt>,
    pub error: Option<String>,
}
impl Receipt {
    /// Structural validation is safe on the trusted host even when these paths
    /// live on another machine. Filesystem identity is checked by the ToolRunner.
    pub fn validate_scope(&self, project: &Path, workspace: &Path) -> Result<()> {
        self.request.validate()?;
        ensure!(
            self.project_root == project && self.path == workspace,
            "Workspace receipt belongs to another project/directory"
        );
        for p in [&self.project_root, &self.path] {
            ensure!(
                p.is_absolute()
                    && p.components().all(|c| !matches!(
                        c,
                        std::path::Component::ParentDir | std::path::Component::CurDir
                    )),
                "Noncanonical workspace receipt"
            );
        }
        let expected = match &self.request.selection {
            Selection::Team {} => project.to_owned(),
            Selection::Directory { path } | Selection::Worktree { path, .. } => project.join(path),
        };
        ensure!(
            self.path == expected && self.path.starts_with(project),
            "Workspace selection/receipt mismatch"
        );
        Ok(())
    }
    pub fn verify(&self) -> Result<()> {
        self.validate_scope(&self.project_root, &self.path)?;
        ensure!(
            self.path.starts_with(&self.project_root) && self.path.is_dir(),
            "Workspace left its authorized project"
        );
        checked_absolute(&self.project_root)?;
        checked_absolute(&self.path)?;
        ensure!(
            identity(&self.path)? == self.identity,
            "Workspace identity changed; do not adopt replacement"
        );
        if let Some(git) = &self.git_dir {
            checked_absolute(git)?;
            ensure!(
                self.git_identity.as_ref() == Some(&identity(git)?),
                "Original Git metadata identity changed"
            );
            ensure!(
                git.starts_with(&self.project_root),
                "Git metadata is outside the authorized project"
            );
            let (private, common) = metadata_paths(&self.path, &self.project_root)?;
            ensure!(
                &common == git
                    && self.worktree_git_dir.as_ref() == Some(&private)
                    && self.worktree_git_identity.as_ref() == Some(&identity(&private)?)
                    && self.pointer_digest.as_ref()
                        == Some(&crate::bundle::digest(&crate::bundle::read_limited(
                            &self.path.join(".git"),
                            4096
                        )?)),
                "Original worktree metadata pointer/identity changed"
            );
            checked_absolute(&private.join("HEAD"))?;
            ensure!(
                fs::read_to_string(private.join("HEAD"))?.trim()
                    == format!(
                        "ref: refs/heads/{}",
                        self.branch.as_deref().context("Missing worktree branch")?
                    ),
                "Managed worktree branch changed"
            );
        }
        Ok(())
    }
}
fn checked_absolute(path: &Path) -> Result<()> {
    ensure!(path.is_absolute(), "Absolute workspace root required");
    let mut current = PathBuf::new();
    for p in path.components() {
        current.push(p);
        if current.exists() {
            ensure!(
                !crate::platform::linked(&fs::symlink_metadata(&current)?),
                "Workspace symlinks are refused"
            );
        }
    }
    Ok(())
}
fn select_path(root: &Path, path: &str) -> Result<PathBuf> {
    if path == "." {
        Ok(root.into())
    } else {
        crate::runner::path(root, path)
    }
}
async fn git(root: &Path, args: &[&str]) -> Result<String> {
    crate::coding::git::run(root, &args.iter().map(|v| (*v).into()).collect::<Vec<_>>()).await
}
/// Resolve on-disk Git pointers without running Git or following metadata outside
/// the app-authorized project. Used before reading any repository configuration.
fn metadata_paths(root: &Path, project: &Path) -> Result<(PathBuf, PathBuf)> {
    let dot = root.join(".git");
    checked_absolute(&dot)?;
    let git = if dot.is_dir() {
        dot
    } else {
        let data = String::from_utf8(crate::bundle::read_limited(&dot, 4096)?)?;
        let path = data
            .trim()
            .strip_prefix("gitdir: ")
            .context("Invalid Git pointer")?;
        let path = root.join(path);
        checked_absolute(&path)?;
        path.canonicalize()?
    };
    ensure!(
        git.starts_with(project) && git.is_dir(),
        "Git metadata outside authorized project"
    );
    let common = git.join("commondir");
    checked_absolute(&common)?;
    let common = if common.exists() {
        let data = String::from_utf8(crate::bundle::read_limited(&common, 4096)?)?;
        let path = git.join(data.trim());
        checked_absolute(&path)?;
        path.canonicalize()?
    } else {
        git.clone()
    };
    ensure!(
        common.starts_with(project) && common.is_dir(),
        "Git common metadata outside authorized project"
    );
    checked_absolute(&common)?;
    ensure!(
        !common.join("objects/info/alternates").exists(),
        "External Git object stores are not supported during preparation"
    );
    Ok((git, common))
}
pub(crate) async fn repository(root: &Path, project: &Path) -> Result<PathBuf> {
    // Validate pointers before invoking Git: rev-parse itself reads repository
    // configuration. A linked worktree may reference metadata only in this project.
    let (_, expected) = metadata_paths(root, project)?;
    let config_path = expected.join("config");
    checked_absolute(&config_path)?;
    let config = git(
        root,
        &[
            "config",
            "--file",
            config_path.to_str().context("Git config must be UTF-8")?,
            "--no-includes",
            "--null",
            "--list",
        ],
    )
    .await?;
    for entry in config.split('\0') {
        let key = entry.split('\n').next().unwrap_or("").to_ascii_lowercase();
        ensure!(!key.starts_with("filter.") && !key.starts_with("include.") && !key.starts_with("includeif.") && !matches!(key.as_str(),"extensions.worktreeconfig"|"core.worktree"|"core.sshcommand"), "Repository helpers/included/worktree configuration are not allowed during managed preparation");
    }
    let top =
        PathBuf::from(git(root, &["rev-parse", "--show-toplevel"]).await?.trim()).canonicalize()?;
    ensure!(
        top == root,
        "Select the repository root, not a directory inside it"
    );
    let common = PathBuf::from(
        git(
            root,
            &["rev-parse", "--path-format=absolute", "--git-common-dir"],
        )
        .await?
        .trim(),
    )
    .canonicalize()?;
    checked_absolute(&common)?;
    ensure!(
        common.starts_with(project),
        "Git metadata must remain in the authorized project"
    );
    ensure!(common == expected, "Git metadata changed during inspection");
    Ok(common)
}
#[derive(Serialize, Deserialize)]
struct Marker {
    request: Request,
    repository: PathBuf,
    common: PathBuf,
    commit: String,
}
/// No mutation is replayed by this function. A recovered operation with missing
/// evidence remains unknown; it does not run git worktree add a second time.
pub async fn prepare(root: &Path, request: Request, inspect_only: bool) -> Result<Receipt> {
    request.validate()?;
    checked_absolute(root)?;
    let root = root.canonicalize()?;
    ensure!(root.is_dir(), "Project root must be a directory");
    let mut receipt = Receipt {
        request: request.clone(),
        path: root.clone(),
        project_root: root.clone(),
        identity: identity(&root)?,
        repository: None,
        git_dir: None,
        git_identity: None,
        worktree_git_dir: None,
        worktree_git_identity: None,
        pointer_digest: None,
        branch: None,
        initial_commit: None,
    };
    match &request.selection {
        Selection::Team {} => {}
        Selection::Directory { path } => {
            let path = select_path(&root, path)?;
            ensure!(path.is_dir(), "Selected directory does not exist");
            receipt.path = path.canonicalize()?;
            receipt.identity = identity(&receipt.path)?;
        }
        Selection::Worktree {
            path,
            repository: repo,
            branch,
            base,
        } => {
            let repo = select_path(&root, repo)?.canonicalize()?;
            checked_absolute(&repo)?;
            let common = repository(&repo, &root).await?;
            git(&repo, &["check-ref-format", "--branch", branch]).await?;
            let path = select_path(&root, path)?;
            ensure!(
                !path.starts_with(&common) && path != repo,
                "Worktree overlaps repository metadata"
            );
            let markers = crate::runner::path(&root, ".openworker/workspaces")?;
            let marker = markers.join(format!("{}.json", request.operation_id));
            let already_dispatched = marker.exists();
            let persisted = if already_dispatched {
                checked_absolute(&marker)?;
                let m: Marker =
                    serde_json::from_slice(&crate::bundle::read_limited(&marker, 16000)?)?;
                ensure!(
                    m.request == request && m.repository == repo && m.common == common,
                    "Workspace intent changed"
                );
                m
            } else {
                ensure!(!inspect_only, "Workspace outcome unknown: no original preparation evidence; do not retry automatically");
                ensure!(
                    !path.exists(),
                    "Worktree target already exists; not adopting it"
                );
                let commit = git(
                    &repo,
                    &[
                        "rev-parse",
                        "--verify",
                        "--end-of-options",
                        &format!("{base}^{{commit}}"),
                    ],
                )
                .await?
                .trim()
                .to_owned();
                ensure!(
                    [40, 64].contains(&commit.len())
                        && commit.bytes().all(|b| b.is_ascii_hexdigit()),
                    "Expected a local commit"
                );
                fs::create_dir_all(&markers)?;
                let m = Marker {
                    request: request.clone(),
                    repository: repo.clone(),
                    common: common.clone(),
                    commit,
                };
                use std::io::Write;
                let mut f = fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&marker)?;
                f.write_all(&serde_json::to_vec(&m)?)?;
                f.sync_all()?;
                m
            };
            if !inspect_only {
                // The host's durable dispatched flag is written before entering
                // here. Existing intent evidence never authorizes replay.
                ensure!(
                    !already_dispatched,
                    "Workspace preparation was already dispatched; inspect original outcome only"
                );
                ensure!(
                    !path.exists(),
                    "Existing worktree requires read-only reconciliation"
                );
                let parent = path.parent().context("Worktree parent missing")?;
                checked_absolute(parent)?;
                fs::create_dir_all(parent)?;
                let target = path.to_str().context("Workspace must be UTF-8")?;
                git(
                    &repo,
                    &[
                        "worktree",
                        "add",
                        "--no-guess-remote",
                        "-b",
                        branch,
                        target,
                        &persisted.commit,
                    ],
                )
                .await?;
            }
            ensure!(
                path.is_dir(),
                "Workspace preparation outcome unknown; original worktree is absent"
            );
            checked_absolute(&path)?;
            let found = repository(&path, &root).await?;
            ensure!(
                found == common
                    && git(&path, &["symbolic-ref", "--short", "HEAD"])
                        .await?
                        .trim()
                        == branch,
                "Original worktree branch/metadata changed"
            );
            ensure!(
                git(&path, &["rev-parse", "HEAD"]).await?.trim() == persisted.commit,
                "Worktree changed before preparation was acknowledged"
            );
            receipt.path = path.canonicalize()?;
            receipt.identity = identity(&receipt.path)?;
            receipt.repository = Some(repo);
            receipt.git_identity = Some(identity(&common)?);
            receipt.git_dir = Some(common);
            let (private, _) = metadata_paths(&receipt.path, &root)?;
            receipt.worktree_git_identity = Some(identity(&private)?);
            receipt.worktree_git_dir = Some(private);
            receipt.pointer_digest = Some(crate::bundle::digest(&crate::bundle::read_limited(
                &receipt.path.join(".git"),
                4096,
            )?));
            receipt.branch = Some(branch.clone());
            receipt.initial_commit = Some(persisted.commit);
        }
    }
    receipt.verify()?;
    Ok(receipt)
}
pub async fn serve(root: &Path) -> Result<()> {
    let root = root.to_owned();
    let mut input = tokio::io::BufReader::new(tokio::io::stdin());
    let mut output = tokio::io::stdout();
    loop {
        let mut line = vec![];
        let n = (&mut input)
            .take(64001)
            .read_until(b'\n', &mut line)
            .await?;
        if n == 0 {
            break;
        }
        ensure!(n <= 64000, "Workspace frame too large");
        let v: Value = serde_json::from_slice(&line)?;
        if v.get("id").is_none() {
            continue;
        }
        let result = async {
            if v["method"] == "initialize" {
                return Ok(json!({"protocolVersion":"2025-11-25","capabilities":{},"serverInfo":{"name":"openworker-workspaces","version":"1"}}));
            }
            if v["method"] == "workspace/verify" {
                let receipt: Receipt = serde_json::from_value(v["params"].clone())?;
                receipt.validate_scope(&root, &receipt.path)?;
                receipt.verify()?;
                return Ok(serde_json::to_value(receipt)?);
            }
            let request: Request = serde_json::from_value(v["params"].clone())?;
            ensure!(
                matches!(
                    v["method"].as_str(),
                    Some("workspace/prepare" | "workspace/inspect")
                ),
                "Unknown workspace method"
            );
            Ok::<_, anyhow::Error>(serde_json::to_value(
                prepare(&root, request, v["method"] == "workspace/inspect").await?,
            )?)
        }
        .await;
        let response = match result {
            Ok(r) => json!({"jsonrpc":"2.0","id":v["id"],"result":r}),
            Err(e) => {
                json!({"jsonrpc":"2.0","id":v["id"],"error":{"code":-32000,"message":e.to_string()}})
            }
        };
        output.write_all(format!("{response}\n").as_bytes()).await?;
        output.flush().await?;
    }
    Ok(())
}

pub fn schema() -> Value {
    json!({"anyOf":[
        {"type":"object","properties":{"mode":{"type":"string","enum":["team"]}},"required":["mode"],"additionalProperties":false},
        {"type":"object","properties":{"mode":{"type":"string","enum":["directory"]},"path":{"type":"string"}},"required":["mode","path"],"additionalProperties":false},
        {"type":"object","properties":{"mode":{"type":"string","enum":["worktree"]},"path":{"type":"string"},"repository":{"type":"string"},"branch":{"type":"string"},"base":{"type":"string"}},"required":["mode","path","repository","branch"],"additionalProperties":false}
    ]})
}
