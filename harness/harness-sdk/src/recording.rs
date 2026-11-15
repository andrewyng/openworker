//! Optional local recording configuration. Does not initialize storage on import.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecordingConfig {
    #[serde(default = "evaluation_capacity")]
    pub max_pending_evaluations: u32,
    #[serde(default = "retention")]
    pub evaluation_retention_days: u16,
    #[serde(default)]
    pub evaluation_sinks: Vec<EvaluationSink>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence: Option<EvidencePolicy>,
    #[serde(default)]
    pub evaluations: bool,
    pub database: RecordingDatabase,
    #[serde(default = "deployment")]
    pub deployment: String,
    #[serde(default = "retention")]
    pub retention_days: u16,
    #[serde(default = "capacity")]
    pub queue_capacity: usize,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum RecordingDatabase {
    Sqlite {
        path: PathBuf,
    },
    Postgres {
        connection_env: String,
        schema: String,
        ca_file: Option<PathBuf>,
        #[serde(default)]
        loopback_plaintext: bool,
    },
}
fn deployment() -> String {
    "sdk".into()
}
fn evaluation_capacity() -> u32 {
    10_000
}
fn retention() -> u16 {
    30
}
fn capacity() -> usize {
    1024
}
impl RecordingConfig {
    pub fn sqlite(path: impl Into<PathBuf>) -> Self {
        Self {
            database: RecordingDatabase::Sqlite { path: path.into() },
            evaluations: false,
            max_pending_evaluations: evaluation_capacity(),
            evaluation_retention_days: retention(),
            evaluation_sinks: vec![],
            evidence: None,
            deployment: deployment(),
            retention_days: retention(),
            queue_capacity: capacity(),
        }
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (1..=1_000_000).contains(&self.max_pending_evaluations),
            "Invalid evaluation backlog limit"
        );
        ensure!(
            !self.deployment.is_empty()
                && self.deployment.len() <= 64
                && self
                    .deployment
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
            "Invalid recording deployment"
        );
        ensure!(
            (1..=365).contains(&self.retention_days) && (1..=2048).contains(&self.queue_capacity),
            "Invalid recording retention or queue capacity"
        );
        ensure!(
            (1..=3650).contains(&self.evaluation_retention_days),
            "Invalid evaluation retention"
        );
        ensure!(
            self.evaluation_sinks.len() <= 8,
            "At most eight evaluation destinations"
        );
        let mut names = std::collections::BTreeSet::new();
        for sink in &self.evaluation_sinks {
            sink.validate()?;
            ensure!(
                names.insert(&sink.name),
                "Duplicate evaluation destination name"
            );
        }
        if let Some(p) = &self.evidence {
            anyhow::ensure!(
                (256..=65536).contains(&p.max_bytes),
                "Evidence byte limit must be 256..65536"
            );
        }
        match &self.database {
            RecordingDatabase::Sqlite { path } => ensure!(
                path.is_absolute() && path.file_name().is_some(),
                "Recording path must be an absolute file path"
            ),
            RecordingDatabase::Postgres {
                connection_env,
                schema,
                ca_file,
                ..
            } => {
                ensure!(
                    !connection_env.is_empty()
                        && connection_env.len() <= 100
                        && connection_env
                            .bytes()
                            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
                    "Invalid recording credential reference"
                );
                ensure!(
                    schema.starts_with("ow_")
                        && schema.len() <= 48
                        && schema
                            .bytes()
                            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_'),
                    "Invalid recording schema"
                );
                ensure!(
                    ca_file.as_ref().is_none_or(|p| p.is_absolute()),
                    "Recording CA path must be absolute"
                );
            }
        }
        Ok(())
    }
}

