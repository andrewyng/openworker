//! Pinned OpenShell lifecycle + mTLS exec stream. No direct execution fallback.
use crate::bundle::Bundle;
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
use tokio::sync::mpsc;
use tonic::transport::{Certificate, Channel, ClientTlsConfig, Identity};

pub const VERSION: &str = "0.0.116";
pub const IMAGE:&str="ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e";
pub fn config_home() -> Result<PathBuf> {
    Ok(std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or(
            PathBuf::from(std::env::var_os("HOME").context("HOME unavailable")?).join(".config"),
        )
        .join("openshell"))
}
pub async fn command(program: &str, args: &[&str], seconds: u64) -> Result<String> {
    let mut c = tokio::process::Command::new(program);
    c.args(args)
        .env_clear()
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    // Lifecycle subprocesses need OpenShell client configuration but never model keys.
    for key in [
        "PATH",
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "TMPDIR",
        "LANG",
        "SSL_CERT_FILE",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "OPENSHELL_GATEWAY",
    ] {
        if let Some(value) = std::env::var_os(key) {
            c.env(key, value);
        }
    }
    c.env("NO_COLOR", "1");
    let out = tokio::time::timeout(Duration::from_secs(seconds), c.output())
        .await
        .context("Lifecycle operation timed out")??;
    if !out.status.success() {
        bail!(
            "{} {} failed: {}",
            program,
            args.first().unwrap_or(&""),
            String::from_utf8_lossy(&out.stderr)
                .chars()
                .take(1000)
                .collect::<String>()
        );
    }
    Ok(String::from_utf8(out.stdout)?)
}
pub async fn doctor() -> Value {
    let mut checks = vec![];
    if cfg!(target_os = "linux") {
        checks.push(json!({"id":"runtime_user","ok":unsafe{libc::geteuid()}!=0,"detail":"Run the runtime as a non-root user with Docker access; OpenShell refuses UID/GID 0."}));
    }
    for (id, program, args) in [
        (
            "docker",
            "docker",
            vec!["info", "--format", "{{.OSType}} {{.Architecture}}"],
        ),
        ("openshell", "openshell", vec!["--version"]),
        (
            "gateway",
            "openshell",
            vec!["gateway", "info", "-o", "json"],
        ),
        (
            "image",
            "docker",
            vec!["image", "inspect", IMAGE, "--format", "{{.Architecture}}"],
        ),
    ] {
        match command(program, &args, 20).await {
            Ok(s) => {
                let ok = match id {
                    "openshell" => s.split_whitespace().last() == Some(VERSION),
                    "docker" => s.starts_with("linux"),
                    "gateway" => serde_json::from_str::<Value>(&s).is_ok_and(|v| {
                        v["version"] == VERSION
                            && v["compute_drivers"]
                                .as_array()
                                .is_some_and(|a| a.iter().any(|d| d["name"] == "docker"))
                    }),
                    _ => true,
                };
                checks.push(json!({"id":id,"ok":ok,"detail":s.trim()}));
            }
            Err(e) => checks.push(json!({"id":id,"ok":false,"detail":e.to_string()})),
        }
    }
    let ready = checks.iter().all(|c| c["ok"] == true);
    json!({"ready":ready,"sandbox":"openshell","supported_release":"linux","host_os":std::env::consts::OS,"checks":checks,"policy_probe":"Mounts, Landlock and network enforcement must additionally pass session startup/live verification. No fallback."})
}

pub fn policy(workspace: &str) -> Value {
    let (user, group) = if cfg!(target_os = "linux") {
        (
            unsafe { libc::getuid() }.to_string(),
            unsafe { libc::getgid() }.to_string(),
        )
    } else {
        ("sandbox".into(), "sandbox".into())
    };
    json!({"version":1,"filesystem_policy":{"include_workdir":false,
        "read_only":["/usr","/lib","/etc","/proc","/dev/urandom","/var/log","/sandbox","/opt/openworker","/opt/partner"],
        "read_write":["/tmp","/dev/null","/dev/pts",workspace]},"landlock":{"compatibility":"hard_requirement"},
        "process":{"run_as_user":user,"run_as_group":group},"network_policies":{}})
}

