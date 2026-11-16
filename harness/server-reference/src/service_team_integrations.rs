//! Bounded original-environment integration; no cloud/GitHub credential needed.
use crate::{management, Config};
use anyhow::{ensure, Result};
use openworker_runtime::{coding_handoff::integrate::Observation, environment::Provider};
use openworker_store_postgres::PostgresStore;
use serde_json::json;
pub(crate) async fn prepare_once(
    config: &Config,
    store: &PostgresStore,
    installation: &str,
) -> Result<bool> {
    let Some(job) = store.claim_team_integration(installation)? else {
        return Ok(false);
    };
    let result = tokio::time::timeout(std::time::Duration::from_secs(150), async {
        store.check_team_integration(&job)?;
        let profile = store.catalog_get("profile", &job.intent.profile_id)?;
        ensure!(
            profile.digest == job.intent.profile_digest,
            "Original integration profile changed"
        );
        let provider = management::provider(config, &profile)?;
        let mut channel = provider
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
        let reply = channel
            .request(
                if job.dispatch {
                    "handoff/integrate"
                } else {
                    "handoff/inspect_integration"
                },
                json!(job.intent.request),
            )
            .await;
        let _ = channel.close().await;
        let outcome: Observation = serde_json::from_value(reply?)?;
        store.complete_team_integration(&job, outcome)
    })
    .await;
    if !matches!(result, Ok(Ok(()))) {
        if let Err(e) = store.defer_team_integration(&job) {
            if e.downcast_ref::<openworker_store_postgres::StorageUnavailable>()
                .is_some()
            {
                return Err(e);
            }
        }
    }
    Ok(true)
}
