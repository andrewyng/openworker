//! The control plane accepts keys over the authenticated admin endpoint. Only a
//! trusted worker opens a sealed provider key; the model checks it per request.
use crate::{
    http::{ApiError, App},
    service, Config,
};
use anyhow::{ensure, Context, Result};
use axum::{extract::Path, http::HeaderMap, Extension, Json};
use base64::{engine::general_purpose::STANDARD, Engine};
use openworker_runtime::connections::{Choice, Manager};
use openworker_store_postgres::{model_registry::Provider, PostgresStore, SubmissionError};
use ring::{
    aead,
    rand::{SecureRandom, SystemRandom},
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;
use zeroize::Zeroizing;
fn key_material() -> Result<Zeroizing<Vec<u8>>> {
    let raw = Zeroizing::new(
        std::env::var("OPENWORKER_MODEL_REGISTRY_KEY")
            .context("Set OPENWORKER_MODEL_REGISTRY_KEY on the cluster API and workers")?,
    );
    let bytes = Zeroizing::new(
        STANDARD
            .decode(raw.as_bytes())
            .context("Invalid model registry key encoding")?,
    );
    ensure!(bytes.len() == 32, "Model registry key must be 32 bytes");
    Ok(bytes)
}
fn aead_key(bytes: &[u8]) -> Result<aead::LessSafeKey> {
    Ok(aead::LessSafeKey::new(
        aead::UnboundKey::new(&aead::AES_256_GCM, bytes)
            .map_err(|_| anyhow::anyhow!("Model registry key must be 32 bytes"))?,
    ))
}
fn seal(name: &str, raw: String) -> Result<Vec<u8>> {
    let raw = Zeroizing::new(raw);
    ensure!(
        (8..=4096).contains(&raw.len()) && raw.bytes().all(|b| (33..=126).contains(&b)),
        "Invalid provider credential"
    );
    let mut nonce = [0; 12];
    SystemRandom::new()
        .fill(&mut nonce)
        .map_err(|_| anyhow::anyhow!("Random generator unavailable"))?;
    let mut data = Zeroizing::new(raw.as_bytes().to_vec());
    let master = key_material()?;
    let label_key = ring::hmac::Key::new(ring::hmac::HMAC_SHA256, &master);
    let subkey = ring::hmac::sign(&label_key, b"openworker-provider-request-v1");
    let mut mac = ring::hmac::Context::with_key(&ring::hmac::Key::new(
        ring::hmac::HMAC_SHA256,
        subkey.as_ref(),
    ));
    mac.update(name.as_bytes());
    mac.update(&[0]);
    mac.update(raw.as_bytes());
    aead_key(&master)?
        .seal_in_place_append_tag(
            aead::Nonce::assume_unique_for_key(nonce),
            aead::Aad::from(name.as_bytes()),
            &mut *data,
        )
        .map_err(|_| anyhow::anyhow!("Cannot seal provider key"))?;
    let mut out = mac.sign().as_ref().to_vec();
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&data);
    Ok(out)
}
fn open(name: &str, cipher: Vec<u8>) -> Result<Zeroizing<String>> {
    let mut data = Zeroizing::new(cipher);
    ensure!(data.len() >= 60, "Invalid sealed credential");
    let nonce = aead::Nonce::try_assume_unique_for_key(&data[32..44])
        .map_err(|_| anyhow::anyhow!("Invalid nonce"))?;
    let raw = aead_key(&key_material()?)?
        .open_in_place(nonce, aead::Aad::from(name.as_bytes()), &mut data[44..])
        .map_err(|_| anyhow::anyhow!("Cannot open provider credential"))?;
    Ok(Zeroizing::new(std::str::from_utf8(raw)?.to_owned()))
}
pub(crate) fn install(
    manager: &Manager,
    route: &Provider,
    store: Option<PostgresStore>,
) -> Result<()> {
    let id = route.installation_id()?;
    manager.register_host_endpoint(
        &route.name,
        route.api.kind(),
        &id,
        None,
        Some(route.endpoint()),
    )?;
    if let Some(db) = store {
        let name = route.name.clone();
        manager.set_host_key_source(
            &route.name,
            Arc::new(move || open(&name, db.model_key_ciphertext(&name)?)),
        )?;
    }
    Ok(())
}
pub(crate) fn refresh(app: &App) -> Result<()> {
    for r in app.store.model_routes()? {
        install(&app.runtime.connections, &r, None)?;
    }
    Ok(())
}
pub(crate) fn check_static(config: &Config, name: &str) -> Result<()> {
    ensure!(
        !config.model_connections.iter().any(|c| c.id == name),
        SubmissionError::Conflict
    );
    Ok(())
}
pub(crate) async fn catalog(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.model_catalog()?))
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Register {
    provider: Provider,
    api_key: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Rotate {
    api_key: String,
}
pub(crate) async fn register(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(r): Json<Register>,
) -> Result<Json<Value>, ApiError> {
    r.provider.validate()?;
    check_static(&app.config, &r.provider.name)?;
    let sealed = seal(&r.provider.name, r.api_key)?;
    Ok(Json(app.store.provider_put(
        r.provider,
        sealed,
        &service::key(&app, &headers, "provider.register")?,
    )?))
}
pub(crate) async fn rotate(
    Extension(app): Extension<App>,
    Path(name): Path<String>,
    headers: HeaderMap,
    Json(r): Json<Rotate>,
) -> Result<Json<Value>, ApiError> {
    app.store.provider_change(
        &name,
        Some(seal(&name, r.api_key)?),
        &service::key(&app, &headers, "provider.rotate")?,
    )?;
    Ok(Json(json!({"name":name})))
}
pub(crate) async fn disable(
    Extension(app): Extension<App>,
    Path(name): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    app.store.provider_change(
        &name,
        None,
        &service::key(&app, &headers, "provider.disable")?,
    )?;
    Ok(Json(json!({"name":name,"enabled":false})))
}
pub(crate) async fn profile(
    Extension(app): Extension<App>,
    Path(name): Path<String>,
    headers: HeaderMap,
    Json(model): Json<Choice>,
) -> Result<Json<Value>, ApiError> {
    let installed = app
        .config
        .model_connections
        .iter()
        .any(|c| c.id == model.connection)
        .then(|| app.runtime.connections.resolve(&model))
        .transpose()?;
    Ok(Json(app.store.set_profile_binding(
        &name,
        model,
        installed,
        &service::key(&app, &headers, "model-profile.set")?,
    )?))
}
