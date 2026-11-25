use crate::http::{ApiError, App};
use axum::{extract::Path, Extension, Json};
use openworker_runtime::workspaces::{
    GetOrCreateWorkspace, ProviderOptions, Registry, WorkspaceClient,
};
use openworker_store_postgres::{
    auth::{Action, Error as AuthError, Role, Scope},
    SubmissionError,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

#[derive(Clone)]
struct Handle {
    actor: String,
    name: String,
    client: Arc<dyn WorkspaceClient>,
    used: Instant,
}
#[derive(Default)]
pub(crate) struct Handles(Mutex<HashMap<String, Handle>>);

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Start {
    name: String,
    #[serde(default)]
    options: ProviderOptions,
}

fn check(app: &App, name: &str) -> Result<(), ApiError> {
    let actor = app.store.authorize(Action::Submit, None, None)?;
    if matches!(actor.scope, Scope::Sessions { .. })
        || !(actor.role == Role::Administrator
            || app
                .config
                .workspace_grants
                .get(name)
                .is_some_and(|principals| principals.contains(&actor.principal)))
    {
        return Err(ApiError(AuthError::Forbidden.into()));
    }
    Ok(())
}
pub(crate) async fn validate(app: &App, reference: &str) -> Result<(), ApiError> {
    let registry = &app.runtime.workspaces;
    let name = registry
        .registration(reference)
        .map_err(|_| ApiError(SubmissionError::InvalidRequest.into()))?;
    check(app, name)?;
    registry.recover(reference).await?;
    Ok(())
}
pub(crate) async fn start(
    Extension(app): Extension<App>,
    Json(input): Json<Start>,
) -> Result<Json<Value>, ApiError> {
    check(&app, &input.name)?;
    let provider = app
        .runtime
        .workspaces
        .provider(&input.name)
        .map_err(|_| ApiError(SubmissionError::NotFound.into()))?;
    let client = provider.start(input.options).await?;
    let id = openworker_runtime::id("wclient");
    let mut handles = app.workspace_handles.0.lock().unwrap();
    handles.retain(|_, h| h.used.elapsed() < Duration::from_secs(3600));
    if handles.len() >= 4096 || handles.values().filter(|h| h.actor == app.actor).count() >= 256 {
        return Err(ApiError(SubmissionError::QueueFull.into()));
    }
    handles.insert(
        id.clone(),
        Handle {
            actor: app.actor,
            name: input.name,
            client,
            used: Instant::now(),
        },
    );
    Ok(Json(json!({"id":id})))
}
pub(crate) async fn open(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Json(request): Json<GetOrCreateWorkspace>,
) -> Result<impl axum::response::IntoResponse, ApiError> {
    let handle = lookup(&app, &id)?;
    let result = Registry::open(&handle.name, handle.client.as_ref(), request).await?;
    Ok((
        [(axum::http::header::CACHE_CONTROL, "private, no-store")],
        Json(result),
    ))
}

fn lookup(app: &App, id: &str) -> Result<Handle, ApiError> {
    let handle = {
        let mut map = app.workspace_handles.0.lock().unwrap();
        let h = map
            .get_mut(id)
            .filter(|h| h.actor == app.actor && h.used.elapsed() < Duration::from_secs(3600))
            .ok_or_else(|| {
                ApiError(openworker_runtime::workspaces::WorkspaceError::HandleExpired.into())
            })?;
        h.used = Instant::now();
        h.clone()
    };
    check(app, &handle.name)?;
    Ok(handle)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Stop {
    workspace_id: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Delete {
    data_id: String,
}

pub(crate) async fn stop(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Json(req): Json<Stop>,
) -> Result<Json<Value>, ApiError> {
    let handle = lookup(&app, &id)?;
    handle
        .client
        .stop_workspace(Registry::local_id(&handle.name, &req.workspace_id)?.into())
        .await?;
    Ok(Json(json!({"stopped":true})))
}
pub(crate) async fn delete_data(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Json(req): Json<Delete>,
) -> Result<Json<Value>, ApiError> {
    let handle = lookup(&app, &id)?;
    handle
        .client
        .delete_data(Registry::local_id(&handle.name, &req.data_id)?.into())
        .await?;
    Ok(Json(json!({"deleted":true})))
}
