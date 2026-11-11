//! Authenticated bounded uploads; model inputs are independent of sandbox files.
use crate::http::{ApiError, App};
use axum::{extract::Path, Extension, Json};
use openworker_runtime::{
    attachments::{Part, Upload},
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
pub(crate) async fn inspect(app: &App, sid: &str, parts: &[Part]) -> Result<Vec<Part>, ApiError> {
    app.store.input_safety_policy(sid)?;
    openworker_runtime::attachments::validate_parts(parts)?;
    // Check ownership using the request-scoped store before trusted runtime
    // inspection. Never allow an ID to cross a project/principal boundary.
    for part in parts {
        if let Part::Image { attachment_id } | Part::Document { attachment_id } = part {
            app.store
                .clone()
                .input_attachment(sid, attachment_id, Upload::Stat)?;
        }
    }
    Ok(app
        .runtime
        .prepare_input_content(sid, parts, Some(&app.config.safety))
        .await?)
}
