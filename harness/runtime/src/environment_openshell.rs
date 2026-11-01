//! Pinned OpenShell 0.1.3 server provider. No CLI, local Docker fallback, host
//! mounts or automatic lifecycle retry. Credential providers require an explicit
//! original provider/profile pin; they are absent from the default profile. The gateway and
//! cluster operator remain trusted; app-owned objects must use this exact profile.
use crate::{
    environment::*,
    sandbox::{ExecConnectionConfig, Mcp, WorkspaceSelector},
};
use anyhow::{bail, Context, Result};
use prost::Message;
use serde_json::Value;
use std::{collections::BTreeMap, time::Duration};
use tonic::transport::{Certificate, Channel, ClientTlsConfig, Identity};

pub struct OpenShell {
    key: String,
    identity: String,
    config: ExecConnectionConfig,
    image: String,
    cpu: u16,
    memory_mb: u32,
    credential_provider: Option<CredentialProvider>,
}
/// Trusted application configuration. No credential value is stored here.
/// Pinning is explicit setup, never inferred from a model-supplied server name.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CredentialProvider {
    pub name: String,
    pub provider_id: String,
    pub profile_id: String,
    pub profile_workspace: String,
    pub profile_digest: String,
}
impl CredentialProvider {
    fn validate(&self) -> Result<()> {
        for id in [&self.name, &self.provider_id, &self.profile_id] {
            anyhow::ensure!(
                !id.is_empty()
                    && id.len() <= 128
                    && id
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
                "Invalid pinned credential provider identity"
            );
        }
        anyhow::ensure!(
            self.profile_workspace.len() <= 128
                && self
                    .profile_workspace
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
            "Invalid provider profile workspace"
        );
        anyhow::ensure!(
            self.profile_digest.len() == 64
                && self
                    .profile_digest
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "Credential provider profile digest required"
        );
        Ok(())
    }
}
impl OpenShell {
    pub fn new(
        key: String,
        identity: String,
        config: ExecConnectionConfig,
        image: String,
    ) -> Result<Self> {
        let this = Self {
            key,
            identity,
            config,
            image,
            cpu: 1,
            memory_mb: 512,
            credential_provider: None,
        };
        this.spec(Sharing::Exclusive, Management::Harness)
            .validate()?;
        if this.config.workspace.is_empty() {
            bail!("Named gateway workspace required");
        }
        Ok(this)
    }
    pub fn with_resources(mut self, cpu: u16, memory_mb: u32) -> Result<Self> {
        anyhow::ensure!(
            (1..=16).contains(&cpu) && (256..=65536).contains(&memory_mb),
            "Unsupported OpenShell resource limits"
        );
        self.cpu = cpu;
        self.memory_mb = memory_mb;
        Ok(self)
    }
    pub fn with_credential_provider(mut self, pin: CredentialProvider) -> Result<Self> {
        pin.validate()?;
        anyhow::ensure!(
            pin.profile_workspace.is_empty() || pin.profile_workspace == self.config.workspace,
            "Credential provider profile belongs to another workspace"
        );
        self.credential_provider = Some(pin);
        Ok(self)
    }
    /// Explicit operator-owned setup. Persist the returned non-secret pin with
    /// the RuntimeProfile; do not resolve names afresh on each session creation.
    pub async fn pin_credential_provider(&self, name: &str) -> Result<CredentialProvider> {
        self.check_gateway().await?;
        self.provider_pin(name).await
    }
    async fn provider_pin(&self, name: &str) -> Result<CredentialProvider> {
        anyhow::ensure!(
            !name.is_empty()
                && name.len() <= 128
                && name
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
            "Invalid credential provider name"
        );
        let response: CredentialProviderResponse = self
            .rpc(
                "/openshell.v1.OpenShell/GetProvider",
                ProviderQuery {
                    name: name.into(),
                    scope: self.scope(),
                },
            )
            .await?;
        let p = response
            .provider
            .context("Original credential provider absent")?;
        let meta = p.metadata.context("Credential provider identity absent")?;
        anyhow::ensure!(
            meta.name == name && meta.workspace == self.config.workspace,
            "Credential provider scope changed"
        );
        let response: ProfileBytesResponse = self
            .rpc(
                "/openshell.v1.OpenShell/GetProviderProfile",
                ProviderQuery {
                    name: p.kind.clone(),
                    scope: (!p.profile_workspace.is_empty()).then(|| WorkspaceSelector {
                        workspace: p.profile_workspace.clone(),
                    }),
                },
            )
            .await?;
        anyhow::ensure!(
            !response.profile.is_empty() && response.profile.len() <= 128000,
            "Credential provider profile unavailable or too large"
        );
        let pin = CredentialProvider {
            name: name.into(),
            provider_id: meta.id,
            profile_id: p.kind,
            profile_workspace: p.profile_workspace,
            profile_digest: crate::bundle::digest(&response.profile),
        };
        pin.validate()?;
        Ok(pin)
    }
    async fn check_credential_provider(&self) -> Result<()> {
        if let Some(pin) = &self.credential_provider {
            anyhow::ensure!(
                &self.provider_pin(&pin.name).await? == pin,
                "Original credential provider/profile changed; no automatic replacement"
            );
        }
        Ok(())
    }
    fn configured_template(&self) -> SandboxSpec {
        let mut spec = Self::template(&self.image);
        spec.template.as_mut().unwrap().resources = Some(resource_limits(self.cpu, self.memory_mb));
        if let Some(pin) = &self.credential_provider {
            spec.providers = vec![pin.name.clone()];
        }
        spec
    }
    pub fn spec(&self, sharing: Sharing, management: Management) -> Spec {
        Spec {
            provider: self.key.clone(),
            provider_identity: self.identity.clone(),
            profile_digest: {
                let bytes = serde_json::to_vec(&(
                    self.config.workspace.as_str(),
                    self.configured_template().encode_to_vec(),
                ))
                .expect("profile serialization");
                crate::bundle::digest(&match &self.credential_provider {
                    None => bytes,
                    Some(pin) => serde_json::to_vec(&("credential-provider-v1", bytes, pin))
                        .expect("provider serialization"),
                })
            },
            image: self.image.clone(),
            workspace_root: "/sandbox".into(),
            sharing,
            management,
        }
    }
    fn scope(&self) -> Option<WorkspaceSelector> {
        Some(WorkspaceSelector {
            workspace: self.config.workspace.clone(),
        })
    }
    fn template(image: &str) -> SandboxSpec {
        SandboxSpec {
            template: Some(Template {
                image: image.into(),
                resources: Some(resources()),
                ..Default::default()
            }),
            policy: Some(Policy {
                version: 1,
                filesystem: Some(Filesystem {
                    include_workdir: false,
                    read_only: [
                        "/usr",
                        "/lib",
                        "/etc",
                        "/proc",
                        "/dev/urandom",
                        "/opt/openworker",
                    ]
                    .map(String::from)
                    .to_vec(),
                    read_write: ["/sandbox", "/tmp", "/dev/null", "/dev/pts"]
                        .map(String::from)
                        .to_vec(),
                }),
                landlock: Some(Landlock {
                    compatibility: "hard_requirement".into(),
                }),
                process: Some(Process {
                    user: "1000".into(),
                    group: "1000".into(),
                }),
                ..Default::default()
            }),
            command: vec!["sleep".into(), "infinity".into()],
            ..Default::default()
        }
    }
    async fn channel(&self) -> Result<Channel> {
        let url = reqwest::Url::parse(&self.config.endpoint)?;
        if url.scheme() != "https"
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            bail!("Private OpenShell endpoint must be HTTPS without credentials/query/fragment");
        }
        let dir = &self.config.certificates;
        let mut tls = ClientTlsConfig::new()
            .ca_certificate(Certificate::from_pem(std::fs::read(dir.join("ca.crt"))?))
            .identity(Identity::from_pem(
                std::fs::read(dir.join("tls.crt"))?,
                std::fs::read(dir.join("tls.key"))?,
            ));
        if let Some(name) = &self.config.tls_server_name {
            tls = tls.domain_name(name);
        } else if matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]")) {
            tls = tls.domain_name("localhost");
        }
        Ok(Channel::from_shared(self.config.endpoint.clone())?
            .tls_config(tls)?
            .connect_timeout(Duration::from_secs(10))
            .connect()
            .await?)
    }
    async fn rpc<Q: Message + Default + 'static, R: Message + Default + 'static>(
        &self,
        path: &'static str,
        query: Q,
    ) -> Result<R> {
        let mut client = tonic::client::Grpc::new(self.channel().await?);
        client.ready().await?;
        let mut request = tonic::Request::new(query);
        // Kubernetes deletion may drain workloads and supervisor streams. Real
        // qualification observed a successful delete taking 43 seconds. Keep
        // reads short; a lifecycle timeout remains unknown, never a retry hint.
        let deadline = if path == "/openshell.v1.OpenShell/DeleteSandbox" {
            Duration::from_secs(90)
        } else {
            Duration::from_secs(15)
        };
        request.set_timeout(deadline);
        Ok(tokio::time::timeout(
            deadline + Duration::from_secs(5),
            client.unary(
                request,
                tonic::codegen::http::uri::PathAndQuery::from_static(path),
                tonic::codec::ProstCodec::<Q, R>::default(),
            ),
        )
        .await
        .context("Gateway operation outcome unknown after timeout")??
        .into_inner())
    }
    /// Read-only gateway/version readiness; does not create or probe a sandbox.
    pub async fn check_gateway(&self) -> Result<()> {
        let info: Gateway = self
            .rpc("/openshell.v1.OpenShell/GetGatewayInfo", Empty {})
            .await?;
        if info.version != "0.1.3"
            || info.drivers.len() != 1
            || info.drivers[0].name != "kubernetes"
        {
            bail!("Server provider requires OpenShell 0.1.3 with the qualified Kubernetes driver");
        }
        Ok(())
    }
    async fn version(&self) -> Result<()> {
        self.check_gateway().await
    }
    async fn get(&self, name: &str) -> Result<Sandbox> {
        let response: SandboxResponse = self
            .rpc(
                "/openshell.v1.OpenShell/GetSandbox",
                Get {
                    name: name.into(),
                    scope: self.scope(),
                },
            )
            .await?;
        response.sandbox.context("Gateway sandbox absent")
    }
    fn observed(&self, object: Sandbox, spec: Spec, environment_id: String) -> Result<Binding> {
        let meta = object.metadata.context("Remote object metadata missing")?;
        let desired = object.spec.context("Remote spec missing")?;
        let status = object.status.context("Remote status missing")?;
        let expected = self.configured_template();
        let template = desired.template.context("Remote image missing")?;
        let expected_template = expected.template.unwrap();
        // Reject extra authority instead of ignoring fields in a partial wire model.
        if template.image != self.image
            || !template.agent_socket.is_empty()
            || !template.environment.is_empty()
            || template.driver_config.is_some()
            || !template.runtime_class.is_empty()
            || template.resources != expected_template.resources
            || desired.policy != expected.policy
            || desired.providers != expected.providers
            || !desired.environment.is_empty()
            || desired.command != expected.command
            || desired.tty
            || meta.workspace != self.config.workspace
        {
            bail!("Remote image, policy, workspace or authority differs from registered profile");
        }
        let accepted = status.admission.context("No remote policy admission")?;
        if status.phase != 2
            || accepted.state != 2
            || accepted.policy_version == 0
            || accepted.policy_hash.is_empty()
        {
            bail!("Original environment is not ready with accepted policy");
        }
        let binding = Binding {
            environment_id,
            spec,
            remote_id: meta.id,
            remote_name: meta.name,
            workspace_scope: meta.workspace,
            policy_hash: accepted.policy_hash,
            policy_version: accepted.policy_version,
        };
        binding.validate()?;
        Ok(binding)
    }
    fn original_for_cleanup(&self, object: &Sandbox, binding: &Binding) -> Result<()> {
        let meta = object
            .metadata
            .as_ref()
            .context("Original cleanup identity absent")?;
        let spec = object
            .spec
            .as_ref()
            .context("Original cleanup spec absent")?;
        anyhow::ensure!(
            meta.id == binding.remote_id
                && meta.name == binding.remote_name
                && meta.workspace == self.config.workspace
                && binding.workspace_scope == self.config.workspace
                && meta.labels.get("openworker-environment") == Some(&binding.environment_id)
                && spec
                    .template
                    .as_ref()
                    .is_some_and(|t| t.image == binding.spec.image),
            "Original cleanup identity changed; refusing replacement"
        );
        Ok(())
    }
    /// Explicit application-owned registration, not a create-or-replace helper.
    pub async fn bind_existing(&self, record: &Record, name: &str) -> Result<Binding> {
        self.validate_spec(&record.spec)?;
        self.version().await?;
        self.check_credential_provider().await?;
        let object = self.get(name).await?;
        if record.spec.management == Management::Harness
            && object
                .metadata
                .as_ref()
                .and_then(|m| m.labels.get("openworker-environment"))
                != Some(&record.id)
        {
            bail!("Harness-managed reconciliation requires the original environment label");
        }
        self.observed(object, record.spec.clone(), record.id.clone())
    }
}
impl Provider for OpenShell {
    fn key(&self) -> &str {
        &self.key
    }
    fn capabilities(&self) -> Capabilities {
        Capabilities {
            create: true,
            shared: true,
            persistent_workspace: true,
            disconnect_terminates_effects: false,
        }
    }
    fn validate_spec(&self, spec: &Spec) -> Result<()> {
        spec.validate()?;
        if spec != &self.spec(spec.sharing.clone(), spec.management.clone()) {
            bail!("Environment provider identity/profile/image changed");
        }
        Ok(())
    }
    fn create(&self, record: Record) -> ProviderFuture<'_, Binding> {
        Box::pin(async move {
            self.validate_spec(&record.spec)?;
            if record.spec.management != Management::Harness || record.phase != Phase::Provisioning
            {
                bail!("Only a fresh harness-managed provisioning intent may create");
            }
            self.version().await?;
            self.check_credential_provider().await?;
            let raw = record
                .id
                .strip_prefix("env_")
                .context("Invalid environment ID")?;
            let id = uuid::Uuid::parse_str(raw)?;
            let name = format!("owr-{}", &id.simple().to_string()[..15]);
            let _: SandboxResponse = self
                .rpc(
                    "/openshell.v1.OpenShell/CreateSandbox",
                    Create {
                        spec: Some(self.configured_template()),
                        name: name.clone(),
                        labels: BTreeMap::from([(
                            "openworker-environment".into(),
                            record.id.clone(),
                        )]),
                        scope: self.scope(),
                        request_id: id.to_string(),
                    },
                )
                .await?;
            // Only reads after admission; a timeout is unknown, never create again.
            tokio::time::timeout(Duration::from_secs(120), async {
                loop {
                    let object = self.get(&name).await?;
                    if object
                        .metadata
                        .as_ref()
                        .and_then(|m| m.labels.get("openworker-environment"))
                        != Some(&record.id)
                    {
                        bail!("Remote provisioning identity changed");
                    }
                    if object.status.as_ref().is_some_and(|s| s.phase == 2) {
                        self.check_credential_provider().await?;
                        return self.observed(object, record.spec.clone(), record.id.clone());
                    }
                    if object
                        .status
                        .as_ref()
                        .is_some_and(|s| matches!(s.phase, 3 | 4 | 5 | 9))
                    {
                        bail!("Environment provisioning failed; inspect original object");
                    }
                    tokio::time::sleep(Duration::from_millis(500)).await;
                }
            })
            .await
            .context("Environment provisioning outcome unknown; reconcile original object")?
        })
    }
    fn inspect(&self, binding: Binding) -> ProviderFuture<'_, ()> {
        Box::pin(async move {
            self.validate_spec(&binding.spec)?;
            self.version().await?;
            self.check_credential_provider().await?;
            if self.observed(
                self.get(&binding.remote_name).await?,
                binding.spec.clone(),
                binding.environment_id.clone(),
            )? != binding
            {
                bail!("Original environment identity changed; refusing replacement");
            }
            Ok(())
        })
    }
    fn delete(&self, binding: Binding, operation_id: String) -> ProviderFuture<'_, ()> {
        Box::pin(async move {
            if binding.spec.management != Management::Harness {
                bail!("Application owns environment deletion");
            }
            // Cleanup is not execution authority. Revoking/detaching a provider
            // must not strand the ORIGINAL owned sandbox. Still fence namespace,
            // UID, name, owner label and image; never adopt a replacement.
            binding.validate()?;
            self.validate_spec(&binding.spec)?;
            self.version().await?;
            self.original_for_cleanup(&self.get(&binding.remote_name).await?, &binding)?;
            let id = uuid::Uuid::parse_str(&operation_id)?;
            if id.is_nil() {
                bail!("Delete ID cannot be nil");
            }
            // Gateway deletion has no expected-UID field. Operators must prohibit
            // out-of-band delete/recreate races; this API cannot fence that race.
            let _: Empty = self
                .rpc(
                    "/openshell.v1.OpenShell/DeleteSandbox",
                    Delete {
                        name: binding.remote_name.clone(),
                        scope: self.scope(),
                        allow_missing: false,
                        request_id: id.to_string(),
                    },
                )
                .await?;
            tokio::time::timeout(Duration::from_secs(60), async {
                loop {
                    match self.get(&binding.remote_name).await {
                        Err(e)
                            if e.downcast_ref::<tonic::Status>()
                                .is_some_and(|s| s.code() == tonic::Code::NotFound) =>
                        {
                            return Ok(())
                        }
                        Err(e) => return Err(e),
                        Ok(o)
                            if o.metadata
                                .as_ref()
                                .is_none_or(|m| m.id != binding.remote_id) =>
                        {
                            bail!("Replacement observed during deletion")
                        }
                        Ok(_) => tokio::time::sleep(Duration::from_millis(500)).await,
                    }
                }
            })
            .await
            .context("Deletion not confirmed; retain deleting state")?
        })
    }
    fn confirm_deleted(&self, binding: Binding) -> ProviderFuture<'_, bool> {
        Box::pin(async move {
            binding.validate()?;
            self.validate_spec(&binding.spec)?;
            if binding.workspace_scope != self.config.workspace {
                bail!("Cannot reconcile deletion in a different gateway workspace");
            }
            self.version().await?;
            match self.get(&binding.remote_name).await {
                Err(e)
                    if e.downcast_ref::<tonic::Status>()
                        .is_some_and(|s| s.code() == tonic::Code::NotFound) =>
                {
                    Ok(true)
                }
                Err(e) => Err(e),
                Ok(o)
                    if o.metadata
                        .as_ref()
                        .is_none_or(|m| m.id != binding.remote_id) =>
                {
                    bail!("Replacement observed; original deletion cannot authorize touching it")
                }
                Ok(_) => Ok(false),
            }
        })
    }
    fn attach(
        &self,
        binding: Binding,
        launch_id: String,
        argv: Vec<String>,
    ) -> ProviderFuture<'_, Box<dyn Connection>> {
        Box::pin(async move {
            self.inspect(binding.clone()).await?;
            if argv.first().map(String::as_str) != Some("/opt/openworker/openworker") {
                bail!("Executable is not in the registered runner profile");
            }
            Ok(Box::new(Stream(
                Mcp::connect_attachment(&self.config, &binding.remote_name, argv, &launch_id)
                    .await?,
            )) as Box<dyn Connection>)
        })
    }
}
struct Stream(Mcp);
impl Connection for Stream {
    fn request<'a>(&'a mut self, method: &'a str, params: Value) -> ProviderFuture<'a, Value> {
        Box::pin(async move { self.0.request(method, params).await })
    }
    fn close(self: Box<Self>) -> ProviderFuture<'static, CloseOutcome> {
        Box::pin(async move {
            Ok(if self.0.close().await.is_ok() {
                CloseOutcome::Exited
            } else {
                CloseOutcome::Unknown
            })
        })
    }
}

