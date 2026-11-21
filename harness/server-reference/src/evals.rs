use crate::{
    http::{ApiError, App},
    service,
};
use axum::{
    extract::Path,
    http::{HeaderMap, StatusCode},
    Extension, Json,
};
use openworker_store_postgres::{evals, PostgresStore, SubmissionError};
use serde::Deserialize;
use serde_json::{json, Value};
use std::time::Duration;
use tokio_util::sync::CancellationToken;

fn enabled(app: &App) -> Result<(), ApiError> {
    if app.config.observability.is_none() {
        return Err(ApiError(SubmissionError::NotFound.into()));
    }
    Ok(())
}

pub(crate) async fn capture(
    Extension(app): Extension<App>,
    Path((session_id, turn_id)): Path<(String, String)>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(json!(app.store.eval_evidence(evals::Turn {
        session_id,
        turn_id,
    })?)))
}

pub(crate) async fn save(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(request): Json<evals::SaveCase>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    let key = service::key(&app, &headers, "eval.case")?;
    Ok(Json(app.store.save_eval_case(key, request)?))
}

pub(crate) async fn datasets(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_datasets()?))
}

pub(crate) async fn dataset(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_dataset(id)?))
}

pub(crate) async fn case(
    Extension(app): Extension<App>,
    Path((id, case)): Path<(String, String)>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_case(id, case)?))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Options {
    dataset_version: i32,
}

pub(crate) async fn enqueue(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(request): Json<Options>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    enabled(&app)?;
    let key = service::key(&app, &headers, "eval.job")?;
    Ok((
        StatusCode::ACCEPTED,
        Json(app.store.enqueue_eval(key, id, request.dataset_version)?),
    ))
}

pub(crate) async fn job(
    Extension(app): Extension<App>,
    Path((id, job)): Path<(String, String)>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_job(id, job)?))
}

pub async fn run(store: PostgresStore, stop: CancellationToken) {
    while !stop.is_cancelled() {
        let store = store.clone();
        match tokio::task::spawn_blocking(move || store.score_next_eval()).await {
            Ok(Ok(true)) => continue,
            Ok(Ok(false)) => (),
            _ => eprintln!("Evaluation scoring unavailable; retrying queued work"),
        }
        tokio::select! {
            _ = stop.cancelled() => break,
            _ = tokio::time::sleep(Duration::from_secs(1)) => (),
        }
    }
}
