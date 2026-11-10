//! Partner child entry point. Existing runner/CLI commands retain their behavior.
use crate::{host::Host, HostConfig, ToolRegistry};
use anyhow::{ensure, Result};
use std::sync::Arc;
struct Endpoint(Arc<Host>);
impl openworker_runtime::protocol::Endpoint for Endpoint {
    fn application_events(&self) -> Option<tokio::sync::broadcast::Receiver<serde_json::Value>> {
        Some(self.0.callbacks.events.subscribe())
    }
    fn gaps(&self) -> Option<tokio::sync::broadcast::Receiver<()>> {
        Some(self.0.gaps.subscribe())
    }
    async fn closed(&self) {
        self.0.failed.cancelled().await
    }
    fn subscribe(&self) -> tokio::sync::broadcast::Receiver<openworker_runtime::store::Event> {
        self.0.events.subscribe()
    }
    async fn dispatch(&self, m: &str, p: serde_json::Value) -> Result<serde_json::Value> {
        Box::pin(self.0.dispatch(m, p)).await
    }
    async fn shutdown(&self) -> Result<()> {
        self.0.shutdown().await
    }
}
pub fn main_with_registry(registry: ToolRegistry) {
    let args = std::env::args().collect::<Vec<_>>();
    if args.get(1).map(String::as_str) != Some("harness-stdio") {
        return openworker_runtime::cli::main_with_registry(registry);
    }
    let result = (|| -> Result<()> {
        ensure!(
            args.len() == 3 && args[2].len() < 64000,
            "Invalid host launch configuration"
        );
        let raw = match args[2].strip_prefix('@') {
            Some(path) => String::from_utf8(openworker_runtime::bundle::read_limited(
                std::path::Path::new(path),
                4 * 1024 * 1024,
            )?)?,
            None => args[2].clone(),
        };
        let config: HostConfig = serde_json::from_str(&raw)
            .map_err(|_| anyhow::anyhow!("Invalid host launch configuration"))?;
        config.validate()?;
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()?
            .block_on(async {
                let host = Host::open(&config, registry)?;
                openworker_runtime::protocol::serve_endpoint(Endpoint(host)).await
            })
    })();
    if result.is_err() {
        eprintln!("Harness host failed; inspect configuration and reconcile durable sessions");
        std::process::exit(1);
    }
}
