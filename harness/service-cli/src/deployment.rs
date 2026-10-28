//! Offline deployment planning. Deliberately no cloud SDK, process execution,
//! ambient credentials, or service-client authentication in this module.
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
    Aws {
        #[command(subcommand)]
        command: AwsCommand,
    },
    /// Reserved provider boundary; not implemented yet.
    Gcp {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        arguments: Vec<String>,
    },
    /// Reserved provider boundary; not implemented yet.
    Azure {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        arguments: Vec<String>,
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
}
#[derive(Args)]
pub struct AwsPlan {
    #[arg(long)]
    account: String,
    #[arg(long)]
    region: String,
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
        ensure!(self.schema_version == 1, "Unsupported plan schema");
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
            _ => bail!("Provider adapter is not implemented; no changes made"),
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
                    architecture: "arm64".into(),
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
    match &plan.target {
        Target::Aws { .. } => AwsPlanner.render(plan),
        _ => bail!("Provider adapter is not implemented"),
    }
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
    let entries = fs::read_dir(root)?
        .take(5)
        .collect::<std::io::Result<Vec<_>>>()?;
    ensure!(entries.len() == 4, "Unexpected or missing plan artifacts");
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
    json!({"deploymentId":p.deployment_id,"target":p.target,"bundleDigest":m.bundle_digest,"state":"planned","cloudObservation":"not_checked","applyReady":false,"expired":now().map(|n|n>=p.expires_at_unix).unwrap_or(true),"budget":p.budget,"expiresAtUnix":p.expires_at_unix})
}
pub fn run(command: &Command) -> Result<()> {
    match command {
        Command::Aws {
            command: AwsCommand::Plan(a),
        } => {
            ensure!((1..=72).contains(&a.ttl_hours), "TTL must be 1–72 hours");
            let created = now()?;
            let p = Plan {
                schema_version: 1,
                deployment_id: format!("ows-{}", uuid::Uuid::new_v4().simple()),
                name: a.name.clone(),
                target: Target::Aws {
                    account: a.account.clone(),
                    region: a.region.clone(),
                },
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
                    architecture: "arm64".into(),
                    access: "private".into(),
                    node_autoscaling: false,
                },
            };
            let manifest = write_bundle(&a.out, &p)?;
            crate::output(result(&p, &manifest))
        }
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
            ensure!(now()? < p.expires_at_unix, "Plan expired");
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
        Command::Gcp { .. } | Command::Azure { .. } => {
            bail!("Provider adapter is not implemented; no cloud calls or changes made")
        }
    }
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
