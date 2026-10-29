//! Small role/session-authorized HTTP adapter. Not physical tenant isolation or
//! a replacement for the Desktop/SDK stdio protocol. Never launches a local tool.
use anyhow::{bail, Context, Result};
use openworker_runtime::{execution::Backend, model::Model, runtime::Runtime};
use openworker_store_postgres::{Config as DbConfig, PostgresStore, Tls};
use serde::Deserialize;
use std::{path::PathBuf, sync::Arc};
mod artifacts;
mod authorization;
pub mod http;
pub mod kubernetes;
pub mod management;
pub mod operations;
pub mod readiness;
pub mod recording;
pub mod service;
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    #[serde(default)]
    pub observability: Option<recording::Options>,
    /// Process-local health only, never accepted from a configuration file.
    #[serde(skip)]
    pub recording_stats: Option<openworker_observability::Statistics>,
    #[serde(default)]
    pub kubernetes: Option<kubernetes::Config>,
    #[serde(default)]
    pub service: Option<service::ServiceConfig>,
    /// Restore inspection only: no HTTP mutations, workers or provider access.
    #[serde(default)]
    pub recovery_read_only: bool,
    pub operational_metrics: Option<operations::Options>,
    pub artifacts: Option<ArtifactConfig>,
    pub telemetry: Option<openworker_telemetry::otlp::Config>,
    pub database_env: String,
    pub schema: String,
    #[serde(default)]
    pub database_loopback_plaintext: bool,
    pub database_ca: Option<PathBuf>,
    pub state_root: PathBuf,
    pub bundle: PathBuf,
    pub runner: PathBuf,
    pub model: String,
    #[serde(default)]
    pub model_provider: openworker_runtime::provider::Kind,
    #[serde(default)]
    pub model_connections: Vec<ModelConnection>,
    #[serde(default)]
    pub scripted: bool,
    /// Local bootstrap/admin CLI credential; API requests authenticate against PG.
    pub api_token_file: PathBuf,
    pub actor: String,
    pub openshell: OpenShellConfig,
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelConnection {
    pub id: String,
    pub provider: openworker_runtime::provider::Kind,
    /// Stable deployment-owned non-secret identity, identical on API and workers.
    pub identity: String,
    pub key_env: String,
}
#[derive(Clone, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ArtifactConfig {
    Filesystem {
        root: PathBuf,
        deployment: String,
        #[serde(default)]
        shared_durable: bool,
    },
    S3 {
        deployment: String,
        bucket: String,
        region: String,
        endpoint: Option<String>,
        #[serde(default)]
        ca_file: Option<PathBuf>,
    },
}
impl ArtifactConfig {
    pub fn build(&self) -> Result<openworker_artifacts::Store> {
        match self {
            Self::Filesystem {
                root,
                deployment,
                shared_durable,
            } => openworker_artifacts::filesystem::Filesystem::open(
                root,
                deployment,
                *shared_durable,
                true,
            ),
            Self::S3 {
                deployment,
                bucket,
                region,
                endpoint,
                ca_file,
            } => openworker_artifacts::s3::Config {
                deployment: deployment.clone(),
                bucket: bucket.clone(),
                region: region.clone(),
                endpoint: endpoint.clone(),
                ca_file: ca_file.clone(),
            }
            .build(None),
        }
    }
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpenShellConfig {
    pub cpu: Option<u16>,
    pub memory_mb: Option<u32>,
    pub identity: String,
    pub endpoint: String,
    pub certificates: PathBuf,
    pub tls_server_name: Option<String>,
    pub workspace: String,
    pub image: String,
}
impl Config {
    pub fn database(&self) -> Result<DbConfig> {
        let mut db = DbConfig::new(
            std::env::var(&self.database_env)
                .context("Database environment variable unavailable")?,
            self.schema.clone(),
        );
        db.tls = if self.database_loopback_plaintext {
            Tls::LoopbackPlaintext
        } else {
            Tls::Verified {
                ca_file: self.database_ca.clone(),
            }
        };
        Ok(db)
    }
    #[cfg(not(windows))]
    pub fn provider(&self) -> Result<Arc<openworker_runtime::environment_openshell::OpenShell>> {
        self.require_execution()?;
        let c = &self.openshell;
        Ok(Arc::new(
            openworker_runtime::environment_openshell::OpenShell::new(
                "openshell-k8s".into(),
                c.identity.clone(),
                openworker_runtime::sandbox::ExecConnectionConfig {
                    endpoint: c.endpoint.clone(),
                    certificates: c.certificates.clone(),
                    tls_server_name: c.tls_server_name.clone(),
                    workspace: c.workspace.clone(),
                },
                c.image.clone(),
            )?
            .with_resources(c.cpu.unwrap_or(1), c.memory_mb.unwrap_or(512))?,
        ))
    }
    #[cfg(not(windows))]
    pub fn runtime(&self, node: &str, store: PostgresStore) -> Result<Arc<Runtime>> {
        self.runtime_with_credentials(node, store, true)
    }
    #[cfg(not(windows))]
    pub fn api_runtime(&self, node: &str, store: PostgresStore) -> Result<Arc<Runtime>> {
        self.runtime_with_credentials(node, store, false)
    }
    #[cfg(not(windows))]
    fn runtime_with_credentials(
        &self,
        node: &str,
        store: PostgresStore,
        load_credentials: bool,
    ) -> Result<Arc<Runtime>> {
        if !openworker_runtime::bundle::safe_name(node) {
            bail!("Invalid node identity");
        }
        let telemetry = store.telemetry();
        let mut rt = Runtime::open_instrumented(
            &self.state_root.join(node),
            &self.bundle,
            &self.runner,
            Model::new(self.model.clone(), self.scripted).with_provider(self.model_provider),
            Backend::Openshell,
            None,
            None,
            Default::default(),
            Some(Box::new(store)),
            telemetry,
        )?;
        if !self.recovery_read_only {
            anyhow::ensure!(
                self.model_connections.len() <= 16,
                "Too many model connections"
            );
            let mut seen = std::collections::BTreeSet::new();
            for connection in &self.model_connections {
                anyhow::ensure!(
                    seen.insert(&connection.id)
                        && !connection.key_env.is_empty()
                        && connection.key_env.len() <= 100
                        && connection
                            .key_env
                            .bytes()
                            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
                    "Invalid or duplicate model connection"
                );
                if load_credentials {
                    let key = std::env::var(&connection.key_env)
                        .context("Configured model credential environment variable missing")?;
                    rt.connections.register_host(
                        &connection.id,
                        connection.provider,
                        &connection.identity,
                        key.into(),
                    )?;
                } else {
                    rt.connections.register_host_reference(
                        &connection.id,
                        connection.provider,
                        &connection.identity,
                    )?;
                }
            }
            let mut registry = openworker_runtime::environment::Registry::default();
            registry.register(self.provider()?)?;
            Runtime::configure_environments(&mut rt, Arc::new(registry))?;
        }
        Ok(rt)
    }
    pub fn require_execution(&self) -> Result<()> {
        anyhow::ensure!(!self.recovery_read_only, "recovery_read_only");
        Ok(())
    }
    #[cfg(windows)]
    pub fn runtime(&self, _node: &str, _store: PostgresStore) -> Result<Arc<Runtime>> {
        bail!("This Kubernetes server reference is qualified on Linux/macOS controllers only")
    }
    #[cfg(windows)]
    pub fn api_runtime(&self, node: &str, store: PostgresStore) -> Result<Arc<Runtime>> {
        self.runtime(node, store)
    }
}

#[cfg(test)]
mod recovery_tests {
    use super::*;
    #[test]
    fn recovery_is_explicit_and_denies_provider_access() {
        let mut c: Config = serde_json::from_str(include_str!("../config.example.json")).unwrap();
        assert!(!c.recovery_read_only);
        assert!(c.require_execution().is_ok());
        c.recovery_read_only = true;
        assert_eq!(
            c.require_execution().unwrap_err().to_string(),
            "recovery_read_only"
        );
        #[cfg(not(windows))]
        assert!(c.provider().is_err());
    }
}
