//! Trusted provider registrations. Providers own key and data persistence.
use crate::environment::ProviderFuture;
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{collections::BTreeMap, sync::Arc};

pub mod http;
pub mod kubernetes {
    pub use super::http::{
        WorkspaceServiceConfig as K8sWorkspaceConfig,
        WorkspaceServiceProvider as K8sWorkspaceProvider,
    };
}
#[cfg(any(test, feature = "workspace-reference"))]
pub mod reference;

/// Safe, stable provider outcomes. Other errors may contain backend diagnostics.
#[derive(Debug, Clone, Copy)]
pub enum WorkspaceError {
    Invalid,
    Missing,
    HandleExpired,
    DataMismatch,
    ProfileMismatch,
    UnsupportedSharing,
    Busy,
}
impl WorkspaceError {
    pub fn code(self) -> &'static str {
        match self {
            Self::Invalid => "invalid_workspace_request",
            Self::Missing => "workspace_not_found",
            Self::HandleExpired => "workspace_handle_expired",
            Self::DataMismatch => "workspace_data_mismatch",
            Self::ProfileMismatch => "workspace_profile_mismatch",
            Self::UnsupportedSharing => "workspace_shared_mount_unsupported",
            Self::Busy => "workspace_in_use",
        }
    }
}
impl std::fmt::Display for WorkspaceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for WorkspaceError {}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ProviderOptions(pub serde_json::Map<String, Value>);

impl ProviderOptions {
    /// Don't surface a serde error containing raw credential values.
    pub fn decode<T: serde::de::DeserializeOwned>(self) -> Result<T> {
        serde_json::from_value(Value::Object(self.0))
            .map_err(|_| anyhow::Error::new(WorkspaceError::Invalid))
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct GetOrCreateWorkspace {
    pub key: String,
    pub data_id: Option<String>,
    #[serde(default)]
    pub options: ProviderOptions,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WorkspaceRef {
    pub workspace_id: String,
    pub data_id: String,
}

/// Stable identity for a session's individual grant, recoverable after worker loss.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkspaceAttachment {
    pub workspace_id: String,
    pub session_id: String,
}

pub trait WorkspaceProvider: Send + Sync {
    fn start(&self, options: ProviderOptions) -> ProviderFuture<'_, Arc<dyn WorkspaceClient>>;
    /// Recover the original workspace using operator configuration. Providers needing
    /// unavailable credentials must fail rather than substituting another client.
    fn recover(&self, workspace: String) -> ProviderFuture<'_, Arc<dyn WorkspaceClient>>;
    /// Retryable after host restart, including after the compute was reclaimed.
    fn detach(&self, workspace: String, session: String) -> ProviderFuture<'_, ()>;
}

pub trait WorkspaceClient: Send + Sync {
    fn get_or_create(&self, request: GetOrCreateWorkspace) -> ProviderFuture<'_, WorkspaceRef>;
    fn attach_session(
        &self,
        workspace: String,
        session: String,
    ) -> ProviderFuture<'_, WorkspaceAttachment>;
    /// The immutable (workspace, session) pair identifies the attachment.
    fn detach_session(&self, attachment: WorkspaceAttachment) -> ProviderFuture<'_, ()>;
    fn stop_workspace(&self, workspace: String) -> ProviderFuture<'_, ()>;
    fn delete_data(&self, data: String) -> ProviderFuture<'_, ()>;
    fn file(
        &self,
        attachment: WorkspaceAttachment,
        operation: FileOperation,
    ) -> ProviderFuture<'_, Value>;
}

#[derive(Serialize, Deserialize)]
#[serde(
    tag = "name",
    content = "arguments",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum FileOperation {
    ListFiles {},
    ReadFile {
        path: String,
    },
    WriteFile {
        path: String,
        content: String,
    },
    EditFile {
        path: String,
        old_text: String,
        new_text: String,
    },
}

pub fn schemas() -> Vec<Value> {
    let descriptions: &[(&str, &str, &[&str])] = &[
        ("list_files", "List project files", &[]),
        ("read_file", "Read a project text file", &["path"]),
        (
            "write_file",
            "Write a project text file",
            &["path", "content"],
        ),
        (
            "edit_file",
            "Replace one exact occurrence in a project text file",
            &["path", "old_text", "new_text"],
        ),
    ];
    descriptions.iter().map(|(name, description, fields)| {
        let properties: BTreeMap<_, _> = fields.iter().map(|key| (key, json!({"type":"string"}))).collect();
        json!({"name":name,"description":description,"inputSchema":{
            "type":"object","properties":properties,"required":fields,"additionalProperties":false
        }})
    }).collect()
}

#[derive(Default, Clone)]
pub struct Registry(BTreeMap<String, Arc<dyn WorkspaceProvider>>);

fn split(reference: &str) -> Result<(&str, &str)> {
    let (name, id) = reference.split_once('.').context(WorkspaceError::Invalid)?;
    ensure!(
        crate::bundle::safe_name(name) && crate::bundle::safe_name(id),
        WorkspaceError::Invalid
    );
    Ok((name, id))
}

// Provider transport diagnostics can include credentials or endpoint URLs.
// Only explicitly public provider outcomes may enter runtime/journal errors.
fn public_error(error: anyhow::Error) -> anyhow::Error {
    match error.downcast::<WorkspaceError>() {
        Ok(public) => public.into(),
        Err(_) => anyhow::anyhow!("Workspace provider unavailable; reconcile the original request"),
    }
}

impl Registry {
    pub fn register(&mut self, name: &str, provider: Arc<dyn WorkspaceProvider>) -> Result<()> {
        ensure!(
            crate::bundle::safe_name(name) && !self.0.contains_key(name),
            "Invalid or duplicate workspace provider name"
        );
        self.0.insert(name.to_owned(), provider);
        Ok(())
    }
    pub fn available(&self) -> bool {
        !self.0.is_empty()
    }
    pub fn provider(&self, name: &str) -> Result<&Arc<dyn WorkspaceProvider>> {
        self.0.get(name).context("Workspace provider unavailable")
    }
    pub async fn recover(&self, reference: &str) -> Result<(Arc<dyn WorkspaceClient>, String)> {
        let (name, id) = split(reference)?;
        Ok((
            self.provider(name)?
                .recover(id.into())
                .await
                .map_err(public_error)?,
            id.into(),
        ))
    }
    pub fn local_id<'a>(name: &str, reference: &'a str) -> Result<&'a str> {
        let (owner, id) = split(reference)?;
        ensure!(owner == name, WorkspaceError::Invalid);
        Ok(id)
    }
    pub async fn open(
        name: &str,
        client: &dyn WorkspaceClient,
        mut request: GetOrCreateWorkspace,
    ) -> Result<WorkspaceRef> {
        ensure!(
            !request.key.is_empty()
                && request.key.len() <= 512
                && !request.key.chars().any(char::is_control),
            WorkspaceError::Invalid
        );
        if let Some(data) = &request.data_id {
            request.data_id = Some(Self::local_id(name, data)?.into());
        }
        let expected = request.data_id.clone();
        let result = client.get_or_create(request).await?;
        ensure!(
            crate::bundle::safe_name(&result.workspace_id)
                && crate::bundle::safe_name(&result.data_id)
                && expected.as_ref().is_none_or(|d| d == &result.data_id),
            "Invalid provider workspace result"
        );
        Ok(WorkspaceRef {
            workspace_id: format!("{name}.{}", result.workspace_id),
            data_id: format!("{name}.{}", result.data_id),
        })
    }
}