pub fn policy_and_mounts(
    workspace: &str,
    bundle: &Bundle,
    files: &std::path::Path,
    resolved: &crate::resource_policy::Resolved,
    credentials: Option<&std::path::Path>,
) -> (Value, Value) {
    let mut policy = policy(workspace);
    let mut mounts = vec![
        json!({"type":"bind","source":workspace,"target":workspace,"read_only":false}),
        json!({"type":"bind","source":files,"target":"/opt/openworker","read_only":true}),
        json!({"type":"bind","source":bundle.root,"target":"/opt/partner","read_only":true}),
    ];
    for f in resolved.files.values() {
        let read_only = matches!(f.grant.access, crate::resource_policy::Access::Read);
        policy["filesystem_policy"][if read_only { "read_only" } else { "read_write" }]
            .as_array_mut()
            .unwrap()
            .push(json!(f.grant.path));
        mounts.push(json!({"type":"bind","source":f.grant.path,"target":f.grant.path,"read_only":read_only}));
    }
    for (id, n) in &resolved.network {
        policy["network_policies"][id] = json!({"name":id,"endpoints":[{"host":n.host,"port":n.port}],"binaries":[{"path":"/**"}]});
    }
    if let Some(source) = credentials {
        let target = "/run/openworker/credentials";
        policy["filesystem_policy"]["read_only"]
            .as_array_mut()
            .unwrap()
            .push(json!(target));
        mounts.push(json!({"type":"bind","source":source,"target":target,"read_only":true}));
    }
    (policy, json!({"docker":{"mounts":mounts}}))
}

