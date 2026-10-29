//! Opt-in service v1 boundary over the same durable reference engine/store.
use crate::http::{ApiError, App};
use anyhow::{ensure, Result};
use axum::{
    extract::{DefaultBodyLimit, Path, Query},
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
    /// Explicit compatibility only; managed service deployments require releases.
    #[serde(default)]
    pub legacy_compatibility: bool,
    pub default_runtime_profile: Option<String>,
    pub id: String,
    pub namespace: String,
    pub agent: String,
}
impl ServiceConfig {
    pub fn public_path(&self, path: &str) -> bool {
        self.legacy_compatibility
            || path.starts_with("/v1/")
            || matches!(path, "/healthz" | "/readyz")
    }

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
        | ("GET", ["", "v1", "agents", _])
        | ("GET", ["", "v1", "releases" | "profiles" | "sessions"])
        | ("GET", ["", "v1", "sessions", _, "events", "page"]) => Some(Action::Read),
        ("POST", ["", "v1", "sessions" | "submissions"]) => Some(Action::Submit),
        ("GET", ["", "v1", "sessions", _])
        | ("GET", ["", "v1", "sessions", _, "events"])
        | ("GET", ["", "v1", "sessions", _, "artifacts"])
        | ("GET", ["", "v1", "sessions", _, "artifacts", _, "content"])
        | ("GET", ["", "v1", "sessions", _, "turns" | "runs" | "controls" | "artifacts", _]) => {
            Some(Action::Read)
        }
        ("POST", ["", "v1", "sessions", _, "artifacts"])
        | ("POST", ["", "v1", "sessions", _, "artifacts", _, "reconcile"])
        | ("PUT", ["", "v1", "sessions", _, "artifacts", _, "content"]) => Some(Action::Artifact),
        ("POST", ["", "v1", "sessions", _, "turns"]) => Some(Action::Submit),
        ("POST", ["", "v1", "sessions", _, "controls" | "close"]) => Some(Action::Control),
        ("GET", ["", "v1", "worker-pools"]) => Some(Action::Read),
        ("POST", ["", "v1", "worker-pools", _, "scale"]) => Some(Action::Admin),
        ("GET", ["", "v1", "configuration"]) => Some(Action::Read),
        ("PUT", ["", "v1", "configuration"]) => Some(Action::Admin),
        ("POST", ["", "v1", "releases"]) => Some(Action::Publish),
        ("POST", ["", "v1", "releases", _, "activate"]) => Some(Action::Activate),
        ("POST", ["", "v1", "profiles"]) | ("POST", ["", "v1", "profiles", _, "activate"]) => {
            Some(Action::Configure)
        }
        ("GET" | "POST", ["", "v1", "credentials"])
        | ("POST", ["", "v1", "credentials", _, "revoke"])
        | ("GET", ["", "v1", "audit"]) => Some(Action::Admin),
        ("GET", ["", "v1", "profiles" | "releases" | "environments" | "operations", _]) => {
            Some(Action::Read)
        }
        ("GET", ["", "v1", "environments"]) | ("POST", ["", "v1", "environments", _, "delete"]) => {
            Some(Action::Admin)
        }
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
        .route(
            "/v1/profiles",
            post(crate::management::put_profile).get(profiles),
        )
        .route("/v1/profiles/:id", get(crate::management::get_profile))
        .route(
            "/v1/profiles/:id/activate",
            post(crate::management::activate_profile),
        )
        .route(
            "/v1/releases",
            post(crate::management::put_release).get(releases),
        )
        .route("/v1/releases/:id", get(crate::management::get_release))
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
        .route(
            "/v1/credentials",
            get(crate::authorization::list).post(crate::authorization::issue_v1),
        )
        .route(
            "/v1/credentials/:id/revoke",
            post(crate::authorization::revoke),
        )
        .route("/v1/audit", get(crate::authorization::audit))
        .route("/v1/agents/:id", get(agent))
        .route("/v1/worker-pools", get(pools))
        .route("/v1/worker-pools/:id/scale", post(scale_pool))
        .route("/v1/configuration", get(configuration).put(configure))
        .route("/v1/sessions", post(create).get(crate::http::session_page))
        .route("/v1/sessions/:sid/events/page", get(event_page))
        .route("/v1/submissions", post(submit))
        .route("/v1/sessions/:sid/close", post(close))
        .route("/v1/environments", get(environment_list))
        .route(
            "/v1/environments/:id/delete",
            post(crate::management::delete_environment),
        )
        .route("/v1/sessions/:sid/runs/:id", get(run_result))
        .route("/v1/sessions/:sid", get(snapshot))
        .route("/v1/sessions/:sid/events", get(crate::http::events))
        .route("/v1/sessions/:sid/turns", post(send))
        .route("/v1/sessions/:sid/turns/:id", get(result))
        .route("/v1/sessions/:sid/controls", post(control))
        .route("/v1/sessions/:sid/controls/:id", get(control_receipt))
        .route(
            "/v1/sessions/:sid/artifacts",
            get(crate::artifacts::list).post(crate::artifacts::register),
        )
        .route(
            "/v1/sessions/:sid/artifacts/:id",
            get(crate::artifacts::metadata),
        )
        .route(
            "/v1/sessions/:sid/artifacts/:id/reconcile",
            post(crate::artifacts::reconcile),
        )
        .route(
            "/v1/sessions/:sid/artifacts/:id/content",
            get(crate::artifacts::download)
                .put(crate::artifacts::upload)
                .layer(DefaultBodyLimit::max(
                    openworker_artifacts::MAX_SIZE as usize + 1,
                )),
        )
}
fn cfg(app: &App) -> &ServiceConfig {
    app.service.as_ref().expect("authenticated service")
}
async fn capabilities(Extension(app): Extension<App>) -> Json<Value> {
    Json(
        json!({"apiVersion":"1","serviceId":cfg(&app).id,"namespace":cfg(&app).namespace,"agent":cfg(&app).agent,"features":{"sessions":true,"events":"sse","controls":true,"artifacts":app.artifacts.is_some(),"combinedSubmission":true,"runtimeProfiles":true,"agentDeployment":true,"credentialExpiry":true,"configuration":true,"workerPools":true,"humanLogin":"token-file","approvedReleasesOnly":!cfg(&app).legacy_compatibility,"teams":false,"singleNamespace":true},"limits":{"jsonBytes":160000,"textBytes":32000,"idempotencyKeyBytes":100,"catalogEntries":64,"pendingOperations":128,"environments":64,"environmentsPerPrincipal":16}}),
    )
}
async fn agent(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    if id == cfg(&app).agent && !cfg(&app).legacy_compatibility {
        return Err(ApiError(SubmissionError::NotFound.into()));
    }
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
    check(
        body.initial_message.is_none(),
        SubmissionError::InvalidRequest,
    )?;
    if body.agent != cfg(&app).agent {
        return crate::management::admit(app, headers, body).await;
    }
    check(cfg(&app).legacy_compatibility, SubmissionError::NotFound)?;
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

async fn submit(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(mut body): Json<Value>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let object = body
        .as_object_mut()
        .ok_or_else(|| ApiError(SubmissionError::InvalidRequest.into()))?;
    check(
        !object.contains_key("initialMessage"),
        SubmissionError::InvalidRequest,
    )?;
    let text = object
        .remove("text")
        .and_then(|v| v.as_str().map(String::from))
        .ok_or_else(|| ApiError(SubmissionError::InvalidRequest.into()))?;
    let mut input: openworker_store_postgres::service_catalog::Admission =
        serde_json::from_value(body)
            .map_err(|_| ApiError(SubmissionError::InvalidRequest.into()))?;
    input.initial_message = Some(text);
    crate::management::admit_kind(app, headers, input, "submission").await
}
async fn run_result(
    Extension(app): Extension<App>,
    Path((sid, id)): Path<(String, String)>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.service_run_id(&sid, &id)?))
}

