//! Native client for the separately deployed Kubernetes workspace service.
use super::*;
use futures_util::StreamExt;
use serde_json::json;
use std::{path::PathBuf, time::Duration};
use zeroize::Zeroizing;

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct K8sWorkspaceConfig {
    pub endpoint: String,
    pub token_env: String,
    pub ca_file: Option<PathBuf>,
}

#[derive(Clone)]
pub struct K8sWorkspaceProvider {
    endpoint: reqwest::Url,
    http: reqwest::Client,
    token: Arc<Zeroizing<String>>,
}

impl K8sWorkspaceProvider {
    pub fn new(config: &K8sWorkspaceConfig) -> Result<Self> {
        let endpoint = reqwest::Url::parse(&config.endpoint)?;
        ensure!(
            endpoint.scheme() == "https"
                && endpoint.username().is_empty()
                && endpoint.password().is_none()
                && endpoint.query().is_none()
                && endpoint.fragment().is_none()
                && endpoint.path() == "/",
            "Expected workspace service HTTPS origin"
        );
        let token = Zeroizing::new(
            std::env::var(&config.token_env).context("Workspace service credential unavailable")?,
        );
        ensure!(
            token.len() >= 32 && token.len() <= 4096 && token.bytes().all(|b| b.is_ascii_graphic()),
            "Invalid workspace service credential"
        );
        let mut builder = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(4))
            .timeout(Duration::from_secs(28));
        if let Some(path) = &config.ca_file {
            builder = builder
                .add_root_certificate(reqwest::Certificate::from_pem(&std::fs::read(path)?)?);
        }
        Ok(Self {
            endpoint,
            http: builder.build()?,
            token: Arc::new(token),
        })
    }

    async fn rpc(&self, action: &str, request: Value) -> Result<Value> {
        let response = self
            .http
            .post(self.endpoint.join(&format!("v1/{action}"))?)
            .bearer_auth(self.token.as_str())
            .json(&request)
            .send()
            .await?;
        let status = response.status();
        let mut stream = response.bytes_stream();
        let mut bytes = Vec::new();
        while let Some(part) = stream.next().await {
            let part = part?;
            ensure!(
                bytes.len() + part.len() <= 1_048_576,
                "Workspace reply too large"
            );
            bytes.extend_from_slice(&part);
        }
        let value: Value =
            serde_json::from_slice(&bytes).context("Invalid workspace service reply")?;
        if !status.is_success() {
            return Err(match value["error"].as_str() {
                Some("workspace_not_found") => WorkspaceError::Missing.into(),
                Some("workspace_data_mismatch") => WorkspaceError::DataMismatch.into(),
                Some("workspace_profile_mismatch") => WorkspaceError::ProfileMismatch.into(),
                Some("workspace_in_use") => WorkspaceError::Busy.into(),
                Some("workspace_shared_mount_unsupported") => {
                    WorkspaceError::UnsupportedSharing.into()
                }
                Some("invalid_workspace_request") => WorkspaceError::Invalid.into(),
                _ => anyhow::anyhow!("Workspace service could not confirm the operation"),
            });
        }
        Ok(value)
    }

    async fn client(&self, action: &str, body: Value) -> Result<Arc<dyn WorkspaceClient>> {
        let result = self.rpc(action, body).await?;
        let profile = result["profile"]
            .as_str()
            .context("Workspace profile missing")?
            .to_owned();
        Ok(Arc::new(Client {
            provider: self.clone(),
            profile,
        }))
    }
}
impl WorkspaceProvider for K8sWorkspaceProvider {
    fn start(&self, options: ProviderOptions) -> ProviderFuture<'_, Arc<dyn WorkspaceClient>> {
        Box::pin(self.client("start", json!({"options":options})))
    }
    fn recover(&self, workspace: String) -> ProviderFuture<'_, Arc<dyn WorkspaceClient>> {
        Box::pin(self.client("recover", json!({"workspaceId":workspace})))
    }
    fn detach(&self, workspace: String, session: String) -> ProviderFuture<'_, ()> {
        Box::pin(async move {
            self.rpc(
                "detach",
                json!({"workspaceId":workspace,"sessionId":session}),
            )
            .await?;
            Ok(())
        })
    }
}
struct Client {
    provider: K8sWorkspaceProvider,
    profile: String,
}
impl WorkspaceClient for Client {
    fn get_or_create(&self, req: GetOrCreateWorkspace) -> ProviderFuture<'_, WorkspaceRef> {
        Box::pin(async move {
            Ok(serde_json::from_value(self.provider.rpc("open",json!({
                "key":req.key,"dataId":req.data_id,"options":req.options,"defaultProfile":self.profile
            })).await?)?)
        })
    }
    fn attach_session(
        &self,
        workspace: String,
        session: String,
    ) -> ProviderFuture<'_, WorkspaceAttachment> {
        Box::pin(async move {
            self.provider
                .rpc(
                    "attach",
                    json!({"workspaceId":workspace,"sessionId":session}),
                )
                .await?;
            Ok(WorkspaceAttachment {
                workspace_id: workspace,
                session_id: session,
            })
        })
    }
    fn detach_session(&self, a: WorkspaceAttachment) -> ProviderFuture<'_, ()> {
        self.provider.detach(a.workspace_id, a.session_id)
    }
    fn stop_workspace(&self, id: String) -> ProviderFuture<'_, ()> {
        Box::pin(async move {
            self.provider.rpc("stop", json!({"workspaceId":id})).await?;
            Ok(())
        })
    }
    fn delete_data(&self, id: String) -> ProviderFuture<'_, ()> {
        Box::pin(async move {
            self.provider.rpc("delete", json!({"dataId":id})).await?;
            Ok(())
        })
    }
    fn file(&self, a: WorkspaceAttachment, op: FileOperation) -> ProviderFuture<'_, Value> {
        Box::pin(async move {
            self.provider
                .rpc(
                    "file",
                    json!({"workspaceId":a.workspace_id,"sessionId":a.session_id,"operation":op}),
                )
                .await
        })
    }
}
