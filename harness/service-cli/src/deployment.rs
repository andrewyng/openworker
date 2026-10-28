//! Offline planning/packaging, local kind installation and read-only AWS preflight.
//! Offline commands do not invoke cloud tools or load service authentication.
#[path = "deployment_bootstrap.rs"]
mod bootstrap;
#[path = "deployment_cloud.rs"]
mod cloud;
#[path = "deployment_guard.rs"]
mod guard;
#[path = "deployment_infrastructure.rs"]
mod infrastructure;
#[path = "deployment_local.rs"]
mod local;
#[path = "deployment_preflight.rs"]
mod preflight;
use anyhow::{bail, ensure, Context, Result};
use clap::{Args, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Subcommand)]
pub enum Command {
    Guard {
        #[command(subcommand)]
        command: guard::Command,
    },
    /// Local kind installation/qualification; no cloud provisioning.
    Local {
        #[command(subcommand)]
        command: local::Command,
    },
    /// Shared Kubernetes installation, including a lifecycle-bound AWS cluster.
    Cluster {
        #[command(subcommand)]
        command: local::Command,
    },
    Aws {
        #[command(subcommand)]
        command: AwsCommand,
    },
    Gcp {
        #[command(subcommand)]
        command: GcpCommand,
    },
    Azure {
        #[command(subcommand)]
        command: AzureCommand,
    },
    /// Read a verified local bundle. Does not query cloud state.
    Inspect {
        #[arg(long)]
        plan: PathBuf,
    },
    /// Check bundle integrity, semantic bounds and expiry, without cloud access.
    Validate {
        #[arg(long)]
        plan: PathBuf,
    },
    /// Show local planning state only; no cloud observation in this milestone.
    Status {
        #[arg(long)]
        plan: PathBuf,
    },
    /// Reserved mutation command. Fails closed until the cloud executor is qualified.
    Apply {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        accept_plan: String,
    },
    /// Reserved mutation command. Never deletes anything in the offline milestone.
    Destroy {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        deployment_id: String,
    },
}
#[derive(Subcommand)]
pub enum AwsCommand {
    Plan(AwsPlan),
    /// Experimental lifecycle; explicit opt-in, role profile and fresh preflight.
    Lifecycle {
        #[command(subcommand)]
        command: cloud::Command,
    },
    /// Query AWS read-only and write a fresh report. Never provisions resources.
    Preflight(preflight::Options),
}
#[derive(Args)]
pub struct AwsPlan {
    #[arg(long)]
    account: String,
    #[arg(long)]
    region: String,
    /// Export identity bootstrap for this explicit same-account IAM principal.
    #[arg(long)]
    operator_arn: Option<String>,
    /// Existing, customer-reviewed CloudFormation execution role (not created here).
    #[arg(long, requires = "operator_arn")]
    execution_role_arn: Option<String>,
    /// Export the AWS infrastructure and generated execution roles from explicit pinned inputs.
    #[arg(long, requires = "operator_arn", conflicts_with = "execution_role_arn")]
    infrastructure_config: Option<PathBuf>,
    #[command(flatten)]
    common: PlanOptions,
}
#[derive(Subcommand)]
pub enum GcpCommand {
    Plan(GcpPlan),
}
#[derive(Subcommand)]
pub enum AzureCommand {
    Plan(AzurePlan),
}
#[derive(Args)]
pub struct GcpPlan {
    #[arg(long)]
    project: String,
    #[arg(long)]
    location: String,
    #[command(flatten)]
    common: PlanOptions,
}
#[derive(Args)]
pub struct AzurePlan {
    #[arg(long)]
    tenant: String,
    #[arg(long)]
    subscription: String,
    #[arg(long)]
    location: String,
    #[command(flatten)]
    common: PlanOptions,
}
#[derive(Args)]
struct PlanOptions {
    #[arg(long)]
    name: String,
    #[arg(long, default_value_t = 100)]
    budget: u32,
    #[arg(long, default_value_t = 24)]
    ttl_hours: u16,
    #[arg(long)]
    out: PathBuf,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "provider", rename_all = "lowercase", deny_unknown_fields)]
