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
            credentials: None,
            access_auth: false,
            refresh: None,
        }
    }
    pub fn base_url(mut self, value: impl Into<String>) -> Self {
        self.base_url = Some(value.into());
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
        if self.access_auth && self.credentials.is_some() {
            return Err(Error::InvalidInput(
                "Use access tokens or static credentials, not both".into(),
            ));
        }
        let mut wire = json!({"name":self.name,"api":self.api,"base_url":self.base_url,"credentials":self.credentials.map(Credentials::wire).transpose()?});
        if self.access_auth {
            wire["access_token_auth"] = json!(true);
        }
        Ok(wire)
    }
}

/// A provider name and exact model ID, resolved and pinned when a session starts.
pub type Model = crate::ModelChoice;