#[derive(Clone)]
pub struct Sandbox {
    pub root: PathBuf,
    pub runner: PathBuf,
}
impl Sandbox {
    pub async fn create(&self, name: &str, workspace: &str, bundle: &Bundle) -> Result<String> {
        self.create_with_policy(name, workspace, bundle, &Default::default())
            .await
    }
    fn credential_dir(&self, name: &str) -> Result<PathBuf> {
        if !name.starts_with("owr-") || !crate::bundle::safe_name(name) {
            bail!("Invalid owned credential directory");
        }
        Ok(self.root.join("credential-files").join(name))
    }
    pub fn cleanup_credentials(&self, name: &str) -> Result<()> {
        let path = self.credential_dir(name)?;
        self.cleanup_flat_directory(&path)
    }
    pub fn cleanup_runner(&self, name: &str) -> Result<()> {
        self.credential_dir(name)?; // same strict generated-name validation
        self.cleanup_flat_directory(&self.root.join("sandbox-files").join(name))
    }
    fn cleanup_flat_directory(&self, path: &std::path::Path) -> Result<()> {
        if !path.try_exists()? {
            return Ok(());
        }
        crate::resource_policy::checked_path(path)?;
        // Only known flat files; never recurse into an unexpected tree.
        for entry in std::fs::read_dir(path)? {
            let entry = entry?;
            if !entry.file_type()?.is_file() {
                bail!("Unexpected generated-file cleanup entry");
            }
            std::fs::remove_file(entry.path())?;
        }
        std::fs::remove_dir(path)?;
        Ok(())
    }
    pub fn prepare_credentials(
        &self,
        name: &str,
        resolved: &crate::resource_policy::Resolved,
    ) -> Result<Option<PathBuf>> {
        use std::{
            io::Write,
            os::unix::fs::{OpenOptionsExt, PermissionsExt},
        };
        if resolved.credentials.is_empty() {
            return Ok(None);
        }
        let dir = self.credential_dir(name)?;
        let parent = dir.parent().unwrap();
        std::fs::create_dir_all(parent)?;
        crate::resource_policy::checked_path(parent)?;
        std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700))?;
        std::fs::create_dir(&dir)?;
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
        for (id, c) in &resolved.credentials {
            if !crate::bundle::safe_name(id) {
                bail!("Invalid credential ID");
            }
            let bytes = crate::resource_policy::read_secret(c)?;
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o400)
                .open(dir.join(id))?;
            file.write_all(&bytes)?;
            file.sync_all()?;
        }
        Ok(Some(dir))
    }
    pub async fn create_with_policy(
        &self,
        name: &str,
        workspace: &str,
        bundle: &Bundle,
        resolved: &crate::resource_policy::Resolved,
    ) -> Result<String> {
        if doctor().await["ready"] != true {
            bail!("OpenShell is not ready; run openworker doctor --json and sandbox setup. Host execution is forbidden.");
        }
        resolved.verify_paths()?;
        let files = self.root.join("sandbox-files").join(name);
        std::fs::create_dir_all(&files)?;
        let bin = files.join("openworker");
        std::fs::copy(&self.runner, &bin)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755))?;
        }
        let p = files.join("policy.json");
        let credentials = self.prepare_credentials(name, resolved)?;
        let (policy, mounts) =
            policy_and_mounts(workspace, bundle, &files, resolved, credentials.as_deref());
        std::fs::write(&p, serde_json::to_vec(&policy)?)?;
        let mounts = mounts.to_string();
        command(
            "openshell",
            &[
                "sandbox",
                "create",
                "--name",
                name,
                "--from",
                IMAGE,
                "--no-tty",
                "--detach",
                "--no-auto-providers",
                "--policy",
                p.to_str().context("Policy path UTF-8")?,
                "--driver-config-json",
                &mounts,
                "--label",
                "openworker-native=1",
                "--env",
                "HOME=/tmp",
                "--cpu",
                "1",
                "--memory",
                "1Gi",
                "--",
                "/bin/sleep",
                "infinity",
            ],
            120,
        )
        .await?;
        for _ in 0..60 {
            let raw = command("openshell", &["sandbox", "get", name, "-o", "json"], 15).await?;
            let v: Value = serde_json::from_str(&raw)?;
            let v = v.get("sandbox").unwrap_or(&v);
            if v["phase"] == "Ready" {
                let id = v["id"]
                    .as_str()
                    .context("Sandbox identity missing")?
                    .to_owned();
                // Pinned release's first exec can hang; warm up without side effects.
                let _ = command(
                    "openshell",
                    &[
                        "sandbox",
                        "exec",
                        "--name",
                        name,
                        "--no-tty",
                        "--timeout",
                        "5",
                        "--",
                        "true",
                    ],
                    8,
                )
                .await;
                return Ok(id);
            }
            if v["phase"]
                .as_str()
                .is_some_and(|s| s.contains("Error") || s.contains("Completed"))
            {
                bail!("Sandbox did not start: {}", v["phase"]);
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        bail!("Sandbox readiness timed out")
    }
    pub async fn delete(&self, name: &str) -> Result<()> {
        if !name.starts_with("owr-") || !crate::bundle::safe_name(name) {
            bail!("Refusing deletion of an unowned sandbox name");
        }
        // A sandbox can already have been removed by shutdown or the gateway.
        // Verify absence from an authoritative list; never mask transport errors.
        let raw = command(
            "openshell",
            &[
                "sandbox",
                "list",
                "--selector",
                "openworker-native=1",
                "-o",
                "json",
            ],
            20,
        )
        .await?;
        let list: Value = serde_json::from_str(&raw)?;
        let rows = list.as_array().context("Unexpected sandbox listing")?;
        if !rows.iter().any(|v| v["name"] == name) {
            return Ok(());
        }
        command("openshell", &["sandbox", "delete", name], 30).await?;
        Ok(())
    }
}

