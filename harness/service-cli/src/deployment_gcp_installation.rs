//! Installation receipts are private and append-only. No blind upload replay.
use super::{control::*, engine::Cloud, *};

#[derive(Subcommand)]
pub enum Command {
    /// Create a private installation journal from an unstarted lifecycle binding.
    Prepare {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        guard_image: String,
        #[arg(long)]
        out: PathBuf,
    },
    /// Observe the resolved topology and control-upload prerequisites; no writes.
    Preflight {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Upload/reconcile source, control and initial checkpoint only. No IAM/compute creation.
    Upload {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        allow_experimental_cloud: bool,
        /// Exact installation digest printed by prepare, not a wildcard/project name.
        #[arg(long)]
        accept: String,
    },
    /// Read back upload outcomes and save local receipts without any cloud write.
    Reconcile {
        #[arg(long)]
        state: PathBuf,
    },
    /// Export the cleanup graph using actual upload receipts, not synthetic generations.
    Package {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Read existing source, job, scheduler and independent runner heartbeat.
    Verify {
        #[arg(long)]
        state: PathBuf,
    },
    /// Local receipts only; never obtains credentials or contacts Google.
    Status {
        #[arg(long)]
        state: PathBuf,
    },
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Installation {
    schema_version: u16,
    plan_path: PathBuf,
    /// Pre-upload scope; generation 1 is never written into an actual control object.
    scope: Control,
}
impl Installation {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.schema_version == 1 && self.scope.source_generation == "1",
            "Invalid installation schema"
        );
        self.scope.validate()?;
        let (p, m) = inspect(&self.plan_path)?;
        ensure!(
            m.bundle_digest == self.scope.bundle_digest
                && json_bytes(&p)? == json_bytes(&self.scope.plan)?,
            "Installation plan drift"
        );
        Ok(())
    }
    fn digest(&self) -> Result<String> {
        Ok(hash(&json_bytes(self)?))
    }
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Intent {
    installation_digest: String,
    name: String,
    sha256: String,
    content_type: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Receipt {
    installation_digest: String,
    name: String,
    sha256: String,
    generation: String,
}

pub(super) trait ObjectStore {
    async fn get(&mut self, name: &str) -> Result<Option<(String, Vec<u8>)>>;
    async fn create(&mut self, name: &str, bytes: &[u8], content_type: &str) -> Result<String>;
}
impl ObjectStore for super::api::Google {
    async fn get(&mut self, name: &str) -> Result<Option<(String, Vec<u8>)>> {
        self.object(name, None, 1024 * 1024).await
    }
    async fn create(&mut self, name: &str, bytes: &[u8], content_type: &str) -> Result<String> {
        self.initial_object(name, bytes, content_type).await
    }
}
fn private_dir(path: &Path) -> Result<()> {
    let mut d = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        d.mode(0o700);
    }
    d.create(path).context("Use a fresh output directory")?;
    Ok(())
}
fn outside(out: &Path, root: &Path) -> Result<()> {
    let parent = out
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    ensure!(
        !parent.starts_with(root.canonicalize()?),
        "Output must be outside immutable input/state"
    );
    Ok(())
}
fn put(root: &Path, name: &str, b: &[u8]) -> Result<()> {
    let mut o = fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(root.join(name))?;
    f.write_all(b)?;
    f.sync_all()?;
    #[cfg(unix)]
    fs::File::open(root)?.sync_all()?;
    Ok(())
}
fn optional(root: &Path, name: &str) -> Result<Option<Vec<u8>>> {
    match fs::symlink_metadata(root.join(name)) {
        Ok(_) => Ok(Some(read_file(root, name)?)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.into()),
    }
}
fn load(root: &Path) -> Result<(Installation, fs::File)> {
    let meta = fs::symlink_metadata(root)?;
    ensure!(
        meta.is_dir() && !meta.file_type().is_symlink(),
        "Invalid installation directory"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        ensure!(
            meta.permissions().mode() & 0o077 == 0,
            "Installation state must be private"
        );
    }
    if let Ok(m) = fs::symlink_metadata(root.join("installation.lock")) {
        ensure!(
            m.is_file() && !m.file_type().is_symlink(),
            "Invalid installation lock"
        );
    }
    let mut o = fs::OpenOptions::new();
    o.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let lock = o.open(root.join("installation.lock"))?;
    lock.try_lock()
        .context("Installation locked by another process")?;
    let i: Installation = serde_json::from_slice(&read_file(root, "installation.json")?)?;
    i.validate()?;
    Ok((i, lock))
}
fn fresh(c: &Control, at: u64) -> Result<()> {
    ensure!(
        c.plan.created_at_unix <= at && at < c.plan.expires_at_unix,
        "Installation plan expired or future-dated; observation/reconciliation is still allowed"
    );
    Ok(())
}

// Persist intent BEFORE the effect. A missing object after a prior intent is an
// unknown outcome, not permission to repeat the upload. The caller/operator can
// inspect the original action. GCS generation 0 additionally forbids replacement.
struct ObjectSpec<'a> {
    key: &'a str,
    name: &'a str,
    bytes: &'a [u8],
    mime: &'a str,
    checkpoint: Option<&'a Control>,
    create_until: Option<u64>,
}
async fn install_one<S: ObjectStore>(
    api: &mut S,
    root: &Path,
    owner: &str,
    spec: ObjectSpec<'_>,
) -> Result<Receipt> {
    let ObjectSpec {
        key,
        name,
        bytes,
        mime,
        checkpoint,
        create_until,
    } = spec;
    ensure!(bytes.len() <= 1024 * 1024, "Installation object too large");
    let intent = Intent {
        installation_digest: owner.into(),
        name: name.into(),
        sha256: hash(bytes),
        content_type: mime.into(),
    };
    let intent_file = format!("{key}-intent.json");
    let receipt_file = format!("{key}-receipt.json");
    let prior = optional(root, &intent_file)?;
    if let Some(ref b) = prior {
        ensure!(
            *b == json_bytes(&intent)?,
            "Installation upload intent changed"
        );
    }
    let saved = optional(root, &receipt_file)?
        .map(|b| serde_json::from_slice::<Receipt>(&b))
        .transpose()?;
    if let Some(ref r) = saved {
        ensure!(
            prior.is_some()
                && r.installation_digest == owner
                && r.name == name
                && r.sha256 == intent.sha256
                && decimal(&r.generation),
            "Invalid upload receipt"
        );
    }
    let found = api.get(name).await?;
    let (generation, observed) = match found {
        Some(v) => v,
        None => {
            ensure!(
                prior.is_none() && saved.is_none(),
                "Upload outcome unknown; object absent after original intent. No replay attempted"
            );
            ensure!(
                create_until.is_some_and(|t| now().is_ok_and(|at| at < t)),
                "Read-only reconciliation or expired plan cannot dispatch an upload"
            );
            put(root, &intent_file, &json_bytes(&intent)?)?;
            let ack = api.create(name, bytes, mime).await?;
            ensure!(decimal(&ack), "Invalid upload acknowledgement generation");
            let found = api
                .get(name)
                .await?
                .context("Upload acknowledgement has no readable object")?;
            ensure!(
                found.0 == ack,
                "Upload generation changed before verification"
            );
            found
        }
    };
    ensure!(decimal(&generation), "Invalid observed generation");
    let exact = observed == bytes;
    if let Some(c) = checkpoint {
        let j: Checkpoint = serde_json::from_slice(&observed)?;
        j.validate(c)?;
        // A scheduled cleanup runner may have advanced this object already.
        // Preserve it exactly; installing/restarting must never reset the state.
    } else {
        ensure!(exact, "Existing immutable object has conflicting bytes");
        if let Some(r) = &saved {
            ensure!(
                r.generation == generation,
                "Immutable object generation changed"
            );
        }
    }
    if prior.is_none() && optional(root, &intent_file)?.is_none() {
        put(root, &intent_file, &json_bytes(&intent)?)?;
    }
    if let Some(r) = saved {
        return Ok(r);
    }
    let r = Receipt {
        installation_digest: owner.into(),
        name: name.into(),
        sha256: intent.sha256,
        generation,
    };
    put(root, &receipt_file, &json_bytes(&r)?)?;
    Ok(r)
}
fn actual_control(root: &Path, i: &Installation) -> Result<Control> {
    let r: Receipt = serde_json::from_slice(&read_file(root, "source-receipt.json")?)?;
    ensure!(
        r.installation_digest == i.digest()?
            && r.name == i.scope.source_object()
            && r.sha256 == i.scope.source_sha256
            && decimal(&r.generation),
        "Source upload receipt does not match installation"
    );
    let mut c = i.scope.clone();
    c.source_generation = r.generation;
    c.validate()?;
    Ok(c)
}
fn payload(root: &Path, name: &str, expected: Option<Vec<u8>>, c: &Control) -> Result<Vec<u8>> {
    if let Some(b) = optional(root, name)? {
        if let Some(e) = expected {
            ensure!(e == b, "Stored control payload changed");
        } else {
            let j: Checkpoint = serde_json::from_slice(&b)?;
            j.validate(c)?;
        }
        return Ok(b);
    }
    if expected.is_none() {
        ensure!(
            optional(root, "checkpoint-intent.json")?.is_none(),
            "Original initial-checkpoint payload missing; never regenerate a dispatched checkpoint"
        );
    }
    let b = match expected {
        Some(b) => b,
        None => json_bytes(&Checkpoint::new(c)?)?,
    };
    put(root, name, &b)?;
    Ok(b)
}
async fn upload<S: ObjectStore>(
    api: &mut S,
    root: &Path,
    i: &Installation,
    mutate: bool,
) -> Result<Control> {
    let id = i.digest()?;
    let create_until = mutate.then_some(i.scope.plan.expires_at_unix);
    let bytes = source_zip(&i.scope.plan)?;
    install_one(
        api,
        root,
        &id,
        ObjectSpec {
            key: "source",
            name: &i.scope.source_object(),
            bytes: &bytes,
            mime: "application/zip",
            checkpoint: None,
            create_until,
        },
    )
    .await?;
    let c = actual_control(root, i)?;
    let b = payload(root, "control.json", Some(json_bytes(&c)?), &c)?;
    install_one(
        api,
        root,
        &id,
        ObjectSpec {
            key: "control",
            name: &c.control_object()?,
            bytes: &b,
            mime: "application/json",
            checkpoint: None,
            create_until,
        },
    )
    .await?;
    let b = payload(root, "initial-checkpoint.json", None, &c)?;
    install_one(
        api,
        root,
        &id,
        ObjectSpec {
            key: "checkpoint",
            name: &c.checkpoint_object(),
            bytes: &b,
            mime: "application/json",
            checkpoint: Some(&c),
            create_until,
        },
    )
    .await?;
    Ok(c)
}
fn status(root: &Path, i: &Installation) -> Result<Value> {
    let mut receipts = BTreeMap::new();
    for k in ["source", "control", "checkpoint"] {
        let b = optional(root, &format!("{k}-receipt.json"))?;
        receipts.insert(
            k,
            b.map(|b| serde_json::from_slice::<Receipt>(&b))
                .transpose()?,
        );
    }
    Ok(
        json!({"installationDigest":i.digest()?,"deploymentId":i.scope.plan.deployment_id,"receipts":receipts,"cloudObservation":"not_checked","cloudMutations":0,"applyReady":false,"cleanupInstalled":false,"controlInfrastructureCreation":"separate reviewed Terraform step"}),
    )
}
pub(super) async fn run(command: &Command) -> Result<()> {
    if let Command::Prepare {
        state,
        guard_image,
        out,
    } = command
    {
        let (p, m, request_id, plan_path) = super::recovery::binding(state)?;
        outside(out, state)?;
        outside(out, &plan_path)?;
        let c = Control {
            schema_version: 1,
            source_generation: "1".into(),
            source_sha256: hash(&source_zip(&p)?),
            plan: p,
            bundle_digest: m.bundle_digest,
            request_id,
            guard_image: guard_image.clone(),
        };
        c.validate()?;
        fresh(&c, now()?)?;
        let i = Installation {
            schema_version: 1,
            plan_path,
            scope: c,
        };
        i.validate()?;
        private_dir(out)?;
        put(out, "installation.json", &json_bytes(&i)?)?;
        return crate::output(status(out, &i)?);
    }
    let state = match command {
        Command::Preflight { state, .. }
        | Command::Upload { state, .. }
        | Command::Package { state, .. }
        | Command::Reconcile { state }
        | Command::Status { state }
        | Command::Verify { state } => state,
        _ => unreachable!(),
    };
    let (i, _lock) = load(state)?;
    match command {
        Command::Status { .. } => crate::output(status(state, &i)?),
        Command::Package { out, .. } => {
            outside(out, state)?;
            outside(out, &i.plan_path)?;
            let c = actual_control(state, &i)?;
            let j: Checkpoint =
                serde_json::from_slice(&read_file(state, "initial-checkpoint.json")?)?;
            j.validate(&c)?;
            super::control_export::package(out, &c, &j)
        }
        Command::Preflight { out, .. } => {
            outside(out, state)?;
            outside(out, &i.plan_path)?;
            private_dir(out)?;
            let token = super::api::login(&i.scope, false).await?;
            let api = super::api::Google::new(i.scope.clone(), token)?;
            let report = api.installation_preflight(now()?).await;
            put(out, "preflight.json", &json_bytes(&report)?)?;
            crate::output(report)
        }
        Command::Upload {
            allow_experimental_cloud,
            accept,
            ..
        } => {
            ensure!(
                *allow_experimental_cloud && *accept == i.digest()?,
                "Explicit installation digest and --allow-experimental-cloud required"
            );
            fresh(&i.scope, now()?)?;
            let token = super::api::login(&i.scope, false).await?;
            let mut api = super::api::Google::new(i.scope.clone(), token)?;
            let preflight = api.installation_preflight(now()?).await;
            ensure!(preflight["artifactUploadReady"]==true,"Control artifact upload prerequisites are blocked; inspect installation preflight. No upload dispatched");
            let c = upload(&mut api, state, &i, true).await?;
            crate::output(
                json!({"controlUri":c.control_uri()?,"controlSha256":c.sha()?,"sourceGeneration":c.source_generation,"artifactsVerified":true,"cleanupInstalled":false,"applyReady":false,"next":"Export the actual cleanup package, install its reviewed Terraform graph, then verify independent heartbeat"}),
            )
        }
        Command::Reconcile { .. } => {
            let token = super::api::login(&i.scope, false).await?;
            let mut api = super::api::Google::new(i.scope.clone(), token)?;
            api.bucket().await?;
            let c = upload(&mut api, state, &i, false).await?;
            crate::output(
                json!({"artifactsVerified":true,"controlUri":c.control_uri()?,"cloudMutations":0,"applyReady":false}),
            )
        }
        Command::Verify { .. } => {
            let c = actual_control(state, &i)?;
            let token = super::api::login(&c, false).await?;
            let mut api = super::api::Google::new(c.clone(), token)?;
            api.source().await?;
            let (j, g) = api.load().await?;
            let uid = api.guard().await?;
            let at = now()?;
            ensure!(
                j.guard_uid.as_ref() == Some(&uid)
                    && j.heartbeat_at.is_some_and(|t| t <= at && at - t <= 180),
                "Independent cleanup heartbeat missing/stale or job replaced"
            );
            crate::output(
                json!({"controlArtifactsVerified":true,"cleanupHeartbeatVerified":true,"guardUid":uid,"checkpointGeneration":g,"phase":j.phase,"applyReady":false,"fullCostIamAccessQualification":false}),
            )
        }
        _ => unreachable!(),
    }
}
#[cfg(test)]
#[path = "deployment_gcp_installation_tests.rs"]
mod tests;
