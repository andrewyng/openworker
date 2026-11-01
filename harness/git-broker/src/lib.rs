//! Optional trusted repository adapter. No agent-supplied URL, executable,
//! credential or Git arguments; no automatic retry of an uncertain push.
use anyhow::{ensure, Context, Result};
use openworker_runtime::coding_handoff::git::{Bundle, Export};
use serde::{Deserialize, Serialize};
#[cfg(any(test, feature = "fixture"))]
use std::collections::BTreeMap;
use std::{
    path::{Path, PathBuf},
    time::Duration,
};
use tokio_util::sync::CancellationToken;
mod process;
mod publication;
use openworker_runtime::coding_handoff::publication::reference;
pub use openworker_runtime::coding_handoff::publication::{
    branch, Grant, Outcome, Publication, Repository,
};

/// A trusted caller must independently authorize the session, source/artifact,
/// profile and original durable dispatch before invoking this adapter.
pub struct Broker {
    root: PathBuf,
    grant: Grant,
    #[cfg(any(test, feature = "fixture"))]
    fixture: BTreeMap<String, PathBuf>,
    #[cfg(any(test, feature = "fixture"))]
    https_fixture: Option<(u16, PathBuf)>,
}
impl Broker {
    pub fn new(root: &Path, grant: Grant) -> Result<Self> {
        grant.validate()?;
        ensure!(
            cfg!(unix),
            "Trusted Git adapter requires qualified Unix process cleanup"
        );
        checked(&std::path::absolute(root)?)?;
        std::fs::create_dir_all(root)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(root, std::fs::Permissions::from_mode(0o700))?;
        }
        let root = root.canonicalize()?;
        Ok(Self {
            root,
            grant,
            #[cfg(any(test, feature = "fixture"))]
            fixture: BTreeMap::new(),
            #[cfg(any(test, feature = "fixture"))]
            https_fixture: None,
        })
    }
    #[cfg(any(test, feature = "fixture"))]
    pub fn with_fixture(mut self, repository: &str, path: &Path) -> Result<Self> {
        ensure!(
            self.grant.repositories.contains_key(repository),
            "Unknown fixture repository"
        );
        let path = path.canonicalize()?;
        checked(&path)?;
        self.fixture.insert(repository.into(), path);
        Ok(self)
    }
    /// Loopback-only synthetic HTTPS qualification. Production has no endpoint
    /// or CA override and always authenticates to the fixed GitHub origin.
    #[cfg(any(test, feature = "fixture"))]
    pub fn with_https_fixture(mut self, port: u16, ca: &Path) -> Result<Self> {
        ensure!(port > 0, "Invalid fixture port");
        let ca = ca.canonicalize()?;
        checked(&ca)?;
        ensure!(ca.is_file(), "Fixture CA missing");
        self.https_fixture = Some((port, ca));
        Ok(self)
    }
    fn remote(&self, repository: &str) -> Result<String> {
        let repo = self
            .grant
            .repositories
            .get(repository)
            .context("Repository scope denied")?;
        #[cfg(any(test, feature = "fixture"))]
        if let Some(path) = self.fixture.get(repository) {
            return Ok(path.to_str().context("Nonportable fixture path")?.into());
        }
        #[cfg(any(test, feature = "fixture"))]
        if let Some((port, _)) = &self.https_fixture {
            return Ok(format!("https://127.0.0.1:{port}/{}.git", repo.github));
        }
        Ok(format!("https://github.com/{}.git", repo.github))
    }
    fn credential(&self) -> Result<Option<zeroize::Zeroizing<String>>> {
        #[cfg(any(test, feature = "fixture"))]
        if !self.fixture.is_empty() {
            return Ok(None);
        }
        let value = zeroize::Zeroizing::new(
            std::env::var(&self.grant.credential_env)
                .map_err(|_| anyhow::anyhow!("Repository credential unavailable"))?,
        );
        ensure!(
            !value.is_empty() && value.len() <= 16384 && !value.chars().any(char::is_control),
            "Repository credential invalid"
        );
        Ok(Some(value))
    }
    fn directory(&self, id: &str) -> Result<PathBuf> {
        ensure!(
            !id.is_empty()
                && id.len() <= 80
                && id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
            "Invalid original operation identity"
        );
        let path = self.root.join(id);
        checked(&path)?;
        Ok(path)
    }
    async fn git(
        &self,
        root: &Path,
        args: &[String],
        network: bool,
        stop: &CancellationToken,
    ) -> Result<String> {
        #[cfg(any(test, feature = "fixture"))]
        let (origin, ca) = self
            .https_fixture
            .as_ref()
            .map(|(p, ca)| (format!("https://127.0.0.1:{p}/"), Some(ca.as_path())))
            .unwrap_or_else(|| ("https://github.com/".into(), None));
        #[cfg(not(any(test, feature = "fixture")))]
        let (origin, ca) = ("https://github.com/".to_owned(), None);
        process::run(
            root,
            args,
            if network { self.credential()? } else { None },
            &origin,
            ca,
            stop,
            Duration::from_secs(45),
        )
        .await
    }
    async fn tip(
        &self,
        root: &Path,
        repository: &str,
        branch: &str,
        stop: &CancellationToken,
    ) -> Result<Option<String>> {
        branch_validate(branch)?;
        let remote = self.remote(repository)?;
        let reference = format!("refs/heads/{branch}");
        let text = self
            .git(
                root,
                &[
                    "ls-remote".into(),
                    "--refs".into(),
                    remote,
                    reference.clone(),
                ],
                true,
                stop,
            )
            .await?;
        let rows: Vec<_> = text.lines().collect();
        ensure!(rows.len() <= 1, "Remote ref is ambiguous");
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        let (oid, name) = row
            .split_once('\t')
            .context("Invalid remote ref response")?;
        ensure!(
            name == reference
                && (oid.len() == 40 || oid.len() == 64)
                && oid
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "Invalid remote ref response"
        );
        Ok(Some(oid.into()))
    }
}
fn branch_validate(value: &str) -> Result<()> {
    branch(value)
}
fn checked(path: &Path) -> Result<()> {
    ensure!(path.is_absolute(), "Trusted broker path must be absolute");
    let mut cursor = PathBuf::new();
    for part in path.components() {
        ensure!(
            !matches!(
                part,
                std::path::Component::ParentDir | std::path::Component::CurDir
            ),
            "Broker path traversal denied"
        );
        cursor.push(part);
        match std::fs::symlink_metadata(&cursor) {
            Ok(m) => ensure!(!m.file_type().is_symlink(), "Broker state symlink rejected"),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
    }
    Ok(())
}
fn atomic_new<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    use std::io::Write;
    checked(path)?;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    file.write_all(&serde_json::to_vec(value)?)?;
    file.sync_all()?;
    Ok(())
}
fn read<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T> {
    checked(path)?;
    Ok(serde_json::from_slice(
        &openworker_runtime::bundle::read_limited(path, 65536)?,
    )?)
}

