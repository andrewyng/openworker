//! Explicitly unavailable on native Windows. Never downgrade an isolation request.
use anyhow::{bail, Result};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
pub const VERSION: &str = "0.0.116";
pub struct Sandbox {
    pub root: PathBuf,
    pub runner: PathBuf,
}
pub struct Mcp;
pub fn config_home() -> Result<PathBuf> {
    bail!("OpenShell is unavailable on native Windows")
}
pub async fn doctor() -> Value {
    json!({"ready":false,"sandbox":"openshell","host_os":"windows","error":"Native Windows sandboxing is not implemented. Select --sandbox off explicitly for trusted host execution; no fallback."})
}
pub async fn setup(_: bool) -> Result<Value> {
    bail!("Native Windows sandboxing/setup is not implemented")
}
pub async fn verify(_: &Path, _: &Path, _: &Path) -> Result<Value> {
    bail!("Native Windows sandboxing is not implemented")
}
pub async fn command(_: &str, _: &[&str], _: u64) -> Result<String> {
    bail!("OpenShell is unavailable on native Windows")
}
impl Sandbox {
    pub async fn create_with_policy(
        &self,
        _: &str,
        _: &str,
        _: &crate::bundle::Bundle,
        _: &crate::resource_policy::Resolved,
    ) -> Result<String> {
        bail!("Native Windows sandboxing is not implemented")
    }
    pub async fn delete(&self, _: &str) -> Result<()> {
        bail!("Cannot manage an OpenShell sandbox from this native Windows profile")
    }
    pub fn cleanup_credentials(&self, _: &str) -> Result<()> {
        bail!("No Windows sandbox credentials supported")
    }
    pub fn cleanup_runner(&self, _: &str) -> Result<()> {
        bail!("No Windows sandbox runner supported")
    }
}
impl Mcp {
    pub async fn connect(_: &str, _: Vec<String>) -> Result<Self> {
        bail!("Native Windows sandboxing is not implemented")
    }
    pub async fn request(&mut self, _: &str, _: Value) -> Result<Value> {
        bail!("Native Windows sandboxing is not implemented")
    }
}