// Minimal messages matching NVIDIA OpenShell 0.0.116 proto/openshell.proto.
// prost skips future unknown fields; the gateway version is pinned by preflight.
#[derive(Clone, PartialEq, prost::Message)]
pub struct ExecStart {
    #[prost(string, tag = "1")]
    pub sandbox_id: String,
    #[prost(string, repeated, tag = "2")]
    pub command: Vec<String>,
    #[prost(bool, tag = "7")]
    pub tty: bool,
    #[prost(bool, tag = "10")]
    pub no_login_shell: bool,
    #[prost(string, tag = "11")]
    pub request_id: String,
    #[prost(message, optional, tag = "12")]
    pub workspace_scope: Option<WorkspaceSelector>,
}
/// Named workspace variant from OpenShell v0.1.3 datamodel.proto.
#[derive(Clone, PartialEq, prost::Message)]
pub struct WorkspaceSelector {
    #[prost(string, tag = "1")]
    pub workspace: String,
}
/// Explicit transport configuration for the Kubernetes provider. Certificate
/// material stays outside persisted environment IDs and tool arguments.
#[derive(Clone)]
pub struct ExecConnectionConfig {
    pub endpoint: String,
    pub certificates: PathBuf,
    /// Explicit verified certificate name for a private proxy/port-forward.
    pub tls_server_name: Option<String>,
    pub workspace: String,
}
#[derive(Clone, PartialEq, prost::Message)]
pub struct ExecInput {
    #[prost(oneof = "input::Payload", tags = "1,2")]
    pub payload: Option<input::Payload>,
}
pub mod input {
    #[derive(Clone, PartialEq, prost::Oneof)]
    pub enum Payload {
        #[prost(message, tag = "1")]
        Start(super::ExecStart),
        #[prost(bytes, tag = "2")]
        Stdin(Vec<u8>),
    }
}
#[derive(Clone, PartialEq, prost::Message)]
pub struct Bytes {
    #[prost(bytes, tag = "1")]
    pub data: Vec<u8>,
}
#[derive(Clone, PartialEq, prost::Message)]
pub struct Exit {
    #[prost(int32, tag = "1")]
    pub code: i32,
}
#[derive(Clone, PartialEq, prost::Message)]
pub struct ExecEvent {
    #[prost(oneof = "event::Payload", tags = "1,2,3")]
    pub payload: Option<event::Payload>,
}
pub mod event {
    #[derive(Clone, PartialEq, prost::Oneof)]
    pub enum Payload {
        #[prost(message, tag = "1")]
        Stdout(super::Bytes),
        #[prost(message, tag = "2")]
        Stderr(super::Bytes),
        #[prost(message, tag = "3")]
        Exit(super::Exit),
    }
}

