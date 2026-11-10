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
    fn resolve(&self) -> Result<(http::HeaderName, http::HeaderValue)> {
        let name = http::HeaderName::from_bytes(self.name.trim().to_ascii_lowercase().as_bytes())
            .context("Invalid telemetry header name")?;
        ensure!(
            !matches!(
                name.as_str(),
                "host"
                    | "accept"
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
            (Some(v), None) => {
                ensure!(
                    !matches!(name.as_str(), "authorization" | "api-key" | "x-api-key"),
                    "Telemetry authentication must use value_env"
                );
                v.clone()
            }
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
    acknowledgement: Acknowledgement,
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
            .header("accept", "application/x-protobuf")
            .header("content-type", "application/x-protobuf");
        let signal = request
            .uri()
            .path()
            .rsplit('/')
            .next()
            .unwrap_or_default()
            .to_owned();
        for (name, value) in self.headers.iter() {
            post = post.header(name.clone(), value.clone());
        }
        let response = post.body(request.into_body()).send()?;
        let status = response.status();
        let json_ack = response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .is_some_and(|v| {
                v.split(';')
                    .next()
                    .unwrap_or_default()
                    .trim()
                    .eq_ignore_ascii_case("application/json")
            });
        // OTLP success responses are tiny. Bound an adversarial collector body.
        use std::io::Read;
        let mut body = Vec::new();
        response.take(65537).read_to_end(&mut body)?;
        if body.len() > 65536 {
            return Err("Collector response exceeds limit".into());
        }
        if status.is_success() {
            if json_ack {
                if self.acknowledgement == Acknowledgement::LangfuseV4 {
                    langfuse_acknowledgement(&signal, &body)?;
                } else {
                    json_acknowledgement(&signal, &body)?;
                }
                // The official exporter is configured for binary requests. A
                // validated JSON response (Langfuse) must not be decoded as proto.
                body.clear();
            } else {
                acknowledgement(&signal, &body)?;
            }
        } else {
            // The SDK may log rejection bodies. Do not let an untrusted collector
            // echo payloads/credentials into diagnostics. Status is sufficient.
            body.clear();
        }
        Ok(http::Response::builder()
            .status(status)
            .body(bytes::Bytes::from(body))?)
    }
}
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct JsonAck {
    #[serde(default, rename = "partialSuccess", alias = "partial_success")]
    partial: Option<JsonPartial>,
}
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct JsonPartial {
    #[serde(default, rename = "rejectedSpans", alias = "rejected_spans")]
    spans: Option<JsonCount>,
    #[serde(default, rename = "rejectedDataPoints", alias = "rejected_data_points")]
    metrics: Option<JsonCount>,
    #[serde(default, rename = "rejectedLogRecords", alias = "rejected_log_records")]
    logs: Option<JsonCount>,
    #[serde(default, rename = "errorMessage", alias = "error_message")]
    message: Option<String>,
}
#[derive(serde::Deserialize)]
#[serde(untagged)]
enum JsonCount {
    Int(i64),
    Text(String),
}
fn langfuse_acknowledgement(signal: &str, body: &[u8]) -> Result<(), HttpError> {
    if json_acknowledgement(signal, body).is_ok() {
        return Ok(());
    }
    // Never reinterpret a rejected OTLP partial-success response as a job receipt.
    let value: serde_json::Value = serde_json::from_slice(body)
        .map_err(|_| -> HttpError { "Invalid Langfuse acknowledgement".into() })?;
    if value.get("partialSuccess").is_some() || value.get("partial_success").is_some() {
        return Err("Invalid Langfuse acknowledgement".into());
    }
    // Validate only receipt markers. Do not retain/log server queue metadata,
    // storage references, authentication scope or receiver-supplied error text.
    #[derive(serde::Deserialize)]
    struct Receipt {
        name: String,
        id: String,
        data: Job,
    }
    #[derive(serde::Deserialize)]
    struct Job {
        name: String,
        payload: Payload,
    }
    #[derive(serde::Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct Payload {
        ingestion_version: String,
        auth_check: Auth,
    }
    #[derive(serde::Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct Auth {
        valid_key: bool,
    }
    let receipt: Receipt = serde_json::from_slice(body)
        .map_err(|_| -> HttpError { "Invalid Langfuse acknowledgement".into() })?;
    if signal != "traces"
        || receipt.name != "otel-ingestion-job"
        || receipt.data.name != "otel-ingestion-job"
        || receipt.id.is_empty()
        || receipt.id.len() > 256
        || receipt.data.payload.ingestion_version != "4"
        || !receipt.data.payload.auth_check.valid_key
    {
        return Err("Invalid Langfuse acknowledgement".into());
    }
    Ok(())
}
fn json_acknowledgement(signal: &str, body: &[u8]) -> Result<(), HttpError> {
    let invalid = || -> HttpError { "Invalid collector acknowledgement".into() };
    let ack: JsonAck = serde_json::from_slice(body).map_err(|_| invalid())?;
    if let Some(p) = ack.partial {
        let value = match signal {
            "traces" if p.metrics.is_none() && p.logs.is_none() => p.spans,
            "metrics" if p.spans.is_none() && p.logs.is_none() => p.metrics,
            "logs" if p.spans.is_none() && p.metrics.is_none() => p.logs,
            _ => return Err(invalid()),
        };
        let count = match value {
            None => 0,
            Some(JsonCount::Int(n)) => n,
            Some(JsonCount::Text(n)) => {
                if n.is_empty() || n.len() > 19 || !n.bytes().all(|b| b.is_ascii_digit()) {
                    return Err(invalid());
                }
                n.parse::<i64>().map_err(|_| invalid())?
            }
        };
        if count < 0 {
            return Err(invalid());
        }
        if count > 0 || p.message.is_some_and(|m| !m.is_empty()) {
            return Err("Collector reported incomplete acceptance".into());
        }
    }
    Ok(())
}
fn acknowledgement(signal: &str, body: &[u8]) -> Result<(), HttpError> {
    use opentelemetry_proto::tonic::collector::{
        logs::v1::ExportLogsServiceResponse, metrics::v1::ExportMetricsServiceResponse,
        trace::v1::ExportTraceServiceResponse,
    };
    use prost::Message;
    let rejected = match signal {
        "traces" => ExportTraceServiceResponse::decode(body).map(|r| {
            r.partial_success
                .is_some_and(|p| p.rejected_spans != 0 || !p.error_message.is_empty())
        }),
        "metrics" => ExportMetricsServiceResponse::decode(body).map(|r| {
            r.partial_success
                .is_some_and(|p| p.rejected_data_points != 0 || !p.error_message.is_empty())
        }),
        "logs" => ExportLogsServiceResponse::decode(body).map(|r| {
            r.partial_success
                .is_some_and(|p| p.rejected_log_records != 0 || !p.error_message.is_empty())
        }),
        _ => return Err("Invalid telemetry signal".into()),
    }
    .map_err(|_| -> HttpError { "Invalid collector acknowledgement".into() })?;
    if rejected {
        return Err("Collector reported incomplete acceptance".into());
    }
    Ok(())
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
        ensure!(
            self.acknowledgement != Acknowledgement::LangfuseV4 || self.signals == [Signal::Traces],
            "Langfuse v4 acknowledgements require traces only"
        );
        let client = Client {
            acknowledgement: self.acknowledgement,
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
                opentelemetry::KeyValue::new(crate::attributes::SERVICE_NAME, "openworker"),
                opentelemetry::KeyValue::new(
                    crate::attributes::SERVICE_VERSION,
                    env!("CARGO_PKG_VERSION"),
                ),
                opentelemetry::KeyValue::new(
                    crate::attributes::SCHEMA_VERSION,
                    crate::attributes::VERSION,
                ),
                opentelemetry::KeyValue::new(
                    crate::attributes::SEMCONV_VERSION,
                    crate::attributes::SEMCONV,
                ),
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

#[cfg(test)]
mod tests {
    use super::*;
    use prost::Message;
    #[test]
    fn acknowledgements_are_typed_bounded_and_never_echo_receiver_text() {
        use opentelemetry_proto::tonic::collector::trace::v1::{
            ExportTracePartialSuccess, ExportTraceServiceResponse,
        };
        for signal in ["traces", "metrics", "logs"] {
            assert!(acknowledgement(signal, &[]).is_ok());
            assert!(acknowledgement(signal, &[0xff]).is_err());
        }
        let rejected = ExportTraceServiceResponse {
            partial_success: Some(ExportTracePartialSuccess {
                rejected_spans: 1,
                error_message: "PRIVATE_COLLECTOR_ECHO".into(),
            }),
        }
        .encode_to_vec();
        let error = acknowledgement("traces", &rejected)
            .unwrap_err()
            .to_string();
        assert_eq!(error, "Collector reported incomplete acceptance");
        assert!(!error.contains("PRIVATE"));
        assert!(json_acknowledgement("traces", br#"{}"#).is_ok());
        assert!(json_acknowledgement(
            "metrics",
            br#"{"partialSuccess":{"rejectedDataPoints":"0"}}"#
        )
        .is_ok());
        let receipt = br#"{"name":"otel-ingestion-job","id":"fixture","data":{"name":"otel-ingestion-job","payload":{"ingestionVersion":"4","authCheck":{"validKey":true}}}}"#;
        assert!(langfuse_acknowledgement("traces", receipt).is_ok());
        assert!(json_acknowledgement("traces", receipt).is_err());
        assert!(langfuse_acknowledgement("logs", receipt).is_err());
        let mut hybrid: serde_json::Value = serde_json::from_slice(receipt).unwrap();
        hybrid["partialSuccess"] = serde_json::json!({"rejectedSpans":1});
        assert!(langfuse_acknowledgement("traces", &serde_json::to_vec(&hybrid).unwrap()).is_err());
        assert!(Config {
            endpoint: "http://127.0.0.1:4318".into(),
            acknowledgement: Acknowledgement::LangfuseV4,
            ..Default::default()
        }
        .build_sink("test")
        .is_err());
        for body in [
            br#"{"success":true}"#.as_slice(),
            br#"{"partialSuccess":{"rejectedSpans":-1}}"#,
            br#"{"partialSuccess":{"rejectedDataPoints":0}}"#,
            br#"{"partialSuccess":{"rejectedSpans":"1","errorMessage":"PRIVATE_ECHO"}}"#,
        ] {
            assert!(json_acknowledgement("traces", body).is_err());
        }
    }
}
