//! Optional trusted-side Git execution. No key, endpoint or workspace path is
//! supplied by the agent; authority is rechecked before the remote mutation.
use crate::Config;
use anyhow::{ensure, Context, Result};
use futures_util::StreamExt;
use openworker_store_postgres::{service_team_publications::Job, PostgresStore};
use std::path::PathBuf;
use tokio::io::AsyncWriteExt;
use tokio_util::sync::CancellationToken;
struct Spool(PathBuf);
impl Drop for Spool {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
async fn download(
    config: &Config,
    store: &PostgresStore,
    job: &Job,
    backend: &openworker_artifacts::Store,
    stop: &CancellationToken,
) -> Result<Spool> {
    let artifact = &job.intent.artifact;
    artifact.validate()?;
    ensure!(
        backend.binding() == &artifact.binding,
        "Original artifact store changed"
    );
    let proof = artifact
        .proof
        .as_ref()
        .context("Original artifact is not published")?;
    let mut bytes = backend.get(artifact, proof, stop.clone()).await?;
    let parent = config
        .state_root
        .canonicalize()?
        .join("repository-downloads");
    if !parent.exists() {
        openworker_runtime::resource_policy::checked_path(
            &parent.parent().unwrap().canonicalize()?,
        )?;
        std::fs::create_dir(&parent)?;
    }
    openworker_runtime::resource_policy::checked_path(&parent)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&parent, std::fs::Permissions::from_mode(0o700))?;
    }
    let path = parent.join(format!("{}-{}.bundle", job.intent.request.id, job.claim));
    let mut file = tokio::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .await?;
    let spool = Spool(path);
    let mut count = 0u64;
    while let Some(next) = bytes.next().await {
        ensure!(
            !stop.is_cancelled(),
            "Publication artifact transfer cancelled"
        );
        store.check_team_publication(job)?;
        let data = next?;
        count = count
            .checked_add(data.len() as u64)
            .context("Artifact byte count overflow")?;
        ensure!(
            count <= artifact.registration.expected.size,
            "Artifact stream exceeds original size"
        );
        file.write_all(&data).await?;
    }
    ensure!(
        count == artifact.registration.expected.size,
        "Artifact stream truncated"
    );
    file.flush().await?;
    file.sync_all().await?;
    drop(file);
    Ok(spool)
}
pub(crate) async fn prepare_once(
    config: &Config,
    store: &PostgresStore,
    installed: &str,
    artifacts: &openworker_artifacts::Store,
    stop: &CancellationToken,
) -> Result<bool> {
    let Some(job) = store.claim_team_publication(installed)? else {
        return Ok(false);
    };
    let result = tokio::time::timeout(std::time::Duration::from_secs(150), async {
        store.check_team_publication(&job)?;
        let grant = config
            .repository_grants
            .get(&job.intent.request.grant)
            .context("Original repository grant unavailable")?;
        ensure!(
            grant.digest()? == job.intent.request.grant_digest,
            "Original repository grant changed"
        );
        let broker = config.repository_broker(grant)?;
        let spool = if job.dispatch {
            Some(download(config, store, &job, artifacts, stop).await?)
        } else {
            None
        };
        let absent = config.state_root.join("inspection-only-no-artifact");
        let path = spool
            .as_ref()
            .map(|s| s.0.as_path())
            .unwrap_or(absent.as_path());
        let outcome = broker
            .publish_checked(
                job.intent.publication.clone(),
                path,
                job.dispatch,
                openworker_runtime::store::now_ms(),
                stop,
                || store.check_team_publication(&job),
            )
            .await?;
        store.complete_team_publication(&job, outcome)
    })
    .await;
    if !matches!(result, Ok(Ok(()))) {
        if let Err(error) = store.defer_team_publication(&job) {
            if error
                .downcast_ref::<openworker_store_postgres::StorageUnavailable>()
                .is_some()
            {
                return Err(error);
            }
        }
    }
    Ok(true)
}
