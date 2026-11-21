use crate::{
    http::{ApiError, App},
    management, service,
};
use axum::{extract::Path, http::HeaderMap, Extension, Json};
use openworker_store_postgres::service_catalog::Release;
use serde::Deserialize;
use serde_json::{json, Value};

pub(crate) async fn list(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.app_agents()?))
}
pub(crate) async fn publish(
    Extension(app): Extension<App>,
    Path(name): Path<String>,
    headers: HeaderMap,
    Json(mut definition): Json<Value>,
) -> Result<Json<Value>, ApiError> {
    // Identity, revision and visibility are assigned by the server.
    if !definition.is_object()
        || definition.get("id").is_some()
        || definition.get("allowedPrincipals").is_some()
    {
        return Err(ApiError(
            openworker_store_postgres::SubmissionError::InvalidRequest.into(),
        ));
    }
    definition["id"] = json!("proposed@1");
    let r: Release = serde_json::from_value(definition)
        .map_err(|_| ApiError(openworker_store_postgres::SubmissionError::InvalidRequest.into()))?;
    management::validate_release(&app, &r)?;
    Ok(Json(json!(app.store.app_publish(
        &service::key(&app, &headers, "app.publish")?,
        &name,
        r,
        &management::installation(&app.config)?
    )?)))
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Sharing {
    shared_with: Vec<String>,
}
pub(crate) async fn share(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(s): Json<Sharing>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.share_app_agent(
        &id,
        s.shared_with,
        &service::key(&app, &headers, "app.share")?,
    )?))
}

pub(crate) fn default_tools(bundle: &openworker_runtime::bundle::Bundle) -> Vec<String> {
    let mut names = Vec::new();
    for (alias, binding) in &bundle.tools.bindings {
        if matches!(
            openworker_runtime::permissions::native_tool(bundle, alias),
            Some(
                "read_file"
                    | "list_files"
                    | "search_files"
                    | "write_file"
                    | "edit_file"
                    | "apply_patch"
                    | "run_shell"
                    | "process_start"
                    | "process_status"
                    | "process_logs"
                    | "process_stop"
            )
        ) {
            names.push(alias.clone());
            names.push(format!("{}.{}", binding.server, binding.tool));
        }
    }
    names
}
