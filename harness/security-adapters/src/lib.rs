//! Optional, trusted outbound scanners. These are not agent tools. Only the
//! selected boundary's text is transmitted; history, system instructions and
//! private provider reasoning are never implicitly attached.
use anyhow::{ensure, Result};
use openworker_guardrails::{CheckFuture, Context, Decision, Handler, Phase};
use reqwest::{Client, Url};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use tokio_util::sync::CancellationToken;
use zeroize::Zeroizing;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
pub enum Protocol {
    /// OpenWorker selected-context scan v1. Return {"action":"allow"|"block"}.
    SelectedContext,
    /// Mock-qualified against the synchronous Prisma AIRS scan contract.
    PrismaAirs { profile: String },
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    pub endpoint: String,
    pub protocol: Protocol,
    pub credential_env: String,
    #[serde(default = "timeout")]
    pub timeout_ms: u64,
    /// Development fixtures only; still limited to numeric loopback addresses.
    #[serde(default)]
    pub allow_loopback_http: bool,
}
fn timeout() -> u64 {
    10_000
}
/// Applications can supply their own secure-store resolver. No secret is part
/// of serialized configuration, events, provider context or diagnostics.
pub trait Credential: Send + Sync {
    fn resolve(&self) -> Result<Zeroizing<String>>;
}
struct EnvironmentCredential(String);
impl Credential for EnvironmentCredential {
    fn resolve(&self) -> Result<Zeroizing<String>> {
        let value = std::env::var(&self.0)
            .map_err(|_| anyhow::anyhow!("Scanner credential unavailable"))?;
        ensure!(
            !value.is_empty() && value.len() <= 8192,
            "Scanner credential unavailable"
        );
        Ok(Zeroizing::new(value))
    }
}
pub struct Scanner {
    client: Client,
    endpoint: Url,
    protocol: Protocol,
    credential: Arc<dyn Credential>,
    timeout: Duration,
}
impl Scanner {
    pub fn from_environment(config: Config) -> Result<Self> {
        ensure!(
            !config.credential_env.is_empty()
                && config.credential_env.len() <= 128
                && config
                    .credential_env
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'_'),
            "Invalid credential reference"
        );
        let credential = Arc::new(EnvironmentCredential(config.credential_env.clone()));
        Self::new(config, credential)
    }
    pub fn new(config: Config, credential: Arc<dyn Credential>) -> Result<Self> {
        let endpoint = Url::parse(&config.endpoint)
            .map_err(|_| anyhow::anyhow!("Invalid scanner endpoint"))?;
        ensure!(
            endpoint.username().is_empty()
                && endpoint.password().is_none()
                && endpoint.query().is_none()
                && endpoint.fragment().is_none(),
            "Invalid scanner endpoint"
        );
        let local = endpoint
            .host_str()
            .is_some_and(|v| matches!(v, "127.0.0.1" | "[::1]"));
        ensure!(
            endpoint.scheme() == "https"
                || (config.allow_loopback_http && local && endpoint.scheme() == "http"),
            "Scanner requires HTTPS (or explicit loopback fixture)"
        );
        ensure!(
            (1..=120_000).contains(&config.timeout_ms),
            "Invalid scanner timeout"
        );
        if let Protocol::PrismaAirs { profile } = &config.protocol {
            ensure!(
                !profile.is_empty() && profile.len() <= 128,
                "Invalid Prisma profile"
            );
        }
        let client = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .build()
            .map_err(|_| anyhow::anyhow!("Scanner setup failed"))?;
        Ok(Self {
            client,
            endpoint,
            protocol: config.protocol,
            credential,
            timeout: Duration::from_millis(config.timeout_ms),
        })
    }
    fn request(&self, c: &Context) -> Result<Value> {
        ensure!(c.text.len() <= 128_000, "Scanner text limit exceeded");
        // Before-model payloads can contain system text or opaque provider state.
        // Require explicit boundary selection rather than sending them blindly.
        ensure!(
            matches!(
                c.phase,
                Phase::UserInput | Phase::ModelOutput | Phase::ToolCall | Phase::ToolResult
            ),
            "Scanner requires a selected text boundary"
        );
        Ok(match &self.protocol {
            Protocol::SelectedContext => {
                json!({"schemaVersion":1,"scanId":c.evaluation_id,"phase":c.phase,"text":c.text,"tool":c.tool})
            }
            Protocol::PrismaAirs { profile } => {
                let field = if matches!(c.phase, Phase::UserInput | Phase::ToolCall) {
                    "prompt"
                } else {
                    "response"
                };
                let mut content = serde_json::Map::new();
                content.insert(field.into(), json!(c.text));
                json!({"tr_id":c.evaluation_id,"ai_profile":{"profile_name":profile},"contents":[content]})
            }
        })
    }
    async fn scan(&self, c: Context) -> Result<Decision> {
        let body = self.request(&c)?;
        let secret = self.credential.resolve()?;
        let header = match self.protocol {
            Protocol::PrismaAirs { .. } => "x-pan-token",
            Protocol::SelectedContext => "authorization",
        };
        let auth = Zeroizing::new(if header == "authorization" {
            format!("Bearer {}", secret.as_str())
        } else {
            secret.to_string()
        });
        let mut response = self
            .client
            .post(self.endpoint.clone())
            .header(header, auth.as_str())
            .json(&body)
            .send()
            .await
            .map_err(|_| anyhow::anyhow!("Scanner transport unavailable"))?;
        ensure!(
            response.status().is_success(),
            "Scanner returned non-success status"
        );
        ensure!(
            response.content_length().is_none_or(|n| n <= 65536),
            "Scanner response too large"
        );
        let mut bytes = vec![];
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| anyhow::anyhow!("Scanner response unavailable"))?
        {
            ensure!(
                bytes.len() + chunk.len() <= 65536,
                "Scanner response too large"
            );
            bytes.extend_from_slice(&chunk);
        }
        let result: Value = serde_json::from_slice(&bytes)
            .map_err(|_| anyhow::anyhow!("Malformed scanner verdict"))?;
        let correlation = match self.protocol {
            Protocol::PrismaAirs { .. } => "tr_id",
            Protocol::SelectedContext => "scanId",
        };
        ensure!(
            result
                .get(correlation)
                .is_none_or(|v| v == &json!(c.evaluation_id)),
            "Scanner correlation mismatch"
        );
        match result["action"].as_str() {
            Some("allow") => Ok(Decision::Allow),
            Some("block") => Ok(Decision::block("EXTERNAL_SECURITY_BLOCK")),
            _ => anyhow::bail!("Unknown scanner verdict"),
        }
    }
}
impl Handler for Scanner {
    fn call(&self, context: Context, cancel: CancellationToken) -> CheckFuture<'_> {
        Box::pin(async move {
            tokio::select! {biased;_=cancel.cancelled()=>anyhow::bail!("Scanner cancelled"),r=tokio::time::timeout(self.timeout,self.scan(context))=>r.map_err(|_|anyhow::anyhow!("Scanner timeout"))?}
        })
    }
}
