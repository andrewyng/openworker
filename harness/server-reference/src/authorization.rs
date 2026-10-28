use crate::http::{ApiError, App};
use axum::{
    extract::{Path, Query},
    Extension, Json,
};
use openworker_store_postgres::auth::{Action, Spec};
use serde::Deserialize;
use serde_json::{json, Value};
/// Deny unmatched/new routes until their required authority is explicit.
/// Resource scope is rechecked with decoded IDs inside store transactions.
pub(crate) fn route(method: &str, path: &str) -> Option<Action> {
    if path.starts_with("/v1/") {
        return crate::service::authority(method, path);
    }
    let p: Vec<_> = path.split('/').collect();
    match (method, p.as_slice()) {
        ("GET", ["", "healthz" | "readyz" | "sessions"]) | ("GET", ["", "auth", "me"]) => {
            Some(Action::Read)
        }
        ("POST", ["", "sessions"]) => Some(Action::Submit),
        ("POST", ["", "teams"]) | ("POST", ["", "teams", _, "commands"]) => Some(Action::Control),
        ("GET", ["", "teams", _])
        | ("GET", ["", "teams", _, "events"])
        | ("GET", ["", "teams", _, "events", "page"]) => Some(Action::Read),
        ("GET", ["", "credentials"])
        | ("POST", ["", "credentials"])
        | ("POST", ["", "credentials", _, "revoke"])
        | ("GET", ["", "security", "audit"]) => Some(Action::Admin),
        ("GET", ["", "sessions", _])
        | ("GET", ["", "sessions", _, "events" | "artifacts"])
        | ("GET", ["", "sessions", _, "turns" | "controls" | "artifacts", _])
        | ("GET", ["", "sessions", _, "artifacts", _, "content"]) => Some(Action::Read),
        ("POST", ["", "sessions", _, "turns"]) => Some(Action::Submit),
        ("POST", ["", "sessions", _, "controls"]) => Some(Action::Control),
        ("POST", ["", "sessions", _, "artifacts"])
        | ("POST", ["", "sessions", _, "artifacts", _, "reconcile"])
        | ("PUT", ["", "sessions", _, "artifacts", _, "content"]) => Some(Action::Artifact),
        _ => None,
    }
}
pub(crate) async fn me(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    Ok(Json(json!(app.store.authorize(
        Action::Read,
        None,
        None
    )?)))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Issue {
    request_id: String,
    spec: Spec,
    token: String,
}
pub(crate) async fn issue(
    Extension(app): Extension<App>,
    Json(body): Json<Issue>,
) -> Result<Json<Value>, ApiError> {
    let token = zeroize::Zeroizing::new(body.token);
    Ok(Json(app.store.auth_issue(
        &body.request_id,
        body.spec,
        &token,
    )?))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Page {
    after: Option<String>,
}
pub(crate) async fn list(
    Extension(app): Extension<App>,
    Query(q): Query<Page>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.auth_list(q.after.as_deref().unwrap_or(""))?))
}
pub(crate) async fn revoke(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.auth_revoke(&id)?))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Cursor {
    after: Option<i64>,
}
pub(crate) async fn audit(
    Extension(app): Extension<App>,
    Query(q): Query<Cursor>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.auth_audit(q.after.unwrap_or(0))?))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct IssueV1 {
    spec: Spec,
    token: String,
}
pub(crate) async fn issue_v1(
    Extension(app): Extension<App>,
    headers: axum::http::HeaderMap,
    Json(body): Json<IssueV1>,
) -> Result<Json<Value>, ApiError> {
    let token = zeroize::Zeroizing::new(body.token);
    if body.spec.expires_at.is_none() {
        return Err(ApiError(
            openworker_store_postgres::auth::Error::Invalid.into(),
        ));
    }
    let key = crate::service::key(&app, &headers, "credential.issue")?;
    Ok(Json(app.store.auth_issue(&key, body.spec, &token)?))
}
