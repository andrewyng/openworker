use crate::{
    http::{ApiError, App},
    service,
};
use anyhow::Result;
use axum::{
    extract::Path,
    http::{HeaderMap, StatusCode},
    Extension, Json,
};
use openworker_store_postgres::{
    auth::Action,
    eval_replay::{Enqueue, Task},
    evals::Turn,
    service_catalog::Admission,
};
use serde_json::{json, Value};
use std::time::Duration;

pub(crate) async fn enqueue(
    Extension(app): Extension<App>,
    Path(dataset): Path<String>,
    headers: HeaderMap,
    Json(request): Json<Enqueue>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    crate::evals::enabled(&app)?;
    let id = service::key(&app, &headers, "eval.replay")?;
    Ok((
        StatusCode::ACCEPTED,
        Json(app.store.enqueue_replay(id, dataset, request)?),
    ))
}

pub(crate) async fn get(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    crate::evals::enabled(&app)?;
    Ok(Json(app.store.eval_replay(id)?))
}

pub(crate) async fn list(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    crate::evals::enabled(&app)?;
    Ok(Json(app.store.eval_replays()?))
}

async fn launch(app: &App, task: &Task) -> Result<String> {
    let mut scoped = app.clone();
    scoped.store = app.store.for_background_credential(&task.credential)?;
    scoped.actor = scoped
        .store
        .authorize(Action::Submit, None, None)?
        .principal;
    anyhow::ensure!(scoped.actor == task.owner, "Replay owner changed");
    let mut headers = HeaderMap::new();
    headers.insert(
        "idempotency-key",
        format!("replay-{}-{}", task.replay, task.ordinal).parse()?,
    );
    let admission = Admission {
        agent: task.release.clone(),
        runtime_profile: task.profile.clone(),
        initial_message: Some(task.input.clone()),
        limits: Some(openworker_runtime::budget::Limits {
            max_model_calls: 12,
            max_output_tokens: 8192,
            max_output_tokens_per_call: 2048,
            summary_tokens: 256,
        }),
        name: Some(
            if task.ordinal % 2 == 0 {
                "Eval replay · baseline"
            } else {
                "Eval replay · candidate"
            }
            .into(),
        ),
        project: "eval-replay".into(),
        model: None,
        workspace_id: None,
        environment_id: None,
        integrations: Default::default(),
    };
    let (_, Json(value)) = crate::management::admit_kind(scoped, headers, admission, "eval-replay")
        .await
        .map_err(|e| e.0)?;
    Ok(value["operation"]["id"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("Missing replay operation"))?
        .into())
}

async fn step(app: &App, task: Task) -> Result<()> {
    let Some(operation) = task.operation.as_deref() else {
        let (id, result) = match launch(app, &task).await {
            Ok(id) => (Some(id), None),
            Err(_) => (
                None,
                Some(
                    json!({"status":"incomplete","reason":"Admission failed or the credential was revoked."}),
                ),
            ),
        };
        return app.store.update_eval_replay(task, id, result, false);
    };
    let op = app.store.service_operation(operation)?;
    if task.cleanup {
        // Only the operation created for this row is eligible. The ordinary
        // lifecycle refuses deletion while any attachment or work is open.
        let closed = op.status == "failed" || app.store.service_close(&op.session_id).is_ok();
        let key = format!("replay-cleanup-{}-{}", task.replay, task.ordinal);
        let cleanup = closed
            && app
                .store
                .begin_environment_delete(&op.environment_id, &key, "eval-replay")
                .is_ok()
            && app
                .store
                .service_queue_delete(&op.environment_id, &key)
                .is_ok();
        return app.store.update_eval_replay(task, None, None, cleanup);
    }
    let result = match op.status.as_str() {
        "complete" => app.store.eval_replay_result(
            Turn {
                session_id: op.session_id,
                turn_id: op
                    .run_id
                    .ok_or_else(|| anyhow::anyhow!("Replay operation has no turn"))?,
            },
            task.check.clone(),
        )?,
        "failed" => Some(
            json!({"status":"incomplete","reason":"The execution environment could not be provisioned."}),
        ),
        _ => None,
    };
    app.store.update_eval_replay(task, None, result, false)
}

pub async fn run(app: App) {
    let mut failed = false;
    while !app.stop.is_cancelled() {
        let outcome = async {
            let Some(task) = app.store.claim_eval_replay()? else {
                return Ok(false);
            };
            step(&app, task).await?;
            Ok::<_, anyhow::Error>(true)
        }
        .await;
        match outcome {
            Ok(true) => {
                failed = false;
                continue;
            }
            Ok(false) => failed = false,
            Err(_) if !failed => {
                eprintln!("Replay dispatcher unavailable; retrying");
                failed = true;
            }
            Err(_) => {}
        }
        tokio::select! {_=app.stop.cancelled()=>break,_=tokio::time::sleep(Duration::from_secs(1))=>{}}
    }
}
