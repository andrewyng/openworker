use crate::{
    http::{ApiError, App},
    service,
};
use axum::{
    extract::{Path, Query},
    http::{HeaderMap, StatusCode},
    Extension, Json,
};
use openworker_store_postgres::{
    eval_schedule, eval_selection, evals, PostgresStore, SubmissionError,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::time::Duration;
use tokio_util::sync::CancellationToken;

pub(crate) fn enabled(app: &App) -> Result<(), ApiError> {
    if app.config.observability.is_none() {
        return Err(ApiError(SubmissionError::NotFound.into()));
    }
    Ok(())
}

fn judges_enabled(config: &crate::Config) -> bool {
    config
        .observability
        .as_ref()
        .is_some_and(|options| options.eval_model_judges)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CaptureQuery {
    batch_id: Option<String>,
}

pub(crate) async fn capture(
    Extension(app): Extension<App>,
    Path((session_id, turn_id)): Path<(String, String)>,
    Query(query): Query<CaptureQuery>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(json!(app.store.eval_evidence(
        evals::Turn {
            session_id,
            turn_id,
        },
        query.batch_id
    )?)))
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
    Query(query): Query<evals::DatasetVersion>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_dataset(id, query.version)?))
}

pub(crate) async fn revise_case(
    Extension(app): Extension<App>,
    Path((dataset, case)): Path<(String, String)>,
    headers: HeaderMap,
    Json(request): Json<evals::ReviseCase>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    let key = service::key(&app, &headers, "eval.case-revision")?;
    Ok(Json(
        app.store.revise_eval_case(key, dataset, case, request)?,
    ))
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

pub(crate) async fn session_tags(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_tags(sid, None)?))
}

pub(crate) async fn turn_tags(
    Extension(app): Extension<App>,
    Path((sid, tid)): Path<(String, String)>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_tags(sid, Some(tid))?))
}

pub(crate) async fn update_session_tag(
    Extension(app): Extension<App>,
    Path(sid): Path<String>,
    Json(request): Json<eval_selection::UpdateTag>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    app.store.update_eval_tag(sid, None, request)?;
    Ok(Json(json!({})))
}

pub(crate) async fn update_turn_tag(
    Extension(app): Extension<App>,
    Path((sid, tid)): Path<(String, String)>,
    Json(request): Json<eval_selection::UpdateTag>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    app.store.update_eval_tag(sid, Some(tid), request)?;
    Ok(Json(json!({})))
}

pub(crate) async fn evaluators(
    Extension(app): Extension<App>,
    Query(filter): Query<eval_selection::EvaluatorList>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_evaluators(filter)?))
}

pub(crate) async fn save_evaluator(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(request): Json<eval_selection::SaveEvaluator>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    if request.judge.is_some() && !judges_enabled(&app.config) {
        return Err(ApiError(SubmissionError::NotFound.into()));
    }
    let key = service::key(&app, &headers, "eval.evaluator")?;
    Ok(Json(app.store.save_eval_evaluator(key, request)?))
}

pub(crate) async fn judge_profiles(
    Extension(app): Extension<App>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    let profiles = if judges_enabled(&app.config) {
        app.store.eval_judge_profiles()?
    } else {
        Vec::new()
    };
    Ok(Json(json!({"profiles":profiles})))
}

pub(crate) async fn candidates(
    Extension(app): Extension<App>,
    Query(filter): Query<eval_selection::TagFilter>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_candidates(filter)?))
}

pub(crate) async fn enqueue_tagged(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(request): Json<eval_selection::Enqueue>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    enabled(&app)?;
    let key = service::key(&app, &headers, "eval.batch")?;
    Ok((
        StatusCode::ACCEPTED,
        Json(
            app.store
                .enqueue_tag_eval(key, request, judges_enabled(&app.config))?,
        ),
    ))
}

pub(crate) async fn tagged_job(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.tagged_eval_job(id)?))
}

pub(crate) async fn retry_judge(
    Extension(app): Extension<App>,
    Path(original): Path<String>,
    headers: HeaderMap,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    enabled(&app)?;
    if !judges_enabled(&app.config) {
        return Err(ApiError(SubmissionError::NotFound.into()));
    }
    let id = service::key(&app, &headers, "eval.judge.retry")?;
    Ok((
        StatusCode::ACCEPTED,
        Json(app.store.retry_eval_judge(id, original)?),
    ))
}

pub(crate) async fn schedules(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(app.store.eval_schedules()?))
}

pub(crate) async fn save_schedule(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(spec): Json<eval_schedule::Spec>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    let key = service::key(&app, &headers, "eval.schedule")?;
    Ok(Json(app.store.save_eval_schedule(key, spec)?))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ScheduleState {
    enabled: bool,
}

pub(crate) async fn schedule_state(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Json(state): Json<ScheduleState>,
) -> Result<Json<Value>, ApiError> {
    enabled(&app)?;
    Ok(Json(
        app.store.set_eval_schedule_enabled(id, state.enabled)?,
    ))
}

/// The eval role uses an independent database connection and reopens it after
/// an outage. A restarted worker resumes from the last committed batch.
pub async fn run_worker(
    db: openworker_store_postgres::Config,
    service: service::ServiceConfig,
    config: crate::Config,
    readiness: crate::readiness::Readiness,
    stop: CancellationToken,
) {
    let mut connection: Option<PostgresStore> = None;
    let mut reported_unavailable = false;
    let judge_enabled = judges_enabled(&config);
    let judge_worker = judge_enabled.then(|| {
        tokio::spawn(crate::eval_judge::run(
            config,
            db.clone(),
            service.clone(),
            stop.child_token(),
        ))
    });
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
            let scored = store.score_next_eval()?;
            let scheduled = store.tick_eval_schedule(judge_enabled)?;
            Ok((store, scored || scheduled))
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
    if let Some(judge) = judge_worker {
        let _ = judge.await;
    }
}
