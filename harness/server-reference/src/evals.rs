use crate::{
    http::{ApiError, App},
    service,
};
use axum::{
    extract::{Path, Query},
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

pub(crate) async fn datasets(
    Extension(app): Extension<App>,
    Query(filter): Query<evals::DatasetList>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_datasets(filter)?))
}

pub(crate) async fn runs(
    Extension(app): Extension<App>,
    Query(filter): Query<evals::RunList>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_runs(filter)?))
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

/// The eval role uses an independent database connection and reopens it after
/// an outage. A restarted worker resumes from the last committed batch.
pub async fn run_worker(
    db: openworker_store_postgres::Config,
    service: service::ServiceConfig,
    readiness: crate::readiness::Readiness,
    stop: CancellationToken,
) {
    let mut connection: Option<PostgresStore> = None;
    let mut reported_unavailable = false;
    while !stop.is_cancelled() {
        let existing = connection.take();
        let db = db.clone();
        let service = service.clone();
        let step = tokio::task::spawn_blocking(move || -> anyhow::Result<_> {
            let store = match existing {
                Some(store) => store,
                None => {
                    let store = PostgresStore::open(db)?;
                    store.verify_service(&service.id, &service.namespace)?;
                    store
                }
            };
            let progressed = store.score_next_eval()?;
            Ok((store, progressed))
        })
        .await;
        match step {
            Ok(Ok((store, progressed))) => {
                connection = Some(store);
                reported_unavailable = false;
                readiness.checked();
                if progressed {
                    continue;
                }
            }
            _ => {
                if !reported_unavailable {
                    eprintln!("Evaluation store unavailable; retrying");
                    reported_unavailable = true;
                }
                readiness.clear();
            }
        }
        tokio::select! {
            _ = stop.cancelled() => break,
            _ = tokio::time::sleep(Duration::from_secs(1)) => (),
        }
    }
    readiness.clear();
}
