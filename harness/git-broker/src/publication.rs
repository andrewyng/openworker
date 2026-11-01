use super::*;
struct Lock(std::fs::File);
impl Drop for Lock {
    fn drop(&mut self) {
        let _ = fs2::FileExt::unlock(&self.0);
    }
}
impl Broker {
    /// `dispatch` comes only from the durable controller's first-dispatch bit.
    /// A lost connection is UNKNOWN, not permission to push again. Recovery only
    /// reads the original ref so the model/human can inspect before a new action.
    pub async fn publish(
        &self,
        request: Publication,
        bundle_path: &Path,
        dispatch: bool,
        now_ms: u64,
        stop: &CancellationToken,
    ) -> Result<Outcome> {
        self.publish_checked(request, bundle_path, dispatch, now_ms, stop, || Ok(()))
            .await
    }
    pub async fn publish_checked<F: Fn() -> Result<()> + Send + Sync>(
        &self,
        request: Publication,
        bundle_path: &Path,
        dispatch: bool,
        now_ms: u64,
        stop: &CancellationToken,
        authorize: F,
    ) -> Result<Outcome> {
        request.validate()?;
        authorize()?;
        self.grant.live(now_ms)?;
        ensure!(
            request.grant_digest == self.grant.digest()?,
            "Original repository grant changed"
        );
        let config = self
            .grant
            .repositories
            .get(&request.bundle.request.repository)
            .context("Repository publication denied")?;
        ensure!(
            config
                .write_prefixes
                .iter()
                .any(|p| request.branch.starts_with(p) && request.branch.len() > p.len()),
            "Branch publication denied"
        );
        if !dispatch {
            // The durable service intent, not this node's cache, is authority
            // for inspecting the ORIGINAL remote ref after controller failover.
            let observed = self
                .tip(
                    &self.root,
                    &request.bundle.request.repository,
                    &request.branch,
                    stop,
                )
                .await?;
            return Ok(
                if observed.as_deref() == Some(&request.bundle.request.submitted_commit) {
                    Outcome::Published {
                        request: request.clone(),
                        observed_head: request.bundle.request.submitted_commit.clone(),
                    }
                } else {
                    Outcome::Unknown {
                        request,
                        observed_head: observed,
                    }
                },
            );
        }
        let dir = self.directory(&request.id)?;
        if !dir.exists() {
            ensure!(
                dispatch,
                "Original publication state absent; outcome unknown"
            );
            std::fs::create_dir(&dir)?;
        }
        let lock_path = dir.join("operation.lock");
        checked(&lock_path)?;
        let lock = Lock(
            std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(lock_path)?,
        );
        fs2::FileExt::try_lock_exclusive(&lock.0).context("Publication already executing")?;
        let marker = dir.join("request.json");
        ensure!(
            dispatch || marker.is_file(),
            "Original publication intent absent; inspection cannot dispatch"
        );
        let fresh = match atomic_new(&marker, &request) {
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
            read::<Publication>(&marker)? == request,
            "Original publication request changed"
        );
        let result = dir.join("result.json");
        if result.exists() {
            let outcome: Outcome = read(&result)?;
            ensure!(
                match &outcome {
                    Outcome::Published { request: r, .. }
                    | Outcome::Rejected { request: r, .. }
                    | Outcome::Unknown { request: r, .. } => *r == request,
                },
                "Publication receipt changed"
            );
            return Ok(outcome);
        }
        if !fresh || !dispatch {
            let observed = self
                .tip(
                    &dir,
                    &request.bundle.request.repository,
                    &request.branch,
                    stop,
                )
                .await?;
            let outcome = if observed.as_deref() == Some(&request.bundle.request.submitted_commit) {
                Outcome::Published {
                    request: request.clone(),
                    observed_head: request.bundle.request.submitted_commit.clone(),
                }
            } else {
                Outcome::Unknown {
                    request: request.clone(),
                    observed_head: observed,
                }
            };
            // Inspection never rewrites the original dispatch marker or pushes.
            return Ok(outcome);
        }
        let before = self
            .tip(
                &dir,
                &request.bundle.request.repository,
                &request.branch,
                stop,
            )
            .await?;
        if before != request.expected_head {
            let outcome = Outcome::Rejected {
                request,
                reason: "Expected remote tip is stale; no push was attempted".into(),
            };
            atomic_new(&result, &outcome)?;
            return Ok(outcome);
        }
        checked(bundle_path)?;
        let path = bundle_path.canonicalize()?;
        checked(&path)?;
        ensure!(
            std::fs::metadata(&path)?.len() == request.bundle.size
                && request.bundle.size <= 64 * 1024 * 1024,
            "Submitted artifact size differs"
        );
        let bytes = openworker_runtime::bundle::read_limited(&path, 64 * 1024 * 1024)?;
        ensure!(
            openworker_runtime::bundle::digest(&bytes) == request.bundle.sha256,
            "Submitted artifact digest differs"
        );
        drop(bytes);
        openworker_runtime::coding_handoff::git::validate(
            &path,
            &request.bundle.request,
            &request.bundle.advertised_ref,
        )
        .await?;
        let repo = dir.join("repository.git");
        self.git(
            &dir,
            &[
                "init".into(),
                "--bare".into(),
                "--quiet".into(),
                format!(
                    "--object-format={}",
                    request.bundle.request.object_format.git_name()
                ),
                repo.to_str().context("Nonportable broker path")?.into(),
            ],
            false,
            stop,
        )
        .await?;
        self.git(
            &repo,
            &[
                "fetch".into(),
                "--no-tags".into(),
                "--no-write-fetch-head".into(),
                "--no-recurse-submodules".into(),
                path.to_str().context("Nonportable bundle path")?.into(),
                format!(
                    "{}:refs/openworker/submitted",
                    request.bundle.advertised_ref
                ),
            ],
            false,
            stop,
        )
        .await?;
        if let Some(expected) = &request.expected_head {
            // Fetch only the original permitted branch into trusted metadata.
            let remote = self.remote(&request.bundle.request.repository)?;
            self.git(
                &repo,
                &[
                    "fetch".into(),
                    "--no-tags".into(),
                    "--no-write-fetch-head".into(),
                    "--no-recurse-submodules".into(),
                    remote,
                    format!("refs/heads/{}:refs/openworker/expected", request.branch),
                ],
                true,
                stop,
            )
            .await?;
            let observed = self
                .git(
                    &repo,
                    &["rev-parse".into(), "refs/openworker/expected".into()],
                    false,
                    stop,
                )
                .await?;
            if observed.trim() != expected {
                let outcome = Outcome::Rejected {
                    request,
                    reason: "Remote changed during preflight; no push was attempted".into(),
                };
                atomic_new(&result, &outcome)?;
                return Ok(outcome);
            }
            let ancestry = self
                .git(
                    &repo,
                    &[
                        "merge-base".into(),
                        "--is-ancestor".into(),
                        expected.clone(),
                        request.bundle.request.submitted_commit.clone(),
                    ],
                    false,
                    stop,
                )
                .await;
            if let Err(error) = ancestry {
                if error
                    .downcast_ref::<super::process::CommandFailure>()
                    .is_none_or(|failure| failure.code != Some(1))
                {
                    return Err(error);
                }
                let outcome = Outcome::Rejected {
                    request,
                    reason: "Non-fast-forward publication denied; no push was attempted".into(),
                };
                atomic_new(&result, &outcome)?;
                return Ok(outcome);
            }
        }
        authorize()?;
        self.grant
            .live(openworker_runtime::store::now_ms().max(now_ms))?;
        let remote = self.remote(&request.bundle.request.repository)?;
        let reference = format!("refs/heads/{}", request.branch);
        let lease = format!(
            "--force-with-lease={reference}:{}",
            request.expected_head.as_deref().unwrap_or("")
        );
        // This lease is a compare-and-swap, never permission to overwrite
        // divergent history: ancestry above is mandatory for an existing ref.
        self.git(
            &repo,
            &[
                "push".into(),
                "--porcelain".into(),
                "--no-verify".into(),
                "--recurse-submodules=no".into(),
                lease,
                remote,
                format!("{}:{reference}", request.bundle.request.submitted_commit),
            ],
            true,
            stop,
        )
        .await?;
        let observed = self
            .tip(
                &dir,
                &request.bundle.request.repository,
                &request.branch,
                stop,
            )
            .await?;
        let outcome = if observed.as_deref() == Some(&request.bundle.request.submitted_commit) {
            Outcome::Published {
                request: request.clone(),
                observed_head: request.bundle.request.submitted_commit.clone(),
            }
        } else {
            Outcome::Unknown {
                request: request.clone(),
                observed_head: observed,
            }
        };
        if matches!(outcome, Outcome::Published { .. }) {
            atomic_new(&result, &outcome)?;
        }
        Ok(outcome)
    }
}
