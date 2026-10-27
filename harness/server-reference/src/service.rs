//! Opt-in service v1 boundary over the same durable reference engine/store.
use crate::http::{ApiError, App};
use anyhow::{ensure, Result};
use axum::{
    extract::Path,
    http::{HeaderMap, StatusCode},
    routing::{get, post},
    Extension, Json, Router,
};
use openworker_store_postgres::{auth::Action, SessionCreation, SubmissionError, TurnSubmission};
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServiceConfig {
    pub default_runtime_profile: Option<String>,
    pub id: String,
    pub namespace: String,
    pub agent: String,
}
impl ServiceConfig {
    pub fn validate(&self) -> Result<()> {
        for text in [&self.id, &self.namespace, &self.agent] {
            ensure!(
                !text.is_empty()
                    && text.len() <= 80
                    && text
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"-_@.".contains(&b)),
                "Invalid service identity"
            );
        }
        Ok(())
    }
}
pub(crate) fn authority(method: &str, path: &str) -> Option<Action> {
    let p = path.split('/').collect::<Vec<_>>();
    match (method, p.as_slice()) {
        ("GET", ["", "v1", "capabilities"])
        | ("GET", ["", "v1", "auth", "me"])
        | ("GET", ["", "v1", "agents", _]) => Some(Action::Read),
        ("POST", ["", "v1", "sessions"]) => Some(Action::Submit),
        ("GET", ["", "v1", "sessions", _])
        | ("GET", ["", "v1", "sessions", _, "events"])
        | ("GET", ["", "v1", "sessions", _, "turns" | "controls", _]) => Some(Action::Read),
        ("POST", ["", "v1", "sessions", _, "turns"]) => Some(Action::Submit),
        ("POST", ["", "v1", "sessions", _, "controls"]) => Some(Action::Control),
        ("POST", ["", "v1", "profiles" | "releases"])
        | ("POST", ["", "v1", "profiles" | "releases", _, "activate"]) => Some(Action::Admin),
        ("GET", ["", "v1", "profiles" | "environments" | "operations", _]) => Some(Action::Read),
        _ => None,
    }
}
fn check(ok: bool, error: SubmissionError) -> Result<(), ApiError> {
    if ok {
        Ok(())
    } else {
        Err(ApiError(error.into()))
    }
}
pub(crate) fn router() -> Router<App> {
    Router::new()
        .route("/v1/profiles", post(crate::management::put_profile))
        .route("/v1/profiles/:id", get(crate::management::get_profile))
        .route(
            "/v1/profiles/:id/activate",
            post(crate::management::activate_profile),
        )
        .route("/v1/releases", post(crate::management::put_release))
        .route(
            "/v1/releases/:id/activate",
            post(crate::management::activate_release),
        )
        .route(
            "/v1/environments/:id",
            get(crate::management::get_environment),
        )
        .route("/v1/operations/:id", get(crate::management::get_operation))
        .route("/v1/capabilities", get(capabilities))
        .route("/v1/auth/me", get(crate::authorization::me))
        .route("/v1/agents/:id", get(agent))
        .route("/v1/sessions", post(create))
        .route("/v1/sessions/:sid", get(snapshot))
        .route("/v1/sessions/:sid/events", get(crate::http::events))
        .route("/v1/sessions/:sid/turns", post(send))
        .route("/v1/sessions/:sid/turns/:id", get(result))
        .route("/v1/sessions/:sid/controls", post(control))
        .route("/v1/sessions/:sid/controls/:id", get(control_receipt))
}
fn cfg(app: &App) -> &ServiceConfig {
    app.service.as_ref().expect("authenticated service")
}
async fn capabilities(Extension(app): Extension<App>) -> Json<Value> {
    Json(
        json!({"apiVersion":"1","serviceId":cfg(&app).id,"namespace":cfg(&app).namespace,"agent":cfg(&app).agent,"features":{"sessions":true,"events":"sse","controls":true,"runtimeProfiles":true,"agentDeployment":true,"teams":false,"singleNamespace":true},"limits":{"jsonBytes":160000,"textBytes":32000,"idempotencyKeyBytes":100,"catalogEntries":64,"pendingOperations":128,"environments":64,"environmentsPerPrincipal":16}}),
    )
}
async fn agent(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    if id != cfg(&app).agent {
        let release = app.store.catalog_get("release", &id)?;
        check(release.activated, SubmissionError::NotFound)?;
        return Ok(Json(json!({"id":release.id,"environmentRequired":false})));
    }
    Ok(Json(
        json!({"id":id,"executionDigest":app.runtime.execution_digest(),"environmentRequired":true}),
    ))
}
pub(crate) fn key(app: &App, headers: &HeaderMap, kind: &str) -> Result<String> {
    let raw = headers
        .get("idempotency-key")
        .and_then(|s| s.to_str().ok())
        .ok_or(SubmissionError::InvalidRequest)?;
    ensure!(
        !raw.is_empty() && raw.len() <= 100 && raw.bytes().all(|b| b.is_ascii_graphic()),
        SubmissionError::InvalidRequest
    );
    Ok(openworker_runtime::bundle::digest(&serde_json::to_vec(
        &json!([cfg(app).namespace, app.actor, kind, raw]),
    )?))
}
async fn create(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(body): Json<openworker_store_postgres::service_catalog::Admission>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if body.agent != cfg(&app).agent {
        return crate::management::admit(app, headers, body).await;
    }
    check(
        body.runtime_profile.is_none() && body.environment_id.is_some(),
        SubmissionError::InvalidRequest,
    )?;
    let request = SessionCreation {
        request_id: key(&app, &headers, "session")?,
        actor: app.actor.clone(),
        parameters: json!({"coworker":app.runtime.coworker(),"workspace":"/sandbox","environment_id":body.environment_id,"name":body.name.unwrap_or_else(||"Service session".into())}),
    };
    let receipt = if let Some(r) = app.store.creation(request.clone())? {
        r
    } else {
        let candidate = app
            .runtime
            .prepare_session(request.parameters.clone())
            .map_err(|_| ApiError(SubmissionError::InvalidRequest.into()))?;
        app.store.create_session(request, candidate)?
    };
    Ok((
        if receipt.duplicate {
            StatusCode::OK
        } else {
            StatusCode::CREATED
        },
        Json(
            json!({"serviceId":cfg(&app).id,"namespace":cfg(&app).namespace,"sessionId":receipt.session_id,"agent":cfg(&app).agent,"environmentId":body.environment_id}),
        ),
    ))
}
async fn snapshot(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let mut snapshot = app.store.read_snapshot(&sid)?;
    snapshot["service"] = app
        .store
        .service_session_identity(&sid)?
        .unwrap_or(Value::Null);
    Ok(Json(snapshot))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Send {
    text: String,
}
async fn send(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    Json(body): Json<Send>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let r = app.store.submit_service_turn(TurnSubmission {
        session_id: sid.clone(),
        submission_id: key(&app, &headers, &format!("turn:{sid}"))?,
        text: body.text,
    })?;
    Ok((
        StatusCode::ACCEPTED,
        Json(
            json!({"sessionId":r.session_id,"runId":r.turn_id,"submissionId":r.submission_id,"status":r.status}),
        ),
    ))
}
async fn result(
    Extension(app): Extension<App>,
    Path((sid, id)): Path<(String, String)>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.service_run(&sid, &id)?))
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Control {
    run_id: String,
    action: openworker_runtime::control::Action,
}
async fn control(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    Json(body): Json<Control>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let id = key(&app, &headers, &format!("control:{sid}"))?;
    let record = app
        .store
        .submit_control(openworker_runtime::control::Command {
            session_id: sid,
            request_id: id,
            turn_id: body.run_id,
            actor: app.actor.clone(),
            action: body.action,
        })?;
    Ok((StatusCode::ACCEPTED, Json(json!(record))))
}
async fn control_receipt(
    Extension(app): Extension<App>,
    Path((sid, id)): Path<(String, String)>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(json!(app.store.control_receipt(&sid, &id)?)))
}