enum Target {
    Aws {
        account: String,
        region: String,
    },
    Gcp {
        project: String,
        location: String,
    },
    Azure {
        tenant: String,
        subscription: String,
        location: String,
    },
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Budget {
    authorized_usd: u32,
    hard_cap: bool,
    estimate: Option<u64>,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Topology {
    cluster_mode: String,
    node_count: u16,
    max_worker_replicas: u16,
    architecture: String,
    access: String,
    node_autoscaling: bool,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Plan {
    schema_version: u16,
    deployment_id: String,
    name: String,
    target: Target,
    created_at_unix: u64,
    expires_at_unix: u64,
    budget: Budget,
    topology: Topology,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    bootstrap: Option<bootstrap::AwsIdentity>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    infrastructure: Option<infrastructure::Inputs>,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Manifest {
    schema_version: u16,
    deployment_id: String,
    artifacts: BTreeMap<String, String>,
    bundle_digest: String,
}
fn now() -> Result<u64> {
    Ok(SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs())
}
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn json_bytes(value: &impl Serialize) -> Result<Vec<u8>> {
    let mut v = serde_json::to_vec_pretty(value)?;
    v.push(b'\n');
    Ok(v)
}
fn name_ok(s: &str) -> bool {
    (3..=30).contains(&s.len())
        && s.as_bytes()[0].is_ascii_lowercase()
        && s.as_bytes()[s.len() - 1].is_ascii_alphanumeric()
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}
fn region_ok(s: &str) -> bool {
    let parts: Vec<_> = s.split('-').collect();
    parts.len() == 3
        && ["us", "eu", "ap", "ca", "sa", "me", "af", "il", "mx"].contains(&parts[0])
        && (3..=12).contains(&parts[1].len())
        && parts[1].bytes().all(|b| b.is_ascii_lowercase())
        && (1..=2).contains(&parts[2].len())
        && parts[2].bytes().all(|b| b.is_ascii_digit())
        && !parts[2].starts_with('0')
}
impl Plan {
    fn validate(&self) -> Result<()> {
        ensure!(
            [1, 2, 3].contains(&self.schema_version),
            "Unsupported plan schema"
        );
        ensure!(
            name_ok(&self.name),
            "Name must be 3–30 lowercase letters/digits/hyphens, starting with a letter"
        );
        let id = self
            .deployment_id
            .strip_prefix("ows-")
            .context("Invalid deployment identity")?;
        ensure!(
            id.len() == 32
                && id
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
            "Invalid deployment identity"
        );
        match &self.target {
            Target::Aws { account, region } => {
                ensure!(
                    account.len() == 12 && account.bytes().all(|b| b.is_ascii_digit()),
                    "AWS account must be 12 digits"
                );
                ensure!(region_ok(region),"Unsupported AWS region syntax/partition; availability requires online preflight");
            }
            Target::Gcp { project, location } => {
                ensure!(self.schema_version == 2, "GCP requires schema 2");
                ensure!(
                    (6..=30).contains(&project.len()) && name_ok(project),
                    "Invalid GCP project ID"
                );
                let parts: Vec<_> = location.split('-').collect();
                ensure!(
                    parts.len() == 2
                        && (2..=20).contains(&parts[0].len())
                        && parts[0].bytes().all(|b| b.is_ascii_lowercase())
                        && (2..=20).contains(&parts[1].len())
                        && parts[1]
                            .bytes()
                            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
                        && parts[1].as_bytes().last().unwrap().is_ascii_digit(),
                    "Use a GCP region, not a zone; availability requires preflight"
                );
            }
            Target::Azure {
                tenant,
                subscription,
                location,
            } => {
                ensure!(self.schema_version == 2, "Azure requires schema 2");
                for id in [tenant, subscription] {
                    ensure!(
                        uuid::Uuid::parse_str(id)
                            .map(|u| u.hyphenated().to_string() == *id && !u.is_nil())
                            .unwrap_or(false),
                        "Azure tenant/subscription must be lowercase nonzero hyphenated UUIDs"
                    );
                }
                ensure!(
                    (3..=30).contains(&location.len())
                        && location
                            .bytes()
                            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
                        && location.as_bytes()[0].is_ascii_lowercase(),
                    "Invalid Azure location; availability requires preflight"
                );
            }
        }
        if let Some(identity) = &self.bootstrap {
            ensure!(
                self.schema_version >= 2,
                "Bootstrap requires schema 2 or later"
            );
            identity.validate(self)?;
        }
        if let Some(inputs) = &self.infrastructure {
            ensure!(
                self.schema_version == 3 && self.bootstrap.is_some(),
                "Infrastructure requires schema 3 and bootstrap identity"
            );
            inputs.validate(self)?;
        } else {
            ensure!(
                self.schema_version != 3,
                "Schema 3 requires infrastructure inputs"
            );
        }
        ensure!(
            self.created_at_unix > 0
                && self.expires_at_unix > self.created_at_unix
                && self.expires_at_unix - self.created_at_unix <= 72 * 3600,
            "Expiry must be within 72 hours of plan creation"
        );
        ensure!(
            (1..=100_000).contains(&self.budget.authorized_usd)
                && !self.budget.hard_cap
                && self.budget.estimate.is_none(),
            "Offline plans require a positive budget, unknown estimate, and no hard-cap claim"
        );
        ensure!(
            self.topology
                == Topology {
                    cluster_mode: "new".into(),
                    node_count: 2,
                    max_worker_replicas: 2,
                    architecture: if matches!(self.target, Target::Aws { .. }) {
                        "arm64"
                    } else {
                        "amd64"
                    }
                    .into(),
                    access: "private".into(),
                    node_autoscaling: false
                },
            "Unsupported topology for this planning milestone"
        );
        Ok(())
    }
    fn prefix(&self) -> String {
        format!("ows-{}-{}", self.name, &self.deployment_id[4..16])
    }
}
trait Planner {
    fn render(&self, plan: &Plan) -> Result<BTreeMap<String, Vec<u8>>>;
}
struct AwsPlanner;
impl Planner for AwsPlanner {
    fn render(&self, p: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
        p.validate()?;
        let Target::Aws { account, region } = &p.target else {
            bail!("AWS target required")
        };
        let prefix = p.prefix();
        let review=format!("# {} deployment review\n\nDeployment: `{}`\nAWS account: `{}`\nRegion: `{}`\nResource prefix: `{}`\n\n## Proposed experiment\n\nNew EKS cluster; two ARM64 nodes; at most two harness worker replicas.\nPrivate access. No automatic node autoscaler. PostgreSQL and OpenShell are\nrequired dependencies. Nodes and worker replicas are separate capacity settings.\n\nBudget authorization: ${} USD. **No verified cost estimate; not a hard cap.**\nCreated (Unix seconds): {}. Fixed expiry (Unix seconds): {}.\n\n## Readiness\n\nThis is an offline plan. No AWS call or resource change has occurred.\nIt cannot be applied yet. Still required: complete scoped bootstrap policies,\ncaller/role identity, live price/quota/version checks, pinned service/UI/runner\nimages, infrastructure templates, network/storage inputs, secret references,\ncloud-side expiry guard, and a qualified cloud executor.\n\n`permissions-request.json` describes required authority; it is NOT an IAM policy.\nNo bootstrap.sh is emitted until its permissions and operations are implemented.\nThe bundle digest detects changes; it is not a digital signature.\n",
            p.name,p.deployment_id,account,region,prefix,p.budget.authorized_usd,p.created_at_unix,p.expires_at_unix);
        let permissions = json!({"schemaVersion":1,"format":"ows.permission-requirements.v1","deploymentId":p.deployment_id,"target":p.target,"resourcePrefix":prefix,
          "setup":{"suggestedRoleName":format!("{prefix}-setup"),"trust":"explicit operator principal required","temporaryCredentials":true,"operations":["manage owned CloudFormation infrastructure","configure scoped EKS access and platform dependencies","install namespace-bound Helm releases"],"policyReady":false},
          "workload":{"suggestedRoleName":format!("{prefix}-artifacts"),"identity":"EKS Pod Identity","operations":["access only the deployment artifact bucket/prefix if enabled"],"sharedWithAgents":false,"policyReady":false},
          "constraints":["no AdministratorAccess grant","no arbitrary IAM PassRole","no setup credentials in pods or agents","no access to unrelated resource scopes"],
          "requiresCloudValidation":true});
        Ok(BTreeMap::from([
            ("deployment-plan.json".into(), json_bytes(p)?),
            ("REVIEW.md".into(), review.into_bytes()),
            ("permissions-request.json".into(), json_bytes(&permissions)?),
        ]))
    }
}
fn artifacts(plan: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
    let mut files = match &plan.target {
        Target::Aws { .. } => AwsPlanner.render(plan)?,
        _ => other_cloud_artifacts(plan)?,
    };
    if plan.schema_version >= 2 {
        files.insert(
            "resource-graph.json".into(),
            json_bytes(&resource_graph(plan))?,
        );
    }
    if plan.infrastructure.is_some() {
        files.extend(infrastructure::render(plan)?);
    } else if plan.bootstrap.is_some() {
        files.extend(bootstrap::render(plan)?);
        let review = files.get_mut("REVIEW.md").unwrap();
        *review = String::from_utf8(review.clone())?.replace(
            "No bootstrap.sh is emitted until its permissions and operations are implemented.",
            "Identity-only bootstrap artifacts are included. Read BOOTSTRAP.md before use. The existing execution role and complete infrastructure deployment are NOT generated or qualified here.").into_bytes();
    }
    Ok(files)
}
fn write_bundle(root: &Path, p: &Plan) -> Result<Manifest> {
    let files = artifacts(p)?;
    let hashes: BTreeMap<_, _> = files.iter().map(|(n, b)| (n.clone(), hash(b))).collect();
    let manifest = Manifest {
        schema_version: 1,
        deployment_id: p.deployment_id.clone(),
        bundle_digest: hash(&serde_json::to_vec(&hashes)?),
        artifacts: hashes,
    };
    let mut dir = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        dir.mode(0o700);
    }
    dir.create(root)
        .context("Output directory must not exist; choose a new plan directory")?;
    for (name, bytes) in files.into_iter().chain(std::iter::once((
        "manifest.json".into(),
        json_bytes(&manifest)?,
    ))) {
        let mut opts = fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut file = opts.open(root.join(name))?;
        file.write_all(&bytes)?;
        file.sync_all()?;
    }
    Ok(manifest)
}
fn read_file(root: &Path, name: &str) -> Result<Vec<u8>> {
    let path = root.join(name);
    let meta = fs::symlink_metadata(&path).context("Plan artifact missing")?;
    ensure!(
        meta.is_file() && !meta.file_type().is_symlink() && meta.len() <= 1_048_576,
        "Plan artifacts must be bounded regular files, not links"
    );
    let mut data = vec![];
    fs::File::open(path)?
        .take(1_048_577)
        .read_to_end(&mut data)?;
    ensure!(data.len() <= 1_048_576, "Plan artifact too large");
    Ok(data)
}
fn inspect(root: &Path) -> Result<(Plan, Manifest)> {
    let meta = fs::symlink_metadata(root).context("Plan directory missing")?;
    ensure!(
        meta.is_dir() && !meta.file_type().is_symlink(),
        "Plan root must be a directory, not a link"
    );
    let m: Manifest = serde_json::from_slice(&read_file(root, "manifest.json")?)
        .context("Invalid plan manifest")?;
    ensure!(m.schema_version == 1, "Unsupported manifest schema");
    let p: Plan = serde_json::from_slice(&read_file(root, "deployment-plan.json")?)
        .context("Invalid deployment plan")?;
    p.validate()?;
    ensure!(
        p.deployment_id == m.deployment_id,
        "Deployment identity mismatch"
    );
    let expected = artifacts(&p)?;
    let entries = fs::read_dir(root)?
        .take(expected.len() + 2)
        .collect::<std::io::Result<Vec<_>>>()?;
    ensure!(
        entries.len() == expected.len() + 1,
        "Unexpected or missing plan artifacts"
    );
    let hashes: BTreeMap<_, _> = expected.iter().map(|(n, b)| (n.clone(), hash(b))).collect();
    ensure!(
        hashes == m.artifacts && hash(&serde_json::to_vec(&hashes)?) == m.bundle_digest,
        "Plan manifest/content mismatch; regenerate and review a new bundle"
    );
    for (name, bytes) in expected {
        ensure!(
            read_file(root, &name)? == bytes,
            "Plan artifact changed: {name}"
        );
    }
    Ok((p, m))
}
fn result(p: &Plan, m: &Manifest) -> Value {
    json!({"deploymentId":p.deployment_id,"target":p.target,"bundleDigest":m.bundle_digest,"state":"planned","cloudObservation":"not_checked","applyReady":false,"expired":now().map(|n|n>=p.expires_at_unix).unwrap_or(true),"budget":p.budget,"expiresAtUnix":p.expires_at_unix,
        "identityBootstrap": if p.infrastructure.is_some() { "exported_generated_execution_roles" } else if p.bootstrap.is_some() { "exported_existing_execution_role_required" } else { "not_generated" }})
}
pub async fn dispatch(command: &Command) -> Result<()> {
    if let Command::Cluster { command } = command {
        return local::run_cluster(command).await;
    }
    if let Command::Aws {
        command: AwsCommand::Lifecycle { command },
    } = command
    {
        return cloud::run(command).await;
    }
    if let Command::Local { command } = command {
        return local::run(command).await;
    }
    if let Command::Aws {
        command: AwsCommand::Preflight(options),
    } = command
    {
        return preflight::run(options).await;
    }
    run(command)
}
pub fn run(command: &Command) -> Result<()> {
    match command {
        Command::Aws {
            command: AwsCommand::Lifecycle { .. },
        } => bail!("Cloud lifecycle requires asynchronous dispatch"),
        Command::Guard { command } => guard::run(command),
        Command::Local { .. } => bail!("Local installer requires asynchronous dispatch"),
        Command::Cluster { .. } => bail!("Cluster installer requires asynchronous dispatch"),
        Command::Aws {
            command: AwsCommand::Preflight(_),
        } => bail!("Preflight requires asynchronous dispatch"),
        Command::Aws {
            command: AwsCommand::Plan(a),
        } => create(
            &a.common,
            Target::Aws {
                account: a.account.clone(),
                region: a.region.clone(),
            },
            match (&a.operator_arn, &a.execution_role_arn) {
                (Some(operator), None) if a.infrastructure_config.is_some() => {
                    Some(bootstrap::AwsIdentity {
                        operator_arn: operator.clone(),
                        execution_role_arn: String::new(),
                    })
                }
                (Some(operator), Some(execution)) => Some(bootstrap::AwsIdentity {
                    operator_arn: operator.clone(),
                    execution_role_arn: execution.clone(),
                }),
                (None, None) => None,
                _ => bail!(
                    "Operator requires either an existing execution role or infrastructure config"
                ),
            },
            a.infrastructure_config
                .as_ref()
                .map(|path| {
                    let parent = path
                        .parent()
                        .filter(|x| !x.as_os_str().is_empty())
                        .unwrap_or(Path::new("."));
                    let file = path
                        .file_name()
                        .and_then(|x| x.to_str())
                        .context("Invalid infrastructure config filename")?;
                    serde_json::from_slice(&read_file(parent, file)?)
                        .context("Invalid infrastructure config")
                })
                .transpose()?,
        ),
        Command::Gcp {
            command: GcpCommand::Plan(a),
        } => create(
            &a.common,
            Target::Gcp {
                project: a.project.clone(),
                location: a.location.clone(),
            },
            None,
            None,
        ),
        Command::Azure {
            command: AzureCommand::Plan(a),
        } => create(
            &a.common,
            Target::Azure {
                tenant: a.tenant.clone(),
                subscription: a.subscription.clone(),
                location: a.location.clone(),
            },
            None,
            None,
        ),
        Command::Inspect { plan } | Command::Status { plan } => {
            let (p, m) = inspect(plan)?;
            crate::output(result(&p, &m))
        }
        Command::Validate { plan } => {
            let (p, m) = inspect(plan)?;
            let n = now()?;
            ensure!(
                p.created_at_unix <= n && n < p.expires_at_unix,
                "Plan is expired or future-dated; inspect remains available"
            );
            crate::output(
                json!({"valid":true,"validation":"offline_only","applyReady":false,"bundleDigest":m.bundle_digest}),
            )
        }
        Command::Apply { plan, accept_plan } => {
            let (p, m) = inspect(plan)?;
            ensure!(
                *accept_plan == m.bundle_digest,
                "Accepted digest does not match reviewed plan"
            );
            let n = now()?;
            ensure!(
                p.created_at_unix <= n && n < p.expires_at_unix,
                "Plan expired or future-dated"
            );
            bail!("Cloud apply is not implemented/qualified in this milestone; no cloud calls or changes made")
        }
        Command::Destroy {
            plan,
            deployment_id,
        } => {
            let (p, _) = inspect(plan)?;
            ensure!(
                *deployment_id == p.deployment_id,
                "Deployment identity mismatch"
            );
            bail!("Cloud destroy is not implemented/qualified in this milestone; no cloud calls or changes made")
        }
    }
}

fn create(
    a: &PlanOptions,
    target: Target,
    bootstrap: Option<bootstrap::AwsIdentity>,
    infrastructure: Option<infrastructure::Inputs>,
) -> Result<()> {
    ensure!((1..=72).contains(&a.ttl_hours), "TTL must be 1–72 hours");
    let created = now()?;
    let architecture = if matches!(target, Target::Aws { .. }) {
        "arm64"
    } else {
        "amd64"
    };
    let mut p = Plan {
        schema_version: if infrastructure.is_some() { 3 } else { 2 },
        deployment_id: format!("ows-{}", uuid::Uuid::new_v4().simple()),
        name: a.name.clone(),
        target,
        bootstrap,
        infrastructure,
        created_at_unix: created,
        expires_at_unix: created
            .checked_add(u64::from(a.ttl_hours) * 3600)
            .context("Expiry overflow")?,
        budget: Budget {
            authorized_usd: a.budget,
            hard_cap: false,
            estimate: None,
        },
        topology: Topology {
            cluster_mode: "new".into(),
            node_count: 2,
            max_worker_replicas: 2,
            architecture: architecture.into(),
            access: "private".into(),
            node_autoscaling: false,
        },
    };
    if p.infrastructure.is_some() {
        let Target::Aws { account, .. } = &p.target else {
            bail!("Infrastructure export requires AWS");
        };
        let arn = format!("arn:aws:iam::{account}:role/{}-executor", p.prefix());
        p.bootstrap
            .as_mut()
            .context("Infrastructure requires operator")?
            .execution_role_arn = arn;
    }
    let m = write_bundle(&a.out, &p)?;
    crate::output(result(&p, &m))
}

/// Logical resource/authority boundaries. Not a resolved infrastructure template.
fn resource_graph(p: &Plan) -> Value {
    let (cluster, database, registry, artifacts, identity, engine) = match p.target {
        Target::Aws { .. } => (
            "EKS",
            "RDS PostgreSQL",
            "ECR",
            "S3",
            "EKS Pod Identity",
            "CloudFormation",
        ),
        Target::Gcp { .. } => (
            "GKE Standard",
            "Cloud SQL PostgreSQL",
            "Artifact Registry",
            "Cloud Storage",
            "Workload Identity Federation for GKE",
            "Infrastructure Manager / Terraform",
        ),
        Target::Azure { .. } => (
            "AKS",
            "Azure Database for PostgreSQL Flexible Server",
            "ACR",
            "Blob Storage",
            "Microsoft Entra Workload ID",
            "Bicep / ARM",
        ),
    };
    json!({"schemaVersion":1,"resolved":false,"deploymentId":p.deployment_id,"engine":engine,
        "nodes":[
            {"id":"bootstrap","authority":"customer administrator","dependsOn":[]},
            {"id":"expiry-guard","authority":"dedicated cleanup identity","dependsOn":["bootstrap"]},
            {"id":"network","authority":"infrastructure executor","dependsOn":["expiry-guard"]},
            {"id":"cluster","type":cluster,"authority":"infrastructure executor","dependsOn":["network"]},
            {"id":"database","type":database,"authority":"infrastructure executor","dependsOn":["network"]},
            {"id":"registry","type":registry,"authority":"infrastructure executor","dependsOn":["expiry-guard"]},
            {"id":"artifacts","type":artifacts,"authority":"infrastructure executor","dependsOn":["expiry-guard"]},
            {"id":"workload-identity","type":identity,"authority":"customer bootstrap + exact workload binding","dependsOn":["cluster","artifacts"]},
            {"id":"openshell","type":"Helm platform installation","authority":"scoped platform installer","dependsOn":["cluster","registry"]},
            {"id":"service","type":"Helm service/UI installation","authority":"namespace installer","dependsOn":["openshell","database","workload-identity"]}
        ],"unresolved":["cloud IAM and RBAC qualification","network connectivity to private APIs","image digests and supported versions","quotas and verified cost","independent expiry guard","storage and credential references"]})
}
fn other_cloud_artifacts(p: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
    p.validate()?;
    let (provider, scope, engine, setup, workload) = match &p.target {
        Target::Gcp {project, location} => ("GCP", format!("project {project}; region {location}"), "Infrastructure Manager / Terraform", "Impersonated setup service account; separate Terraform executor service account", "Workload Identity Federation for GKE; bucket-scoped artifact principal"),
        Target::Azure {tenant, subscription, location} => ("Azure", format!("tenant {tenant}; subscription {subscription}; location {location}"), "Bicep / ARM", "Federated deployment principal scoped to a dedicated resource group", "AKS OIDC federation to a dedicated managed identity; container-scoped Blob data access"),
        _ => bail!("Expected GCP or Azure"),
    };
    let review = format!("# {} deployment review\n\nDeployment: `{}`\n{provider}: {scope}\nResource prefix: `{}`\n\nInfrastructure: {engine}. Two proposed AMD64 nodes; at most two worker replicas.\nPrivate service access; no node autoscaler. PostgreSQL, artifact storage and OpenShell.\n\nBudget authorization: ${} USD. No verified estimate; not a spending cap.\nCreated: {}. Expires: {} (Unix seconds).\n\nSetup: {setup}.\nWorkloads: {workload}.\n\nThis is an offline plan, not a runnable installer. No cloud calls or resource changes.\nThe resource graph and permissions request describe intent, not grantable policy.\nBootstrap and deployment executors for this provider are not implemented.\nResolve resource scopes, IAM, images, versions, quotas, cost and expiry guard before apply.\nThe bundle digest detects changes; it is not a signature.\n",p.name,p.deployment_id,p.prefix(),p.budget.authorized_usd,p.created_at_unix,p.expires_at_unix);
    let permissions = json!({"schemaVersion":1,"format":"ows.permission-requirements.v1","deploymentId":p.deployment_id,"target":p.target,"resourcePrefix":p.prefix(),"setup":{"identity":setup,"policyReady":false},"workload":{"identity":workload,"policyReady":false,"sharedWithAgents":false},"constraints":["no project-wide Owner/Editor or subscription-wide Owner grants","no static service-account keys or client secrets","workload identity separate from installer authority","private cluster access requires an explicit operator network path"],"requiresCloudValidation":true});
    Ok(BTreeMap::from([
        ("deployment-plan.json".into(), json_bytes(p)?),
        ("REVIEW.md".into(), review.into_bytes()),
        ("permissions-request.json".into(), json_bytes(&permissions)?),
    ]))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn plan() -> Plan {
        Plan {
            schema_version: 1,
            deployment_id: "ows-0123456789abcdef0123456789abcdef".into(),
            name: "test-service".into(),
            target: Target::Aws {
                account: "123456789012".into(),
                region: "us-west-2".into(),
            },
            created_at_unix: 100,
            expires_at_unix: 200,
            budget: Budget {
                authorized_usd: 100,
                hard_cap: false,
                estimate: None,
            },
            bootstrap: None,
            infrastructure: None,
            topology: Topology {
                cluster_mode: "new".into(),
                node_count: 2,
                max_worker_replicas: 2,
                architecture: "arm64".into(),
                access: "private".into(),
                node_autoscaling: false,
            },
        }
    }
    #[test]
    fn validation_rejects_widened_scope_and_false_cost_claims() {
        let mut p = plan();
        p.budget.hard_cap = true;
        assert!(p.validate().is_err());
        p = plan();
        p.topology.node_count = 200;
        assert!(p.validate().is_err());
        p = plan();
        p.expires_at_unix = u64::MAX;
        assert!(p.validate().is_err());
        p = plan();
        p.target = Target::Gcp {
            project: "p".into(),
            location: "l".into(),
        };
        assert!(p.validate().is_err());
        assert!(!name_ok("../bad"));
        assert!(!name_ok("bad;cmd"));
        assert!(!region_ok("us-gov-west-1"));
    }
    #[test]
    fn content_and_manifest_tampering_fail_closed() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("plan");
        let p = plan();
        write_bundle(&root, &p).unwrap();
        assert_eq!(inspect(&root).unwrap().0, p);
        fs::write(root.join("REVIEW.md"), "edited").unwrap();
        assert!(inspect(&root).is_err());
    }
    #[test]
    fn expired_bundle_still_inspectable_and_not_overwritten() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("plan");
        write_bundle(&root, &plan()).unwrap();
        assert!(inspect(&root).is_ok());
        assert!(run(&Command::Validate { plan: root.clone() }).is_err());
        assert!(write_bundle(&root, &plan()).is_err());
        fs::write(root.join("unexpected"), "x").unwrap();
        assert!(inspect(&root).is_err());
    }
    #[cfg(unix)]
    #[test]
    fn links_and_unsafe_permissions_are_not_created() {
        use std::os::unix::{fs::symlink, fs::PermissionsExt};
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("plan");
        write_bundle(&root, &plan()).unwrap();
        assert_eq!(
            fs::metadata(&root).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(root.join("manifest.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        fs::remove_file(root.join("REVIEW.md")).unwrap();
        symlink("manifest.json", root.join("REVIEW.md")).unwrap();
        assert!(inspect(&root).is_err());
        let link = dir.path().join("linked");
        symlink(&root, &link).unwrap();
        assert!(inspect(&link).is_err());
    }
}
