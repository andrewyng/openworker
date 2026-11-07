//! API admission checks run before durable submission writes. They do not claim
//! a worker slot. The working model still runs only on a worker.
use crate::http::{ApiError, App};
use openworker_runtime::safety::{Config, Phase};
#[derive(Debug)]
pub(crate) enum InputError {
    Blocked,
    Unavailable,
}
impl std::fmt::Display for InputError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Blocked => "Input blocked by application policy",
            Self::Unavailable => "Required input check unavailable",
        })
    }
}
impl std::error::Error for InputError {}
pub(crate) async fn inspect(
    app: &App,
    sid: Option<&str>,
    request: &str,
    text: &str,
) -> Result<String, ApiError> {
    inspect_with(app, sid, request, text, None).await
}
async fn inspect_with(
    app: &App,
    sid: Option<&str>,
    request: &str,
    text: &str,
    extra: Option<&Config>,
) -> Result<String, ApiError> {
    if text.trim().is_empty() || text.len() > 32000 || text.contains('\0') {
        return Err(ApiError(
            openworker_store_postgres::SubmissionError::InvalidRequest.into(),
        ));
    }
    let (config, pins) = if let Some(sid) = sid {
        let (session, pins) = app.store.input_safety_policy(sid)?;
        (app.config.safety.merge(&session)?, pins)
    } else {
        let config: Config = app.config.safety.clone();
        let pins = app.runtime.pin_safety_models(&config)?;
        (config, pins)
    };
    let config = config.merge(&extra.cloned().unwrap_or_default())?;
    if !config.protects(Phase::UserInput, None) {
        return Ok(text.into());
    }
    let _permit = app
        .safety_readers
        .try_acquire()
        .map_err(|_| ApiError(InputError::Unavailable.into()))?;
    let result = app
        .runtime
        .inspect_ingress(sid.unwrap_or(request), &config, &pins, text)
        .await
        .map_err(|_| ApiError(InputError::Unavailable.into()))?;
    app.store
        .record_input_safety(sid, request, &result.findings)?;
    if result.unavailable {
        return Err(ApiError(InputError::Unavailable.into()));
    }
    if result.block.is_some() {
        return Err(ApiError(InputError::Blocked.into()));
    }
    Ok(result.text)
}

pub(crate) async fn inspect_control(
    app: &App,
    sid: &str,
    request: &str,
    mut action: openworker_runtime::control::Action,
) -> Result<openworker_runtime::control::Action, ApiError> {
    if let openworker_runtime::control::Action::Response {
        interaction_id,
        response,
        ..
    } = &mut action
    {
        let snapshot = app.store.read_snapshot(sid)?;
        let question = snapshot["interactions"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|i| i["id"].as_str() == Some(interaction_id.as_str()) && i["kind"] != "approval");
        if question {
            let extra = snapshot["turn"]
                .get("safety")
                .map(|v| serde_json::from_value::<Config>(v.clone()))
                .transpose()
                .map_err(|_| ApiError(InputError::Unavailable.into()))?;
            *response = inspect_with(app, Some(sid), request, response, extra.as_ref()).await?;
        }
    }
    Ok(action)
}
