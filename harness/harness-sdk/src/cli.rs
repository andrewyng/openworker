//! Partner child entry point. Existing runner/CLI commands retain their behavior.
use crate::{host::Host, HostConfig, ToolRegistry};
use anyhow::{ensure, Result};
use std::sync::Arc;
struct Endpoint(Arc<Host>);
// Return allowlisted startup conflicts through the normal initialize response,
// so managed clients do not reduce them to a lost pipe/unknown outcome.
struct StartupConflict {
    diagnostic: crate::AgentDefinitionConflict,
    events: tokio::sync::broadcast::Sender<openworker_runtime::store::Event>,
}
impl openworker_runtime::protocol::Endpoint for StartupConflict {
    fn request_error(&self, _: &anyhow::Error) -> serde_json::Value {
        serde_json::to_value(&self.diagnostic).unwrap_or_default()
    }
    fn subscribe(&self) -> tokio::sync::broadcast::Receiver<openworker_runtime::store::Event> {
        self.events.subscribe()
    }
    async fn dispatch(&self, _: &str, _: serde_json::Value) -> Result<serde_json::Value> {
        Err(self.diagnostic.clone().into())
    }
    async fn shutdown(&self) -> Result<()> {
        Ok(())
    }
}
impl openworker_runtime::protocol::Endpoint for Endpoint {
    fn request_error(&self, error: &anyhow::Error) -> serde_json::Value {
        error
            .downcast_ref::<crate::AgentDefinitionConflict>()
            .filter(|diagnostic| diagnostic.valid())
            .and_then(|diagnostic| serde_json::to_value(diagnostic).ok())
            .unwrap_or(serde_json::Value::Null)
    }
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
    main_with_integrations(registry, Default::default())
}
/// Entry point for a managed harness executable with additional trusted crates.
pub fn main_with_integrations(registry: ToolRegistry, integrations: crate::integrations::Registry) {
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
        let mut config: HostConfig = serde_json::from_str(&raw)
            .map_err(|_| anyhow::anyhow!("Invalid host launch configuration"))?;
        config.desktop.integration_registry = integrations;
        config.validate()?;
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()?
            .block_on(async {
                match Host::open(&config, registry) {
                    Ok(host) => openworker_runtime::protocol::serve_endpoint(Endpoint(host)).await,
                    Err(error) => match error
                        .downcast_ref::<crate::AgentDefinitionConflict>()
                        .filter(|d| d.valid())
                    {
                        Some(diagnostic) => {
                            openworker_runtime::protocol::serve_endpoint(StartupConflict {
                                diagnostic: diagnostic.clone(),
                                events: tokio::sync::broadcast::channel(1).0,
                            })
                            .await
                        }
                        None => Err(error),
                    },
                }
            })
    })();
    if result.is_err() {
        eprintln!("Harness host failed; inspect configuration and reconcile durable sessions");
        std::process::exit(1);
    }
}
