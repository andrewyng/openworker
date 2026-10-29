//! Opt-in OTLP/HTTP. No inherited proxy, environment headers, resource
//! detector or redirect. Authentication headers are explicit, and their secret
//! values are read from named environment variables at startup, never from the
//! configuration file, URL or process arguments.
use crate::{otel::OtelSink, Controller, Telemetry};
use anyhow::{ensure, Context, Result};
use opentelemetry_http::{HttpClient, HttpError};
use opentelemetry_otlp::{Protocol, RetryPolicy, WithExportConfig, WithHttpConfig};
use opentelemetry_sdk::{
    logs::{BatchLogProcessor, SdkLoggerProvider},
    metrics::{PeriodicReader, SdkMeterProvider},
    trace::{BatchConfigBuilder, BatchSpanProcessor, SpanProcessor},
    Resource,
};
use std::{sync::Arc, time::Duration};

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
    fn resolve(&self) -> Result<(http::HeaderName, http::HeaderValue)> {
        let name = http::HeaderName::from_bytes(self.name.trim().to_ascii_lowercase().as_bytes())
            .context("Invalid telemetry header name")?;
        ensure!(
            !matches!(
                name.as_str(),
                "host"
                    | "content-type"
                    | "content-length"
                    | "content-encoding"
                    | "transfer-encoding"
                    | "connection"
                    | "keep-alive"
                    | "te"
                    | "trailer"
                    | "upgrade"
                    | "expect"
            ) && !name.as_str().starts_with("proxy-"),
            "Telemetry header {} is managed by the exporter",
            name
        );
        let raw = match (&self.value, &self.value_env) {
            (Some(v), None) => v.clone(),
            (None, Some(env)) => {
                ensure!(
                    !env.is_empty()
                        && env.len() <= 100
                        && env
                            .bytes()
                            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
                    "Invalid telemetry credential reference"
                );
                std::env::var(env)
                    .with_context(|| format!("Telemetry credential variable {env} is not set"))?
            }
            _ => anyhow::bail!("Telemetry header {name} needs exactly one of value/value_env"),
        };
        ensure!(raw.len() <= 4096, "Telemetry header {name} value too long");
        let mut value =
            http::HeaderValue::from_str(&raw).context("Invalid telemetry header value")?;
        value.set_sensitive(self.value_env.is_some());
        Ok((name, value))
    }
}
#[derive(Clone)]
struct Client {
    client: reqwest::blocking::Client,
    headers: Arc<Vec<(http::HeaderName, http::HeaderValue)>>,
}
impl std::fmt::Debug for Client {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // Never print header values; they may be credentials.
        f.debug_struct("Client")
            .field(
                "headers",
                &self.headers.iter().map(|(n, _)| n).collect::<Vec<_>>(),
            )
            .finish()
    }
}
#[async_trait::async_trait]
impl HttpClient for Client {
    async fn send_bytes(
        &self,
        request: http::Request<bytes::Bytes>,
    ) -> Result<http::Response<bytes::Bytes>, HttpError> {
        // The SDK also reads OTEL_*_HEADERS. Deliberately don't copy those headers
        // to the wire. The explicitly configured collector is the only authority.
        let mut post = self
            .client
            .post(request.uri().to_string())
            .header("content-type", "application/x-protobuf");
        for (name, value) in self.headers.iter() {
            post = post.header(name.clone(), value.clone());
        }
        let response = post.body(request.into_body()).send()?;
        let status = response.status();
        // OTLP success responses are tiny. Bound an adversarial collector body.
        use std::io::Read;
        let mut body = Vec::new();
        response.take(65537).read_to_end(&mut body)?;
        if body.len() > 65536 {
            return Err("Collector response exceeds limit".into());
        }
        Ok(http::Response::builder()
            .status(status)
            .body(bytes::Bytes::from(body))?)
    }
}
impl Config {
    pub fn start(&self, role: &'static str) -> Result<(Telemetry, Controller)> {
        Telemetry::start_with_content(self.build_sink(role)?, 2048, self.content.clone())
    }
    /// Build instance-owned export components without installing a dispatcher.
    /// Trusted hosts can compose an independent local recorder before export.
    pub fn build_sink(&self, role: &'static str) -> Result<OtelSink> {
        ensure!(
            !self.deployment.is_empty()
                && self.deployment.len() <= 64
                && self
                    .deployment
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-'),
            "Invalid telemetry deployment identifier"
        );
        let url = reqwest::Url::parse(&self.endpoint).context("Invalid telemetry endpoint")?;
        ensure!(
            url.username().is_empty()
                && url.password().is_none()
                && url.query().is_none()
                && url.fragment().is_none(),
            "Telemetry endpoint must not contain credentials/query/fragment"
        );
        let path = url.path().trim_end_matches('/');
        ensure!(
            !["/v1/traces", "/v1/metrics", "/v1/logs"]
                .iter()
                .any(|signal| path.ends_with(signal)),
            "Telemetry endpoint is a base URL; signal paths are appended"
        );
        ensure!(
            !self.signals.is_empty() && self.signals.len() <= 3,
            "Select at least one telemetry signal"
        );
        ensure!(self.headers.len() <= 8, "At most 8 telemetry headers");
        self.content.validate()?;
        let loopback = url.host_str().is_some_and(|h| {
            h == "localhost"
                || h.trim_matches(['[', ']'])
                    .parse::<std::net::IpAddr>()
                    .is_ok_and(|ip| ip.is_loopback())
        });
        ensure!(
            url.scheme() == "https" || (url.scheme() == "http" && loopback),
            "Telemetry requires HTTPS except explicit loopback HTTP"
        );
        let endpoint = url.as_str().trim_end_matches('/');
        let mut headers = Vec::with_capacity(self.headers.len());
        for h in &self.headers {
            let (name, value) = h.resolve()?;
            ensure!(
                headers.iter().all(|(n, _)| *n != name),
                "Duplicate telemetry header {name}"
            );
            headers.push((name, value));
        }
        let client = Client {
            client: reqwest::blocking::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .timeout(Duration::from_secs(3))
                .connect_timeout(Duration::from_secs(3))
                .build()?,
            headers: Arc::new(headers),
        };
        let resource = Resource::builder_empty()
            .with_attributes([
                opentelemetry::KeyValue::new("service.name", "openworker"),
                opentelemetry::KeyValue::new(
                    "service.instance.id",
                    uuid::Uuid::new_v4().to_string(),
                ),
                opentelemetry::KeyValue::new("openworker.role", role),
                opentelemetry::KeyValue::new("openworker.deployment", self.deployment.clone()),
            ])
            .build();
        let enabled = |signal| self.signals.contains(&signal);
        let traces = if enabled(Signal::Traces) {
            Some(self.traces(endpoint, &client, &resource)?)
        } else {
            None
        };
        let logs = if enabled(Signal::Logs) {
            self.logs(endpoint, &client, &resource)?
        } else {
            SdkLoggerProvider::builder().build()
        };
        let metrics = if enabled(Signal::Metrics) {
            self.metrics(endpoint, &client, &resource)?
        } else {
            SdkMeterProvider::builder().build()
        };
        Ok(OtelSink::with_parts(traces, logs, metrics))
    }
    fn traces(
        &self,
        endpoint: &str,
        client: &Client,
        resource: &Resource,
    ) -> Result<Arc<dyn SpanProcessor>> {
        let traces = opentelemetry_otlp::SpanExporter::builder()
            .with_http()
            .with_endpoint(format!("{endpoint}/v1/traces"))
            .with_protocol(Protocol::HttpBinary)
            .with_timeout(Duration::from_secs(3))
            .with_retry_policy(RetryPolicy::default().with_max_retries(0))
            .with_http_client(client.clone())
            .build()?;
        let mut traces = BatchSpanProcessor::builder(traces)
            .with_batch_config(
                BatchConfigBuilder::default()
                    .with_max_queue_size(2048)
                    .with_max_export_batch_size(128)
                    .with_scheduled_delay(Duration::from_secs(1))
                    .build(),
            )
            .build();
        traces.set_resource(resource);
        Ok(Arc::new(traces))
    }
    fn logs(
        &self,
        endpoint: &str,
        client: &Client,
        resource: &Resource,
    ) -> Result<SdkLoggerProvider> {
        let logs = opentelemetry_otlp::LogExporter::builder()
            .with_http()
            .with_endpoint(format!("{endpoint}/v1/logs"))
            .with_protocol(Protocol::HttpBinary)
            .with_timeout(Duration::from_secs(3))
            .with_retry_policy(RetryPolicy::default().with_max_retries(0))
            .with_http_client(client.clone())
            .build()?;
        Ok(SdkLoggerProvider::builder()
            .with_resource(resource.clone())
            .with_log_processor(
                BatchLogProcessor::builder(logs)
                    .with_batch_config(
                        opentelemetry_sdk::logs::BatchConfigBuilder::default()
                            .with_max_queue_size(2048)
                            .with_max_export_batch_size(128)
                            .with_scheduled_delay(Duration::from_secs(1))
                            .build(),
                    )
                    .build(),
            )
            .build())
    }
    fn metrics(
        &self,
        endpoint: &str,
        client: &Client,
        resource: &Resource,
    ) -> Result<SdkMeterProvider> {
        let metrics = opentelemetry_otlp::MetricExporter::builder()
            .with_http()
            .with_endpoint(format!("{endpoint}/v1/metrics"))
            .with_protocol(Protocol::HttpBinary)
            .with_timeout(Duration::from_secs(3))
            .with_retry_policy(RetryPolicy::default().with_max_retries(0))
            .with_http_client(client.clone())
            .build()?;
        Ok(SdkMeterProvider::builder()
            .with_resource(resource.clone())
            .with_reader(
                PeriodicReader::builder(metrics)
                    .with_interval(Duration::from_secs(10))
                    .build(),
            )
            .build())
    }
}