pub struct Mcp {
    tx: Option<mpsc::Sender<ExecInput>>,
    stream: tonic::Streaming<ExecEvent>,
    buffer: Vec<u8>,
    sequence: u64,
    usable: bool,
}
impl Mcp {
    pub async fn connect(sandbox_id: &str, command: Vec<String>) -> Result<Self> {
        let home = config_home()?;
        let active = std::fs::read_to_string(home.join("active_gateway"))?;
        let active = active.trim();
        if !crate::bundle::safe_name(active) {
            bail!("Invalid gateway name");
        }
        let base = home.join("gateways").join(active);
        let meta: Value = serde_json::from_slice(&std::fs::read(base.join("metadata.json"))?)?;
        let endpoint = meta["gateway_endpoint"]
            .as_str()
            .context("Gateway endpoint missing")?;
        Self::connect_request(
            endpoint,
            &base.join("mtls"),
            None,
            ExecStart {
                sandbox_id: sandbox_id.into(),
                command,
                tty: false,
                no_login_shell: false,
                request_id: String::new(),
                workspace_scope: None,
            },
        )
        .await
    }
    /// OpenShell 0.1.3 exec uses the sandbox name and an explicit workspace.
    /// Lifecycle/version qualification belongs to the provider. Never retries an
    /// uncertain launch or resumes an old stream automatically.
    pub async fn connect_v1(
        config: &ExecConnectionConfig,
        sandbox_name: &str,
        command: Vec<String>,
    ) -> Result<Self> {
        Self::connect_attachment(
            config,
            sandbox_name,
            command,
            &uuid::Uuid::new_v4().to_string(),
        )
        .await
    }
    /// Launch exactly this durable attachment ID. Repeating it does not reattach
    /// or replay a lost stream. Reconcile an unknown launch instead of retrying.
    pub async fn connect_attachment(
        config: &ExecConnectionConfig,
        sandbox_name: &str,
        command: Vec<String>,
        request_id: &str,
    ) -> Result<Self> {
        let id =
            uuid::Uuid::parse_str(request_id).context("Attachment launch ID must be a UUID")?;
        if id.is_nil() {
            bail!("Attachment launch ID cannot be nil");
        }
        if config.workspace.is_empty() || sandbox_name.is_empty() {
            bail!("Named OpenShell workspace and sandbox are required");
        }
        tokio::time::timeout(
            Duration::from_secs(30),
            Self::connect_request(
                &config.endpoint,
                &config.certificates,
                config.tls_server_name.as_deref(),
                ExecStart {
                    sandbox_id: sandbox_name.into(),
                    command,
                    tty: false,
                    no_login_shell: true,
                    request_id: id.to_string(),
                    workspace_scope: Some(WorkspaceSelector {
                        workspace: config.workspace.clone(),
                    }),
                },
            ),
        )
        .await
        .context("Attachment launch outcome unknown after timeout; do not relaunch")?
    }
    async fn connect_request(
        endpoint: &str,
        certs: &std::path::Path,
        server_name: Option<&str>,
        start: ExecStart,
    ) -> Result<Self> {
        let url = reqwest::Url::parse(endpoint)?;
        if url.scheme() != "https" || !url.username().is_empty() || url.password().is_some() {
            bail!("OpenShell exec requires an HTTPS endpoint without embedded credentials");
        }
        let mut tls = ClientTlsConfig::new()
            .ca_certificate(Certificate::from_pem(std::fs::read(certs.join("ca.crt"))?))
            .identity(Identity::from_pem(
                std::fs::read(certs.join("tls.crt"))?,
                std::fs::read(certs.join("tls.key"))?,
            ));
        if let Some(name) = server_name {
            tls = tls.domain_name(name);
        } else if matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]")) {
            tls = tls.domain_name("localhost");
        }
        let channel = Channel::from_shared(endpoint.to_string())?
            .tls_config(tls)?
            .connect_timeout(Duration::from_secs(10))
            .connect()
            .await?;
        let mut client = tonic::client::Grpc::new(channel);
        client.ready().await?;
        let (tx, rx) = mpsc::channel(16);
        tx.send(ExecInput {
            payload: Some(input::Payload::Start(start)),
        })
        .await?;
        let codec = tonic::codec::ProstCodec::<ExecInput, ExecEvent>::default();
        let path = tonic::codegen::http::uri::PathAndQuery::from_static(
            "/openshell.v1.OpenShell/ExecSandboxInteractive",
        );
        let response = client
            .streaming(
                tonic::Request::new(tokio_stream::wrappers::ReceiverStream::new(rx)),
                path,
                codec,
            )
            .await?;
        let mut m = Self {
            tx: Some(tx),
            stream: response.into_inner(),
            buffer: vec![],
            sequence: 0,
            usable: true,
        };
        let init=m.request("initialize",json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"openworker-native","version":"0.1.0"}})).await?;
        if !["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
            .contains(&init["protocolVersion"].as_str().unwrap_or(""))
        {
            bail!("Unsupported MCP protocol version");
        }
        m.send(json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}))
            .await?;
        Ok(m)
    }
    async fn send(&mut self, v: Value) -> Result<()> {
        let data = format!("{v}\n").into_bytes();
        if data.len() > crate::MAX_FRAME {
            bail!("MCP request exceeds frame limit");
        }
        self.tx
            .as_ref()
            .context("MCP stream is closed")?
            .send(ExecInput {
                payload: Some(input::Payload::Stdin(data)),
            })
            .await?;
        Ok(())
    }
    async fn line(&mut self) -> Result<Value> {
        loop {
            if let Some(n) = self.buffer.iter().position(|&c| c == b'\n') {
                let raw = self.buffer.drain(..=n).collect::<Vec<_>>();
                return Ok(serde_json::from_slice(&raw)?);
            }
            let e = self
                .stream
                .message()
                .await?
                .context("MCP exec stream disconnected")?;
            match e.payload {
                Some(event::Payload::Stdout(b)) => {
                    self.buffer.extend(b.data);
                    if self.buffer.len() > crate::MAX_FRAME {
                        bail!("MCP response exceeds frame limit");
                    }
                }
                Some(event::Payload::Exit(e)) => bail!("MCP process exited ({})", e.code),
                // Never forward untrusted stderr (it may contain tool inputs/secrets) into the protocol.
                _ => {}
            }
        }
    }
    pub async fn request(&mut self, method: &str, params: Value) -> Result<Value> {
        if !self.usable {
            bail!("MCP stream unusable; request not dispatched");
        }
        // Cancellation of this future also leaves it poisoned: late results must
        // never be correlated with a subsequent action on the same stream.
        self.usable = false;
        self.sequence += 1;
        let id = self.sequence;
        self.send(json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}))
            .await?;
        let result=tokio::time::timeout(Duration::from_secs(75),async {
            loop {let v=self.line().await?;
                if v.get("method").is_some() {if v.get("id").is_some(){self.send(json!({"jsonrpc":"2.0","id":v["id"],"error":{"code":-32601,"message":"Server-initiated requests are not permitted"}})).await?;}continue;}
                if v["id"]!=id {bail!("MCP response correlation mismatch");}
                if v.get("error").is_some(){bail!("MCP returned an error for {method}");}
                let result=v.get("result").context("MCP result missing")?;
                if serde_json::to_vec(result)?.len()>256000 {bail!("MCP result exceeds 256 KB; a called action may have executed. Do not automatically retry.");}
                return Ok(result.clone());
            }
        }).await.context("MCP request timed out")?;
        if result.is_ok() {
            self.usable = true;
        }
        result
    }
    /// Close only this exec stream's stdin; never stop/delete the sandbox.
    /// Missing exit evidence is an unknown outcome, not proof all effects stopped.
    pub async fn close(mut self) -> Result<()> {
        self.usable = false;
        self.tx.take();
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                match self.stream.message().await? {
                    Some(ExecEvent {
                        payload: Some(event::Payload::Exit(_)),
                    }) => return Ok(()),
                    Some(_) => {}
                    None => bail!("MCP stream ended without process exit evidence"),
                }
            }
        })
        .await
        .context("MCP close outcome unknown; remote work may continue")?
    }
}

