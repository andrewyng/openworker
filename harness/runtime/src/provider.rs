//! Typed, sanitized Responses outcomes. This module never retries or executes tools.
use crate::model::{validate_output, Progress};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::mpsc;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    #[default]
    Openai,
    Anthropic,
}
impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Self::Openai => "openai",
            Self::Anthropic => "anthropic",
        }
    }
    pub fn key_env(self) -> &'static str {
        match self {
            Self::Openai => "OPENAI_API_KEY",
            Self::Anthropic => "ANTHROPIC_API_KEY",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Failure {
    RateLimit,
    Transient,
    Transport,
    IncompleteStream,
    InvalidResponse,
    ContextOverflow,
    Truncated,
    Authentication,
    Configuration,
    SafetyBlocked,
}
#[derive(Clone, Debug)]
pub struct Error {
    pub kind: Failure,
    pub retry_after: Option<u64>,
    /// Set only on an HTTP rejection, never on an error inside a stream.
    pub http_status: Option<u16>,
    pub request_id: Option<String>,
    pub api: Option<crate::model_api::Api>,
    /// Fixed parser code or an allowlisted upstream code; never arbitrary text.
    pub code: Option<&'static str>,
}

/// Safe structured context. Never contains request/response bodies or credentials.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Diagnostic {
    pub category: Failure,
    pub retryable: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub http_status: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api: Option<crate::model_api::Api>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
}

impl std::fmt::Display for Diagnostic {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Provider {:?}", self.category)?;
        if let Some(status) = self.http_status {
            write!(f, " (HTTP {status})")?;
        }
        if let Some(id) = &self.request_id {
            write!(f, "; request {id}")?;
        }
        if let Some(code) = &self.code {
            write!(f, "; {code}")?;
        }
        Ok(())
    }
}

/// Request IDs are identifiers, not arbitrary upstream diagnostic text.
pub(crate) fn request_id(headers: &reqwest::header::HeaderMap, key: &str) -> Option<String> {
    ["x-request-id", "request-id"].into_iter().find_map(|name| {
        let id = headers.get(name)?.to_str().ok()?;
        (!id.is_empty()
            && id.len() <= 128
            && id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_:".contains(&b))
            && !id.starts_with("sk-")
            && (key.is_empty() || !id.contains(key)))
        .then(|| id.to_owned())
    })
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{}; no tool actions accepted from this attempt",
            self.diagnostic()
        )
    }
}
impl std::error::Error for Error {}
impl Error {
    pub fn new(kind: Failure) -> Self {
        Self {
            kind,
            retry_after: None,
            http_status: None,
            request_id: None,
            api: None,
            code: match kind {
                Failure::InvalidResponse => Some("invalid_response"),
                Failure::IncompleteStream => Some("incomplete_stream"),
                _ => None,
            },
        }
    }
    pub fn diagnostic(&self) -> Diagnostic {
        Diagnostic {
            category: self.kind,
            retryable: self.retryable(),
            http_status: self.http_status,
            request_id: self.request_id.clone(),
            api: self.api,
            code: self.code.map(str::to_owned),
        }
    }
    pub fn with_api(mut self, api: crate::model_api::Api) -> Self {
        self.api = Some(api);
        self
    }
    pub fn with_code(mut self, code: &'static str) -> Self {
        self.code = Some(code);
        self
    }
    pub fn retryable(&self) -> bool {
        matches!(
            self.kind,
            Failure::RateLimit
                | Failure::Transient
                | Failure::Transport
                | Failure::IncompleteStream
        ) && self.retry_after.is_none_or(|s| s <= 30)
    }
}

pub(crate) fn upstream_code(value: &Value) -> Option<&'static str> {
    const CODES: &[&str] = &[
        "invalid_api_key",
        "authentication_error",
        "permission_error",
        "invalid_request_error",
        "rate_limit_exceeded",
        "rate_limit_error",
        "insufficient_quota",
        "context_length_exceeded",
        "server_error",
        "api_error",
        "overloaded_error",
        "misalignment_policy_violation",
        "RESOURCE_EXHAUSTED",
        "INVALID_ARGUMENT",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
    ];
    CODES
        .iter()
        .copied()
        .find(|code| value["code"] == *code || value["type"] == *code || value["status"] == *code)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReasoningKind {
    Summary,
    Thinking,
}

/// Provider-designated display text only. Opaque signatures never enter this type.
#[derive(Clone, Debug)]
pub struct ReasoningDelta {
    pub kind: ReasoningKind,
    pub text: String,
    pub block_index: u64,
}

