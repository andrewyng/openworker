//! Trusted app policy, separate from tool classification and approval mode.
//! Secrets are never serialized here. Sandbox grants are shared by ALL its processes.
use crate::{
    bundle::{digest, read_limited, safe_name, Bundle},
    execution::Backend,
};
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Component, Path, PathBuf},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Placement {
    Host,
    Sandbox,
}
/// Remote files/processes stay in the environment. Explicit compiled callbacks
/// execute in the trusted harness, never implicitly ship into that environment.
/// A remote workspace in ToolContext is logical, not a path on the worker host.
pub fn remote_placements(
    bundle: &Bundle,
    local_policy: bool,
) -> Result<BTreeMap<String, Placement>> {
    anyhow::ensure!(
        !local_policy,
        "Remote profiles cannot use local resource grants"
    );
    bundle.tools.servers.iter().map(|(id, server)| {
        let placement = match server.program.as_str() {
            "$OPENWORKER" => Placement::Sandbox,
            "$NATIVE" if bundle.registry_digest.is_some()
                && bundle.tools.bindings.values().filter(|b| b.server == *id)
                    .all(|b| bundle.registry.native(&b.tool).is_some()) => Placement::Host,
            _ => bail!("Remote profile requires image-bundled OpenWorker tools or explicitly registered native callbacks"),
        };
        Ok((id.clone(), placement))
    }).collect()
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Server {
    pub placement: Placement,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Access {
    Read,
    ReadWrite,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileGrant {
    pub path: PathBuf,
    pub access: Access,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NetworkGrant {
    pub host: String,
    pub port: u16,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CredentialGrant {
    pub version: String,
    pub source_file: PathBuf,
    pub delivery: String,
    pub target: String,
    #[serde(default)]
    pub requires_network: Vec<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Profile {
    sandbox: Backend,
    workspace_roots: Vec<PathBuf>,
    #[serde(deserialize_with = "unique_map")]
    servers: BTreeMap<String, Server>,
    #[serde(default, deserialize_with = "unique_map")]
    files: BTreeMap<String, FileGrant>,
    #[serde(default, deserialize_with = "unique_map")]
    network: BTreeMap<String, NetworkGrant>,
    #[serde(default, deserialize_with = "unique_map")]
    credentials: BTreeMap<String, CredentialGrant>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FilePolicy {
    version: u32,
    bundle_digest: String,
    #[serde(deserialize_with = "unique_map")]
    profiles: BTreeMap<String, Profile>,
}
// serde's ordinary map accepts duplicate keys. Authority maps must not.
fn unique_map<'de, D, T>(d: D) -> std::result::Result<BTreeMap<String, T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct Visitor<T>(std::marker::PhantomData<T>);
    impl<'de, T: Deserialize<'de>> serde::de::Visitor<'de> for Visitor<T> {
        type Value = BTreeMap<String, T>;
        fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
            f.write_str("a map with unique grant names")
        }
        fn visit_map<M: serde::de::MapAccess<'de>>(
            self,
            mut m: M,
        ) -> std::result::Result<Self::Value, M::Error> {
            let mut out = BTreeMap::new();
            while let Some((k, v)) = m.next_entry::<String, T>()? {
                if out.insert(k, v).is_some() {
                    return Err(serde::de::Error::custom("Duplicate policy key"));
                }
            }
            Ok(out)
        }
    }
    d.deserialize_map(Visitor(std::marker::PhantomData))
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub profile: String,
    #[serde(default)]
    pub files: Vec<String>,
    #[serde(default)]
    pub network: Vec<String>,
    #[serde(default)]
    pub credentials: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Stamp {
    dev: u64,
    ino: u64,
    // Credential change detection only, not a public secret hash.
    #[serde(skip_serializing_if = "Option::is_none")]
    change: Option<(u64, i64, i64, i64, i64)>,
}
impl Stamp {
    fn read(path: &Path, secret: bool) -> Result<Self> {
        #[cfg(unix)]
        {
            Ok(Self::from(&fs::metadata(path)?, secret))
        }
        #[cfg(windows)]
        {
            let (dev, ino, len, created, written) = crate::platform::windows::file_identity(path)?;
            Ok(Self {
                dev,
                ino,
                change: secret.then_some((len, created as i64, 0, written as i64, 0)),
            })
        }
    }
    #[cfg(unix)]
    fn from(m: &fs::Metadata, secret: bool) -> Self {
        use std::os::unix::fs::MetadataExt;
        Self {
            dev: m.dev(),
            ino: m.ino(),
            change: secret.then(|| {
                (
                    m.len(),
                    m.mtime(),
                    m.mtime_nsec(),
                    m.ctime(),
                    m.ctime_nsec(),
                )
            }),
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResolvedFile {
    pub grant: FileGrant,
    pub identity: Stamp,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResolvedCredential {
    pub grant: CredentialGrant,
    pub identity: Stamp,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Resolved {
    pub digest: String,
    pub policy_digest: String,
    pub selection: Option<Selection>,
    pub workspace: PathBuf,
    pub workspace_identity: Option<Stamp>,
    pub servers: BTreeMap<String, Placement>,
    /// Set only by trusted bundle preparation after exact registry selection.
    /// Runtime reload revalidates the compiled descriptors before execution.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub remote_native_registry: Option<String>,
    pub files: BTreeMap<String, ResolvedFile>,
    pub network: BTreeMap<String, NetworkGrant>,
    pub credentials: BTreeMap<String, ResolvedCredential>,
    pub warnings: Vec<String>,
}
impl Resolved {
    pub fn remote_digest(&self, binding: &crate::environment::Binding) -> Result<String> {
        let fingerprint = binding.fingerprint()?;
        match &self.remote_native_registry {
            Some(registry) => {
                anyhow::ensure!(
                    registry.len() == 64 && registry.bytes().all(|b| b.is_ascii_hexdigit()),
                    "Invalid native registry identity"
                );
                Ok(digest(&serde_json::to_vec(
                    &serde_json::json!({"environment":fingerprint,"registry":registry,"servers":self.servers}),
                )?))
            }
            None => {
                anyhow::ensure!(
                    self.servers.values().all(|p| *p == Placement::Sandbox),
                    "Remote host placement needs trusted registry selection"
                );
                Ok(fingerprint)
            }
        }
    }
    pub fn verify_paths(&self) -> Result<()> {
        if let Some(expected) = &self.workspace_identity {
            if &{
                checked_path(&self.workspace)?;
                Stamp::read(&self.workspace, false)?
            } != expected
            {
                bail!("Workspace identity changed");
            }
        }
        for f in self.files.values() {
            if {
                checked_path(&f.grant.path)?;
                Stamp::read(&f.grant.path, false)?
            } != f.identity
            {
                bail!("File grant identity changed");
            }
        }
        for c in self.credentials.values() {
            if {
                checked_path(&c.grant.source_file)?;
                Stamp::read(&c.grant.source_file, true)?
            } != c.identity
            {
                bail!("Credential identity changed");
            }
        }
        Ok(())
    }
    pub fn context(&self) -> serde_json::Value {
        serde_json::json!({"workspace":self.workspace,"servers":self.servers,
            "files":self.files.iter().map(|(id,f)|(id,&f.grant)).collect::<BTreeMap<_,_>>(),
            "network":self.network,
            "credentials":self.credentials.iter().map(|(id,c)|serde_json::json!({"id":id,"version":c.grant.version,"path":c.grant.target,"scope":"shared-session-sandbox"})).collect::<Vec<_>>(),
            "warnings":self.warnings})
    }
    pub fn backend(&self, server: &str) -> Backend {
        if self.servers.get(server) == Some(&Placement::Sandbox) {
            Backend::Openshell
        } else {
            Backend::Off
        }
    }
    pub fn bounded_lifecycle(&self) -> bool {
        self.selection.is_some()
    }
    pub fn needs_sandbox(&self) -> bool {
        self.servers.values().any(|p| *p == Placement::Sandbox)
    }
}
pub struct Policy {
    pub digest: String,
    path: Option<PathBuf>,
    profiles: BTreeMap<String, Profile>,
}
pub fn overlap(a: &Path, b: &Path) -> bool {
    a.starts_with(b) || b.starts_with(a)
}
fn absolute_path(p: &Path) -> Result<()> {
    if !p.is_absolute()
        || p.components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
    {
        bail!("Policy paths must be absolute, normalized paths");
    }
    Ok(())
}
pub fn checked_path(p: &Path) -> Result<fs::Metadata> {
    absolute_path(p)?;
    crate::platform::validate_path(p)?;
    let mut current = PathBuf::new();
    for c in p.components() {
        current.push(c);
        #[cfg(windows)]
        if matches!(c, Component::Prefix(_)) {
            continue;
        }
        if crate::platform::linked(&fs::symlink_metadata(&current)?) {
            bail!("Symlinks are not allowed in policy paths");
        }
    }
    if !cfg!(windows) && p.canonicalize()? != p {
        bail!("Noncanonical policy path");
    }
    let m = fs::metadata(p)?;
    if !m.is_dir() && !m.is_file() {
        bail!("Special files cannot be granted");
    }
    Ok(m)
}
fn safe_mount(p: &Path) -> Result<()> {
    if p.parent().is_none() {
        bail!("Cannot grant filesystem root");
    }
    for x in [
        "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/dev", "/sys", "/run", "/opt",
        "/sandbox", "/var/log",
    ] {
        if overlap(p, Path::new(x)) {
            bail!("Grant overlaps a reserved sandbox path");
        }
    }
    // /tmp is already RW in the image; a nested bind is OK, the entire root is not.
    if p == Path::new("/tmp") || p == Path::new("/var") || p == Path::new("/home") {
        bail!("Grant is too broad");
    }
    Ok(())
}
fn names<'a, T>(
    requested: &'a [String],
    available: &BTreeMap<String, T>,
) -> Result<BTreeSet<&'a String>> {
    let selected: BTreeSet<_> = requested.iter().collect();
    if selected.len() != requested.len()
        || requested.len() > 32
        || selected.iter().any(|id| !available.contains_key(*id))
    {
        bail!("Duplicate, unknown or excessive resource grants");
    }
    Ok(selected)
}
impl Policy {
    pub fn configured(&self) -> bool {
        self.path.is_some()
    }
    pub fn load(path: Option<&Path>, bundle: &Bundle) -> Result<Self> {
        let Some(path) = path else {
            return Ok(Self {
                digest: digest(b"no-resource-policy-v1"),
                path: None,
                profiles: BTreeMap::new(),
            });
        };
        checked_path(path)?;
        #[cfg(windows)]
        let canonical_path = path.canonicalize()?;
        #[cfg(windows)]
        let path = canonical_path.as_path();
        if path.starts_with(&bundle.root) {
            bail!("Runtime policy must be outside coworker bundle");
        }
        let bytes = read_limited(path, 128000)?;
        #[allow(unused_mut)]
        let mut file: FilePolicy = serde_json::from_slice(&bytes)?;
        #[cfg(windows)]
        for profile in file.profiles.values_mut() {
            if profile.sandbox != Backend::Off {
                bail!("Windows profiles require explicit host execution");
            }
            for root in &mut profile.workspace_roots {
                checked_path(root)?;
                *root = root.canonicalize()?;
            }
        }
        if file.version != 1
            || file.bundle_digest != bundle.digest
            || file.profiles.is_empty()
            || file.profiles.len() > 16
        {
            bail!("Invalid runtime policy version, bundle pin or profile count");
        }
        for (name, p) in &file.profiles {
            if !safe_name(name)
                || p.workspace_roots.is_empty()
                || p.workspace_roots.len() > 16
                || p.servers.keys().ne(bundle.tools.servers.keys())
            {
                bail!("Profile must name exact bundled servers and allowed workspace roots");
            }
            for root in &p.workspace_roots {
                absolute_path(root)?;
                safe_mount(root)?;
            }
            for (id, server) in &bundle.tools.servers {
                if server.program == "$NATIVE" && p.servers[id].placement != Placement::Host {
                    bail!("In-process native tool providers have host placement, not sandbox isolation");
                }
                if (server.program == "$HTTP" || Path::new(&server.program).is_absolute())
                    && p.servers[id].placement != Placement::Host
                {
                    bail!("External connections/executables require host placement; sandbox grants do not constrain the external service");
                }
            }
            for f in p.files.values() {
                absolute_path(&f.path)?;
            }
            for map in [
                p.files.keys().collect::<Vec<_>>(),
                p.network.keys().collect(),
                p.credentials.keys().collect(),
            ] {
                if map.len() > 32 || map.iter().any(|id| !safe_name(id)) {
                    bail!("Invalid grant IDs/count");
                }
            }
            for n in p.network.values() {
                // Exact ASCII DNS names or IPv4 literals only; no schemes, wildcards,
                // userinfo, implicit ports, IPv6 or OpenShell's port-0 wildcard.
                if n.port == 0
                    || n.host.len() > 253
                    || n.host.split('.').count() < 2
                    || n.host.split('.').any(|s| {
                        s.is_empty()
                            || s.len() > 63
                            || s.starts_with('-')
                            || s.ends_with('-')
                            || !s
                                .bytes()
                                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
                    })
                {
                    bail!("Network grant requires exact lowercase host and nonzero port");
                }
            }
            for (id, c) in &p.credentials {
                if c.delivery != "sandbox_file"
                    || c.target != format!("/run/openworker/credentials/{id}")
                    || !safe_name(&c.version)
                {
                    bail!("Credential requires version and fixed sandbox_file destination");
                }
                names(&c.requires_network, &p.network)?;
                // Existence/identity are checked on selection, not runtime startup:
                // removing a source must not prevent cleanup of prior copies.
                absolute_path(&c.source_file)?;
                if overlap(&c.source_file, &bundle.root) || c.source_file == path {
                    bail!("Credential source overlaps installed package or runtime policy");
                }
            }
            if p.sandbox == Backend::Off
                && (p
                    .servers
                    .values()
                    .any(|s| s.placement == Placement::Sandbox)
                    || !p.files.is_empty()
                    || !p.network.is_empty()
                    || !p.credentials.is_empty())
            {
                bail!("Off profiles cannot claim enforced sandbox grants");
            }
        }
        Ok(Self {
            digest: digest(&bytes),
            path: Some(path.into()),
            profiles: file.profiles,
        })
    }
    pub fn verify(&self) -> Result<()> {
        if let Some(p) = &self.path {
            checked_path(p)?;
            if digest(&read_limited(p, 128000)?) != self.digest {
                bail!("Runtime policy changed; use a new runtime/session");
            }
        }
        Ok(())
    }
    pub fn resolve(
        &self,
        bundle: &Bundle,
        backend: Backend,
        workspace: &Path,
        selection: Option<Selection>,
        protected: &[PathBuf],
    ) -> Result<Resolved> {
        self.verify()?;
        let mut result = Resolved {
            policy_digest: self.digest.clone(),
            workspace: workspace.into(),
            ..Default::default()
        };
        let mut forbidden = protected.to_vec();
        if let Some(path) = &self.path {
            forbidden.push(path.clone());
        }
        // ALL configured sources are protected, including unselected grants and profiles.
        forbidden.extend(
            self.profiles
                .values()
                .flat_map(|p| p.credentials.values().map(|c| c.source_file.clone())),
        );
        if forbidden.iter().any(|p| overlap(workspace, p)) {
            bail!("Workspace overlaps protected policy/runtime/credential paths");
        }
        if let Some(mut selection) = selection {
            let p = self
                .profiles
                .get(&selection.profile)
                .context("Unknown runtime policy profile")?;
            if p.sandbox != backend {
                bail!("Profile requires a different sandbox backend; no fallback");
            }
            safe_mount(workspace)?;
            let m = checked_path(workspace)?;
            if !m.is_dir()
                || !p
                    .workspace_roots
                    .iter()
                    .any(|r| workspace.starts_with(r) && workspace != r)
            {
                bail!("Workspace must be a child of an authorized root");
            }
            for root in &p.workspace_roots {
                if !checked_path(root)?.is_dir() {
                    bail!("Workspace root must be a directory");
                }
            }
            result.workspace_identity = Some(Stamp::read(workspace, false)?);
            result.servers = p
                .servers
                .iter()
                .map(|(k, v)| (k.clone(), v.placement))
                .collect();
            let mut mounted = vec![workspace.to_path_buf()];
            for id in names(&selection.files, &p.files)? {
                let f = &p.files[id];
                safe_mount(&f.path)?;
                if forbidden
                    .iter()
                    .chain(mounted.iter())
                    .any(|x| overlap(&f.path, x))
                {
                    bail!("File grant overlaps protected or another mounted path");
                }
                checked_path(&f.path)?;
                mounted.push(f.path.clone());
                result.files.insert(
                    id.clone(),
                    ResolvedFile {
                        grant: f.clone(),
                        identity: Stamp::read(&f.path, false)?,
                    },
                );
            }
            for id in names(&selection.network, &p.network)? {
                result.network.insert(id.clone(), p.network[id].clone());
            }
            for id in names(&selection.credentials, &p.credentials)? {
                let c = &p.credentials[id];
                if c.requires_network
                    .iter()
                    .any(|id| !result.network.contains_key(id))
                {
                    bail!("Credential requires explicitly selected network grants");
                }
                if protected
                    .iter()
                    .chain(std::iter::once(&bundle.root))
                    .any(|x| overlap(&c.source_file, x))
                {
                    bail!("Credential source overlaps protected runtime paths");
                }
                let m = checked_path(&c.source_file)?;
                if !m.is_file() || m.len() > 65536 {
                    bail!("Invalid credential source");
                }
                result.credentials.insert(
                    id.clone(),
                    ResolvedCredential {
                        grant: c.clone(),
                        identity: Stamp::read(&c.source_file, true)?,
                    },
                );
            }
            if !result.needs_sandbox()
                && (!result.files.is_empty()
                    || !result.network.is_empty()
                    || !result.credentials.is_empty())
            {
                bail!("Sandbox grants require a sandbox server");
            }
            selection.files.sort();
            selection.network.sort();
            selection.credentials.sort();
            result.selection = Some(selection);
        } else {
            if self.path.is_some() {
                bail!("Configured runtime policy requires explicit profile selection");
            }
            result.servers = bundle
                .tools
                .servers
                .keys()
                .map(|id| {
                    (
                        id.clone(),
                        if backend == Backend::Off
                            || matches!(
                                bundle.tools.servers[id].program.as_str(),
                                "$NATIVE" | "$HTTP"
                            )
                            || Path::new(&bundle.tools.servers[id].program).is_absolute()
                        {
                            Placement::Host
                        } else {
                            Placement::Sandbox
                        },
                    )
                })
                .collect();
        }
        if result
            .servers
            .iter()
            .any(|(id, p)| *p == Placement::Host && bundle.tools.servers[id].program != "$NATIVE")
        {
            result.warnings.push("Host MCP processes have host-account access; sandbox file/network grants do not constrain them. Trust the installed executable independently of tool approval.".into());
        }
        if bundle
            .tools
            .servers
            .values()
            .any(|s| s.program == "$NATIVE")
        {
            result.warnings.push("Native crate tools are trusted in-process host code. Sandbox grants do not constrain them; cancellation requires cooperative, nonblocking implementations.".into());
        }
        if result.needs_sandbox() {
            result.warnings.push("File/network grants are shared by every process in the session sandbox; destination rules do not constrain payloads or account privileges.".into());
        }
        if !result.credentials.is_empty() {
            result.warnings.push("Selected raw credentials are readable by processes in this session sandbox and can be returned or leaked by tools. No per-server secret isolation.".into());
        }
        result.digest = digest(&serde_json::to_vec(&result)?);
        Ok(result)
    }
}

/// Open a selected source without following a replaced leaf and verify metadata
/// before and after bounded reading. Parent directories/policy are operator-owned;
/// this does not defend against a malicious process with the same host UID.
#[cfg(windows)]
pub fn read_secret(_: &ResolvedCredential) -> Result<Vec<u8>> {
    bail!("Sandbox credential materialization is unavailable on Windows");
}

#[cfg(unix)]
pub fn read_secret(c: &ResolvedCredential) -> Result<Vec<u8>> {
    use std::{io::Read, os::unix::fs::OpenOptionsExt};
    checked_path(&c.grant.source_file)?;
    let mut f = fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(&c.grant.source_file)?;
    if Stamp::from(&f.metadata()?, true) != c.identity {
        bail!("Credential changed; create a new session");
    }
    let mut bytes = vec![];
    (&mut f).take(65537).read_to_end(&mut bytes)?;
    if bytes.len() > 65536 || Stamp::from(&f.metadata()?, true) != c.identity {
        bail!("Credential changed during materialization");
    }
    Ok(bytes)
}
