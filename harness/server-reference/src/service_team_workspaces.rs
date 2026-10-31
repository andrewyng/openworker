//! Trusted controller prepares in the ORIGINAL sandbox, outside model execution.
//! A lost response is not permission to repeat worktree creation. The durable
//! store grants first dispatch once; later claims inspect the original marker.
use crate::{management, Config};
use anyhow::{Context, Result};
use openworker_runtime::{environment::Provider, team_workspace::Receipt};
use openworker_store_postgres::PostgresStore;
use serde_json::json;

pub(crate) async fn prepare_once(
    config: &Config,
    store: &PostgresStore,
    installed: &str,
) -> Result<bool> {
    let Some(job) = store.claim_team_workspace(installed)? else {
        return Ok(false);
    };
    let outcome: Result<Receipt> = async {
        let binding = job
            .lead
            .environment
            .clone()
            .context("Workspace has no original environment")?;
        let identity = store
            .service_session_identity(&job.lead.id)?
            .context("Missing admitted lead")?;
        let profile = store.catalog_get(
            "profile",
            identity["runtimeProfile"]
                .as_str()
                .context("Missing profile")?,
        )?;
        let provider = management::provider(config, &profile)?;
        let mut connection = provider
            .attach(
                binding,
                job.claim.clone(),
                vec![
                    "/opt/openworker/openworker".into(),
                    "workspace-stdio".into(),
                    "--root".into(),
                    job.lead.workspace.clone(),
                ],
            )
            .await?;
        let (method, params) = if let Some(receipt) = &job.intent.receipt {
            ("workspace/verify", json!(receipt))
        } else {
            (
                if job.dispatch {
                    "workspace/prepare"
                } else {
                    "workspace/inspect"
                },
                json!(job.intent.request),
            )
        };
        let result = tokio::time::timeout(
            std::time::Duration::from_secs(20),
            connection.request(method, params),
        )
        .await;
        // Closing the channel is cleanup, not proof the filesystem action failed.
        let _ = connection.close().await;
        let receipt: Receipt = serde_json::from_value(
            result
                .context("Workspace response lost; inspect original operation before retrying")??,
        )?;
        anyhow::ensure!(
            receipt.request == job.intent.request,
            "Workspace response belongs to another operation"
        );
        receipt.validate_scope(std::path::Path::new(&job.lead.workspace), &receipt.path)?;
        Ok(receipt)
    }
    .await;
    store.finish_team_workspace(job,outcome.map_err(|_|"Workspace preparation stopped; outcome may be unknown. Inspect the original operation and sandbox before choosing a new action. No creation is automatically retried.".into()))?;
    Ok(true)
}
