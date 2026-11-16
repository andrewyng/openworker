//! Trusted source staging through the existing artifact store and ToolRunner.
//! Storage credentials stay here; sandboxes see neither URLs nor object keys.
use crate::{management, Config};
use anyhow::{ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use openworker_runtime::{
    coding_handoff::{git::Bundle, transfer::CHUNK},
    environment::{Connection, Provider},
};
use openworker_store_postgres::{service_team_sources::Job, PostgresStore};
use serde_json::json;
use tokio::io::AsyncWriteExt;
use tokio_util::sync::CancellationToken;

async fn open(config: &Config, store: &PostgresStore, job: &Job) -> Result<Box<dyn Connection>> {
    store.check_team_source(job)?;
    let profile = store.catalog_get("profile", &job.intent.profile_id)?;
    ensure!(
        profile.digest == job.intent.profile_digest,
        "Original source profile changed"
    );
    let provider = management::provider(config, &profile)?;
    provider
        .attach(
            job.intent.environment.clone(),
            job.claim.clone(),
            vec![
                "/opt/openworker/openworker".into(),
                "workspace-stdio".into(),
                "--root".into(),
                job.intent.root.clone(),
            ],
        )
        .await
}
pub(crate) async fn prepare_once(
    config: &Config,
    store: &PostgresStore,
    installed: &str,
    artifacts: Option<&openworker_artifacts::Store>,
    stop: &CancellationToken,
) -> Result<bool> {
    let Some(job) = store.claim_team_source(installed)? else {
        return Ok(false);
    };
    let result = tokio::time::timeout(std::time::Duration::from_secs(150), async {
        let backend = artifacts.context("Source handoff requires configured artifacts")?;
        ensure!(
            backend.binding() == &job.intent.artifact_binding,
            "Original source artifact binding changed"
        );
        if job.intent.source.remote_ref.is_some() {
            #[cfg(feature = "trusted-git")]
            {
                return remote(config, store, &job, backend, stop).await;
            }
            #[cfg(not(feature = "trusted-git"))]
            anyhow::bail!("Remote source requires trusted-git service support");
        }
        let mut connection = open(config, store, &job).await?;
        let result = async {
            if job.intent.bundle.is_none() {
                let method = if job.dispatch {
                    "handoff/export"
                } else {
                    "handoff/inspect_export"
                };
                let reply = connection
                    .request(method, json!({"request":job.intent.export}))
                    .await?;
                let bundle: Bundle = serde_json::from_value(reply)?;
                store.observe_team_source(&job, Some(bundle))?;
            } else {
                upload(store, &job, backend, &mut *connection, stop).await?;
            }
            Ok::<_, anyhow::Error>(())
        }
        .await;
        // Close is cleanup, not evidence a previously dispatched export failed.
        let _ = connection.close().await;
        result
    })
    .await;
    if !matches!(result, Ok(Ok(()))) {
        // The original claim can already have expired or authority been revoked.
        // No replacement export/identity is created when recording that fact.
        if let Err(error) = store.defer_team_source(&job) {
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

#[cfg(feature = "trusted-git")]
async fn remote(
    config: &Config,
    store: &PostgresStore,
    job: &Job,
    backend: &openworker_artifacts::Store,
    stop: &CancellationToken,
) -> Result<()> {
    use tokio::io::AsyncReadExt;
    store.check_team_source(job)?;
    let access = job
        .intent
        .repository_access
        .as_ref()
        .context("Original source grant missing")?;
    let grant = config
        .repository_grants
        .get(&access.grant)
        .context("Original source grant unavailable")?;
    ensure!(
        grant.digest()? == access.digest,
        "Original source grant changed"
    );
    let broker = config.repository_broker(grant)?;
    let original = openworker_git_broker::Source {
        id: job.intent.id.clone(),
        grant_digest: access.digest.clone(),
        reference: job.intent.source.remote_ref.clone().unwrap(),
        export: job.intent.export.clone(),
    };
    // After lost export acknowledgement inspect only the original trusted cache.
    // Losing that cache cannot silently regenerate different artifact bytes.
    grant.live(openworker_runtime::store::now_ms())?;
    let bundle = if let Some(bundle) = &job.intent.bundle {
        bundle.clone()
    } else {
        let bundle = broker
            .stage(
                original.clone(),
                job.dispatch,
                openworker_runtime::store::now_ms(),
                stop,
            )
            .await?;
        return store.observe_team_source(job, Some(bundle));
    };
    store.check_team_source(job)?;
    let artifact = store.register_team_source_artifact(job)?;
    let proof = match backend.verify(&artifact, stop).await {
        Ok(proof) => proof,
        Err(e)
            if e.downcast_ref::<openworker_artifacts::Error>()
                == Some(&openworker_artifacts::Error::NotFound) =>
        {
            let inspected = broker
                .stage(original, false, openworker_runtime::store::now_ms(), stop)
                .await?;
            ensure!(
                inspected == bundle,
                "Original staged source receipt changed"
            );
            let mut file =
                tokio::fs::File::open(broker.exported_path(&job.intent.id, &bundle)?).await?;
            let (input, mut output) = tokio::io::duplex(CHUNK * 2);
            let copy = async {
                let mut bytes = vec![0; CHUNK];
                let mut count = 0u64;
                loop {
                    store.check_team_source(job)?;
                    ensure!(!stop.is_cancelled(), "Source upload cancelled");
                    let n = file.read(&mut bytes).await?;
                    if n == 0 {
                        break;
                    }
                    count += n as u64;
                    ensure!(count <= bundle.size, "Source bytes changed");
                    output.write_all(&bytes[..n]).await?;
                }
                ensure!(count == bundle.size, "Source bytes truncated");
                output.shutdown().await?;
                Ok::<_, anyhow::Error>(())
            };
            let (_, proof) = tokio::try_join!(copy, backend.put(&artifact, input, stop))?;
            proof
        }
        Err(e) => return Err(e),
    };
    let artifact = store.publish_team_source_artifact(job, &artifact.id, proof)?;
    let (transfer, _, prepare, checkout) = store.prepare_team_source_checkout(job, &artifact.id)?;
    let mut connection = open(config, store, job).await?;
    let result = crate::service_team_imports::transfer(
        backend,
        &mut *connection,
        &transfer,
        &artifact,
        prepare,
        checkout,
        stop,
        || store.check_team_source(job),
        || store.dispatch_team_source_checkout(job),
    )
    .await;
    let _ = connection.close().await;
    store.observe_team_source_checkout(job, &artifact.id, result?)?;
    store.complete_team_source(job, &artifact.id)
}
async fn upload(
    store: &PostgresStore,
    job: &Job,
    backend: &openworker_artifacts::Store,
    connection: &mut dyn Connection,
    stop: &CancellationToken,
) -> Result<()> {
    let bundle = job
        .intent
        .bundle
        .as_ref()
        .context("Missing original export receipt")?;
    let a = store.register_team_source_artifact(job)?;
    let proof = upload_bundle(backend, connection, bundle, &a, stop, || {
        store.check_team_source(job)
    })
    .await?;
    // Recheck original credential, source/profile, claim and artifact bytes
    // before the catalog can acknowledge publication. No fake tool turn.
    let a = store.publish_team_source_artifact(job, &a.id, proof)?;
    store.complete_team_source(job, &a.id)
}

/// Shared immutable byte transfer; authority is checked for every original chunk.
pub(crate) async fn upload_bundle(
    backend: &openworker_artifacts::Store,
    connection: &mut dyn Connection,
    bundle: &Bundle,
    a: &openworker_artifacts::Artifact,
    stop: &CancellationToken,
    check: impl Fn() -> Result<()>,
) -> Result<openworker_artifacts::Proof> {
    let proof = match backend.verify(a, stop).await {
        Ok(proof) => proof,
        Err(e)
            if e.downcast_ref::<openworker_artifacts::Error>()
                == Some(&openworker_artifacts::Error::NotFound) =>
        {
            let (input, mut output) = tokio::io::duplex(CHUNK * 2);
            let copy = async {
                let mut offset = 0;
                while offset < bundle.size {
                    ensure!(!stop.is_cancelled(), "Source transfer cancelled");
                    check()?;
                    let reply = connection
                        .request("handoff/read", json!({"bundle":bundle,"offset":offset}))
                        .await?;
                    ensure!(
                        reply["offset"].as_u64() == Some(offset),
                        "Changed source chunk offset"
                    );
                    let text = reply["data"].as_str().context("Missing source bytes")?;
                    ensure!(
                        text.len() <= CHUNK.div_ceil(3) * 4,
                        "Source frame exceeds limit"
                    );
                    let bytes = STANDARD.decode(text)?;
                    ensure!(
                        bytes.len() as u64 == (bundle.size - offset).min(CHUNK as u64),
                        "Source chunk has unexpected length"
                    );
                    output.write_all(&bytes).await?;
                    offset += bytes.len() as u64;
                }
                output.shutdown().await?;
                Ok::<_, anyhow::Error>(())
            };
            let (_, proof) = tokio::try_join!(copy, backend.put(a, input, stop))?;
            proof
        }
        Err(e) => return Err(e),
    };
    Ok(proof)
}