// Minimal wire messages from the pinned public 0.1.3 protocol. Presence-only
// fields use bytes/Empty when their contents are never authorized by this profile.
#[derive(Clone, PartialEq, Message)]
struct Empty {}
#[derive(Clone, PartialEq, Message)]
struct Gateway {
    #[prost(string, tag = "2")]
    version: String,
    #[prost(message, repeated, tag = "3")]
    drivers: Vec<Driver>,
}
#[derive(Clone, PartialEq, Message)]
struct Driver {
    #[prost(string, tag = "1")]
    name: String,
}
#[derive(Clone, PartialEq, Message)]
struct Get {
    #[prost(string, tag = "1")]
    name: String,
    #[prost(message, optional, tag = "2")]
    scope: Option<WorkspaceSelector>,
}
#[derive(Clone, PartialEq, Message)]
struct ProviderQuery {
    #[prost(string, tag = "1")]
    name: String,
    #[prost(message, optional, tag = "2")]
    scope: Option<WorkspaceSelector>,
}
#[derive(Clone, PartialEq, Message)]
struct CredentialProviderResponse {
    #[prost(message, optional, tag = "1")]
    provider: Option<ProviderMetadata>,
}
#[derive(Clone, PartialEq, Message)]
struct ProviderMetadata {
    #[prost(message, optional, tag = "1")]
    metadata: Option<Meta>,
    #[prost(string, tag = "2")]
    kind: String,
    // Credential/config fields deliberately omitted. Do not decode/export their
    // values into the sandbox or persist them in the execution identity.
    #[prost(string, tag = "6")]
    profile_workspace: String,
}
#[derive(Clone, PartialEq, Message)]
struct ProfileBytesResponse {
    // Preserve all profile fields, including unfamiliar policy/refresh settings,
    // for a conservative exact pin rather than silently dropping authority.
    #[prost(bytes = "vec", tag = "1")]
    profile: Vec<u8>,
}
#[derive(Clone, PartialEq, Message)]
struct Delete {
    #[prost(string, tag = "1")]
    name: String,
    #[prost(message, optional, tag = "2")]
    scope: Option<WorkspaceSelector>,
    #[prost(bool, tag = "3")]
    allow_missing: bool,
    #[prost(string, tag = "4")]
    request_id: String,
}
#[derive(Clone, PartialEq, Message)]
struct Create {
    #[prost(message, optional, tag = "1")]
    spec: Option<SandboxSpec>,
    #[prost(string, tag = "2")]
    name: String,
    #[prost(btree_map = "string,string", tag = "3")]
    labels: BTreeMap<String, String>,
    #[prost(message, optional, tag = "7")]
    scope: Option<WorkspaceSelector>,
    #[prost(string, tag = "8")]
    request_id: String,
}
#[derive(Clone, PartialEq, Message)]
struct SandboxResponse {
    #[prost(message, optional, tag = "1")]
    sandbox: Option<Sandbox>,
}
#[derive(Clone, PartialEq, Message)]
struct Sandbox {
    #[prost(message, optional, tag = "1")]
    metadata: Option<Meta>,
    #[prost(message, optional, tag = "2")]
    spec: Option<SandboxSpec>,
    #[prost(message, optional, tag = "3")]
    status: Option<Status>,
}
#[derive(Clone, PartialEq, Message)]
struct Meta {
    #[prost(string, tag = "1")]
    id: String,
    #[prost(string, tag = "2")]
    name: String,
    #[prost(btree_map = "string,string", tag = "4")]
    labels: BTreeMap<String, String>,
    #[prost(string, tag = "7")]
    workspace: String,
}
#[derive(Clone, PartialEq, Message)]
struct SandboxSpec {
    #[prost(btree_map = "string,string", tag = "5")]
    environment: BTreeMap<String, String>,
    #[prost(message, optional, tag = "6")]
    template: Option<Template>,
    #[prost(message, optional, tag = "7")]
    policy: Option<Policy>,
    #[prost(string, repeated, tag = "8")]
    providers: Vec<String>,
    #[prost(string, repeated, tag = "12")]
    command: Vec<String>,
    #[prost(bool, tag = "13")]
    tty: bool,
}
#[derive(Clone, PartialEq, Message)]
struct Template {
    #[prost(string, tag = "1")]
    image: String,
    #[prost(string, tag = "2")]
    runtime_class: String,
    #[prost(string, tag = "3")]
    agent_socket: String,
    #[prost(btree_map = "string,string", tag = "6")]
    environment: BTreeMap<String, String>,
    #[prost(message, optional, tag = "7")]
    resources: Option<Struct>,
    #[prost(bytes = "vec", optional, tag = "11")]
    driver_config: Option<Vec<u8>>,
}
#[derive(Clone, PartialEq, Message)]
struct Status {
    #[prost(int32, tag = "6")]
    phase: i32,
    #[prost(message, optional, tag = "11")]
    admission: Option<Admission>,
}
#[derive(Clone, PartialEq, Message)]
struct Admission {
    #[prost(int32, tag = "2")]
    state: i32,
    #[prost(uint32, tag = "3")]
    policy_version: u32,
    #[prost(string, tag = "4")]
    policy_hash: String,
}
#[derive(Clone, PartialEq, Message)]
struct Policy {
    #[prost(uint32, tag = "1")]
    version: u32,
    #[prost(message, optional, tag = "2")]
    filesystem: Option<Filesystem>,
    #[prost(message, optional, tag = "3")]
    landlock: Option<Landlock>,
    #[prost(message, optional, tag = "4")]
    process: Option<Process>,
    #[prost(btree_map = "string,message", tag = "5")]
    network: BTreeMap<String, Empty>,
    #[prost(btree_map = "string,message", tag = "6")]
    middlewares: BTreeMap<String, Empty>,
}
#[derive(Clone, PartialEq, Message)]
struct Filesystem {
    #[prost(bool, tag = "1")]
    include_workdir: bool,
    #[prost(string, repeated, tag = "2")]
    read_only: Vec<String>,
    #[prost(string, repeated, tag = "3")]
    read_write: Vec<String>,
}
#[derive(Clone, PartialEq, Message)]
struct Landlock {
    #[prost(string, tag = "1")]
    compatibility: String,
}
#[derive(Clone, PartialEq, Message)]
struct Process {
    #[prost(string, tag = "1")]
    user: String,
    #[prost(string, tag = "2")]
    group: String,
}
#[derive(Clone, PartialEq, Message)]
struct Struct {
    #[prost(btree_map = "string,message", tag = "1")]
    fields: BTreeMap<String, StructValue>,
}
#[derive(Clone, PartialEq, Message)]
struct StructValue {
    #[prost(oneof = "struct_value::Kind", tags = "3,5")]
    kind: Option<struct_value::Kind>,
}
mod struct_value {
    #[derive(Clone, PartialEq, prost::Oneof)]
    pub enum Kind {
        #[prost(string, tag = "3")]
        Text(String),
        #[prost(message, tag = "5")]
        Object(super::Struct),
    }
}
fn resources() -> Struct {
    resource_limits(1, 512)
}
fn resource_limits(cpu: u16, memory_mb: u32) -> Struct {
    use struct_value::Kind::{Object, Text};
    let limits = Struct {
        fields: BTreeMap::from([
            (
                "cpu".into(),
                StructValue {
                    kind: Some(Text(cpu.to_string())),
                },
            ),
            (
                "memory".into(),
                StructValue {
                    kind: Some(Text(format!("{memory_mb}Mi"))),
                },
            ),
        ]),
    };
    Struct {
        fields: BTreeMap::from([(
            "limits".into(),
            StructValue {
                kind: Some(Object(limits)),
            },
        )]),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn provider() -> OpenShell {
        OpenShell::new(
            "k8s".into(),
            "cluster-one".into(),
            ExecConnectionConfig {
                endpoint: "https://localhost:1".into(),
                certificates: "/not-read-in-unit-test".into(),
                workspace: "default".into(),
                tls_server_name: None,
            },
            format!("registry.invalid/runner@sha256:{}", "a".repeat(64)),
        )
        .unwrap()
    }
    fn credential() -> CredentialProvider {
        CredentialProvider {
            name: "github-test".into(),
            provider_id: "provider-original".into(),
            profile_id: "github-read".into(),
            profile_workspace: "default".into(),
            profile_digest: "a".repeat(64),
        }
    }
    #[test]
    fn provider_queries_use_pinned_013_workspace_selector_wire_contract() {
        // v0.1.3 proto/openshell.proto: GetProvider[Profile]Request field 2
        // is a WorkspaceSelector MESSAGE, not the older workspace string.
        let q = ProviderQuery {
            name: "git".into(),
            scope: Some(WorkspaceSelector {
                workspace: "default".into(),
            }),
        };
        assert_eq!(
            q.encode_to_vec(),
            [10, 3, b'g', b'i', b't', 18, 9, 10, 7, b'd', b'e', b'f', b'a', b'u', b'l', b't']
        );
        let platform = ProviderQuery {
            name: "git".into(),
            scope: None,
        };
        assert_eq!(platform.encode_to_vec(), [10, 3, b'g', b'i', b't']);
    }
    #[test]
    fn provider_revocation_fences_execution_but_original_cleanup_keeps_identity_guards() {
        let p = provider().with_credential_provider(credential()).unwrap();
        let mut original = ready(&p);
        original
            .metadata
            .as_mut()
            .unwrap()
            .labels
            .insert("openworker-environment".into(), "env-test".into());
        let b = p
            .observed(
                original.clone(),
                p.spec(Sharing::Shared, Management::Harness),
                "env-test".into(),
            )
            .unwrap();
        let mut revoked = original.clone();
        revoked.spec.as_mut().unwrap().providers.clear();
        assert!(p
            .observed(revoked.clone(), b.spec.clone(), b.environment_id.clone())
            .is_err());
        assert!(p.original_for_cleanup(&revoked, &b).is_ok());
        let mut replaced = revoked.clone();
        replaced.metadata.as_mut().unwrap().id = "replacement".into();
        assert!(p.original_for_cleanup(&replaced, &b).is_err());
        let mut replaced = revoked.clone();
        replaced.metadata.as_mut().unwrap().labels.clear();
        assert!(p.original_for_cleanup(&replaced, &b).is_err());
        let mut replaced = revoked.clone();
        replaced.metadata.as_mut().unwrap().workspace = "another".into();
        assert!(p.original_for_cleanup(&replaced, &b).is_err());
        let mut replaced = revoked;
        replaced
            .spec
            .as_mut()
            .unwrap()
            .template
            .as_mut()
            .unwrap()
            .image = "other".into();
        assert!(p.original_for_cleanup(&replaced, &b).is_err());
    }
    #[test]
    fn credential_attachment_is_explicit_pinned_and_changes_execution_identity() {
        let plain = provider();
        let p = provider().with_credential_provider(credential()).unwrap();
        assert_ne!(
            plain.spec(Sharing::Shared, Management::Harness),
            p.spec(Sharing::Shared, Management::Harness)
        );
        assert!(plain
            .validate_spec(&p.spec(Sharing::Shared, Management::Harness))
            .is_err());
        let good = ready(&p);
        assert!(p
            .observed(
                good.clone(),
                p.spec(Sharing::Shared, Management::Harness),
                "env-test".into()
            )
            .is_ok());
        let mut bad = good.clone();
        bad.spec.as_mut().unwrap().providers.clear();
        assert!(p
            .observed(
                bad,
                p.spec(Sharing::Shared, Management::Harness),
                "env-test".into()
            )
            .is_err());
        let mut bad = good;
        bad.spec.as_mut().unwrap().providers.push("extra".into());
        assert!(p
            .observed(
                bad,
                p.spec(Sharing::Shared, Management::Harness),
                "env-test".into()
            )
            .is_err());
        let mut changed = credential();
        changed.provider_id = "replacement".into();
        assert_ne!(
            p.spec(Sharing::Shared, Management::Harness),
            provider()
                .with_credential_provider(changed)
                .unwrap()
                .spec(Sharing::Shared, Management::Harness)
        );
        let mut bad = credential();
        bad.profile_workspace = "another-workspace".into();
        assert!(provider().with_credential_provider(bad).is_err());
        let mut bad = credential();
        bad.profile_digest.clear();
        assert!(provider().with_credential_provider(bad).is_err());
    }
    fn ready(p: &OpenShell) -> Sandbox {
        Sandbox {
            metadata: Some(Meta {
                id: "remote-id".into(),
                name: "sandbox".into(),
                workspace: "default".into(),
                ..Default::default()
            }),
            spec: Some(p.configured_template()),
            status: Some(Status {
                phase: 2,
                admission: Some(Admission {
                    state: 2,
                    policy_version: 1,
                    policy_hash: "accepted".into(),
                }),
            }),
        }
    }
    #[test]
    fn altered_authority_and_unready_policy_are_rejected() {
        let p = provider();
        let good = ready(&p);
        let check = |s| {
            p.observed(
                s,
                p.spec(Sharing::Shared, Management::Application),
                "env-test".into(),
            )
        };
        assert!(check(good.clone()).is_ok());
        let mut bad = good.clone();
        bad.spec
            .as_mut()
            .unwrap()
            .policy
            .as_mut()
            .unwrap()
            .network
            .insert("extra".into(), Empty {});
        assert!(check(bad).is_err());
        let mut bad = good.clone();
        bad.spec
            .as_mut()
            .unwrap()
            .providers
            .push("credential".into());
        assert!(check(bad).is_err());
        let mut bad = good.clone();
        bad.spec
            .as_mut()
            .unwrap()
            .template
            .as_mut()
            .unwrap()
            .driver_config = Some(vec![]);
        assert!(check(bad).is_err());
        let mut bad = good.clone();
        bad.spec
            .as_mut()
            .unwrap()
            .template
            .as_mut()
            .unwrap()
            .image
            .push_str("-changed");
        assert!(check(bad).is_err());
        let mut bad = good.clone();
        bad.status.as_mut().unwrap().phase = 7;
        assert!(check(bad).is_err());
        let mut bad = good.clone();
        bad.status
            .as_mut()
            .unwrap()
            .admission
            .as_mut()
            .unwrap()
            .state = 1;
        assert!(check(bad).is_err());
        let mut bad = good;
        bad.metadata.as_mut().unwrap().workspace = "other".into();
        assert!(check(bad).is_err());
    }
    #[test]
    fn provider_identity_and_immutable_image_are_pinned() {
        let p = provider();
        let original = p.spec(Sharing::Exclusive, Management::Harness);
        p.validate_spec(&original).unwrap();
        let mut changed = original.clone();
        changed.provider_identity = "other".into();
        assert!(p.validate_spec(&changed).is_err());
        let mut changed = original.clone();
        changed.profile_digest = "other".into();
        assert!(p.validate_spec(&changed).is_err());
        let mut changed = original;
        changed.image = "runner:latest".into();
        assert!(changed.validate().is_err());
        assert!(!p.capabilities().disconnect_terminates_effects);
        let saved = p.spec(Sharing::Exclusive, Management::Harness);
        let mut changed = p;
        changed.config.workspace = "different-workspace".into();
        assert!(changed.validate_spec(&saved).is_err());
    }
    #[test]
    fn resource_limits_are_in_profile_identity_and_verified_on_attach() {
        let old = provider();
        let spec = old.spec(Sharing::Shared, Management::Harness);
        let bigger = provider().with_resources(2, 768).unwrap();
        assert_ne!(
            spec.profile_digest,
            bigger
                .spec(Sharing::Shared, Management::Harness)
                .profile_digest
        );
        assert!(bigger.validate_spec(&spec).is_err());
        assert!(bigger
            .observed(
                ready(&old),
                bigger.spec(Sharing::Shared, Management::Harness),
                "env-test".into()
            )
            .is_err());
        assert!(bigger
            .observed(
                ready(&bigger),
                bigger.spec(Sharing::Shared, Management::Harness),
                "env-test".into()
            )
            .is_ok());
        assert!(provider().with_resources(0, 512).is_err());
    }
}