pub async fn setup(apply: bool) -> Result<Value> {
    let current = doctor().await;
    if !apply {
        return Ok(
            json!({"status":current,"changes_require":"sandbox setup --apply","docker":"Must already be running; never installed by OpenWorker","install":"Pinned NVIDIA OpenShell installer; may require administrator interaction","configure":"Enable bind mounts and restart gateway only when there are no existing sandboxes","image":IMAGE}),
        );
    }
    if !cfg!(target_os = "linux") {
        bail!("Automatic setup is Linux-only. Existing developer-host gateways may be used for tests.");
    }
    command("docker", &["info"], 20)
        .await
        .context("Start Docker yourself before setup")?;
    install_if_missing().await?;
    let home = config_home()?;
    let path = home.join("gateway.toml");
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    if !existing.contains("enable_bind_mounts = true") {
        let list = command("openshell", &["sandbox", "list", "-o", "json"], 20).await?;
        let list: Value = serde_json::from_str(&list)?;
        if list.as_array().is_none_or(|a| !a.is_empty()) {
            bail!("Setup would restart a gateway with existing sandboxes. Refusing; schedule downtime first.");
        }
        if existing.contains("[openshell.drivers.docker]") {
            bail!("Existing Docker gateway configuration requires manual review; not overwriting");
        }
        let envpath = home.join("gateway.env");
        let env = std::fs::read_to_string(&envpath).unwrap_or_default();
        if env.contains("OPENSHELL_GATEWAY_CONFIG=") {
            bail!("Existing gateway config override requires manual review");
        }
        std::fs::create_dir_all(&home)?;
        std::fs::write(
            &path,
            format!("{existing}\n[openshell.drivers.docker]\nenable_bind_mounts = true\n"),
        )?;
        std::fs::write(
            envpath,
            format!("{env}\nOPENSHELL_GATEWAY_CONFIG={}\n", path.display()),
        )?;
        command("systemctl", &["--user", "restart", "openshell-gateway"], 30).await?;
    }
    if command("docker", &["image", "inspect", IMAGE], 20)
        .await
        .is_err()
    {
        let mut cmd = tokio::process::Command::new("docker");
        cmd.args(["pull", IMAGE])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::inherit())
            .stderr(std::process::Stdio::inherit())
            .kill_on_drop(true);
        if !cmd.status().await?.success() {
            bail!("Image download failed");
        }
    }
    Ok(doctor().await)
}

