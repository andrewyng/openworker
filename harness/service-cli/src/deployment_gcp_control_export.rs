//! Independent control-plane packaging. Export never invokes cloud/Terraform tools.
use super::{control::*, *};

#[derive(Subcommand)]
pub enum Command {
    /// Export separate source/control storage and cleanup image registry.
    Foundation {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Export immutable control/checkpoint and independent Cloud Run/Scheduler graph.
    Package {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        source_generation: String,
        #[arg(long)]
        guard_image: String,
        #[arg(long)]
        out: PathBuf,
    },
    /// One independent cleanup observation/action; Cloud Run metadata identity only.
    Tick {
        #[arg(long)]
        control_uri: String,
        #[arg(long)]
        control_sha256: String,
        #[arg(long)]
        service_account: String,
    },
}
pub(super) fn tick_args(c: &Control) -> Result<Vec<String>> {
    Ok(vec![
        "deploy".into(),
        "gcp".into(),
        "cleanup".into(),
        "tick".into(),
        "--control-uri".into(),
        c.control_uri()?,
        "--control-sha256".into(),
        c.sha()?,
        "--service-account".into(),
        accounts(&c.plan)["cleanup"].clone(),
    ])
}
fn base(p: &Plan) -> Value {
    let region = match &p.target {
        Target::Gcp { location, .. } => location,
        _ => unreachable!(),
    };
    json!({"terraform":{"required_version":"=1.5.7","required_providers":{"google":{"source":"hashicorp/google","version":"=6.44.0"}}},"provider":{"google":{"project":project(p),"region":region}}})
}
fn write(out: &Path, files: BTreeMap<String, Vec<u8>>) -> Result<()> {
    let mut d = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        d.mode(0o700);
    }
    d.create(out).context("Use a fresh package directory")?;
    for (name, bytes) in &files {
        let mut opts = fs::OpenOptions::new();
        opts.create_new(true).write(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut f = opts.open(out.join(name))?;
        f.write_all(bytes)?;
        f.sync_all()?;
    }
    crate::output(
        json!({"exported":true,"cloudMutations":0,"installed":false,"applyReady":false,"files":files.keys().collect::<Vec<_>>()}),
    )
}
fn outside(out: &Path, root: &Path) -> Result<()> {
    let parent = out
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    ensure!(
        !parent.starts_with(root.canonicalize()?),
        "Package output must be outside plan/lifecycle state"
    );
    Ok(())
}
pub(super) fn export(command: &Command) -> Result<()> {
    match command {
        Command::Foundation { plan, out } => {
            outside(out, plan)?;
            let (p, _) = inspect(plan)?;
            ensure!(
                p.gcp_infrastructure.is_some(),
                "GCP infrastructure graph required"
            );
            let at = now()?;
            ensure!(
                p.created_at_unix <= at && at < p.expires_at_unix,
                "Plan expired/future-dated"
            );
            let prefix = format!("ow-{}", &p.deployment_id[4..16]);
            let bucket = format!("{}-{prefix}-control", project(&p));
            let region = match &p.target {
                Target::Gcp { location, .. } => location,
                _ => unreachable!(),
            };
            let mut tf = base(&p);
            tf["resource"] = json!({"google_storage_bucket":{"control":{"name":bucket,"location":region,"uniform_bucket_level_access":true,"public_access_prevention":"enforced","force_destroy":false,"versioning":[{"enabled":true}],"labels":{"openworker_deployment":p.deployment_id,"purpose":"durable-cleanup-control"}}},"google_artifact_registry_repository":{"control_images":{"repository_id":format!("{prefix}-control-images"),"location":region,"format":"DOCKER","labels":{"openworker_deployment":p.deployment_id}}}});
            let zip = source_zip(&p)?;
            let sha = hash(&zip);
            let source = format!("{}/source-{sha}.zip", p.deployment_id);
            write(
                out,
                BTreeMap::from([
                    ("main.tf.json".into(), json_bytes(&tf)?),
                    (
                        ".terraform.lock.hcl".into(),
                        include_bytes!("../assets/gcp-google-6.44.0.lock.hcl").to_vec(),
                    ),
                    ("source.zip".into(), zip),
                    (
                        "source-manifest.json".into(),
                        json_bytes(
                            &json!({"bucket":bucket,"object":source,"sourceSha256":sha,"uploadPrecondition":"ifGenerationMatch=0","sourceGeneration":"must be read from the actual upload receipt","controlResourcesMustOutliveWorkload":true,"installed":false,"permissionPolicyReady":false}),
                        )?,
                    ),
                ]),
            )
        }
        Command::Package {
            state,
            source_generation,
            guard_image,
            out,
        } => {
            let (p, m, request_id, plan_path) = super::recovery::binding(state)?;
            outside(out, state)?;
            outside(out, &plan_path)?;
            let at = now()?;
            ensure!(
                p.created_at_unix <= at && at < p.expires_at_unix,
                "Plan expired/future-dated"
            );
            let c = Control {
                schema_version: 1,
                source_sha256: hash(&source_zip(&p)?),
                plan: p,
                bundle_digest: m.bundle_digest,
                source_generation: source_generation.clone(),
                request_id,
                guard_image: guard_image.clone(),
            };
            c.validate()?;
            let j = Checkpoint::new(&c)?;
            let mut tf = base(&c.plan);
            let a = accounts(&c.plan);
            let name = format!("{}-cleanup", c.prefix());
            tf["resource"] = json!({
                "google_cloud_run_v2_job":{"cleanup":{"name":name,"location":c.region(),"deletion_protection":false,"template":[{"task_count":1,"parallelism":1,"template":[{"service_account":a["cleanup"],"max_retries":0,"timeout":"180s","containers":[{"image":c.guard_image,"command":["/opt/openworker/ows"],"args":tick_args(&c)?,"resources":[{"limits":{"cpu":"1","memory":"512Mi"}}]}]}]}]}},
                "google_cloud_run_v2_job_iam_member":{"invoker":{"project":project(&c.plan),"location":c.region(),"name":"${google_cloud_run_v2_job.cleanup.name}","role":"roles/run.invoker","member":format!("serviceAccount:{}",a["cleanup"])}},
                "google_cloud_scheduler_job":{"cleanup":{"name":name,"region":c.region(),"schedule":"*/1 * * * *","time_zone":"Etc/UTC","attempt_deadline":"180s","http_target":[{"uri":format!("https://run.googleapis.com/v2/{}:run",c.job()),"http_method":"POST","headers":{"Content-Type":"application/json"},"body":"e30=","oauth_token":[{"service_account_email":a["cleanup"],"scope":"https://www.googleapis.com/auth/cloud-platform"}]}],"depends_on":["google_cloud_run_v2_job_iam_member.invoker"]}}
            });
            let permissions = json!({"schemaVersion":1,"policyReady":false,"granted":false,"cleanupIdentity":a["cleanup"],"setupIdentity":a["setup"],"deployment":c.deployment(),"bucket":c.bucket(),"requiredApis":["run.googleapis.com","cloudscheduler.googleapis.com","storage.googleapis.com"],"requirements":[{"resource":c.deployment(),"permissions":["config.deployments.get","config.deployments.delete","config.revisions.get"]},{"resource":c.parent(),"permissions":["config.operations.get","config.operations.list"]},{"resource":format!("{}/{}",c.bucket(),c.checkpoint_object()),"permissions":["storage.objects.get","storage.objects.create","storage.objects.delete"],"generationPreconditionRequired":true},{"resource":format!("{}/{}",c.bucket(),c.source_object()),"permissions":["storage.objects.get"]},{"resource":c.control_uri()?,"permissions":["storage.objects.get"]},{"resource":c.job(),"permissions":["run.jobs.get","run.jobs.run","cloudscheduler.jobs.get","cloudscheduler.jobs.pause"]}],"remainingAuthority":["read control bucket metadata","read named workload roots to verify absence","zone-scoped compute.instances.list and compute.disks.list; get captured instances/disks/groups/templates","installer-specific Cloud Run/Scheduler service-agent and image pull authority","resource-specific executor/node/workload roles","effective IAM/deny/condition qualification"],"constraints":["no cleanup IAM administration or workload creation authority","no cleanup source/state bucket deletion authority","operations listing is region/project scoped; do not claim per-deployment IAM fencing for it","no generic project-wide deployment delete grant is generated"]});
            write(
                out,
                BTreeMap::from([
                    ("main.tf.json".into(), json_bytes(&tf)?),
                    (
                        ".terraform.lock.hcl".into(),
                        include_bytes!("../assets/gcp-google-6.44.0.lock.hcl").to_vec(),
                    ),
                    ("control.json".into(), json_bytes(&c)?),
                    ("checkpoint.json".into(), json_bytes(&j)?),
                    ("permissions-request.json".into(), json_bytes(&permissions)?),
                    (
                        "package-manifest.json".into(),
                        json_bytes(
                            &json!({"controlUri":c.control_uri()?,"controlSha256":c.sha()?,"checkpointObject":c.checkpoint_object(),"controlUploadPrecondition":"ifGenerationMatch=0","checkpointUploadPrecondition":"ifGenerationMatch=0; never overwrite an existing checkpoint","image":c.guard_image,"cleanupInstalled":false,"liveQualificationRequired":true,"allCleanupComplete":false}),
                        )?,
                    ),
                ]),
            )
        }
        Command::Tick { .. } => bail!("Cleanup tick requires asynchronous dispatch"),
    }
}
pub(super) async fn run(command: &Command) -> Result<()> {
    if let Command::Tick {
        control_uri,
        control_sha256,
        service_account,
    } = command
    {
        let mut api = super::api::independent(control_uri, control_sha256, service_account).await?;
        let c = api.control.clone();
        let (mut j, mut g) = api.load().await?;
        crate::output(
            super::engine::drive(
                &mut api,
                &c,
                &mut j,
                &mut g,
                super::engine::Mode::Tick,
                now()?,
            )
            .await?,
        )
    } else {
        export(command)
    }
}
