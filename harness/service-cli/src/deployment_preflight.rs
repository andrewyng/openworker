//! Explicit read-only AWS observation. No generic command forwarding, mutation,
//! policy installation, secret retrieval, shell invocation or automatic retries.
use super::*;
use std::{
    process::Stdio,
    time::{Duration, Instant},
};
use tokio::{io::AsyncReadExt, process::Command as Process};
use zeroize::{Zeroize, Zeroizing};
#[path = "deployment_preflight_checks.rs"]
mod checks;
#[derive(Args)]
pub struct Options {
    #[arg(long)]
    plan: PathBuf,
    /// Fresh output directory, outside the immutable plan bundle.
    #[arg(long)]
    out: PathBuf,
    #[arg(long)]
    profile: Option<String>,
    /// Deadline per AWS operation, including output collection.
    #[arg(long, default_value_t = 20)]
    timeout_seconds: u64,
    #[arg(long, default_value_t = 300)]
    overall_timeout_seconds: u64,
    /// Planning reserve for metered/variable charges; never a spending ceiling.
    #[arg(long, default_value_t = 25)]
    variable_allowance_usd: u32,
    /// After bootstrap, verify exact setup-role assumption with a read-only session policy.
    #[arg(long)]
    check_setup_role: bool,
}
#[derive(Clone, Copy, Debug)]
enum Failure {
    AccessDenied,
    NotFound,
    Timeout,
    OutputLimit,
    InvalidResponse,
    Unavailable,
    RequestFailed,
    IdentityMismatch,
}
impl Failure {
    fn code(self) -> &'static str {
        match self {
            Self::AccessDenied => "access_denied",
            Self::NotFound => "not_found",
            Self::Timeout => "timeout",
            Self::OutputLimit => "output_limit",
            Self::InvalidResponse => "invalid_response",
            Self::Unavailable => "cli_unavailable",
            Self::RequestFailed => "request_failed",
            Self::IdentityMismatch => "identity_mismatch",
        }
    }
}
type Observed<T> = std::result::Result<T, Failure>;
struct Credentials {
    access_key: String,
    secret_key: String,
    token: String,
}
impl Drop for Credentials {
    fn drop(&mut self) {
        self.access_key.zeroize();
        self.secret_key.zeroize();
        self.token.zeroize();
    }
}
struct Aws {
    region: String,
    profile: Option<String>,
    timeout: Duration,
    deadline: Instant,
    credentials: Option<Credentials>,
    calls: u32,
}
impl Aws {
    async fn bytes(
        &mut self,
        service: &str,
        operation: &str,
        args: Vec<String>,
        region: Option<&str>,
    ) -> Observed<Zeroizing<Vec<u8>>> {
        // Keep the boundary narrow even if a future check accidentally requests a
        // mutating API. STS AssumeRole only creates temporary credentials; its
        // session policy permits GetCallerIdentity and no cloud mutations.
        let allowed = matches!(
            (service, operation),
            ("sts", "get-caller-identity" | "assume-role")
                | ("iam", "get-role")
                | (
                    "eks",
                    "describe-cluster-versions" | "describe-addon-versions" | "list-clusters"
                )
                | (
                    "ec2",
                    "describe-availability-zones"
                        | "describe-instance-type-offerings"
                        | "describe-instance-types"
                        | "describe-instances"
                        | "describe-capacity-reservations"
                        | "describe-images"
                        | "describe-vpcs"
                        | "describe-addresses"
                        | "describe-nat-gateways"
                )
                | (
                    "rds",
                    "describe-db-engine-versions"
                        | "describe-orderable-db-instance-options"
                        | "describe-account-attributes"
                )
                | ("service-quotas", "get-service-quota")
                | ("ssm", "get-parameter")
                | ("pricing", "get-products")
                | (
                    "s3api",
                    "get-bucket-location" | "head-object" | "get-object"
                )
                | ("accessanalyzer", "validate-policy")
        );
        if !allowed {
            return Err(Failure::RequestFailed);
        }
        let remaining = self.deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(Failure::Timeout);
        }
        let mut cmd = Process::new("aws");
        cmd.args([service, operation])
            .args(args)
            .args([
                "--region",
                region.unwrap_or(&self.region),
                "--output",
                "json",
                "--no-cli-pager",
                "--no-cli-auto-prompt",
                "--no-paginate",
                "--cli-connect-timeout",
                "5",
                "--cli-read-timeout",
                "15",
            ])
            .env("AWS_PAGER", "")
            .env("AWS_CLI_AUTO_PROMPT", "off")
            .env("AWS_MAX_ATTEMPTS", "1")
            .env("AWS_EC2_METADATA_DISABLED", "true")
            .env("AWS_IGNORE_CONFIGURED_ENDPOINT_URLS", "true")
            .env_remove("AWS_ENDPOINT_URL")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::null())
            .kill_on_drop(true);
        if let Some(c) = &self.credentials {
            cmd.env_remove("AWS_PROFILE")
                .env_remove("AWS_DEFAULT_PROFILE")
                .env("AWS_ACCESS_KEY_ID", &c.access_key)
                .env("AWS_SECRET_ACCESS_KEY", &c.secret_key)
                .env("AWS_SESSION_TOKEN", &c.token);
        } else if let Some(profile) = &self.profile {
            cmd.args(["--profile", profile]);
        }
        let mut child = cmd.spawn().map_err(|_| Failure::Unavailable)?;
        self.calls += 1;
        let stdout = child.stdout.take().ok_or(Failure::Unavailable)?;
        let stderr = child.stderr.take().ok_or(Failure::Unavailable)?;
        async fn read<R: tokio::io::AsyncRead + Unpin>(
            stream: R,
            max: u64,
        ) -> std::io::Result<Zeroizing<Vec<u8>>> {
            let mut v = Zeroizing::new(vec![]);
            stream.take(max + 1).read_to_end(&mut v).await?;
            Ok(v)
        }
        let operation = async {
            tokio::try_join!(
                child.wait(),
                read(stdout, 4 * 1024 * 1024),
                read(stderr, 65536)
            )
        };
        let captured = tokio::time::timeout(self.timeout.min(remaining), operation).await;
        let (status, stdout, stderr) = match captured {
            Ok(Ok(v)) => v,
            Ok(Err(_)) => {
                let _ = child.kill().await;
                return Err(Failure::Unavailable);
            }
            Err(_) => {
                let _ = child.kill().await;
                let _ = child.wait().await;
                return Err(Failure::Timeout);
            }
        };
        if stdout.len() > 4 * 1024 * 1024 || stderr.len() > 65536 {
            return Err(Failure::OutputLimit);
        }
        if !status.success() {
            // Never include provider stderr/stdout: credential helpers and proxies
            // may put secrets there. Only map known error classes to fixed codes.
            let error = String::from_utf8_lossy(&stderr);
            return Err(
                if [
                    "AccessDenied",
                    "UnauthorizedOperation",
                    "ExpiredToken",
                    "InvalidClientTokenId",
                    "UnrecognizedClientException",
                ]
                .iter()
                .any(|c| error.contains(c))
                {
                    Failure::AccessDenied
                } else if [
                    "NoSuchEntity",
                    "NoSuchKey",
                    "NoSuchVersion",
                    "NotFound",
                    "ResourceNotFound",
                ]
                .iter()
                .any(|c| error.contains(c))
                {
                    Failure::NotFound
                } else {
                    Failure::RequestFailed
                },
            );
        }
        Ok(stdout)
    }
    async fn json(&mut self, s: &str, o: &str, args: Vec<String>) -> Observed<Value> {
        let bytes = self.bytes(s, o, args, None).await?;
        serde_json::from_slice(&bytes).map_err(|_| Failure::InvalidResponse)
    }
    async fn pages(
        &mut self,
        s: &str,
        o: &str,
        args: Vec<String>,
        field: &str,
    ) -> Observed<Vec<Value>> {
        let mut rows = vec![];
        let mut next: Option<String> = None;
        let mut seen = std::collections::BTreeSet::new();
        for _ in 0..20 {
            let mut a = args.clone();
            if let Some(t) = next.take() {
                let key = if s == "rds" {
                    "Marker"
                } else if s == "eks" || s == "accessanalyzer" {
                    "nextToken"
                } else {
                    "NextToken"
                };
                a.extend(["--cli-input-json".into(), json!({key:t}).to_string()]);
            }
            let page = self.json(s, o, a).await?;
            rows.extend(
                page.get(field)
                    .and_then(Value::as_array)
                    .ok_or(Failure::InvalidResponse)?
                    .iter()
                    .cloned(),
            );
            if rows.len() > 10000 {
                return Err(Failure::OutputLimit);
            }
            let token = page
                .get("NextToken")
                .or_else(|| page.get("nextToken"))
                .or_else(|| page.get("Marker"));
            match token {
                None | Some(Value::Null) => return Ok(rows),
                Some(Value::String(t)) if t.is_empty() => return Ok(rows),
                Some(Value::String(t)) if t.len() <= 8192 && seen.insert(t.clone()) => {
                    next = Some(t.clone())
                }
                _ => return Err(Failure::InvalidResponse),
            }
        }
        Err(Failure::OutputLimit)
    }
}
fn args(values: &[&str]) -> Vec<String> {
    values.iter().map(|s| s.to_string()).collect()
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Check {
    name: String,
    status: &'static str,
    required: bool,
    detail: Value,
}
fn record(checks: &mut Vec<Check>, name: &str, result: Observed<Value>) {
    checks.push(match result {
        Ok(detail) => Check {
            name: name.into(),
            status: if detail.get("blockingReason").is_some() {
                "blocked"
            } else {
                "passed"
            },
            required: true,
            detail,
        },
        Err(e) => Check {
            name: name.into(),
            status: "blocked",
            required: true,
            detail: json!({"reason":e.code()}),
        },
    });
}
fn valid_identity(p: &Plan, identity: &Value, setup: bool) -> bool {
    let Target::Aws { account, .. } = &p.target else {
        return false;
    };
    let Some(arn) = identity.get("Arn").and_then(Value::as_str) else {
        return false;
    };
    if identity.get("Account").and_then(Value::as_str) != Some(account) {
        return false;
    }
    let Some(b) = &p.bootstrap else { return false };
    let principal = if setup {
        format!("arn:aws:iam::{account}:role/{}-setup", p.prefix())
    } else {
        b.operator_arn.clone()
    };
    if let Some(role) = principal.strip_prefix(&format!("arn:aws:iam::{account}:role/")) {
        let prefix = format!(
            "arn:aws:sts::{account}:assumed-role/{}/",
            role.rsplit('/').next().unwrap()
        );
        arn.strip_prefix(&prefix)
            .map(|s| !s.is_empty() && !s.contains('/'))
            .unwrap_or(false)
    } else {
        arn == principal
    }
}
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}
fn fresh_directory(path: &Path) -> Result<()> {
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder
        .create(path)
        .context("Preflight output directory must not already exist; choose a fresh path")?;
    Ok(())
}
fn write_report(root: &Path, report: &Value) -> Result<()> {
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut f = options.open(root.join("preflight.json"))?;
    f.write_all(&json_bytes(report)?)?;
    f.sync_all()?;
    Ok(())
}
pub(super) async fn run(options: &Options) -> Result<()> {
    ensure!(
        (1..=60).contains(&options.timeout_seconds)
            && (1..=600).contains(&options.overall_timeout_seconds),
        "Preflight deadlines must be 1–60 seconds per call and 1–600 seconds overall"
    );
    ensure!(
        options.variable_allowance_usd <= 100000,
        "Variable-cost reserve too large"
    );
    if let Some(profile) = &options.profile {
        ensure!(
            !profile.is_empty()
                && profile.len() <= 128
                && !profile.starts_with('-')
                && profile
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-.@".contains(&b)),
            "Invalid AWS profile name"
        );
    }
    let (p, m) = inspect(&options.plan)?;
    let at = now()?;
    ensure!(
        p.schema_version == 3 && p.infrastructure.is_some(),
        "AWS preflight requires a schema-3 infrastructure bundle"
    );
    ensure!(
        p.created_at_unix <= at && at < p.expires_at_unix,
        "Plan expired or future-dated"
    );
    let Target::Aws { account, region } = &p.target else {
        bail!("AWS plan required")
    };
    // Creating the report inside the immutable bundle would corrupt its integrity.
    let output_parent = options
        .out
        .parent()
        .filter(|x| !x.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    let plan_root = options.plan.canonicalize()?;
    ensure!(
        !output_parent.starts_with(&plan_root),
        "Preflight output must be outside the immutable plan bundle"
    );
    fresh_directory(&options.out)?;
    let mut aws = Aws {
        region: region.clone(),
        profile: options.profile.clone(),
        timeout: Duration::from_secs(options.timeout_seconds),
        deadline: Instant::now() + Duration::from_secs(options.overall_timeout_seconds),
        credentials: None,
        calls: 0,
    };
    let mut results = vec![];
    let identity = aws.json("sts", "get-caller-identity", vec![]).await;
    let identity_ok = identity
        .as_ref()
        .map(|v| valid_identity(&p, v, false))
        .unwrap_or(false);
    record(
        &mut results,
        "operator_identity",
        identity.and_then(|v| {
            if valid_identity(&p, &v, false) {
                Ok(json!({"account":account,"operator":p.bootstrap.as_ref().unwrap().operator_arn}))
            } else {
                Err(Failure::IdentityMismatch)
            }
        }),
    );
    if identity_ok {
        checks::all(
            &mut aws,
            &p,
            &options.plan,
            &options.out,
            options.variable_allowance_usd,
            options.check_setup_role,
            &mut results,
        )
        .await;
    }
    let finished = now()?;
    results.push(Check {
        name: "observation_freshness".into(),
        status: if finished < p.expires_at_unix && finished < at + 900 {
            "passed"
        } else {
            "blocked"
        },
        required: true,
        detail: json!({"finishedAtUnix":finished,"planExpiresAtUnix":p.expires_at_unix}),
    });
    let complete = identity_ok
        && results
            .iter()
            .filter(|c| c.required)
            .all(|c| c.status == "passed");
    let report = json!({"schemaVersion":1,"deploymentId":p.deployment_id,"bundleDigest":m.bundle_digest,"target":p.target,"observedAtUnix":at,"validUntilUnix":p.expires_at_unix.min(at+900),"checksPassed":complete,"applyReady":false,"mode":"read_only","awsCliInvocations":aws.calls,"checks":results,
        "remainingGates":["preflight observations are not reservations or a hard spending cap","IAM validation is not proof of effective provisioning permissions","qualify guard package behavior and independent cleanup","pin runtime images and verify private Kubernetes connectivity","implement durable lifecycle and platform installation","live provisioning remains disabled"],"identityMismatchStoppedFurtherCalls":!identity_ok});
    write_report(&options.out, &report)?;
    crate::output(report)?;
    ensure!(
        complete,
        "Preflight has blocked checks; inspect the fresh preflight.json report"
    );
    Ok(())
}