async fn install_if_missing() -> Result<()> {
    match command("openshell",&["--version"],15).await {
        Ok(v) if v.split_whitespace().last()==Some(VERSION)=>return Ok(()),
        Ok(_)=>bail!("A different OpenShell version is installed. Refusing an implicit upgrade/downgrade of a shared gateway; install 0.0.116 explicitly."),
        Err(_)=>{}
    }
    // Pin BOTH the installer source revision and its byte digest, as well as
    // OPENSHELL_VERSION. Do not pipe an unpinned main-branch script into a shell.
    const INSTALLER:&str="https://raw.githubusercontent.com/NVIDIA/OpenShell/d1155aa70042d3e2ee49dbfa15346b108b7c1d92/install.sh";
    const HASH: &str = "55863e314bb990733b8cc8fb369798921b4c8e791c9cdfa284e99d00648e80e7";
    let user = command("id", &["-un"], 10).await?;
    let user = user.trim();
    let linger = command(
        "loginctl",
        &["show-user", user, "--property=Linger", "--value"],
        10,
    )
    .await
    .unwrap_or_default();
    if linger.trim() != "yes" {
        eprintln!(
            "setup: enabling the current user's service persistence (linger) before installation"
        );
        if command("sudo", &["-n", "loginctl", "enable-linger", user], 15)
            .await
            .is_err()
        {
            bail!("Administrator action required before installation: sudo loginctl enable-linger {user}. Then rerun sandbox setup --apply. OpenWorker never requests or stores your password.");
        }
    }
    if unsafe { libc::geteuid() } != 0 && command("sudo", &["-n", "true"], 10).await.is_err() {
        bail!("OpenShell package installation needs administrator access. In your own terminal, authorize sudo, then rerun sandbox setup --apply. No password is handled by OpenWorker.");
    }
    eprintln!("setup: downloading and verifying the pinned OpenShell installer (native Linux package, no Python)");
    let response = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()?
        .get(INSTALLER)
        .send()
        .await?
        .error_for_status()?;
    let bytes = response.bytes().await?;
    if bytes.len() > 256000 || crate::bundle::digest(&bytes) != HASH {
        bail!("Installer checksum mismatch; refusing execution");
    }
    let cache = config_home()?.join("openworker-setup");
    std::fs::create_dir_all(&cache)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&cache, std::fs::Permissions::from_mode(0o700))?;
    }
    let script = cache.join(format!("install-{}.sh", crate::id("setup")));
    {
        use std::io::Write;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        options.open(&script)?.write_all(&bytes)?;
    }
    let mut install = tokio::process::Command::new("/bin/sh");
    install
        .arg(&script)
        .env("OPENSHELL_VERSION", format!("v{VERSION}"))
        .env_remove("OPENAI_API_KEY")
        .env_remove("ANTHROPIC_API_KEY")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::inherit())
        .stderr(std::process::Stdio::inherit())
        .kill_on_drop(true);
    let status = tokio::time::timeout(Duration::from_secs(600), install.status())
        .await
        .context("Installer timed out; inspect system package state before retrying")??;
    if !status.success() {
        bail!("Pinned OpenShell installer failed; see setup output");
    }
    let version = command("openshell", &["--version"], 15).await?;
    if version.split_whitespace().last() != Some(VERSION) {
        bail!("Installed version verification failed");
    }
    eprintln!("setup: OpenShell installation verified; continuing gateway/image checks");
    Ok(())
}