#[cfg(all(test, unix))]
mod tests;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub id: String,
    pub grant_digest: String,
    pub reference: String,
    pub export: Export,
}
impl Broker {
    /// Stage an authorized exact commit from a permitted ref. No project files,
    /// scripts, submodules or hooks are executed in the trusted broker.
    pub async fn stage(
        &self,
        source: Source,
        dispatch: bool,
        now_ms: u64,
        stop: &CancellationToken,
    ) -> Result<Bundle> {
        source.export.validate()?;
        reference(&source.reference)?;
        self.grant.live(now_ms)?;
        ensure!(
            source.id == source.export.operation_id
                && source.grant_digest == self.grant.digest()?,
            "Original source grant/operation changed"
        );
        let repo = self
            .grant
            .repositories
            .get(&source.export.repository)
            .context("Repository staging denied")?;
        ensure!(
            repo.read_refs.contains(&source.reference),
            "Source ref denied"
        );
        ensure!(
            source.export.base_commit == source.export.submitted_commit,
            "Initial source requires one exact revision"
        );
        let dir = self.directory(&source.id)?;
        if !dir.exists() {
            ensure!(dispatch, "Original source state absent; outcome unknown");
            std::fs::create_dir(&dir)?;
        }
        let marker = dir.join("source.json");
        ensure!(
            dispatch || marker.is_file(),
            "Original source intent absent; inspection cannot dispatch"
        );
        let fresh = match atomic_new(&marker, &source) {
            Ok(()) => true,
            Err(e)
                if e.downcast_ref::<std::io::Error>()
                    .is_some_and(|e| e.kind() == std::io::ErrorKind::AlreadyExists) =>
            {
                false
            }
            Err(e) => return Err(e),
        };
        ensure!(
            read::<Source>(&marker)? == source,
            "Original source request changed"
        );
        if !fresh || !dispatch {
            return openworker_runtime::coding_handoff::git::export(
                &dir,
                source.export,
                true,
                None,
            )
            .await;
        }
        let path = dir.join(&source.export.directory);
        ensure!(
            path != dir,
            "Trusted remote staging requires a relative repository directory"
        );
        checked(&path)?;
        self.git(
            &dir,
            &[
                "init".into(),
                "--quiet".into(),
                format!("--object-format={}", source.export.object_format.git_name()),
                path.to_str().context("Nonportable staging path")?.into(),
            ],
            false,
            stop,
        )
        .await?;
        let remote = self.remote(&source.export.repository)?;
        self.git(
            &path,
            &[
                "fetch".into(),
                "--no-tags".into(),
                "--no-write-fetch-head".into(),
                "--no-recurse-submodules".into(),
                remote,
                format!("{}:refs/heads/staged", source.reference),
            ],
            true,
            stop,
        )
        .await?;
        self.git(
            &path,
            &[
                "merge-base".into(),
                "--is-ancestor".into(),
                source.export.submitted_commit.clone(),
                "refs/heads/staged".into(),
            ],
            false,
            stop,
        )
        .await?;
        // Repository has objects only. Export never checks out or executes files.
        openworker_runtime::coding_handoff::git::export(&dir, source.export, false, None).await
    }
    pub fn exported_path(&self, id: &str, bundle: &Bundle) -> Result<PathBuf> {
        bundle.validate()?;
        ensure!(
            id == bundle.request.operation_id,
            "Original source identity differs"
        );
        let path = self.directory(id)?.join(&bundle.path);
        checked(&path)?;
        Ok(path)
    }
}
