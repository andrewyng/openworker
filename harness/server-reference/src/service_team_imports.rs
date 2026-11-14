//! Bounded private import I/O. Artifact keys/credentials never enter a sandbox.
use crate::{management, Config};
use anyhow::{ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use futures_util::TryStreamExt;
use openworker_runtime::{
    coding_handoff::transfer::{Receipt as Checkout, Status, CHUNK},
    environment::{Connection, Provider},
    team_workspace::Receipt,
};
use openworker_store_postgres::{service_team_imports::Job, PostgresStore};
use serde_json::json;
use tokio::io::AsyncReadExt;
use tokio_util::sync::CancellationToken;

pub(super) async fn import(
    store: &PostgresStore,
    job: &mut Job,
    backend: &openworker_artifacts::Store,
    connection: &mut dyn Connection,
    stop: &CancellationToken,
) -> Result<Receipt> {
    let transfer = &job.intent.transfer;
    let original = job.clone();
    let dispatched = std::sync::atomic::AtomicBool::new(job.checkout_dispatched);
    let current = || {
        let mut j = original.clone();
        j.checkout_dispatched = dispatched.load(std::sync::atomic::Ordering::Relaxed);
        j
    };
    let result = self::transfer(
        backend,
        connection,
        transfer,
        &job.intent.artifact,
        job.prepare,
        job.checkout_dispatched,
        stop,
        || store.check_team_import(&current()),
        || {
            store.dispatch_team_import_checkout(&current())?;
            dispatched.store(true, std::sync::atomic::Ordering::Relaxed);
            Ok(())
        },
    )
    .await;
    job.checkout_dispatched = dispatched.load(std::sync::atomic::Ordering::Relaxed);
    result?;
    let h = transfer.handle();
    store.check_team_import(job)?;
    let receipt: Receipt = serde_json::from_value(
        connection
            .request(
                "handoff/workspace",
                json!({"handle":h,"request":job.intent.workspace}),
            )
            .await?,
    )?;
    let verified: Receipt = serde_json::from_value(
        connection
            .request("workspace/verify", json!(receipt))
            .await?,
    )?;
    ensure!(
        receipt == verified,
        "Original imported workspace changed before admission"
    );
    Ok(receipt)
}
/// Shared trusted transfer, used for child admission and app-owned lead setup.
/// Preparation and checkout dispatch bits are durably fenced by the caller.
#[allow(clippy::too_many_arguments)] // One shared protocol with explicit caller-owned authority/dispatch fences.
pub(super) async fn transfer(
    backend: &openworker_artifacts::Store,
    connection: &mut dyn Connection,
    transfer: &openworker_runtime::coding_handoff::transfer::Transfer,
    artifact: &openworker_artifacts::Artifact,
    prepare: bool,
    checkout_dispatched: bool,
    stop: &CancellationToken,
    check: impl Fn() -> Result<()>,
    dispatch: impl Fn() -> Result<()>,
) -> Result<Checkout> {
    let h = transfer.handle();
    if !checkout_dispatched {
        let status: Status = serde_json::from_value(
            connection
                .request(
                    if prepare {
                        "handoff/prepare"
                    } else {
                        "handoff/inspect"
                    },
                    if prepare { json!(transfer) } else { json!(h) },
                )
                .await?,
        )?;
        ensure!(
            status.handle == h
                && status.size == transfer.bundle.size
                && status.received <= status.size
                && (status.received == status.size || status.received.is_multiple_of(CHUNK as u64))
                && !status.checkout_dispatched
                && status.receipt.is_none(),
            "Original import preparation changed"
        );
        let proof = artifact
            .proof
            .as_ref()
            .context("Original artifact is not verified")?;
        let stream = backend.get(artifact, proof, stop.clone()).await?;
        let mut reader = tokio_util::io::StreamReader::new(stream.map_err(std::io::Error::other));
        let mut offset = 0;
        let mut bytes = vec![0; CHUNK];
        while offset < status.size {
            ensure!(!stop.is_cancelled(), "Child source transfer cancelled");
            check()?;
            let n = ((status.size - offset).min(CHUNK as u64)) as usize;
            reader.read_exact(&mut bytes[..n]).await?;
            if offset >= status.received {
                let next: Status = serde_json::from_value(
                    connection
                        .request(
                            "handoff/write",
                            json!({"handle":h,"offset":offset,"data":STANDARD.encode(&bytes[..n])}),
                        )
                        .await?,
                )?;
                ensure!(
                    next.handle == h
                        && next.size == status.size
                        && next.received == offset + n as u64
                        && !next.checkout_dispatched
                        && next.receipt.is_none(),
                    "Original chunk acknowledgement changed"
                );
            }
            offset += n as u64;
        }
        // Consume EOF so the artifact stream verifies its complete digest even
        // when all remote chunks already existed after a lost acknowledgement.
        ensure!(
            reader.read(&mut bytes[..1]).await? == 0,
            "Artifact exceeds original size"
        );
        dispatch()?;
        Ok(serde_json::from_value(
            connection.request("handoff/finish", json!(h)).await?,
        )?)
    } else {
        // An unknown checkout is conveyed through the durable lead inbox. Never
        // call finish again or remove/adopt a directory because a reply was lost.
        check()?;
        Ok(serde_json::from_value(
            connection
                .request("handoff/inspect_checkout", json!(h))
                .await?,
        )?)
    }
}

pub(crate) async fn prepare_once(
    config: &Config,
    store: &PostgresStore,
    installation: &str,
    backend: &openworker_artifacts::Store,
    stop: &CancellationToken,
) -> Result<bool> {
    let Some(mut job) = (match store.claim_team_import(installation)? {
        Some(job) => Some(job),
        None => store.claim_team_reception(installation)?,
    }) else {
        return Ok(false);
    };
    let outcome = tokio::time::timeout(std::time::Duration::from_secs(150), async {
        store.check_team_import(&job)?;
        ensure!(
            backend.binding() == &job.intent.artifact.binding,
            "Original artifact store binding changed"
        );
        let profile = store.catalog_get("profile", &job.intent.profile_id)?;
        ensure!(
            profile.digest == job.intent.profile_digest,
            "Original import profile changed"
        );
        let provider = management::provider(config, &profile)?;
        let mut connection = provider
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
            .await?;
        let result = import(store, &mut job, backend, &mut *connection, stop).await;
        // Closing a channel is cleanup, not proof checkout did not take effect.
        let _ = connection.close().await;
        let receipt = result?;
        store.complete_team_import(&job, receipt)
    })
    .await;
    if !matches!(outcome, Ok(Ok(()))) {
        if let Err(e) = store.defer_team_import(&job) {
            if e.downcast_ref::<openworker_store_postgres::StorageUnavailable>()
                .is_some()
            {
                return Err(e);
            }
        }
    }
    Ok(true)
}
