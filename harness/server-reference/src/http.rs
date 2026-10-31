use crate::Config;
use anyhow::Result;
use axum::{
    extract::{DefaultBodyLimit, Path, Query, Request, State},
    http::{header, HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
    routing::{get, post},
    Extension, Json, Router,
};
use openworker_runtime::runtime::Runtime;
use openworker_store_postgres::auth::{Action, Error as AuthError};
use openworker_store_postgres::{PostgresStore, SessionCreation, SubmissionError, TurnSubmission};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{convert::Infallible, sync::Arc, time::Duration};
use tokio::sync::Semaphore;
use tokio_util::sync::CancellationToken;

#[derive(Clone)]
pub struct App {
    pub(crate) recording_readers: Arc<Semaphore>,
    recovery_read_only: bool,
    pub(crate) store: PostgresStore,
    pub(crate) runtime: Arc<Runtime>,
    pub(crate) actor: String,
    streams: Arc<Semaphore>,
    pub(crate) service: Option<crate::service::ServiceConfig>,
    pub(crate) config: Arc<Config>,
    pub(crate) stop: CancellationToken,
    pub(crate) artifacts: Option<openworker_artifacts::Store>,
    pub(crate) transfers: Arc<Semaphore>,
}
impl App {
    pub fn new(
        config: &Config,
        runtime: Arc<Runtime>,
        store: PostgresStore,
        stop: CancellationToken,
    ) -> Result<Self> {
        if config.actor.trim().is_empty() || config.actor.len() > 128 {
            anyhow::bail!("Invalid reference principal");
        }
        if let Some(service) = &config.service {
            service.validate()?;
            if config.recovery_read_only {
                store.verify_service(&service.id, &service.namespace)?;
            } else {
                store.bind_service(&service.id, &service.namespace)?;
            }
        }
        Ok(Self {
            recording_readers: Arc::new(Semaphore::new(4)),
            config: Arc::new(config.clone()),
            service: config.service.clone(),
            recovery_read_only: config.recovery_read_only,
            artifacts: config.artifacts.as_ref().map(|a| a.build()).transpose()?,
            transfers: Arc::new(Semaphore::new(4)),
            store,
            runtime,
            actor: config.actor.clone(),
            streams: Arc::new(Semaphore::new(32)),
            stop,
        })
    }
    pub fn router(self) -> Router {
        Router::new()
            .merge(crate::service::router())
            .merge(crate::recording_query::router(""))
            .merge(crate::recording_query::router("/v1"))
            .route(
                "/healthz",
                get(|| async { Json(json!({"status":"up","reference":true})) }),
            )
            .route("/sessions", post(create).get(session_page))
            .route("/teams", post(team_create))
            .route("/teams/:id", get(team_snapshot))
            .route("/teams/:id/commands", post(team_command))
            .route("/teams/:id/events", get(team_events))
            .route("/teams/:id/events/page", get(team_event_page))
            .route("/readyz", get(ready))
            .route("/auth/me", get(crate::authorization::me))
            .route(
                "/credentials",
                get(crate::authorization::list).post(crate::authorization::issue),
            )
            .route(
                "/credentials/:id/revoke",
                post(crate::authorization::revoke),
            )
            .route("/security/audit", get(crate::authorization::audit))
            .route("/sessions/:sid", get(snapshot))
            .route("/sessions/:sid/events", get(events))
            .route("/sessions/:sid/turns", post(submit))
            .route("/sessions/:sid/turns/:id", get(receipt))
            .route("/sessions/:sid/controls", post(control))
            .route("/sessions/:sid/controls/:id", get(control_receipt))
            .route(
                "/sessions/:sid/artifacts",
                get(crate::artifacts::list).post(crate::artifacts::register),
            )
            .route(
                "/sessions/:sid/artifacts/:id",
                get(crate::artifacts::metadata),
            )
            .route(
                "/sessions/:sid/artifacts/:id/reconcile",
                post(crate::artifacts::reconcile),
            )
            .route(
                "/sessions/:sid/artifacts/:id/content",
                get(crate::artifacts::download)
                    .put(crate::artifacts::upload)
                    .layer(DefaultBodyLimit::max(
                        openworker_artifacts::MAX_SIZE as usize + 1,
                    )),
            )
            .layer(DefaultBodyLimit::max(160_000))
            .layer(middleware::from_fn_with_state(self.clone(), authenticate))
            .with_state(self)
    }
}
async fn authenticate(State(app): State<App>, request: Request, next: Next) -> Response {
    let recording_route =
        crate::recording_query::authority(request.method().as_str(), request.uri().path());
    let span = app
        .runtime
        .telemetry
        .span(openworker_telemetry::Operation::Api, None, None);
    let service = request
        .uri()
        .path()
        .starts_with("/v1/")
        .then(|| app.service.clone())
        .flatten();
    let mut result =
        openworker_telemetry::scope(span.context(), authenticate_inner(app, request, next)).await;
    // Include identity and a fresh trace ID on errors too. This ID is never an
    // admission/deduplication key and is not supplied by an untrusted caller.
    if let Some(config) = service {
        result.headers_mut().insert(
            "x-openworker-service-id",
            config.id.parse().expect("validated"),
        );
        result.headers_mut().insert(
            "x-openworker-namespace",
            config.namespace.parse().expect("validated"),
        );
        result.headers_mut().insert(
            "x-request-id",
            openworker_runtime::id("req").parse().unwrap(),
        );
    }
    if recording_route {
        result.headers_mut().insert(
            header::CACHE_CONTROL,
            axum::http::HeaderValue::from_static("no-store"),
        );
    }
    span.finish(if result.status().is_success() {
        openworker_telemetry::Outcome::Ok
    } else {
        openworker_telemetry::Outcome::Error
    });
    result
}
async fn authenticate_inner(mut app: App, mut request: Request, next: Next) -> Response {
    let supplied = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .unwrap_or("");
    app.store = match app.store.authorized(supplied) {
        Ok(s) => s,
        Err(_) => return ApiError(AuthError::Unauthorized.into()).into_response(),
    };
    let p = match app.store.authorize(Action::Read, None, None) {
        Ok(p) => p,
        Err(e) => return ApiError(e).into_response(),
    };
    app.actor = p.principal;
    if request.headers().contains_key(header::ORIGIN) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error":"browser_origin_not_enabled"})),
        )
            .into_response();
    }
    if app.recovery_read_only
        && !matches!(
            *request.method(),
            axum::http::Method::GET | axum::http::Method::HEAD
        )
    {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"recovery_read_only"})),
        )
            .into_response();
    }
    if app.stop.is_cancelled() {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"shutting_down"})),
        )
            .into_response();
    }
    if app
        .service
        .as_ref()
        .is_some_and(|s| !s.public_path(request.uri().path()))
    {
        return StatusCode::NOT_FOUND.into_response();
    }
    let service_request = request.uri().path().starts_with("/v1/");
    if service_request {
        let Some(service) = &app.service else {
            return StatusCode::NOT_FOUND.into_response();
        };
        if request
            .headers()
            .get("x-openworker-service-id")
            .is_some_and(|v| v.to_str().ok() != Some(service.id.as_str()))
        {
            return ApiError(AuthError::Forbidden.into()).into_response();
        }
        if request
            .headers()
            .get("x-openworker-namespace")
            .and_then(|h| h.to_str().ok())
            != Some(service.namespace.as_str())
        {
            return ApiError(AuthError::Forbidden.into()).into_response();
        }
    }
    let Some(action) = crate::authorization::route(request.method().as_str(), request.uri().path())
    else {
        return ApiError(AuthError::Forbidden.into()).into_response();
    };
    if let Err(e) = app.store.authorize(action, None, None) {
        let _ = app.store.auth_denied(action);
        return ApiError(e).into_response();
    }
    request.extensions_mut().insert(app.clone());
    let response = next.run(request).await;
    if matches!(
        response.status(),
        StatusCode::FORBIDDEN | StatusCode::UNAUTHORIZED
    ) {
        let _ = app.store.auth_denied(action);
    }
    response
}
async fn ready(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    app.store.ready()?;
    Ok(Json(
        json!({"status":"ready","recovery_read_only":app.recovery_read_only,"telemetry_enabled":app.runtime.telemetry.enabled(),"telemetry":app.runtime.telemetry.snapshot(),"recording":app.config.recording_stats.as_ref().map(|s|s.snapshot())}),
    ))
}
pub(crate) struct ApiError(pub(crate) anyhow::Error);
impl From<anyhow::Error> for ApiError {
    fn from(e: anyhow::Error) -> Self {
        Self(e)
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        if let Some(error) = self.0.downcast_ref::<crate::recording_query::Error>() {
            use crate::recording_query::Error as E;
            let (status, code) = match error {
                E::Invalid => (StatusCode::BAD_REQUEST, "invalid_recording_query"),
                E::Unavailable => (StatusCode::SERVICE_UNAVAILABLE, "recording_unavailable"),
                E::Busy => (StatusCode::TOO_MANY_REQUESTS, "recording_query_busy"),
            };
            return (status, Json(json!({"error":code}))).into_response();
        }
        if let Some(error) = self
            .0
            .downcast_ref::<openworker_store_postgres::teams::Error>()
        {
            use openworker_store_postgres::teams::Error as E;
            let status = match error {
                E::Invalid => StatusCode::BAD_REQUEST,
                E::Conflict => StatusCode::CONFLICT,
                E::NotFound => StatusCode::NOT_FOUND,
                E::Limit => StatusCode::TOO_MANY_REQUESTS,
            };
            return (status, Json(json!({"error":error.to_string()}))).into_response();
        }
        if let Some(error) = self.0.downcast_ref::<AuthError>() {
            let status = match error {
                AuthError::Unauthorized => StatusCode::UNAUTHORIZED,
                AuthError::Forbidden => StatusCode::FORBIDDEN,
                AuthError::Invalid => StatusCode::BAD_REQUEST,
                AuthError::Conflict => StatusCode::CONFLICT,
                AuthError::Unconfigured => StatusCode::SERVICE_UNAVAILABLE,
            };
            return (status, Json(json!({"error":error.to_string()}))).into_response();
        }
        if let Some(error) = self.0.downcast_ref::<openworker_artifacts::Error>() {
            use openworker_artifacts::Error as E;
            let status = match error {
                E::Invalid => StatusCode::BAD_REQUEST,
                E::NotFound => StatusCode::NOT_FOUND,
                E::Conflict | E::NotReady => StatusCode::CONFLICT,
                E::TooLarge => StatusCode::PAYLOAD_TOO_LARGE,
                E::Cancelled => StatusCode::REQUEST_TIMEOUT,
                _ => StatusCode::SERVICE_UNAVAILABLE,
            };
            return (status, Json(json!({"error":error.to_string()}))).into_response();
        }
        let (status, code) = match self.0.downcast_ref::<SubmissionError>() {
            Some(SubmissionError::InvalidRequest) => (StatusCode::BAD_REQUEST, "invalid_request"),
            Some(SubmissionError::NotFound) => (StatusCode::NOT_FOUND, "not_found"),
            Some(SubmissionError::Conflict) => (StatusCode::CONFLICT, "idempotency_conflict"),
            Some(SubmissionError::QueueFull) => (StatusCode::TOO_MANY_REQUESTS, "queue_full"),
            Some(SubmissionError::Busy) => (StatusCode::CONFLICT, "session_busy"),
            Some(SubmissionError::Closed) => (StatusCode::CONFLICT, "session_closed"),
            Some(SubmissionError::ControlConflict) => {
                (StatusCode::CONFLICT, "control_target_conflict")
            }
            Some(SubmissionError::ControlsPending) => (StatusCode::CONFLICT, "controls_pending"),
            Some(SubmissionError::ProviderBlocked) => (StatusCode::CONFLICT, "provider_blocked"),
            _ => (
                StatusCode::SERVICE_UNAVAILABLE,
                "operation_failed_reconcile_receipt",
            ),
        };
        (status, Json(json!({"error":code}))).into_response()
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Create {
    request_id: String,
    parameters: Value,
}
async fn create(
    Extension(app): Extension<App>,
    Json(body): Json<Create>,
) -> Result<Response, ApiError> {
    let request = SessionCreation {
        request_id: body.request_id,
        actor: app.actor,
        parameters: body.parameters,
    };
    if let Some(receipt) = app.store.creation(request.clone())? {
        return Ok((StatusCode::OK, Json(receipt)).into_response());
    }
    let candidate = app
        .runtime
        .prepare_session(request.parameters.clone())
        .map_err(|_| ApiError(SubmissionError::InvalidRequest.into()))?;
    let receipt = app.store.create_session(request, candidate)?;
    Ok((
        if receipt.duplicate {
            StatusCode::OK
        } else {
            StatusCode::CREATED
        },
        Json(receipt),
    )
        .into_response())
}
async fn snapshot(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.read_snapshot(&sid)?))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SessionPage {
    after: Option<String>,
    limit: Option<u16>,
}
pub(crate) async fn session_page(
    Extension(app): Extension<App>,
    Query(q): Query<SessionPage>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(
        app.store.session_page(q.after, q.limit.unwrap_or(50))?,
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Turn {
    submission_id: String,
    text: String,
    #[serde(default)]
    limits: Option<openworker_runtime::budget::Limits>,
}
async fn submit(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    Json(body): Json<Turn>,
) -> Result<Response, ApiError> {
    Ok((
        StatusCode::ACCEPTED,
        Json(app.store.submit_turn(TurnSubmission {
            session_id: sid,
            submission_id: body.submission_id,
            text: body.text,
            limits: body.limits,
        })?),
    )
        .into_response())
}
async fn receipt(
    Extension(app): Extension<App>,
    Path((sid, id)): Path<(String, String)>,
) -> Result<Response, ApiError> {
    Ok(Json(app.store.submission(&sid, &id)?).into_response())
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Control {
    request_id: String,
    turn_id: String,
    action: openworker_runtime::control::Action,
}
async fn control(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    Json(body): Json<Control>,
) -> Result<Response, ApiError> {
    Ok((
        StatusCode::ACCEPTED,
        Json(
            app.store
                .submit_control(openworker_runtime::control::Command {
                    session_id: sid,
                    request_id: body.request_id,
                    turn_id: body.turn_id,
                    actor: app.actor,
                    action: body.action,
                })?,
        ),
    )
        .into_response())
}
async fn control_receipt(
    Extension(app): Extension<App>,
    Path((sid, id)): Path<(String, String)>,
) -> Result<Response, ApiError> {
    Ok(Json(app.store.control_receipt(&sid, &id)?).into_response())
}
#[derive(Default, Deserialize)]
pub(crate) struct Cursor {
    after: Option<i64>,
}
async fn team_create(
    Extension(app): Extension<App>,
    Json(body): Json<openworker_store_postgres::teams::Create>,
) -> Result<Response, ApiError> {
    Ok((StatusCode::CREATED, Json(app.store.create_team(body)?)).into_response())
}
async fn team_snapshot(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.team_snapshot(&id)?))
}
async fn team_command(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Json(body): Json<openworker_store_postgres::teams::Command>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.team_command(&id, body)?))
}
async fn team_event_page(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Query(q): Query<Cursor>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.team_events(&id, q.after.unwrap_or(0))?))
}
async fn team_events(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Query(q): Query<Cursor>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let header = headers
        .get("last-event-id")
        .map(|v| {
            v.to_str()
                .ok()
                .and_then(|s| s.parse::<i64>().ok())
                .ok_or_else(|| ApiError(SubmissionError::InvalidRequest.into()))
        })
        .transpose()?;
    let mut cursor = header.or(q.after).unwrap_or(0);
    let initial = app.store.team_events(&id, cursor)?;
    let permit = app
        .streams
        .clone()
        .try_acquire_owned()
        .map_err(|_| ApiError(SubmissionError::QueueFull.into()))?;
    let stream = async_stream::stream! {
        let _permit=permit;
        let mut batch=initial;
        loop {
            for event in batch["events"].as_array().expect("team event array") {
                cursor=event["cursor"].as_i64().expect("team cursor");
                yield Ok::<_,Infallible>(Event::default().id(cursor.to_string()).event("team_progress").data(event.to_string()));
            }
            tokio::select! {biased;_=app.stop.cancelled()=>break,_=tokio::time::sleep(Duration::from_millis(250))=>{}}
            match app.store.team_events(&id,cursor) {
                Ok(next)=>batch=next,
                Err(_)=>{yield Ok(Event::default().event("stream_error").data("{\"error\":\"reconnect_and_reconcile\"}"));break;}
            }
        }
    };
    Ok(Sse::new(stream)
        .keep_alive(KeepAlive::new().interval(Duration::from_secs(10)))
        .into_response())
}
pub(crate) async fn events(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    Query(query): Query<Cursor>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let header = headers
        .get("last-event-id")
        .map(|v| {
            v.to_str()
                .ok()
                .and_then(|v| v.parse::<i64>().ok())
                .ok_or_else(|| ApiError(SubmissionError::InvalidRequest.into()))
        })
        .transpose()?;
    let mut cursor = header.or(query.after).unwrap_or(0);
    let initial = app.store.read_events(&sid, cursor)?;
    let permit = app
        .streams
        .clone()
        .try_acquire_owned()
        .map_err(|_| ApiError(SubmissionError::QueueFull.into()))?;
    let stream = async_stream::stream! {
        let _permit=permit;
        let mut batch=initial;
        loop {
            for event in batch.drain(..) {
                cursor=event.event_id;
                yield Ok::<_,Infallible>(Event::default().id(cursor.to_string()).event("progress").data(serde_json::to_string(&event).expect("JSON event")));
            }
            tokio::select! {biased; _=app.stop.cancelled()=>break, _=tokio::time::sleep(Duration::from_millis(250))=>{} }
            match app.store.read_events(&sid,cursor) {
                Ok(next)=>batch=next,
                Err(_)=>{yield Ok(Event::default().event("stream_error").data("{\"error\":\"reconnect_and_reconcile\"}"));break;}
            }
        }
    };
    Ok(Sse::new(stream)
        .keep_alive(KeepAlive::new().interval(Duration::from_secs(10)))
        .into_response())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn permanent_control_errors_are_not_service_outages() {
        for (error, status) in [
            (SubmissionError::InvalidRequest, StatusCode::BAD_REQUEST),
            (SubmissionError::NotFound, StatusCode::NOT_FOUND),
            (SubmissionError::Conflict, StatusCode::CONFLICT),
            (SubmissionError::ControlConflict, StatusCode::CONFLICT),
            (SubmissionError::Closed, StatusCode::CONFLICT),
            (SubmissionError::ProviderBlocked, StatusCode::CONFLICT),
            (SubmissionError::QueueFull, StatusCode::TOO_MANY_REQUESTS),
        ] {
            assert_eq!(ApiError(error.into()).into_response().status(), status);
        }
        assert_eq!(
            ApiError(anyhow::anyhow!("unknown database outcome"))
                .into_response()
                .status(),
            StatusCode::SERVICE_UNAVAILABLE
        );
    }
}
