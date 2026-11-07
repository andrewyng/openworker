//! Portable execution identities. Provider credentials are deliberately absent.
use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeMap, future::Future, pin::Pin, sync::Arc};

pub type ProviderFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T>> + Send + 'a>>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CloseOutcome {
    Exited,
    Unknown,
}
#[derive(Clone, Copy, Debug)]
pub struct Capabilities {
    pub create: bool,
    pub shared: bool,
    pub persistent_workspace: bool,
    /// A false value forbids treating connection loss as proof effects stopped.
    pub disconnect_terminates_effects: bool,
}

/// A connection owns one tool process/channel, never the entire environment.
/// Uncertain-outcome rule: after dispatch, connection loss is not proof that an
/// action failed or stopped. Never transparently replay it. The harness must
/// persist a tool_outcome_unknown response for the original call so the model
/// can inspect status/read back state before deciding whether to issue another
/// action. This response is not retry authorization: any new call still passes
/// normal policy/approval checks. Cancellation must not restart the agent loop.
pub trait Connection: Send {
    fn request<'a>(&'a mut self, method: &'a str, params: Value) -> ProviderFuture<'a, Value>;
    fn close(self: Box<Self>) -> ProviderFuture<'static, CloseOutcome>;
}

/// Trusted host extension point. Implementations must not turn delivery loss
/// into transparent retries, rebind changed identities, or delete on release.
pub trait Provider: Send + Sync {
    fn key(&self) -> &str;
    fn capabilities(&self) -> Capabilities;
    fn validate_spec(&self, spec: &Spec) -> Result<()>;
    fn create(&self, record: Record) -> ProviderFuture<'_, Binding>;
    fn inspect(&self, binding: Binding) -> ProviderFuture<'_, ()>;
    fn delete(&self, binding: Binding, operation_id: String) -> ProviderFuture<'_, ()>;
    /// Read-only reconciliation after an uncertain delete; never issue deletion.
    fn confirm_deleted(&self, _binding: Binding) -> ProviderFuture<'_, bool> {
        Box::pin(async { bail!("Provider does not implement deletion reconciliation") })
    }
    fn attach(
        &self,
        binding: Binding,
        launch_id: String,
        argv: Vec<String>,
    ) -> ProviderFuture<'_, Box<dyn Connection>>;
}

#[derive(Default)]
pub struct Registry {
    providers: BTreeMap<String, Arc<dyn Provider>>,
}
impl Registry {
    pub fn register(&mut self, provider: Arc<dyn Provider>) -> Result<()> {
        let key = provider.key().to_owned();
        if !crate::bundle::safe_name(&key) || self.providers.contains_key(&key) {
            bail!("Invalid or duplicate environment provider");
        }
        self.providers.insert(key, provider);
        Ok(())
    }
    pub fn provider(&self, spec: &Spec) -> Result<Arc<dyn Provider>> {
        spec.validate()?;
        let p = self
            .providers
            .get(&spec.provider)
            .ok_or_else(|| anyhow::anyhow!("Environment provider unavailable"))?;
        p.validate_spec(spec)?;
        if spec.sharing == Sharing::Shared && !p.capabilities().shared {
            bail!("Provider does not support shared attachments");
        }
        Ok(p.clone())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Sharing {
    Exclusive,
    Shared,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Management {
    Harness,
    Application,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spec {
    pub provider: String,
    pub provider_identity: String,
    /// Trusted provider profile identity, not a credential or worker-local path.
    pub profile_digest: String,
    pub image: String,
    pub workspace_root: String,
    pub sharing: Sharing,
    pub management: Management,
}
impl Spec {
    pub fn validate(&self) -> Result<()> {
        if !crate::bundle::safe_name(&self.provider) || self.provider.len() > 80 {
            bail!("Invalid environment provider key");
        }
        for value in [&self.provider_identity, &self.profile_digest, &self.image] {
            if value.is_empty() || value.len() > 512 || value.chars().any(char::is_control) {
                bail!("Invalid environment identity");
            }
        }
        // Images are immutable inputs. Local qualification imports by digest too.
        let Some((_, sha)) = self.image.rsplit_once("@sha256:") else {
            bail!("Environment image requires an immutable digest");
        };
        if sha.len() != 64 || !sha.bytes().all(|b| b.is_ascii_hexdigit()) {
            bail!("Invalid environment image digest");
        }
        if self.workspace_root != "/sandbox" {
            bail!("Initial remote workspace profile supports /sandbox only");
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub environment_id: String,
    pub spec: Spec,
    pub remote_id: String,
    pub remote_name: String,
    pub workspace_scope: String,
    /// Gateway-accepted policy identity; runtime generation alone is not workspace identity.
    pub policy_hash: String,
    pub policy_version: u32,
}
impl Binding {
    pub fn validate(&self) -> Result<()> {
        self.spec.validate()?;
        for value in [
            &self.environment_id,
            &self.remote_id,
            &self.remote_name,
            &self.workspace_scope,
            &self.policy_hash,
        ] {
            if value.is_empty() || value.len() > 256 || value.chars().any(char::is_control) {
                bail!("Invalid remote environment binding");
            }
        }
        if self.policy_version == 0 {
            bail!("Environment policy has not been accepted");
        }
        Ok(())
    }
    pub fn fingerprint(&self) -> Result<String> {
        self.validate()?;
        Ok(crate::bundle::digest(&serde_json::to_vec(self)?))
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Provisioning,
    Ready,
    Unknown,
    Deleting,
    Deleted,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record {
    pub id: String,
    pub request_id: String,
    pub actor: String,
    pub spec: Spec,
    pub phase: Phase,
    pub binding: Option<Binding>,
    pub revision: i64,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AttachmentState {
    Pending,
    Active,
    Released,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Attachment {
    /// UUID used as OpenShell's durable exec launch ID.
    pub id: String,
    pub environment_id: String,
    pub session_id: String,
    pub server: String,
    pub owner_epoch: i64,
    pub state: AttachmentState,
    pub outcome_unknown: bool,
}
