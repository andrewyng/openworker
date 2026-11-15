//! Build-time native tools and explicit MCP provider registration. No coworker ACL.
use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeMap, future::Future, path::PathBuf, pin::Pin, sync::Arc};
use tokio::sync::mpsc;
pub use tokio_util::sync::CancellationToken;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ToolDescriptor {
    pub qualified_name: String,
    pub description: String,
    pub input_schema: Value,
    /// Partner-maintained implementation version, pinned along with the schema.
    pub version: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ToolOutput {
    pub data: Value,
    /// Bounded image payloads for the current model context, never durable logs.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub images: Vec<ToolImage>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ToolImage {
    pub mime_type: String,
    pub base64: String,
}
impl ToolOutput {
    pub fn json(data: Value) -> Self {
        Self {
            data,
            images: vec![],
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ToolError {
    pub code: String,
    pub message: String,
}
impl ToolError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
    pub fn cancelled() -> Self {
        Self::new("cancelled", "Tool cancelled")
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ToolProgress {
    pub message: String,
    pub data: Value,
}
/// Identity comes from the harness, never model-supplied arguments.
/// Host-native code is trusted and must not detach work or block async executors.
#[derive(Clone)]
pub struct ToolContext {
    pub session_id: String,
    pub turn_id: String,
    pub call_id: String,
    pub workspace: PathBuf,
    pub cancellation: CancellationToken,
    progress: mpsc::Sender<ToolProgress>,
}
impl ToolContext {
    pub fn new(
        session_id: String,
        turn_id: String,
        call_id: String,
        workspace: PathBuf,
        cancellation: CancellationToken,
        progress: mpsc::Sender<ToolProgress>,
    ) -> Self {
        Self {
            session_id,
            turn_id,
            call_id,
            workspace,
            cancellation,
            progress,
        }
    }
    pub async fn progress(
        &self,
        message: impl Into<String>,
        data: Value,
    ) -> std::result::Result<(), ToolError> {
        let message = message.into();
        if message.len() > 4000 || data.to_string().len() > 16000 {
            return Err(ToolError::new(
                "progress_limit",
                "Progress payload too large",
            ));
        }
        tokio::select! { biased;
            _ = self.cancellation.cancelled() => Err(ToolError::cancelled()),
            sent = self.progress.send(ToolProgress { message, data }) =>
                sent.map_err(|_| ToolError::cancelled()),
        }
    }
}
pub type ToolFuture<'a> =
    Pin<Box<dyn Future<Output = std::result::Result<ToolOutput, ToolError>> + Send + 'a>>;
pub trait Tool: Send + Sync {
    fn descriptor(&self) -> ToolDescriptor;
    fn call(&self, context: ToolContext, arguments: Value) -> ToolFuture<'_>;
    /// Trusted application control plane. Not advertised to the model. Must be
    /// bounded/nonblocking; never use this to bypass the tool execution path.
    fn control(&self, _session_id: &str, _request: Value) -> std::result::Result<Value, ToolError> {
        Err(ToolError::new(
            "unsupported_control",
            "This tool has no application controls",
        ))
    }
    /// Revoke transient access synchronously on cancel/close. Cannot undo effects.
    fn revoke(&self, _session_id: &str) {}
    /// End-of-turn cleanup. Defaults to revocation. A tool may retain unused,
    /// time-limited consent through a successful clarification-only turn.
    /// Cancellation and session close still call revoke directly.
    fn turn_finished(&self, session_id: &str, _completed: bool) {
        self.revoke(session_id);
    }
    /// Bounded, nonblocking, model-safe live facts, refreshed for each request.
    /// Never return credentials, pixels, user text or authorization instructions.
    /// These facts are advisory; execution must independently recheck access.
    fn model_context(&self, _session_id: &str) -> Option<Value> {
        None
    }
    /// Recheck consent before forwarding ephemeral images to a model.
    fn images_allowed(&self, _session_id: &str) -> bool {
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn model_context_is_selected_scoped_fresh_and_bounded() {
        struct Facts(&'static str, usize);
        impl Tool for Facts {
            fn descriptor(&self) -> ToolDescriptor {
                ToolDescriptor {
                    qualified_name: self.0.into(),
                    version: "1".into(),
                    description: "test".into(),
                    input_schema: json!({"type":"object"}),
                }
            }
            fn call(&self, _: ToolContext, _: Value) -> ToolFuture<'_> {
                Box::pin(async { unreachable!() })
            }
            fn model_context(&self, sid: &str) -> Option<Value> {
                Some(json!({"session":sid,"data":"x".repeat(self.1)}))
            }
        }
        let mut r = ToolRegistry::default();
        r.register(Facts("test.small", 1)).unwrap();
        r.register(Facts("test.large", 4000)).unwrap();
        assert_eq!(
            r.model_context("s1", ["test.small"]).unwrap(),
            json!({"test.small":{"session":"s1","data":"x"}})
        );
        assert_eq!(
            r.model_context("s2", ["test.small"]).unwrap()["test.small"]["session"],
            "s2"
        );
        assert_eq!(r.model_context("s1", ["missing.tool"]).unwrap(), json!({}));
        assert!(r.model_context("s1", ["test.large"]).is_err());
    }
    #[test]
    fn qualified_names_and_provider_registration_are_strict() {
        for name in [
            "bare",
            "a.",
            ".b",
            "a..b",
            "a/b.c",
            "openworker.read",
            "a.β",
        ] {
            assert!(qualified_parts(name).is_err(), "{name}");
        }
        assert_eq!(
            model_alias("example.web_search").unwrap(),
            "example__web_search"
        );
        let mut r = ToolRegistry::default();
        let m = McpServer {
            namespace: "acer".into(),
            program: "bin/acer".into(),
            args: vec![],
        };
        r.register_mcp(m.clone()).unwrap();
        assert!(r.register_mcp(m).is_err());
        assert!(r
            .register_mcp(McpServer {
                namespace: "bad.namespace".into(),
                program: "bin/x".into(),
                args: vec![]
            })
            .is_err());
    }
    #[tokio::test]
    async fn progress_is_bounded_and_cancellation_interrupts_backpressure() {
        let (tx, mut rx) = mpsc::channel(1);
        let token = CancellationToken::new();
        let ctx = ToolContext::new(
            "s".into(),
            "t".into(),
            "c".into(),
            PathBuf::new(),
            token.clone(),
            tx,
        );
        assert_eq!(
            ctx.progress("x".repeat(4001), json!({}))
                .await
                .unwrap_err()
                .code,
            "progress_limit"
        );
        assert_eq!(
            ctx.progress("ok", json!("x".repeat(16000)))
                .await
                .unwrap_err()
                .code,
            "progress_limit"
        );
        ctx.progress("first", json!({})).await.unwrap();
        token.cancel();
        assert_eq!(
            ctx.progress("second", json!({})).await.unwrap_err().code,
            "cancelled"
        );
        assert_eq!(rx.recv().await.unwrap().message, "first");
        assert!(rx.try_recv().is_err());
    }
}
#[derive(Clone)]
pub struct RegisteredTool {
    pub descriptor: ToolDescriptor,
    pub implementation: Arc<dyn Tool>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct McpServer {
    pub namespace: String,
    /// Bundled executable, $OPENWORKER, or a trusted absolute host executable.
    /// An already running service uses HttpServer instead of a launch command.
    pub program: String,
    pub args: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct HttpServer {
    pub namespace: String,
    pub url: String,
}
#[derive(Clone, Default)]
pub struct ToolRegistry {
    native: BTreeMap<String, RegisteredTool>,
    mcp: BTreeMap<String, McpServer>,
    http: BTreeMap<String, HttpServer>,
    config_sources: Vec<PathBuf>,
}
impl std::fmt::Debug for ToolRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ToolRegistry")
            .field("native", &self.native.keys())
            .field("mcp", &self.mcp)
            .finish()
    }
}
pub fn qualified_parts(name: &str) -> Result<(&str, &str)> {
    let Some((namespace, tool)) = name.split_once('.') else {
        bail!("Tool name must be qualified: provider.tool");
    };
    if name.len() > 80
        || name.split('.').any(|part| {
            part.is_empty()
                || !part
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        })
        || namespace == "openworker"
    {
        bail!("Invalid or reserved qualified tool name");
    }
    Ok((namespace, tool))
}
pub fn model_alias(name: &str) -> Result<String> {
    qualified_parts(name)?;
    let alias = name.replace('.', "__");
    if alias.len() > 80 {
        bail!("Model alias exceeds 80 characters");
    }
    Ok(alias)
}
impl ToolRegistry {
    pub fn turn_finished(&self, session_id: &str, completed: bool) {
        for tool in self.native.values() {
            tool.implementation.turn_finished(session_id, completed);
        }
    }
    pub fn model_context<'a>(
        &self,
        session_id: &str,
        selected: impl IntoIterator<Item = &'a str>,
    ) -> Result<Value> {
        let mut facts = serde_json::Map::new();
        for name in selected {
            if let Some(tool) = self.native(name) {
                if let Some(value) = tool.implementation.model_context(session_id) {
                    if value.to_string().len() > 4000 {
                        bail!("Native tool context exceeds limit");
                    }
                    facts.insert(name.into(), value);
                }
            }
        }
        let value = Value::Object(facts);
        if value.to_string().len() > 16000 {
            bail!("Native tool context exceeds total limit");
        }
        Ok(value)
    }
    pub fn revoke(&self, session_id: &str) {
        for tool in self.native.values() {
            tool.implementation.revoke(session_id);
        }
    }
    pub fn register(&mut self, tool: impl Tool + 'static) -> Result<()> {
        let descriptor = tool.descriptor();
        let (namespace, _) = qualified_parts(&descriptor.qualified_name)?;
        if descriptor.description.is_empty()
            || descriptor.description.len() > 8000
            || descriptor.version.is_empty()
            || descriptor.version.len() > 128
            || !descriptor.input_schema.is_object()
            || descriptor.input_schema.to_string().len() > 64000
        {
            bail!("Invalid native tool descriptor");
        }
        if self.mcp.contains_key(namespace)
            || self.http.contains_key(namespace)
            || self.native.contains_key(&descriptor.qualified_name)
        {
            bail!(
                "Duplicate tool or provider namespace: {}",
                descriptor.qualified_name
            );
        }
        self.native.insert(
            descriptor.qualified_name.clone(),
            RegisteredTool {
                descriptor,
                implementation: Arc::new(tool),
            },
        );
        Ok(())
    }
    pub fn register_mcp(&mut self, server: McpServer) -> Result<()> {
        qualified_parts(&format!("{}.check", server.namespace))?;
        if server.namespace.contains('.')
            || matches!(server.program.as_str(), "$NATIVE" | "$HTTP")
            || server.program.is_empty()
            || self.mcp.contains_key(&server.namespace)
            || self.http.contains_key(&server.namespace)
            || self
                .native
                .keys()
                .any(|name| name.starts_with(&format!("{}.", server.namespace)))
        {
            bail!("Invalid or duplicate MCP provider namespace");
        }
        self.mcp.insert(server.namespace.clone(), server);
        Ok(())
    }
    pub fn native(&self, name: &str) -> Option<&RegisteredTool> {
        self.native.get(name)
    }
    pub fn register_http(&mut self, server: HttpServer) -> Result<()> {
        qualified_parts(&format!("{}.check", server.namespace))?;
        if server.namespace.contains('.')
            || server.url.len() > 4096
            || self.http.contains_key(&server.namespace)
            || self.mcp.contains_key(&server.namespace)
            || self
                .native
                .keys()
                .any(|n| n.starts_with(&format!("{}.", server.namespace)))
        {
            bail!("Invalid or duplicate provider namespace");
        }
        self.http.insert(server.namespace.clone(), server);
        Ok(())
    }
    pub fn http(&self, namespace: &str) -> Option<&HttpServer> {
        self.http.get(namespace)
    }
    pub fn protect_config(&mut self, path: PathBuf) {
        self.config_sources.push(path);
    }
    pub fn config_sources(&self) -> &[PathBuf] {
        &self.config_sources
    }
    pub fn mcp(&self, namespace: &str) -> Option<&McpServer> {
        self.mcp.get(namespace)
    }
    pub fn native_catalog(&self, namespace: &str) -> Vec<ToolDescriptor> {
        self.native
            .values()
            .filter(|t| {
                t.descriptor
                    .qualified_name
                    .starts_with(&format!("{namespace}."))
            })
            .map(|t| t.descriptor.clone())
            .collect()
    }
}
