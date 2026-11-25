use crate::http::{ApiError, App};
use axum::{
    extract::Query,
    http::{HeaderMap, StatusCode},
    Extension, Json,
};
use openworker_store_postgres::{
    compliance_catalog::{AuditSearch, CoverageSearch, Overview, Search},
    service_catalog::Release,
    PostgresStore, SubmissionError,
};
use serde_json::Value;

fn permit(app: &App) -> Result<tokio::sync::SemaphorePermit<'_>, ApiError> {
    app.safety_readers
        .try_acquire()
        .map_err(|_| ApiError(SubmissionError::Busy.into()))
}

pub(crate) async fn decisions(
    Extension(app): Extension<App>,
    Query(q): Query<Search>,
) -> Result<Json<Value>, ApiError> {
    let _permit = permit(&app)?;
    Ok(Json(app.store.compliance_decisions(q)?))
}

pub(crate) async fn agents(
    Extension(app): Extension<App>,
    Query(q): Query<Overview>,
) -> Result<Json<Value>, ApiError> {
    let _permit = permit(&app)?;
    Ok(Json(app.store.compliance_agents(q)?))
}

pub(crate) async fn audit(
    Extension(app): Extension<App>,
    Query(q): Query<AuditSearch>,
) -> Result<Json<Value>, ApiError> {
    let _permit = permit(&app)?;
    Ok(Json(app.store.compliance_admin_audit(q)?))
}

pub(crate) async fn coverage(
    Extension(app): Extension<App>,
    Query(q): Query<CoverageSearch>,
) -> Result<Json<Value>, ApiError> {
    let _permit = permit(&app)?;
    let selection = q.release.clone();
    let mut response = app.store.compliance_coverage(q)?;
    let releases = app.store.catalog_visible("release")?;
    response["releases"] = serde_json::json!(releases
        .iter()
        .filter(|entry| entry.activated)
        .map(|entry| &entry.id)
        .collect::<Vec<_>>());
    let mut config = app.config.safety.clone();
    let mut defaults_resolved = None;
    if let Some(id) = selection {
        let entry = releases
            .iter()
            .find(|entry| entry.id == id && entry.activated)
            .ok_or_else(|| ApiError(SubmissionError::NotFound.into()))?;
        let release: Release =
            serde_json::from_value(entry.definition.clone()).map_err(anyhow::Error::from)?;
        match release_checks(&app, &release).and_then(|checks| config.merge(&checks)) {
            Ok(effective) => {
                config = effective;
                defaults_resolved = Some(true);
            }
            Err(_) => defaults_resolved = Some(false),
        }
    }
    response["configured"] = serde_json::json!(check_descriptors(&config));
    response["release_defaults_resolved"] = serde_json::json!(defaults_resolved);
    Ok(Json(response))
}

fn release_checks(
    app: &App,
    release: &Release,
) -> anyhow::Result<openworker_runtime::safety::Config> {
    crate::model_registry::refresh(app)?;
    let profile_model = release
        .model_profile
        .as_deref()
        .map(|name| app.store.current_profile_model(name))
        .transpose()?;
    let model = profile_model
        .as_ref()
        .or(release.default_model.as_ref())
        .map(|choice| app.runtime.connections.resolve(choice))
        .transpose()?;
    crate::management::admission_checks(
        app,
        release,
        model.as_ref(),
        None,
        &Default::default(),
        None,
    )
}