#[cfg(test)]
mod diagnostics_tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn safe_diagnostics_exclude_credentials_and_unbounded_header_text() {
        let mut headers = reqwest::header::HeaderMap::new();
        for value in [
            "sk-secret-token",
            "Bearer token",
            "arbitrary\ttext",
            "credential-sentinel",
        ] {
            headers.insert("x-request-id", value.parse().unwrap());
            assert!(request_id(&headers, "credential-sentinel").is_none());
        }
        headers.insert("x-request-id", "req_fixture-123".parse().unwrap());
        assert_eq!(
            request_id(&headers, "credential-sentinel").as_deref(),
            Some("req_fixture-123")
        );
        let mut e = Error::new(Failure::RateLimit).with_api(crate::model_api::Api::Responses);
        e.http_status = Some(429);
        e.request_id = request_id(&headers, "");
        assert_eq!(
            serde_json::to_value(e.diagnostic()).unwrap(),
            json!({"category":"rate_limit","retryable":true,"http_status":429,"request_id":"req_fixture-123","api":"responses"})
        );
    }
    #[test]
    fn responses_only_exposes_designated_summary_deltas() {
        let mut d = Decoder::default();
        for event in [
            json!({"type":"response.reasoning_text.delta","delta":"hidden"}),
            json!({"type":"response.output_item.added","item":{"type":"reasoning","encrypted_content":"opaque"}}),
        ] {
            assert!(d.event(&event).unwrap().is_none());
            assert!(d.take_reasoning().is_none());
        }
        d.event(&json!({"type":"response.reasoning_summary_text.delta","delta":"Display summary","output_index":0})).unwrap();
        let delta = d.take_reasoning().unwrap();
        assert_eq!(delta.kind, ReasoningKind::Summary);
        assert_eq!(delta.text, "Display summary");
        assert!(d.take_reasoning().is_none());
    }
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_input_tokens: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cache_read_input_tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cache_write_input_tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_output_tokens: Option<u64>,
}
impl Usage {
    pub fn reported(&self) -> crate::telemetry::TokenUsage {
        let bounded = |n: u64| (n <= i64::MAX as u64).then_some(n);
        let mut u = crate::telemetry::TokenUsage {
            input: bounded(self.input_tokens),
            output: bounded(self.output_tokens),
            cache_read: self
                .cache_read_input_tokens
                .filter(|n| *n <= self.input_tokens),
            cache_write: self
                .cache_write_input_tokens
                .filter(|n| *n <= self.input_tokens),
            reasoning: self
                .reasoning_output_tokens
                .filter(|n| *n <= self.output_tokens),
        };
        if u.cache_read
            .zip(u.cache_write)
            .is_some_and(|(a, b)| a.checked_add(b).is_none_or(|n| n > self.input_tokens))
        {
            u.cache_read = None;
            u.cache_write = None;
        }
        if !u.validate() {
            crate::telemetry::TokenUsage::default()
        } else {
            u
        }
    }
}
fn response_usage(u: &Value) -> Option<Usage> {
    Some(Usage {
        input_tokens: u["input_tokens"].as_u64()?,
        output_tokens: u["output_tokens"].as_u64()?,
        cached_input_tokens: u["input_tokens_details"]["cached_tokens"]
            .as_u64()
            .unwrap_or(0),
        cache_read_input_tokens: u["input_tokens_details"]["cached_tokens"].as_u64(),
        cache_write_input_tokens: None,
        reasoning_output_tokens: u["output_tokens_details"]["reasoning_tokens"].as_u64(),
    })
}
#[derive(Clone, Debug)]
pub struct Completion {
    pub output: Vec<Value>,
    pub usage: Option<Usage>,
}

pub fn classify(status: u16, code: &str) -> Failure {
    match code {
        "misalignment_policy_violation" => Failure::SafetyBlocked,
        "context_length_exceeded" => Failure::ContextOverflow,
        "rate_limit_exceeded" => Failure::RateLimit,
        "insufficient_quota" => Failure::Configuration,
        "invalid_api_key" | "authentication_error" => Failure::Authentication,
        "invalid_request_error" => Failure::Configuration,
        "server_error" => Failure::Transient,
        _ => match status {
            401 | 403 => Failure::Authentication,
            408 | 409 | 500 | 502 | 503 | 504 => Failure::Transient,
            429 => Failure::RateLimit,
            _ => Failure::Configuration,
        },
    }
}

