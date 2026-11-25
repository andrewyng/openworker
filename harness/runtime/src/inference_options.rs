//! Application-owned, non-secret extensions for a configured inference endpoint.
use anyhow::{ensure, Result};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::{collections::BTreeMap, time::Duration};

#[derive(Clone, Default, Deserialize, PartialEq, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct InferenceOptions {
    pub headers: BTreeMap<String, String>,
    pub query: BTreeMap<String, String>,
    pub body: Map<String, Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeouts: Option<ModelTimeouts>,
}

#[derive(Clone, Default, Deserialize, PartialEq, Serialize)]
#[serde(default, rename_all = "camelCase", deny_unknown_fields)]
pub struct ModelTimeouts {
    pub request_ms: Option<u64>,
    pub generation_ms: Option<u64>,
}

impl ModelTimeouts {
    pub fn request(&self) -> Duration {
        Duration::from_millis(self.request_ms.unwrap_or(180_000))
    }

    pub fn generation(&self) -> Duration {
        self.generation_ms.map_or_else(
            || {
                self.request()
                    .saturating_add(Duration::from_secs(60))
                    .max(Duration::from_secs(240))
            },
            Duration::from_millis,
        )
    }

    pub fn validate(&self) -> Result<()> {
        ensure!(
            (Duration::from_millis(1)..=Duration::from_secs(3600)).contains(&self.request()),
            "Model request timeout must be between 1 and 3600000 milliseconds"
        );
        ensure!(
            self.generation() >= self.request() && self.generation() <= Duration::from_secs(7200),
            "Generation timeout must cover one request and cannot exceed 7200000 milliseconds"
        );
        Ok(())
    }
}

impl InferenceOptions {
    pub fn is_empty(&self) -> bool {
        self.headers.is_empty()
            && self.query.is_empty()
            && self.body.is_empty()
            && self.timeouts.is_none()
    }

    pub fn headers(&self) -> Result<HeaderMap> {
        let mut out = HeaderMap::new();
        for (name, value) in &self.headers {
            let name = HeaderName::from_bytes(name.as_bytes())?;
            ensure!(
                !matches!(
                    name.as_str(),
                    "authorization"
                        | "proxy-authorization"
                        | "x-api-key"
                        | "host"
                        | "cookie"
                        | "set-cookie"
                        | "content-length"
                        | "transfer-encoding"
                        | "connection"
                        | "upgrade"
                        | "expect"
                        | "te"
                        | "trailer"
                        | "content-type"
                        | "content-encoding"
                        | "accept-encoding"
                        | "anthropic-version"
                        | "forwarded"
                ) && !name.as_str().starts_with("x-forwarded-")
                    && !name.as_str().starts_with("x-openworker-"),
                "Reserved inference header"
            );
            ensure!(
                out.insert(name, HeaderValue::from_str(value)?).is_none(),
                "Duplicate inference header"
            );
        }
        Ok(out)
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            serde_json::to_vec(self)?.len() <= 16384,
            "Inference options exceed 16 KiB"
        );
        self.headers()?;
        if let Some(timeouts) = &self.timeouts {
            timeouts.validate()?;
        }
        for name in self.query.keys().chain(self.body.keys()) {
            ensure!(
                !name.is_empty()
                    && name.len() <= 128
                    && name
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)),
                "Invalid inference option name"
            );
        }
        for name in self.body.keys() {
            ensure!(
                !matches!(
                    name.as_str(),
                    "model"
                        | "messages"
                        | "input"
                        | "instructions"
                        | "system"
                        | "tools"
                        | "tool_choice"
                        | "stream"
                        | "stream_options"
                        | "store"
                        | "include"
                        | "parallel_tool_calls"
                        | "max_tokens"
                        | "max_completion_tokens"
                        | "max_output_tokens"
                        | "text"
                        | "response_format"
                        | "thinking"
                        | "reasoning"
                        | "cache_control"
                        | "previous_response_id"
                        | "conversation"
                ),
                "Inference option conflicts with harness request"
            );
        }
        Ok(())
    }
    pub fn url(&self, endpoint: &str) -> Result<String> {
        if self.query.is_empty() {
            return Ok(endpoint.to_owned());
        }
        let mut url = reqwest::Url::parse(endpoint)?;
        url.query_pairs_mut().extend_pairs(&self.query);
        Ok(url.into())
    }
    pub(crate) fn extend(&self, body: &mut Value) -> Result<()> {
        let fields = body
            .as_object_mut()
            .ok_or_else(|| anyhow::anyhow!("Invalid model request"))?;
        for (k, v) in &self.body {
            ensure!(
                !fields.contains_key(k),
                "Inference option conflicts with generated field"
            );
            fields.insert(k.clone(), v.clone());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extended_request_increases_implicit_generation_budget() {
        let defaults = ModelTimeouts::default();
        assert_eq!(defaults.request(), Duration::from_secs(180));
        assert_eq!(defaults.generation(), Duration::from_secs(240));
        let extended: ModelTimeouts =
            serde_json::from_value(serde_json::json!({"requestMs":600000})).unwrap();
        extended.validate().unwrap();
        assert_eq!(extended.request(), Duration::from_secs(600));
        assert_eq!(extended.generation(), Duration::from_secs(660));
        assert!(ModelTimeouts {
            generation_ms: Some(240000),
            ..extended
        }
        .validate()
        .is_err());
    }

    use serde_json::json;
    #[test]
    fn extensions_preserve_owned_fields_and_encode_query_values() {
        let options:InferenceOptions=serde_json::from_value(json!({"headers":{"x-region":"west"},"query":{"version":"a&b"},"body":{"routing":{"local":true}}})).unwrap();
        options.validate().unwrap();
        assert_eq!(options.headers().unwrap()["x-region"], "west");
        assert_eq!(
            options.url("https://example.com/v1").unwrap(),
            "https://example.com/v1?version=a%26b"
        );
        let mut body = json!({"model":"local"});
        options.extend(&mut body).unwrap();
        assert_eq!(body, json!({"model":"local","routing":{"local":true}}));
        assert!(options.extend(&mut body).is_err());
        for value in [
            json!({"headers":{"Host":"other.example"}}),
            json!({"headers":{"Authorization":"override"}}),
            json!({"body":{"tools":[]}}),
        ] {
            assert!(serde_json::from_value::<InferenceOptions>(value)
                .unwrap()
                .validate()
                .is_err())
        }
    }
}
