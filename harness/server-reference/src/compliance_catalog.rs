use crate::http::{ApiError, App};
use axum::{
    extract::Query,
    http::{HeaderMap, StatusCode},
    Extension, Json,
};
use openworker_store_postgres::{
    compliance_catalog::{AuditSearch, Overview, Search},
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
