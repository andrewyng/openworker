//! One entry point over the existing provider lifecycle and shared installer.
//! Desired state, reviewed observations and effect receipts remain distinct.
use super::*;
#[path = "deployment_up_config.rs"]
mod config;
#[path = "deployment_up_native.rs"]
mod native;
#[path = "deployment_up_package.rs"]
mod package;
#[path = "deployment_up_pricing.rs"]
mod pricing;
#[path = "deployment_up_resolve.rs"]
mod resolve;
#[path = "deployment_up_state.rs"]
mod state;
use config::Config;

#[derive(Args)]
pub struct Options {
    #[arg(long)]
    config: PathBuf,
    /// Private journal directory; defaults to .ows/deployments/<target-and-name hash>.
    #[arg(long)]
    state: Option<PathBuf>,
    /// Resolve and write the review without provisioning.
    #[arg(long)]
    plan_only: bool,
    /// Noninteractive approval of this exact review digest, never a blanket yes.
    #[arg(long)]
    accept_plan: Option<String>,
    /// Separate approval of scoped identity grants in the exact reviewed plan.
    #[arg(long)]
    accept_bootstrap: Option<String>,
    #[arg(long, default_value_t = 3600)]
    timeout_seconds: u64,
    /// Keep the private management route open after installation; Ctrl-C closes it.
    #[arg(long)]
    keep_access: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Review {
    schema_version: u16,
    config: Config,
    config_digest: String,
    deployment_id: String,
    resolution: resolve::Resolution,
    package_digest: String,
    native_bundle_digest: String,
    digest: String,
}
impl Review {
    fn digest(&self) -> Result<String> {
        let mut v = serde_json::to_value(self)?;
        v.as_object_mut().unwrap().remove("digest");
        Ok(hash(&serde_json::to_vec(&v)?))
    }
    fn validate(&self, c: &Config) -> Result<()> {
        ensure!(self.schema_version==1 && self.config==*c && self.config_digest==c.digest()? && self.digest==self.digest()?,"Deployment configuration/review changed. Review an explicit new deployment; do not overwrite the original journal.");
        self.resolution.validate(c)?;
        Ok(())
    }
}
pub(super) fn write_private(path: &Path, value: &impl Serialize) -> Result<()> {
    use std::io::Write;
    let temp = path.with_extension(format!("{}.tmp", uuid::Uuid::new_v4().simple()));
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut f = options.open(&temp)?;
    f.write_all(&json_bytes(value)?)?;
    f.sync_all()?;
    fs::rename(&temp, path)?;
    #[cfg(unix)]
    fs::File::open(path.parent().context("Receipt parent missing")?)?.sync_all()?;
    Ok(())
}
pub(super) fn directory(path: &Path) -> Result<()> {
    if path.exists() {
        let m = fs::symlink_metadata(path)?;
        ensure!(
            m.is_dir() && !m.file_type().is_symlink(),
            "Deployment state must be a directory, not a symlink"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            ensure!(
                m.permissions().mode() & 0o077 == 0,
                "Deployment state must be private (chmod 700)"
            );
        }
    } else {
        let mut b = fs::DirBuilder::new();
        b.recursive(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            b.mode(0o700);
        }
        b.create(path)?;
    }
    Ok(())
}
/// Remove only a local transport route for comparison with the provider's
/// original kubeconfig. Hostname, CA, TLS options and authentication stay intact.
pub(super) fn original_kubeconfig(value: &Value) -> Result<Value> {
    let mut original = value.clone();
    for cluster in original["clusters"]
        .as_array_mut()
        .context("Kubeconfig clusters missing")?
    {
        let c = cluster["cluster"]
            .as_object_mut()
            .context("Cluster definition missing")?;
        if let Some(proxy) = c.remove("proxy-url") {
            let url = reqwest::Url::parse(proxy.as_str().context("Invalid local proxy")?)?;
            ensure!(
                url.scheme() == "http"
                    && url.host_str() == Some("127.0.0.1")
                    && url.port().is_some_and(|p| p >= 1024)
                    && url.username().is_empty()
                    && url.password().is_none()
                    && url.path() == "/"
                    && url.query().is_none()
                    && url.fragment().is_none(),
                "Only the loopback management transport may differ from the original kubeconfig"
            );
        }
    }
    Ok(original)
}
pub(super) fn operation_lock(root: &Path, name: &str) -> Result<fs::File> {
    directory(root)?;
    let path = root.join(name);
    if path.exists() {
        ensure!(
            !fs::symlink_metadata(&path)?.file_type().is_symlink(),
            "Operation lock cannot be a symlink"
        );
    }
    let mut options = fs::OpenOptions::new();
    options.create(true).truncate(false).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let f = options.open(path)?;
    f.try_lock()
        .context("Another command owns this operation journal")?;
    Ok(f)
}
async fn check_local_tools(c: &Config, package: &package::Snapshot) -> Result<()> {
    ensure!(
        !c.management_ui.as_ref().is_some_and(|ui| ui.enabled)
            || package.release.images.service_ui.is_some(),
        "Enabling managementUI requires a digest-pinned service-ui image in the release manifest"
    );
    let mut tools: Vec<(&str, Vec<&str>)> = vec![
        ("kubectl", vec!["version", "--client", "--output=json"]),
        ("helm", vec!["version", "--short"]),
        ("docker", vec!["version", "--format", "{{.Server.Version}}"]),
        ("openssl", vec!["version"]),
    ];
    if c.cloud.provider() == "azure" {
        tools.push(("kubelogin", vec!["--version"]));
        if package
            .setup
            .infrastructure
            .get("managementAccess")
            .is_some_and(|v| !v.is_null())
        {
            tools.push(("ssh", vec!["-V"]));
        }
    } else if package
        .setup
        .infrastructure
        .get("operatorAccess")
        .is_some_and(|v| !v.is_null())
    {
        tools.push(("session-manager-plugin", vec!["--version"]));
    }
    for (program, args) in tools {
        let args: Vec<String> = args.into_iter().map(str::to_owned).collect();
        super::local::exec(Path::new(program),&args,None).await.with_context(||format!("Local prerequisite {program} unavailable. Install/start it before provisioning; no cloud mutation performed."))?;
    }
    Ok(())
}
pub(super) async fn run(provider: &str, o: &Options) -> Result<()> {
    let config = Config::load(&o.config, provider)?;
    let package = package::load(&config)?;
    ensure!(
        (10..=14400).contains(&o.timeout_seconds),
        "Use timeoutSeconds between 10 and 14400"
    );
    let identity = hash(
        format!(
            "{}:{}:{}:{}",
            provider,
            config.cloud.account(),
            config.cloud.region(),
            config.name
        )
        .as_bytes(),
    );
    let state = o
        .state
        .clone()
        .unwrap_or_else(|| PathBuf::from(".ows/deployments").join(&identity[..24]));
    directory(&state)?;
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(state.join("up.lock"))?;
    lock.try_lock()
        .context("Another deployment command owns this journal")?;
    let review_path = state.join("review.json");
    let review: Review = if review_path.exists() {
        let value: Review = serde_json::from_slice(&fs::read(&review_path)?)?;
        value.validate(&config)?;
        ensure!(
            value.package_digest == package.digest,
            "Account setup/release changed; do not alter the original deployment review"
        );
        value
    } else {
        let resolution = resolve::live(&config, &package.setup.infrastructure).await?;
        let mut value = Review {
            schema_version: 1,
            config_digest: config.digest()?,
            config: config.clone(),
            deployment_id: format!("ows-{}", uuid::Uuid::new_v4().simple()),
            resolution,
            package_digest: package.digest.clone(),
            native_bundle_digest: String::new(),
            digest: String::new(),
        };
        value.native_bundle_digest = native::Native {
            root: state.canonicalize()?,
            review: &value,
            package: package.clone(),
            bootstrap_approved: false,
            bridge: Default::default(),
        }
        .artifact_digest()?;
        value.digest = value.digest()?;
        value.validate(&config)?;
        write_private(&review_path, &value)?;
        value
    };
    ensure!(
        native::Native {
            root: state.canonicalize()?,
            review: &review,
            package: package.clone(),
            bootstrap_approved: false,
            bridge: Default::default()
        }
        .artifact_digest()?
            == review.native_bundle_digest,
        "Generated infrastructure/permissions changed since review; do not reuse the old approval"
    );
    crate::output(
        json!({"deploymentId":review.deployment_id,"state":state,"planDigest":review.digest,"nodeCount":config.nodes.count,"instanceType":review.resolution.node.instance_type,"price":review.resolution.price,"phase":"review","mutations":0}),
    )?;
    if o.plan_only {
        native::Native {
            root: state.canonicalize()?,
            review: &review,
            package: package.clone(),
            bootstrap_approved: false,
            bridge: Default::default(),
        }
        .review_bundle()
        .await?;
        return Ok(());
    }
    if let Some(subtotal) = review.resolution.price.known_hourly_subtotal_usd {
        let minimum = subtotal * f64::from(review.resolution.price.planned_hours)
            + f64::from(review.resolution.price.variable_allowance_usd);
        ensure!(minimum<=f64::from(config.budget.alert_usd),"Known planning estimate already exceeds budget.limitUSD. Review a new budget/size/lifetime before provisioning; no mutations performed.");
    }
    // Render the exact permission payloads before either approval prompt.
    let preview = native::Native {
        root: state.canonicalize()?,
        review: &review,
        package: package.clone(),
        bootstrap_approved: false,
        bridge: Default::default(),
    };
    preview.review_bundle().await?;
    approve(
        &state,
        &review.digest,
        "provisioning",
        o.accept_plan.as_deref(),
    )?;
    approve(
        &state,
        &review.digest,
        "bootstrap",
        o.accept_bootstrap.as_deref(),
    )?;
    check_local_tools(&config, &package).await?;
    resolve::identity(&config).await?;
    let mut driver = native::Native {
        root: state.canonicalize()?,
        review: &review,
        package,
        bootstrap_approved: true,
        bridge: Default::default(),
    };
    let path = state.join("up-journal.json");
    let mut journal = state::Journal::open(&path, &review)?;
    if journal
        .stages
        .get(&state::Stage::PrivateAccess)
        .is_some_and(|s| s.phase == state::Phase::Complete)
    {
        driver.restore_access().await?;
    }
    let previously_complete = journal.complete();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(o.timeout_seconds);
    loop {
        let previous = journal
            .stages
            .iter()
            .map(|(stage, s)| (*stage, s.phase))
            .collect::<Vec<_>>();
        if state::advance(&mut journal, &path, &mut driver).await? {
            // Completion receipts are history, not a claim that today’s cluster is healthy.
            if previously_complete {
                driver.verify_current().await?;
            }
            if o.keep_access {
                crate::output(
                    json!({"phase":"access_ready","connection":state.join("connection.json"),"close":"Ctrl-C"}),
                )?;
                tokio::signal::ctrl_c().await?;
            }
            driver.close_access().await?;
            return crate::output(
                json!({"deploymentId":review.deployment_id,"phase":"installed","state":state,"connection":state.join("connection.json"),"modelSmokeTest":"not_run"}),
            );
        }
        let current = journal
            .stages
            .iter()
            .map(|(stage, s)| (*stage, s.phase))
            .collect::<Vec<_>>();
        if current != previous {
            eprintln!("Deployment progress: {:?}", current.last());
        }
        ensure!(std::time::Instant::now()<deadline,"Deployment is still pending. Rerun the same up command to resume; original resources and requests were retained.");
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
    }
}

fn approve(root: &Path, digest: &str, kind: &str, supplied: Option<&str>) -> Result<()> {
    use std::io::{IsTerminal, Write};
    if let Some(value) = supplied {
        ensure!(
            value == digest,
            "Approval digest does not match the reviewed deployment"
        );
    }
    let path = root.join(format!("approval-{kind}.json"));
    if path.exists() {
        let old: Value = serde_json::from_slice(&fs::read(&path)?)?;
        ensure!(
            old["digest"] == digest && old["kind"] == kind,
            "Saved approval belongs to another review"
        );
        return Ok(());
    }
    if supplied.is_none() {
        ensure!(std::io::stdin().is_terminal(), "Review the infrastructure directory, then rerun with --accept-plan <planDigest> and --accept-bootstrap <planDigest>");
        eprint!(
            "Approve {kind} for this exact review? Inspect {}. Type {}: ",
            root.join("infrastructure").display(),
            &digest[..12]
        );
        std::io::stderr().flush()?;
        let mut answer = String::new();
        std::io::stdin().read_line(&mut answer)?;
        ensure!(
            answer.trim() == &digest[..12],
            "Not approved; no cloud mutation performed"
        );
    }
    write_private(
        &path,
        &json!({"digest":digest,"kind":kind,"approvedAtUnix":now()?}),
    )
}
#[cfg(test)]
mod route_tests {
    use super::*;
    #[test]
    fn route_normalization_never_relaxes_cluster_identity_or_authentication() {
        let original = json!({"clusters":[{"cluster":{"server":"https://original.internal","certificate-authority-data":"original-ca"}}],"users":[{"user":{"exec":{"command":"aws"}}}]});
        let mut routed = original.clone();
        routed["clusters"][0]["cluster"]["proxy-url"] = json!("http://127.0.0.1:15000");
        assert_eq!(original_kubeconfig(&routed).unwrap(), original);
        routed["clusters"][0]["cluster"]["certificate-authority-data"] = json!("changed");
        assert_ne!(original_kubeconfig(&routed).unwrap(), original);
        for url in [
            "http://remote.example:15000",
            "https://127.0.0.1:15000",
            "http://user@127.0.0.1:15000",
            "http://127.0.0.1:80",
            "http://127.0.0.1:15000/path",
        ] {
            routed["clusters"][0]["cluster"]["proxy-url"] = json!(url);
            assert!(original_kubeconfig(&routed).is_err());
        }
    }
}

#[cfg(test)]
mod approval_tests {
    use super::*;
    #[test]
    fn approval_is_durable_scoped_and_rejects_changed_review() {
        let temp = tempfile::tempdir().unwrap();
        let digest = "a".repeat(64);
        approve(temp.path(), &digest, "provisioning", Some(&digest)).unwrap();
        approve(temp.path(), &digest, "provisioning", None).unwrap();
        assert!(!temp.path().join("approval-bootstrap.json").exists());
        assert!(approve(
            temp.path(),
            &"b".repeat(64),
            "provisioning",
            Some(&"b".repeat(64))
        )
        .is_err());
        assert!(approve(temp.path(), &digest, "provisioning", Some("wrong")).is_err());
    }
}
