//! Authenticated bounded uploads; model inputs are independent of sandbox files.
use crate::http::{ApiError, App};
use anyhow::ensure;
use axum::{extract::Path, Extension, Json};
use base64::{engine::general_purpose::STANDARD, Engine};
use openworker_runtime::{
    attachments::{InspectionUnsupported, Metadata, Part, Upload, CHUNK, MAX_TURN},
    safety::Phase,
    store::StateStore,
};
use serde_json::Value;
pub(crate) async fn upload(
    Extension(app): Extension<App>,
    Path((sid, id)): Path<(String, String)>,
    Json(request): Json<Upload>,
) -> Result<Json<Value>, ApiError> {
    // The scoped store rechecks the principal and session on every chunk.
    let blob = app.store.clone().input_attachment(&sid, &id, request)?;
    Ok(Json(blob.reply()))
}
pub(crate) async fn inspect(
    app: &App,
    sid: &str,
    request: &str,
    parts: &[Part],
) -> Result<Vec<Part>, ApiError> {
    inspect_parts(app, sid, request, parts)
        .await
        .map_err(ApiError)
}
async fn inspect_parts(
    app: &App,
    sid: &str,
    request: &str,
    parts: &[Part],
) -> anyhow::Result<Vec<Part>> {
    let (session, _) = app.store.input_safety_policy(sid)?;
    let policy = app.config.safety.merge(&session)?;
    openworker_runtime::attachments::validate_parts(parts)?;
    let mut checked = Vec::new();
    let mut total = 0usize;
    // API admission uses the authorized request-scoped store. The API runtime
    // does not own this session's worker lease, so its metadata/inspection path
    // must not be used here. Never claim or steal a lease just to inspect input.
    for part in parts {
        checked.push(match part {
            Part::Text { text } => Part::Text {
                text: crate::service_safety::inspect_attachment_text(app, sid, request, text)
                    .await
                    .map_err(|e| e.0)?,
            },
            Part::Image { attachment_id } | Part::Document { attachment_id } => {
                let blob = app
                    .store
                    .clone()
                    .input_attachment(sid, attachment_id, Upload::Stat)?;
                ensure!(blob.sealed, "Attachment upload is incomplete");
                total = total.saturating_add(blob.metadata.size);
                ensure!(total <= MAX_TURN, "Turn attachment byte limit exceeded");
                let image = matches!(part, Part::Image { .. });
                ensure!(
                    if image {
                        blob.metadata.media_type.starts_with("image/")
                    } else {
                        ["application/pdf", "text/plain"]
                            .contains(&blob.metadata.media_type.as_str())
                    },
                    "Attachment type mismatch"
                );
                let id = if blob.metadata.media_type == "text/plain" {
                    let text = std::str::from_utf8(&blob.bytes)?;
                    let replacement =
                        crate::service_safety::inspect_attachment_text(app, sid, request, text)
                            .await
                            .map_err(|e| e.0)?;
                    if replacement.as_bytes() != blob.bytes {
                        let bytes = replacement.into_bytes();
                        let hash = openworker_runtime::bundle::digest(&bytes);
                        let identity = openworker_runtime::bundle::digest(&serde_json::to_vec(
                            &serde_json::json!([
                                blob.metadata.name,
                                blob.metadata.media_type,
                                hash
                            ]),
                        )?);
                        let metadata = Metadata {
                            id: format!("att_{identity}"),
                            sha256: hash,
                            size: bytes.len(),
                            ..blob.metadata.clone()
                        };
                        let mut store = app.store.clone();
                        store.input_attachment(
                            sid,
                            &metadata.id,
                            Upload::Begin {
                                metadata: metadata.clone(),
                            },
                        )?;
                        for (i, chunk) in bytes.chunks(CHUNK).enumerate() {
                            store.input_attachment(
                                sid,
                                &metadata.id,
                                Upload::Chunk {
                                    offset: i * CHUNK,
                                    data: STANDARD.encode(chunk),
                                },
                            )?;
                        }
                        store.input_attachment(sid, &metadata.id, Upload::Commit)?;
                        metadata.id
                    } else {
                        attachment_id.clone()
                    }
                } else {
                    if policy.protects(Phase::UserInput, None)
                        || policy.protects(Phase::ModelInput, None)
                    {
                        return Err(InspectionUnsupported.into());
                    }
                    attachment_id.clone()
                };
                if image {
                    Part::Image { attachment_id: id }
                } else {
                    Part::Document { attachment_id: id }
                }
            }
        });
    }
    Ok(checked)
}