pub fn validate_tools<'a>(
    bundle: &crate::bundle::Bundle,
    aliases: impl Iterator<Item = &'a String>,
) -> Result<()> {
    for alias in aliases {
        ensure!(
            crate::permissions::native_tool(bundle, alias).is_some_and(file_tool),
            "Workspace-bound release must select workspace file tools only"
        );
    }
    Ok(())
}
fn file_tool(name: &str) -> bool {
    matches!(
        name,
        "read_file" | "write_file" | "edit_file" | "list_files"
    )
}
impl Registry {
    pub async fn attach(&self, reference: &str, session: &str) -> Result<Channel> {
        let (client, workspace_id) = self.recover(reference).await?;
        let attachment = client
            .attach_session(workspace_id.clone(), session.to_owned())
            .await
            .map_err(public_error)?;
        ensure!(
            attachment.workspace_id == workspace_id && attachment.session_id == session,
            "Workspace provider returned an unexpected attachment identity"
        );
        Ok(Channel { client, attachment })
    }
    pub async fn detach(&self, reference: &str, session: &str) -> Result<()> {
        let (name, id) = split(reference)?;
        self.provider(name)?
            .detach(id.into(), session.into())
            .await
            .map_err(public_error)
    }
    pub fn validate_reference(&self, reference: &str) -> Result<()> {
        self.provider(split(reference)?.0)?;
        Ok(())
    }
    pub fn registration<'a>(&self, reference: &'a str) -> Result<&'a str> {
        let (name, _) = split(reference)?;
        self.provider(name)?;
        Ok(name)
    }
}

pub struct Channel {
    client: Arc<dyn WorkspaceClient>,
    attachment: WorkspaceAttachment,
}
impl Channel {
    pub async fn request(&self, method: &str, params: Value) -> Result<Value> {
        ensure!(method == "tools/call", "Unsupported workspace operation");
        let op = serde_json::from_value(params).context("Invalid workspace file operation")?;
        let value = self
            .client
            .file(self.attachment.clone(), op)
            .await
            .map_err(|_| {
                anyhow::anyhow!(
                    "Workspace file operation failed; inspect the workspace before retrying"
                )
            })?;
        Ok(
            json!({"content":[{"type":"text","text":value.to_string()}],"structuredContent":value,"isError":false}),
        )
    }
}