/// A trusted SDK query. Service deployments apply their own session authorization.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TraceQuery {
    pub session_id: String,
    pub turn_id: String,
    #[serde(default)]
    pub after: u64,
    pub through: Option<u64>,
    #[serde(default = "page_size")]
    pub limit: u16,
}
fn page_size() -> u16 {
    100
}
impl TraceQuery {
    pub fn turn(session_id: impl Into<String>, turn_id: impl Into<String>) -> Self {
        Self {
            session_id: session_id.into(),
            turn_id: turn_id.into(),
            after: 0,
            through: None,
            limit: page_size(),
        }
    }
    pub fn validate(&self) -> Result<()> {
        for id in [&self.session_id, &self.turn_id] {
            ensure!(
                !id.is_empty()
                    && id.len() <= 80
                    && id
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
                "Invalid trace scope"
            );
        }
        ensure!(
            (1..=200).contains(&self.limit)
                && self.after <= i64::MAX as u64
                && self
                    .through
                    .is_none_or(|n| n >= self.after && n <= i64::MAX as u64),
            "Invalid trace page"
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn configuration_is_explicit_bounded_and_secret_free() {
        let path = std::env::temp_dir().join("ow-observability-contract.sqlite");
        let mut c = RecordingConfig::sqlite(path);
        assert!(c.validate().is_ok());
        c.queue_capacity = 0;
        assert!(c.validate().is_err());
        assert!(serde_json::from_value::<RecordingConfig>(serde_json::json!({"database":{"kind":"postgres","connection":"secret","connection_env":"PG","schema":"ow_obs"}})).is_err());
    }
    #[test]
    fn trace_query_rejects_raw_hash_scope_and_unbounded_pages() {
        let mut q = TraceQuery::turn("session-1", "turn-2");
        assert!(q.validate().is_ok());
        q.limit = 201;
        assert!(q.validate().is_err());
        assert!(serde_json::from_value::<TraceQuery>(
            serde_json::json!({"session_hash":"other","session_id":"s","turn_id":"t"})
        )
        .is_err());
    }
}

/// Query adapter shared by embedded and managed-process SDK clients.
#[derive(Clone)]
pub struct Observability {
    client: crate::HarnessClient,
}
impl Observability {
    pub(crate) fn new(client: crate::HarnessClient) -> Self {
        Self { client }
    }
    #[cfg(feature = "evaluations")]
    pub async fn record_evaluation(
        &self,
        query: TraceQuery,
        result: openworker_observability::evaluations::Input,
        span_id: Option<String>,
    ) -> crate::Result<serde_json::Value> {
        query
            .validate()
            .map_err(|e| crate::Error::InvalidInput(e.to_string()))?;
        self.client.request("observability/evaluation-record",serde_json::json!({"session_id":query.session_id,"turn_id":query.turn_id,"span_id":span_id,"result":result})).await
    }
    #[cfg(feature = "evaluations")]
    pub async fn evaluations(
        &self,
        query: TraceQuery,
    ) -> crate::Result<openworker_observability::evaluations::Page> {
        query
            .validate()
            .map_err(|e| crate::Error::InvalidInput(e.to_string()))?;
        let value = self
            .client
            .request(
                "observability/evaluation-list",
                serde_json::to_value(query)
                    .map_err(|e| crate::Error::InvalidInput(e.to_string()))?,
            )
            .await?;
        serde_json::from_value(value).map_err(|e| crate::Error::Remote(e.to_string()))
    }
    pub async fn flush(&self, timeout_ms: u64) -> crate::Result<serde_json::Value> {
        self.client
            .request(
                "observability/flush",
                serde_json::json!({"timeout_ms":timeout_ms}),
            )
            .await
    }
    pub async fn health(&self) -> crate::Result<serde_json::Value> {
        self.client
            .request("observability/health", serde_json::json!({}))
            .await
    }
    pub async fn turns(
        &self,
        session_id: &str,
        after: i64,
        limit: u16,
    ) -> crate::Result<serde_json::Value> {
        self.client
            .request(
                "observability/turns",
                serde_json::json!({"session_id":session_id,"after":after,"limit":limit}),
            )
            .await
    }
    pub async fn subtree_summary(&self, query: TraceQuery) -> crate::Result<serde_json::Value> {
        query
            .validate()
            .map_err(|e| crate::Error::InvalidInput(e.to_string()))?;
        self.client
            .request(
                "observability/subtree-summary",
                serde_json::to_value(query)
                    .map_err(|e| crate::Error::InvalidInput(e.to_string()))?,
            )
            .await
    }
    pub async fn children(&self, query: TraceQuery) -> crate::Result<serde_json::Value> {
        query
            .validate()
            .map_err(|e| crate::Error::InvalidInput(e.to_string()))?;
        self.client
            .request(
                "observability/children",
                serde_json::to_value(query)
                    .map_err(|e| crate::Error::InvalidInput(e.to_string()))?,
            )
            .await
    }
    #[cfg(feature = "recording")]
    pub async fn timeline(&self, query: TraceQuery) -> crate::Result<Timeline> {
        query
            .validate()
            .map_err(|e| crate::Error::InvalidInput(e.to_string()))?;
        let v = self
            .client
            .request(
                "observability/timeline",
                serde_json::to_value(query)
                    .map_err(|e| crate::Error::InvalidInput(e.to_string()))?,
            )
            .await?;
        serde_json::from_value(v).map_err(|e| crate::Error::Remote(e.to_string()))
    }
    #[cfg(feature = "recording")]
    pub async fn summary(&self, query: TraceQuery) -> crate::Result<TurnSummary> {
        query
            .validate()
            .map_err(|e| crate::Error::InvalidInput(e.to_string()))?;
        let v = self
            .client
            .request(
                "observability/summary",
                serde_json::to_value(query)
                    .map_err(|e| crate::Error::InvalidInput(e.to_string()))?,
            )
            .await?;
        serde_json::from_value(v).map_err(|e| crate::Error::Remote(e.to_string()))
    }
}

/// Local evidence access is a separate opt-in from remote telemetry capture.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvidencePolicy {
    #[serde(default)]
    pub user_input: bool,
    #[serde(default)]
    pub assistant_output: bool,
    #[serde(default)]
    pub tool_arguments: bool,
    #[serde(default)]
    pub tool_results: bool,
    pub max_bytes: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvaluationSink {
    pub name: String,
    pub url: String,
    pub authorization_env: String,
    #[serde(default)]
    pub include_explanation: bool,
}
impl EvaluationSink {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.name.is_empty()
                && self.name.len() <= 64
                && self
                    .name
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
            "Invalid evaluation destination name"
        );
        ensure!(
            !self.authorization_env.is_empty()
                && self.authorization_env.len() <= 100
                && self
                    .authorization_env
                    .bytes()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
            "Invalid evaluation credential reference"
        );
        let u = reqwest::Url::parse(&self.url)?;
        ensure!(
            u.username().is_empty()
                && u.password().is_none()
                && u.query().is_none()
                && u.fragment().is_none()
                && u.path() == "/"
                && (u.scheme() == "https"
                    || (u.scheme() == "http"
                        && u.host_str().is_some_and(|h| h == "localhost"
                            || h.trim_matches(['[', ']'])
                                .parse::<std::net::IpAddr>()
                                .is_ok_and(|ip| ip.is_loopback())))),
            "Evaluation destination must be HTTPS or explicit loopback origin"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunIdentity {
    pub session_id: String,
    pub turn_id: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RecordedTurn {
    pub session_id: String,
    pub turn_id: String,
    pub status: Option<String>,
    pub agent: String,
    pub name: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TurnPage {
    pub turns: Vec<RecordedTurn>,
    pub next_cursor: Option<i64>,
    pub through: i64,
}
#[cfg(feature = "recording")]
#[derive(Debug, Serialize, Deserialize)]
pub struct Timeline {
    pub run: RunIdentity,
    pub page: openworker_observability::query::Page,
    pub coverage: serde_json::Value,
}
#[cfg(feature = "recording")]
#[derive(Debug, Serialize, Deserialize)]
pub struct TurnSummary {
    pub run: RunIdentity,
    pub summary: openworker_observability::summary::Summary,
}
