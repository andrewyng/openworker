//! Provider setup belongs to the application, never the agent definition.
use crate::{Error, Result};
pub use openworker_runtime::model_api::Api as ModelApi;
use serde_json::{json, Value};

/// Opaque credentials deliberately do not implement Debug or Serialize.
pub struct Credentials {
    source: Source,
    memory: bool,
}
enum Source {
    Key(zeroize::Zeroizing<String>),
    Env(String),
}
impl Credentials {
    pub fn secure(key: impl Into<String>) -> Self {
        Self {
            source: Source::Key(zeroize::Zeroizing::new(key.into())),
            memory: false,
        }
    }
    pub fn from_env(name: impl Into<String>) -> Self {
        Self {
            source: Source::Env(name.into()),
            memory: false,
        }
    }
    pub fn memory_only(mut self) -> Self {
        self.memory = true;
        self
    }
    fn wire(self) -> Result<Value> {
        let key = match self.source {
            Source::Key(key) => key,
            Source::Env(name) => zeroize::Zeroizing::new(std::env::var(name).map_err(|_| {
                Error::InvalidInput("Provider key environment variable is unavailable".into())
            })?),
        };
        if key.is_empty() {
            return Err(Error::InvalidInput(
                "A nonempty provider key is required".into(),
            ));
        }
        Ok(json!({"api_key":key.as_str(),"storage":if self.memory {"memory"}else{"secure"}}))
    }
}
pub struct ModelProvider {
    name: String,
    api: ModelApi,
    base_url: Option<String>,
    stream_usage: Option<bool>,
    prompt_caching: Option<bool>,
    thinking: Option<openworker_runtime::anthropic::Thinking>,
    credentials: Option<Credentials>,
    pub(crate) access_auth: bool,
    pub(crate) refresh: Option<openworker_runtime::provider_auth::Refresh>,
}
impl ModelProvider {
    pub fn responses(name: impl Into<String>) -> Self {
        Self::new(name, ModelApi::Responses)
    }
    pub fn chat_completions(name: impl Into<String>) -> Self {
        Self::new(name, ModelApi::ChatCompletions)
    }
    pub fn anthropic(name: impl Into<String>) -> Self {
        Self::new(name, ModelApi::Anthropic)
    }
    pub fn new(name: impl Into<String>, api: ModelApi) -> Self {
        Self {
            name: name.into(),
            api,
            base_url: None,
            stream_usage: None,
            prompt_caching: None,
            thinking: None,
            credentials: None,
            access_auth: false,
            refresh: None,
        }
    }
    pub fn base_url(mut self, value: impl Into<String>) -> Self {
        self.base_url = Some(value.into());
        self
    }
    /// Request token usage in Chat Completions streams (default true).
    /// Set false only for a compatible server that rejects stream_options.
    /// Missing provider usage remains unknown; this does not estimate tokens.
    pub fn stream_usage(mut self, enabled: bool) -> Self {
        self.stream_usage = Some(enabled);
        self
    }
    /// Anthropic automatic prompt caching (default true). Set false for a
    /// proxy that rejects cache_control or when caching is not desired.
    pub fn prompt_caching(mut self, enabled: bool) -> Self {
        self.prompt_caching = Some(enabled);
        self
    }
    /// Explicit Anthropic generation mode; choose one supported by your model.
    pub fn thinking(mut self, thinking: openworker_runtime::anthropic::Thinking) -> Self {
        self.thinking = Some(thinking);
        self
    }
    pub fn credentials(mut self, value: Credentials) -> Self {
        self.credentials = Some(value);
        self
    }
    /// Memory-only access tokens supplied using update_provider_credentials.
    pub fn access_tokens(mut self) -> Self {
        self.access_auth = true;
        self
    }
    /// App-owned refresh logic, called for missing/expiring tokens or HTTP 401.
    pub fn refresh_access_token<F, Fut>(mut self, callback: F) -> Self
    where
        F: Fn(crate::TokenRefreshReason) -> Fut + Send + Sync + 'static,
        Fut: std::future::Future<Output = crate::Result<crate::AccessToken>> + Send + 'static,
    {
        self.access_auth = true;
        self.refresh = Some(std::sync::Arc::new(move |reason| {
            let future = callback(reason);
            Box::pin(async move {
                future
                    .await
                    .map_err(|_| anyhow::anyhow!("Provider authentication required"))
            })
        }));
        self
    }
    pub(crate) fn name(&self) -> &str {
        &self.name
    }
    pub(crate) fn wire(self) -> Result<Value> {
        if self.prompt_caching.is_some() && self.api != ModelApi::Anthropic {
            return Err(Error::InvalidInput(
                "prompt_caching is only supported for Anthropic Messages".into(),
            ));
        }
        if let Some(thinking) = &self.thinking {
            if self.api != ModelApi::Anthropic { return Err(Error::InvalidInput("thinking is only supported for Anthropic".into())); }
            thinking.validate().map_err(|e|Error::InvalidInput(e.to_string()))?;
        }
        if self.stream_usage.is_some() && self.api != ModelApi::ChatCompletions {
            return Err(Error::InvalidInput(
                "stream_usage is only supported for Chat Completions".into(),
            ));
        }
        if self.access_auth && self.credentials.is_some() {
            return Err(Error::InvalidInput(
                "Use access tokens or static credentials, not both".into(),
            ));
        }
        let mut wire = json!({"name":self.name,"api":self.api,"base_url":self.base_url,"credentials":self.credentials.map(Credentials::wire).transpose()?});
        if let Some(enabled) = self.stream_usage {
            wire["stream_usage"] = json!(enabled);
        }
        if let Some(enabled) = self.prompt_caching {
            wire["prompt_caching"] = json!(enabled);
        }
        if let Some(thinking) = self.thinking { wire["thinking"] = json!(thinking); }
        if self.access_auth {
            wire["access_token_auth"] = json!(true);
        }
        Ok(wire)
    }
}

/// A provider name and exact model ID, resolved and pinned when a session starts.
pub type Model = crate::ModelChoice;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn anthropic_prompt_caching_override_is_explicit_and_api_scoped() {
        assert!(ModelProvider::anthropic("Claude")
            .wire()
            .unwrap()
            .get("prompt_caching")
            .is_none());
        assert_eq!(
            ModelProvider::anthropic("Claude")
                .prompt_caching(false)
                .wire()
                .unwrap()["prompt_caching"],
            false
        );
        assert!(ModelProvider::chat_completions("Local")
            .prompt_caching(false)
            .wire()
            .is_err());
        assert!(ModelProvider::responses("OpenAI")
            .prompt_caching(true)
            .wire()
            .is_err());
    }

    #[test]
    fn chat_stream_usage_override_is_explicit_and_api_scoped() {
        let default = ModelProvider::chat_completions("Local").wire().unwrap();
        assert!(default.get("stream_usage").is_none());
        let disabled = ModelProvider::chat_completions("Local")
            .stream_usage(false)
            .wire()
            .unwrap();
        assert_eq!(disabled["stream_usage"], false);
        assert!(ModelProvider::responses("OpenAI")
            .stream_usage(false)
            .wire()
            .is_err());
        assert!(ModelProvider::anthropic("Claude")
            .stream_usage(true)
            .wire()
            .is_err());
    }
}