/// Exposed as a pure seam for recorded-event and fault-injection tests.
#[derive(Default)]
pub struct Decoder {
    completion: Option<Completion>,
    reported: Option<Usage>,
    output_started: bool,
    reasoning: Option<ReasoningDelta>,
}
impl Decoder {
    pub fn event(&mut self, event: &Value) -> Result<Option<String>, Error> {
        if let Some(u) = response_usage(&event["response"]["usage"]) {
            self.reported = Some(u);
        }
        if matches!(
            event["type"].as_str(),
            Some("response.output_text.delta" | "response.function_call_arguments.delta")
        ) && event["delta"].as_str().is_some_and(|s| !s.is_empty())
            || event["type"] == "response.output_item.added"
                && event["item"]["type"] == "function_call"
                && event["item"]["name"].is_string()
        {
            self.output_started = true;
        }
        if event["response"]["error"]["code"] == "misalignment_policy_violation"
            || event["code"] == "misalignment_policy_violation"
        {
            self.completion = None;
            return Err(Error::new(Failure::SafetyBlocked));
        }
        if self.completion.is_some() {
            return Err(Error::new(Failure::InvalidResponse));
        }
        match event["type"].as_str().unwrap_or("") {
            "response.reasoning_summary_text.delta" => {
                self.reasoning = Some(ReasoningDelta {
                    kind: ReasoningKind::Summary,
                    text: event["delta"]
                        .as_str()
                        .ok_or_else(|| Error::new(Failure::InvalidResponse))?
                        .to_owned(),
                    block_index: event["output_index"]
                        .as_u64()
                        .ok_or_else(|| Error::new(Failure::InvalidResponse))?,
                });
                Ok(None)
            }
            "response.output_text.delta" => Ok(Some(
                event["delta"]
                    .as_str()
                    .ok_or_else(|| Error::new(Failure::InvalidResponse))?
                    .to_owned(),
            )),
            "response.completed" => {
                let r = &event["response"];
                if r["status"] != "completed" {
                    return Err(Error::new(Failure::InvalidResponse));
                }
                let output = r["output"]
                    .as_array()
                    .cloned()
                    .ok_or_else(|| Error::new(Failure::InvalidResponse))?;
                if output.is_empty() || validate_output(&output).is_err() {
                    return Err(Error::new(Failure::InvalidResponse));
                }
                let usage = if r["usage"]["input_tokens"].is_u64()
                    && r["usage"]["output_tokens"].is_u64()
                {
                    Some(Usage {
                        input_tokens: r["usage"]["input_tokens"].as_u64().unwrap(),
                        output_tokens: r["usage"]["output_tokens"].as_u64().unwrap(),
                        cached_input_tokens: r["usage"]["input_tokens_details"]["cached_tokens"]
                            .as_u64()
                            .unwrap_or(0),
                        cache_read_input_tokens: r["usage"]["input_tokens_details"]
                            ["cached_tokens"]
                            .as_u64(),
                        cache_write_input_tokens: None,
                        reasoning_output_tokens: r["usage"]["output_tokens_details"]
                            ["reasoning_tokens"]
                            .as_u64(),
                    })
                } else {
                    None
                };
                self.completion = Some(Completion { output, usage });
                Ok(None)
            }
            "response.incomplete" => Err(Error::new(
                if event["response"]["incomplete_details"]["reason"] == "max_output_tokens" {
                    Failure::Truncated
                } else {
                    Failure::InvalidResponse
                },
            )),
            "response.failed" | "error" => {
                let code = event["response"]["error"]["code"]
                    .as_str()
                    .or(event["code"].as_str())
                    .unwrap_or("");
                Err(Error::new(classify(0, code)))
            }
            _ => Ok(None),
        }
    }
    pub fn finish(self) -> Result<Completion, Error> {
        self.completion
            .ok_or_else(|| Error::new(Failure::IncompleteStream))
    }
}

pub async fn request(
    client: &reqwest::Client,
    url: &str,
    key: &str,
    body: Value,
    tx: mpsc::Sender<Progress>,
) -> Result<(), Error> {
    request_decoded(client, url, key, body, tx, Decoder::default()).await
}