async fn close(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    key(&app, &headers, &format!("close:{sid}"))?;
    Ok(Json(app.store.service_close(&sid)?))
}
async fn environment_list(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.service_environment_list()?))
}

async fn configuration(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    Ok(Json(json!(app.store.service_configuration()?)))
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Configure {
    expected_revision: i64,
    configuration: openworker_store_postgres::service_admin::Configuration,
}
async fn configure(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(body): Json<Configure>,
) -> Result<Json<Value>, ApiError> {
    let key = key(&app, &headers, "configuration")?;
    Ok(Json(json!(app.store.service_configure(
        &key,
        body.expected_revision,
        body.configuration
    )?)))
}

async fn pools(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    Ok(Json(json!({"pools":app.store.service_pools()?})))
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Scale {
    expected_revision: i64,
    replicas: i32,
}
async fn scale_pool(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(body): Json<Scale>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let key = key(&app, &headers, &format!("scale:{id}"))?;
    Ok((
        StatusCode::ACCEPTED,
        Json(json!(app.store.service_scale_pool(
            &id,
            &key,
            body.expected_revision,
            body.replicas
        )?)),
    ))
}

#[cfg(test)]
mod boundary_tests {
    use super::*;
    #[test]
    fn artifact_routes_use_explicit_read_and_write_authority() {
        for path in [
            "/v1/sessions/s/artifacts",
            "/v1/sessions/s/artifacts/a",
            "/v1/sessions/s/artifacts/a/content",
        ] {
            assert!(authority("GET", path) == Some(Action::Read));
        }
        for (method, path) in [
            ("POST", "/v1/sessions/s/artifacts"),
            ("POST", "/v1/sessions/s/artifacts/a/reconcile"),
            ("PUT", "/v1/sessions/s/artifacts/a/content"),
        ] {
            assert!(authority(method, path) == Some(Action::Artifact));
        }
        assert!(authority("DELETE", "/v1/sessions/s/artifacts/a").is_none());
        assert!(authority("POST", "/v1/sessions/s/artifacts/a/content").is_none());
    }
    #[test]
    fn compatibility_routes_require_explicit_opt_in() {
        let mut c: ServiceConfig =
            serde_json::from_value(json!({"id":"test","namespace":"test","agent":"catalog"}))
                .unwrap();
        assert!(!c.legacy_compatibility);
        for p in [
            "/credentials",
            "/sessions",
            "/teams",
            "/auth/me",
            "/v1evil/sessions",
        ] {
            assert!(!c.public_path(p));
        }
        for p in ["/v1/sessions", "/v1/credentials", "/readyz"] {
            assert!(c.public_path(p));
        }
        c.legacy_compatibility = true;
        assert!(c.public_path("/sessions"));
    }
}

// Lists use the same transactional visibility rules as individual catalog reads.
async fn profiles(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    Ok(Json(
        json!({"entries":app.store.catalog_visible("profile")?}),
    ))
}
async fn releases(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    Ok(Json(
        json!({"entries":app.store.catalog_visible("release")?}),
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EventPage {
    after: Option<i64>,
}
async fn event_page(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    Query(q): Query<EventPage>,
) -> Result<Json<Value>, ApiError> {
    let after = q.after.unwrap_or(0);
    check(after >= 0, SubmissionError::InvalidRequest)?;
    let events = app.store.read_events(&sid, after)?;
    Ok(Json(json!({"events":events,"after":after})))
}