/// Explicit operator-invoked, model-free verification. All mutations are in a
/// newly created diagnostic workspace or this invocation's owned sandbox.
pub async fn verify(
    root: &std::path::Path,
    bundle_path: &std::path::Path,
    runner: &std::path::Path,
) -> Result<Value> {
    let run = root.join(crate::id("verify"));
    std::fs::create_dir_all(&run)?;
    let run = run.canonicalize()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&run, std::fs::Permissions::from_mode(0o700))?;
    }
    let workspace = run.join("workspace");
    std::fs::create_dir(&workspace)?;
    let outside = run.join("outside.txt");
    std::fs::write(&outside, "diagnostic marker, not a real secret")?;
    let sandbox = Sandbox {
        root: run.clone(),
        runner: runner.canonicalize()?,
    };
    let bundle = Bundle::load(bundle_path)?;
    let name = format!("owr-{}", &uuid::Uuid::new_v4().simple().to_string()[..12]);
    let result=async {
        let sid=sandbox.create(&name,workspace.to_str().context("UTF-8 workspace")?,&bundle).await?;
        let mut m=Mcp::connect(&sid,vec!["/opt/openworker/openworker".into(),"mcp-example".into(),"--workspace".into(),workspace.to_str().unwrap().into()]).await?;
        let host_network=reqwest::Client::builder().timeout(Duration::from_secs(10)).build()?.get("https://example.com").send().await?.status().is_success();
        if !host_network{bail!("Control-network probe failed; cannot establish a meaningful sandbox network comparison");}
        let outside=outside.to_string_lossy().replace('\'',"'\\''");
        let command=format!("set -eu; printf 'native runner\\n' > probe.txt; test ! -e '{outside}'; test ! -S /var/run/docker.sock; test -z \"${{OPENAI_API_KEY:-}}\"; if touch /opt/openworker/forbidden-write 2>/dev/null; then exit 41; fi; if curl -fsS --connect-timeout 3 --max-time 5 https://example.com -o /dev/null 2>/dev/null; then exit 42; fi; printf 'ISOLATION_PROBES_PASSED\\n'");
        let response=m.request("tools/call",json!({"name":"run_shell","arguments":{"command":command}})).await?;
        let data:Value=serde_json::from_str(response["content"][0]["text"].as_str().context("Probe output missing")?)?;
        if response["isError"]==true||data["exit_code"]!=0||!data["stdout"].as_str().unwrap_or("").contains("ISOLATION_PROBES_PASSED"){bail!("Sandbox isolation verification failed: {data}");}
        if std::fs::read_to_string(workspace.join("probe.txt"))?!="native runner\n"{bail!("Workspace write did not reach the granted host directory");}
        Ok(json!({"passed":true,"provider":"openshell","runner":"native-rust","workspace":workspace,"checks":["workspace write-through","outside marker inaccessible","Docker socket absent","model key absent","runner mount read-only","host network works but sandbox network denied"]}))
    }.await;
    let cleanup = sandbox.delete(&name).await;
    match (result, cleanup) {
        (Ok(v), Ok(())) => Ok(v),
        (Err(e), _) => Err(e),
        (Ok(_), Err(e)) => Err(e.context("Verification passed but sandbox cleanup failed")),
    }
}

#[cfg(test)]
mod wire_tests {
    use super::*;
    use prost::Message;
    #[test]
    fn v1_exec_has_explicit_named_workspace_and_no_login_shell() {
        // Golden wire fields from NVIDIA OpenShell v0.1.3 proto definitions.
        let start = ExecStart {
            sandbox_id: "poc".into(),
            command: vec!["cat".into()],
            tty: false,
            no_login_shell: true,
            request_id: String::new(),
            workspace_scope: Some(WorkspaceSelector {
                workspace: "default".into(),
            }),
        };
        assert_eq!(
            start.encode_to_vec(),
            b"\x0a\x03poc\x12\x03cat\x50\x01\x62\x09\x0a\x07default"
        );
        let mut launch = start;
        launch.request_id = "00000000-0000-0000-0000-000000000001".into();
        let bytes = launch.encode_to_vec();
        assert!(bytes.windows(2).any(|w| w == [0x5a, 36]));
        assert_eq!(
            ExecStart::decode(bytes.as_slice()).unwrap().request_id,
            launch.request_id
        );
    }
}