pub(crate) trait StreamDecoder {
    fn event(&mut self, value: &Value) -> Result<Option<String>, Error>;
    fn done(&mut self) -> Result<(), Error> {
        Ok(())
    }
    fn finish(self) -> Result<Completion, Error>;
    fn usage_report(&self) -> Option<crate::telemetry::TokenUsage> {
        None
    }
    fn output_started(&self) -> bool {
        false
    }
    fn take_reasoning(&mut self) -> Option<ReasoningDelta> {
        None
    }
}
impl StreamDecoder for Decoder {
    fn take_reasoning(&mut self) -> Option<ReasoningDelta> {
        self.reasoning.take()
    }
    fn usage_report(&self) -> Option<crate::telemetry::TokenUsage> {
        self.reported.as_ref().map(Usage::reported)
    }
    fn output_started(&self) -> bool {
        self.output_started
    }
    fn event(&mut self, value: &Value) -> Result<Option<String>, Error> {
        self.event(value)
    }
    fn finish(self) -> Result<Completion, Error> {
        self.finish()
    }
}
pub(crate) async fn request_decoded<D: StreamDecoder>(
    client: &reqwest::Client,
    url: &str,
    key: &str,
    body: Value,
    tx: mpsc::Sender<Progress>,
    mut decoder: D,
) -> Result<(), Error> {
    let request = client.post(url);
    let request = if key.is_empty() {
        request
    } else {
        request.bearer_auth(key)
    };
    let response = request
        .json(&body)
        .send()
        .await
        .map_err(|_| Error::new(Failure::Transport))?;
    if !response.status().is_success() {
        let status = response.status().as_u16();
        let request_id = request_id(response.headers(), key);
        let retry_after = response
            .headers()
            .get("retry-after")
            .and_then(|h| h.to_str().ok())
            .and_then(|h| h.parse().ok());
        let mut stream = response.bytes_stream();
        let mut bytes = vec![];
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| Error {
                kind: classify(status, ""),
                retry_after,
                http_status: Some(status),
                request_id: request_id.clone(),
                api: None,
                code: Some("http_error_body_unavailable"),
            })?;
            if bytes.len() + chunk.len() > 16384 {
                break;
            }
            bytes.extend_from_slice(&chunk);
        }
        let error: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
        return Err(Error {
            kind: classify(status, error["error"]["code"].as_str().unwrap_or("")),
            retry_after,
            http_status: Some(status),
            request_id,
            api: None,
            code: upstream_code(&error["error"]),
        });
    }
    let mut stream = response.bytes_stream();
    let mut buffer = vec![];
    let mut total = 0usize;
    let mut previous_usage = None;
    let mut first_sent = false;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| Error::new(Failure::Transport))?;
        total += chunk.len();
        if total > 8 * 1024 * 1024 {
            return Err(Error::new(Failure::InvalidResponse));
        }
        buffer.extend_from_slice(&chunk);
        while let Some(i) = buffer.iter().position(|&c| c == b'\n') {
            let line = buffer.drain(..=i).collect::<Vec<_>>();
            let line = std::str::from_utf8(&line)
                .map_err(|_| Error::new(Failure::InvalidResponse))?
                .trim();
            let Some(data) = line.strip_prefix("data:") else {
                continue;
            };
            if data.trim() == "[DONE]" {
                decoder.done()?;
                continue;
            }
            let event: Value = serde_json::from_str(data).map_err(|_| {
                Error::new(Failure::InvalidResponse).with_code("invalid_stream_json")
            })?;
            let decoded = decoder.event(&event);
            if let Some(usage) = decoder
                .usage_report()
                .filter(|u| u.present() && Some(*u) != previous_usage)
            {
                tx.send(Progress::Usage(usage))
                    .await
                    .map_err(|_| Error::new(Failure::Transport))?;
                previous_usage = Some(usage);
            }
            let decoded = decoded?;
            if let Some(delta) = decoder.take_reasoning() {
                tx.send(Progress::Reasoning(delta))
                    .await
                    .map_err(|_| Error::new(Failure::Transport))?;
            }
            if !first_sent && decoder.output_started() {
                tx.send(Progress::OutputStarted)
                    .await
                    .map_err(|_| Error::new(Failure::Transport))?;
                first_sent = true;
            }
            if let Some(text) = decoded {
                tx.send(Progress::Delta(text))
                    .await
                    .map_err(|_| Error::new(Failure::Transport))?;
            }
        }
    }
    if !buffer.iter().all(u8::is_ascii_whitespace) {
        return Err(Error::new(Failure::InvalidResponse));
    }
    tx.send(Progress::Complete(decoder.finish()?))
        .await
        .map_err(|_| Error::new(Failure::Transport))?;
    Ok(())
}
