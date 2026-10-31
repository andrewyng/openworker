//! Compile approved team-private definitions for the existing service workers.
//! This adapter does not run models, tools or a second agent loop in the API.
use crate::{management, Config};
use anyhow::{ensure, Result};
use openworker_runtime::{
    bundle::{digest, Bundle},
    connections::Choice,
    team_definition::{Delegation, Placement},
    team_type_catalog::{Record, Status},
};
use openworker_store_postgres::{service_team_types::Team, PostgresStore};
use serde_json::json;

pub(crate) fn validate_delegation(d: &Delegation, source: &Bundle) -> Result<()> {
    ensure!(
        d.placements == std::collections::BTreeSet::from([Placement::Team])
            && d.profiles.is_empty(),
        "This service adapter currently supports the team's original environment only"
    );
    source.tools.select(&d.tools)?;
    Ok(())
}
/// Content-addressed private bundle; source launchers and policy come from the
/// activated lead release. Model-controlled text cannot inject launcher settings.
pub(crate) fn config(config: &Config, team: &Team, record: &Record) -> Result<Config> {
    let catalog = team
        .document
        .type_catalog
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("Missing scoped catalog"))?;
    catalog.check_scope(&team.lead)?;
    ensure!(record.status == Status::Approved, "Unapproved type");
    let stored = catalog
        .records
        .values()
        .flatten()
        .find(|r| r.binding.digest() == record.binding.digest() && r.status == Status::Approved)
        .ok_or_else(|| anyhow::anyhow!("Missing approved revision"))?;
    stored
        .binding
        .revalidate(&catalog.ceiling, &catalog.prebuilt)?;
    ensure!(
        team.release.activated && team.profile.activated,
        "Source not activated"
    );
    let mut c = management::release_config(config, &team.release, &team.profile)?;
    let source = Bundle::load(&c.bundle)?;
    ensure!(
        serde_json::to_value(&source.manifest)? == serde_json::to_value(&team.lead.package)?,
        "Original lead package changed"
    );
    validate_delegation(
        &Delegation {
            tools: catalog.ceiling.tools.clone(),
            models: catalog.ceiling.models.clone(),
            placements: catalog.ceiling.placements.clone(),
            profiles: catalog.ceiling.profiles.clone(),
        },
        &source,
    )?;
    let def = stored.binding.definition();
    ensure!(
        matches!(
            def.sandbox,
            openworker_runtime::team_definition::Sandbox::Team {}
        ),
        "Unsupported placement"
    );
    let tools = source.tools.select(&def.tools)?;
    // Current native bundles require at least one concrete tool. Reject this
    // adapter gap explicitly rather than silently giving the child extra tools.
    ensure!(
        !tools.bindings.is_empty(),
        "Tool-free scoped service types are not supported by this bundle format"
    );
    let mut manifest = team.lead.package.clone();
    manifest.id = stored.runtime_id();
    manifest.name = def.name.clone();
    manifest.version = stored.binding.digest().into();
    manifest.description.clear();
    manifest.tagline.clear();
    manifest.icon.clear();
    manifest.planning = false;
    manifest.memory = false;
    manifest.memory_scope = None;
    manifest.scheduling = false;
    manifest.team_role = None;
    manifest.default_model = def.model.clone().or_else(|| {
        team.lead
            .model_selection
            .as_ref()
            .map(|m| Choice::new(&m.connection, &m.model))
    });
    let text = format!(
        "---\n{}\n---\n{}\n",
        serde_json::to_string(&manifest)?,
        def.instructions
    );
    let key = digest(&serde_json::to_vec(&(
        "service-team-type-v1",
        stored.binding.digest(),
        &team.release.digest,
        &team.profile.digest,
        &team.lead.bundle_digest,
    ))?);
    c.bundle = management::publish_bundle(
        config,
        &key,
        &source,
        &text,
        &serde_json::to_string(&tools)?,
    )?;
    Ok(c)
}
/// Bounded pages allow other service teams to progress. Restart repeats only
/// deterministic compilation; immutable templates absorb competing controllers.
pub(crate) async fn refresh(
    config: &Config,
    store: &PostgresStore,
    installed: &str,
    after: &mut String,
) -> Result<()> {
    let page = store.service_team_type_page(installed, after, 8)?;
    if page.is_empty() {
        after.clear();
        return Ok(());
    }
    for team in page {
        *after = team.document.id.clone();
        let Some(catalog) = &team.document.type_catalog else {
            continue;
        };
        for record in catalog
            .records
            .values()
            .flatten()
            .filter(|r| r.status == Status::Approved)
        {
            if store.service_team_type_installed(&team.document.id, &record.runtime_id())? {
                continue;
            }
            let result = prepare(config, store, &team, record).await;
            match result {
                Ok(session) => {
                    if store
                        .install_service_team_type(&team, record, session)
                        .is_err()
                    {
                        // The source may have been revoked while compiling. A
                        // stale preparation is failure, never fresh authority.
                        store.reject_service_team_type(&team, record)?;
                    }
                }
                Err(_) => store.reject_service_team_type(&team, record)?,
            }
        }
    }
    Ok(())
}
async fn prepare(
    config: &Config,
    store: &PostgresStore,
    team: &Team,
    record: &Record,
) -> Result<openworker_runtime::store::Session> {
    let source = management::release_config(config, &team.release, &team.profile)?;
    let rt = source.api_runtime(
        &format!("team-source-{}", &digest(team.lead.id.as_bytes())[..16]),
        store.clone(),
    )?;
    let matches = rt.execution_digest() == team.lead.bundle_digest;
    rt.shutdown().await?;
    ensure!(matches, "Original lead execution identity changed");
    let c = self::config(config, team, record)?;
    let rt = c.api_runtime(
        &format!("team-type-{}", &record.binding.digest()[..20]),
        store.clone(),
    )?;
    let mut args = json!({"coworker":record.runtime_id(),"name":record.binding.definition().name,"workspace":team.lead.workspace,"environment_id":team.lead.environment.as_ref().map(|e|&e.environment_id),"permissions":team.lead.permissions,"compaction":team.lead.compaction_config});
    if let Some(m) = &team.lead.reviewer_selection {
        args["reviewer_model"] = json!(Choice::new(&m.connection, &m.model))
    }
    let result = rt.prepare_session(args);
    rt.shutdown().await?;
    result
}

#[cfg(all(test, unix))]
#[path = "service_team_types_tests.rs"]
mod tests;
