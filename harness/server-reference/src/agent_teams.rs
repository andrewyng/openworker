//! First-class teams use the same approved releases and original shared environment.
use crate::{
    http::{ApiError, App},
    management, service,
};
use anyhow::{ensure, Context, Result};
use axum::{extract::Path, http::HeaderMap, Extension, Json};
use openworker_store_postgres::service_catalog::{Profile, Release};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Create {
    #[serde(default = "lead")]
    agent_id: String,
    agent_types: BTreeMap<String, String>,
    #[serde(default)]
    policy: openworker_runtime::team_coordination::Policy,
    limits: Option<openworker_runtime::budget::Limits>,
}
fn lead() -> String {
    "lead".into()
}
pub(crate) async fn create(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    Json(body): Json<Create>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(openworker_runtime::agent_team::public_snapshot(
        create_inner(app, sid, body).await?,
    )))
}
async fn create_inner(app: App, sid: String, body: Create) -> Result<Value> {
    ensure!(
        (1..=32).contains(&body.agent_types.len()),
        "Register 1..32 team types"
    );
    let snapshot = app.store.read_snapshot(&sid)?;
    let identity = app
        .store
        .service_session_identity(&sid)?
        .context("Team lead must be an admitted service session")?;
    let profile_id = identity["runtimeProfile"]
        .as_str()
        .context("Missing profile")?;
    let profile = app.store.catalog_get("profile", profile_id)?;
    let p: Profile = serde_json::from_value(profile.definition.clone())?;
    ensure!(
        profile.activated && p.sharing == openworker_runtime::environment::Sharing::Shared,
        "Teams require an activated shared RuntimeProfile"
    );
    let mut templates = BTreeMap::new();
    for (kind, release_id) in &body.agent_types {
        let entry = app.store.catalog_get("release", release_id)?;
        ensure!(entry.activated, "Child release must be activated");
        let release: Release = serde_json::from_value(entry.definition.clone())?;
        let config = management::release_config(&app.config, &entry, &profile)?;
        let rt = config.api_runtime(
            &format!(
                "team-template-{}",
                &openworker_runtime::bundle::digest(kind.as_bytes())[..16]
            ),
            app.store.clone(),
        )?;
        let mut parameters = json!({"coworker":rt.coworker(),"workspace":snapshot["session"]["workspace"],"environment_id":snapshot["session"]["environment"]["environment_id"],"name":kind,"permissions":{"mode":p.approval_mode,"auto_skip_questions":p.auto_skip_questions}});
        if let Some(model) = release.default_model {
            parameters["model"] = json!(model);
        }
        if let Some(model) = release.reviewer_model {
            parameters["reviewer_model"] = json!(model);
        }
        let template = rt.prepare_session(parameters)?;
        rt.shutdown().await?;
        templates.insert(kind.clone(), (release_id.clone(), template));
    }
    let keys: Vec<_> = body.agent_types.keys().cloned().collect();
    app.store.configure_agent_team(&sid,json!({"agent_id":body.agent_id,"agent_types":keys,"policy":body.policy,"limits":body.limits}),templates)
}
pub(crate) async fn snapshot(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.agent_team_api(
        &sid,
        "snapshot",
        json!({}),
    )?))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Command {
    operation: String,
    arguments: Value,
}
pub(crate) async fn command(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    Json(body): Json<Command>,
) -> Result<Json<Value>, ApiError> {
    let result = if body.operation == "team_manage" && body.arguments["action"] == "list" {
        app.store
            .agent_team_api(&sid, "team_manage", body.arguments)?
    } else if body.operation == "team_read_board" {
        app.store
            .agent_team_api(&sid, "team_read_board", body.arguments)?
    } else {
        let key = service::key(&app, &headers, &format!("team-{sid}"))?;
        app.store.agent_team_api(
            &sid,
            "command",
            json!({"request_id":key,"operation":body.operation,"arguments":body.arguments}),
        )?
    };
    Ok(Json(result))
}

pub(crate) async fn history(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    axum::extract::Query(query): axum::extract::Query<
        openworker_runtime::team_coordination::HistoryQuery,
    >,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.agent_team_api(
        &sid,
        "team_read_board",
        serde_json::to_value(query).map_err(anyhow::Error::from)?,
    )?))
}
