//! Export configuration remains available without linking any exporter.
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    /// Collector or vendor OTLP base URL. Signal paths (`/v1/traces` ...) are
    /// appended. A base path is allowed, e.g. `https://cloud.langfuse.com/api/public/otel`.
    pub endpoint: String,
    #[serde(default = "default_deployment")]
    pub deployment: String,
    /// Extra request headers, typically authentication. Requires HTTPS unless
    /// the endpoint is loopback.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub headers: Vec<Header>,
    /// Signals to export. Defaults to all three; vendors that only ingest
    /// traces (e.g. Langfuse) should select `traces` only.
    #[serde(default = "all_signals")]
    pub signals: Vec<Signal>,
    /// Explicit receiver dialect; standard OTLP remains the default.
    #[serde(default)]
    pub acknowledgement: Acknowledgement,
    /// Opt-in content capture. Off by default: records stay content-free.
    #[serde(default)]
    pub content: crate::ContentPolicy,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            endpoint: String::new(),
            deployment: default_deployment(),
            headers: vec![],
            signals: all_signals(),
            acknowledgement: Acknowledgement::default(),
            content: crate::ContentPolicy::default(),
        }
    }
}
fn default_deployment() -> String {
    "default".into()
}
fn all_signals() -> Vec<Signal> {
    vec![Signal::Traces, Signal::Metrics, Signal::Logs]
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Signal {
    Traces,
    Metrics,
    Logs,
}
/// Langfuse v4 currently returns a queue-job JSON receipt for nonempty batches.
/// Select it explicitly; arbitrary HTTP 200 bodies are never assumed successful.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Acknowledgement {
    #[default]
    Otlp,
    LangfuseV4,
}
/// One request header. Exactly one of `value` (non-secret literal, e.g. a
/// protocol version) or `value_env` (name of an environment variable holding
/// the secret, e.g. `Basic <base64>` or an API key) must be set.
#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Header {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value_env: Option<String>,
}
impl Header {
    /// Secret header whose value is read from environment variable `env`.
    pub fn secret(name: impl Into<String>, env: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            value_env: Some(env.into()),
            ..Self::default()
        }
    }
    /// Non-secret literal header.
    pub fn literal(name: impl Into<String>, value: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            value: Some(value.into()),
            ..Self::default()
        }
    }
}

impl Config {
    /// Direct Langfuse v4 export. The trusted host resolves the credential environment variable.
    pub fn langfuse(endpoint: impl Into<String>, authorization_env: impl Into<String>) -> Self {
        Self {
            endpoint: endpoint.into(),
            signals: vec![Signal::Traces],
            acknowledgement: Acknowledgement::LangfuseV4,
            headers: vec![
                Header::secret("Authorization", authorization_env),
                Header::literal("x-langfuse-ingestion-version", "4"),
            ],
            content: crate::ContentPolicy {
                capture: crate::Capture::Metadata,
                ..Default::default()
            },
            ..Default::default()
        }
    }
}
