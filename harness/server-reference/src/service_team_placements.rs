//! Trusted gateway integration, outside every agent sandbox. The database
//! outbox elects one create; replacement controllers inspect the original only.
use crate::{management, Config};
use anyhow::{Context, Result};
use openworker_runtime::{
    environment::{Management, Provider},
    team_coordination::Phase,
    team_definition::Sandbox,
};
use openworker_store_postgres::{service_catalog::Profile, PostgresStore};

pub(crate) async fn refresh(
    config: &Config,
    store: &PostgresStore,
    installed: &str,
    after: &mut String,
) -> Result<()> {
    let page = store.service_team_type_page(installed, after, 8)?;
    if page.is_empty() {
        after.clear();
    }
    for team in &page {
        *after = team.document.id.clone();
        if !team.document.placement_selection {
            continue;
        }
        for member in team
            .document
            .roster
            .instances()
            .filter(|i| i.phase == Phase::Reserved)
        {
            if let Some(Sandbox::New { profile, .. }) =
                team.document.sandbox_choices.get(&member.agent_id)
            {
                let entry = store.catalog_get("profile", profile)?;
                let p: Profile = serde_json::from_value(entry.definition.clone())?;
                let provider = management::provider(config, &entry)?;
                let result = store.reserve_team_environment(
                    &team.lead.id,
                    &member.agent_id,
                    entry,
                    provider.spec(p.sharing, Management::Harness),
                );
                if let Err(error) = result {
                    if error
                        .downcast_ref::<openworker_store_postgres::SubmissionError>()
                        .is_some()
                    {
                        continue;
                    }
                    // Configuration/authority failure is explicit. Never allocate
                    // using another profile or execute in the lead as fallback.
                    if error
                        .downcast_ref::<openworker_store_postgres::StorageUnavailable>()
                        .is_some()
                    {
                        return Err(error);
                    }
                    store.reject_team_placement(&team.lead.id, &member.agent_id)?;
                }
            }
        }
    }
    if let Some(job) = store.claim_team_environment(installed)? {
        let entry = store.catalog_get("profile", &job.intent.profile_id)?;
        anyhow::ensure!(
            entry.digest == job.intent.profile_digest,
            "Original profile changed"
        );
        let provider = management::provider(config, &entry)?;
        let observed = if job.dispatch {
            provider.create(job.environment.clone()).await
        } else {
            let raw = job
                .environment
                .id
                .strip_prefix("env_")
                .context("Invalid original environment ID")?;
            provider
                .bind_existing(&job.environment, &format!("owr-{}", &raw[..15]))
                .await
        };
        // A lost reply becomes an explicit unknown observation, never another
        // create. The store atomically fences stale controllers and emits notice.
        store.observe_team_environment(&job, observed.ok())?;
    }
    for team in &page {
        if !team.document.placement_selection {
            continue;
        }
        for member in team
            .document
            .roster
            .instances()
            .filter(|i| i.phase == Phase::Reserved)
        {
            if let Err(error) = store.bind_team_placement(&team.lead.id, &member.agent_id) {
                if error
                    .downcast_ref::<openworker_store_postgres::StorageUnavailable>()
                    .is_some()
                {
                    return Err(error);
                }
                store.reject_team_placement(&team.lead.id, &member.agent_id)?;
            }
        }
    }
    Ok(())
}