fn check_descriptors(config: &openworker_runtime::safety::Config) -> Vec<Value> {
    use openworker_runtime::safety::{Action, HookPhase, Phase, Rule};
    use serde_json::json;
    let mut rows = Vec::new();
    let redact = openworker_runtime::telemetry::redact;
    let mut rule_row = |rule: &Rule, phase: Phase, tools: &[String]| {
        let action = match rule.action {
            Action::Block => "block",
            Action::Redact { .. } => "redact",
            Action::Callback { .. } => "callback",
        };
        rows.push(json!({"policy":redact(&rule.name),"policy_version":redact(&rule.version),
            "phase":phase,"action":action,"tools":tools.iter().map(|t|redact(t)).collect::<Vec<_>>()}));
    };
    for rule in &config.guardrails.user_input {
        rule_row(rule, Phase::UserInput, &[]);
    }
    for rule in &config.guardrails.model_output {
        rule_row(rule, Phase::ModelOutput, &[]);
    }
    for (groups, phase) in [
        (&config.guardrails.tool_calls, Phase::ToolCall),
        (&config.guardrails.tool_results, Phase::ToolResult),
    ] {
        for group in groups {
            for rule in &group.rules {
                rule_row(rule, phase, &group.tools);
            }
        }
    }
    for hook in &config.hooks {
        let phase = match hook.when {
            HookPhase::BeforeInput => Phase::UserInput,
            HookPhase::BeforeModel => Phase::ModelInput,
            HookPhase::BeforeTool => Phase::ToolCall,
            HookPhase::AfterTool => Phase::ToolResult,
            HookPhase::BeforeOutput => Phase::ModelOutput,
            HookPhase::OnEvent => Phase::Event,
        };
        rows.push(json!({"policy":redact(&hook.name),"policy_version":redact(&hook.version),
            "phase":phase,"action":"hook","tools":hook.tools.iter().map(|t|redact(t)).collect::<Vec<_>>()}));
    }
    rows
}

pub(crate) async fn export(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(q): Json<Search>,
) -> Result<Json<Value>, ApiError> {
    let _permit = permit(&app)?;
    Ok(Json(
        app.store
            .export_compliance_evidence(q, request_key(&headers)?)?,
    ))
}

fn request_key(headers: &HeaderMap) -> Result<String, ApiError> {
    Ok(headers
        .get("idempotency-key")
        .and_then(|value| value.to_str().ok())
        .ok_or_else(|| ApiError(SubmissionError::InvalidRequest.into()))?
        .to_owned())
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Forward {
    sink: String,
    selection: Search,
}

pub(crate) async fn forward(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(request): Json<Forward>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let _permit = permit(&app)?;
    let sink = app
        .config
        .compliance_sinks
        .iter()
        .find(|sink| cfg!(feature = "compliance-export") && sink.name == request.sink)
        .ok_or_else(|| ApiError(SubmissionError::NotFound.into()))?;
    let receipt = app.store.forward_compliance_evidence(
        request.selection,
        request_key(&headers)?,
        sink.name.clone(),
        sink.binding()?,
    )?;
    Ok((StatusCode::ACCEPTED, Json(receipt)))
}

pub(crate) async fn deliveries(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    let _permit = permit(&app)?;
    let targets = app
        .config
        .compliance_sinks
        .iter()
        .filter(|_| cfg!(feature = "compliance-export"))
        .map(|sink| Ok((sink.name.clone(), sink.binding()?)))
        .collect::<anyhow::Result<Vec<_>>>()?;
    Ok(Json(app.store.compliance_evidence_deliveries(targets)?))
}

/// An immutable operator-selected export uses the same transport and checkpoint
/// as the existing session evidence stream. It never dispatches agent work.
pub fn start_delivery(
    #[allow(unused_variables)] store: PostgresStore,
    sinks: Vec<openworker_runtime::compliance_export::Sink>,
    #[allow(unused_variables)] stop: tokio_util::sync::CancellationToken,
) -> anyhow::Result<()> {
    if sinks.is_empty() {
        return Ok(());
    }
    #[cfg(not(feature = "compliance-export"))]
    anyhow::bail!("Compliance exporter not included in this build");
    #[cfg(feature = "compliance-export")]
    {
        use openworker_runtime::compliance_export::{deliver, delivery_client};
        let client = delivery_client()?;
        for sink in sinks {
            let binding = sink.binding()?;
            let (store, stop, client) = (store.clone(), stop.clone(), client.clone());
            tokio::spawn(async move {
                loop {
                    if stop.is_cancelled() {
                        break;
                    }
                    let token = openworker_runtime::id("evidence");
                    if let Ok(Some((id, payload))) = store.claim_evidence_delivery(
                        sink.name.clone(),
                        binding.clone(),
                        token.clone(),
                    ) {
                        let success = tokio::select! {
                            _ = stop.cancelled() => break,
                            result = deliver(&client,&sink,payload) => result
                        };
                        let _ = store.settle_evidence_delivery(id, token, success);
                        continue;
                    }
                    tokio::select! {
                        _ = stop.cancelled() => break,
                        _ = tokio::time::sleep(std::time::Duration::from_secs(1)) => {}
                    }
                }
            });
        }
        Ok(())
    }
}
