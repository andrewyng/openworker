//! Experimental cloud coordinator: explicit profile/opt-in, bounded calls, no retries.
use super::*;
use base64::Engine;
use std::{process::Stdio, time::Duration};
use tokio::{io::AsyncReadExt, process::Command as Process};
use zeroize::Zeroizing;
#[path = "deployment_cloud_images.rs"]
mod images;
#[path = "deployment_cloud_install.rs"]
mod install;
#[path = "deployment_platform_artifact.rs"]
mod platform_artifact;
#[path = "deployment_cloud_state.rs"]
mod state;
pub(super) use install::{
    database_url, enable_strict_network, install_scope, installation_intent, verify_installation,
    verify_network, Binding,
};
use state::{Journal, Provider, Stack};

#[derive(Subcommand)]
pub enum Command {
    PlatformArtifact {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, requires = "openshell_release")]
        openshell_namespace: Option<String>,
        #[arg(long, requires = "openshell_namespace")]
        openshell_release: Option<String>,
    },
    PublishImage {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        profile: String,
        #[arg(long)]
        role: String,
        #[arg(long)]
        source: String,
        #[arg(long)]
        allow_experimental_cloud: bool,
        #[arg(long)]
        confirm_no_effect: bool,
    },
    Installation {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        profile: String,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    Prepare {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    Apply {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        accept_plan: String,
        #[arg(long)]
        preflight: Option<PathBuf>,
        #[arg(long)]
        profile: String,
        #[arg(long)]
        allow_experimental_cloud: bool,
        #[arg(long)]
        confirm_no_effect: Option<String>,
    },
    Status {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        profile: String,
    },
    Destroy {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        deployment_id: String,
        #[arg(long)]
        profile: String,
        #[arg(long)]
        allow_experimental_cloud: bool,
        #[arg(long)]
        installation_state: Option<PathBuf>,
    },
}
struct Aws {
    p: Plan,
    profile: String,
    root: PathBuf,
    preflight: Option<PathBuf>,
}
impl Aws {
    async fn call(
        &self,
        service: &str,
        operation: &str,
        input: Value,
        outfile: Option<&Path>,
    ) -> Result<Option<Value>> {
        ensure!(
            matches!(
                (service, operation),
                ("sts", "get-caller-identity")
                    | (
                        "cloudformation",
                        "describe-stacks"
                            | "describe-stack-events"
                            | "get-template"
                            | "create-stack"
                            | "delete-stack"
                    )
                    | (
                        "lambda",
                        "get-function-configuration" | "get-function-concurrency" | "invoke"
                    )
                    | ("events", "describe-rule" | "list-targets-by-rule")
                    | (
                        "s3api",
                        "get-bucket-tagging"
                            | "get-bucket-versioning"
                            | "list-objects-v2"
                            | "list-multipart-uploads"
                    )
                    | (
                        "eks",
                        "describe-cluster" | "describe-addon" | "update-addon"
                    )
                    | ("secretsmanager", "get-secret-value")
                    | (
                        "ecr",
                        "describe-repositories" | "batch-get-image" | "get-authorization-token"
                    )
                    | ("ec2", "describe-volumes")
            ),
            "AWS lifecycle operation is not allowed"
        );
        let Target::Aws { region, .. } = &self.p.target else {
            bail!("AWS target required")
        };
        let mut c = Process::new("aws");
        c.args([
            service,
            operation,
            "--cli-input-json",
            &serde_json::to_string(&input)?,
            "--profile",
            &self.profile,
            "--region",
            region,
            "--output",
            "json",
            "--no-cli-pager",
            "--no-cli-auto-prompt",
            "--no-paginate",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "30",
            "--cli-binary-format",
            "base64",
        ]);
        if let Some(path) = outfile {
            c.arg(path);
        }
        c.env("AWS_MAX_ATTEMPTS", "1")
            .env("AWS_EC2_METADATA_DISABLED", "true")
            .env("AWS_IGNORE_CONFIGURED_ENDPOINT_URLS", "true")
            .env("AWS_PAGER", "")
            .env("AWS_CLI_AUTO_PROMPT", "off")
            .env_remove("AWS_ENDPOINT_URL")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let mut child = c.spawn().context("AWS CLI could not start")?;
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        async fn collect<R: tokio::io::AsyncRead + Unpin>(
            r: R,
            limit: u64,
        ) -> std::io::Result<Zeroizing<Vec<u8>>> {
            let mut bytes = Zeroizing::new(vec![]);
            r.take(limit + 1).read_to_end(&mut bytes).await?;
            Ok(bytes)
        }
        let task = async {
            tokio::try_join!(
                child.wait(),
                collect(stdout, 4 * 1024 * 1024),
                collect(stderr, 65536)
            )
        };
        let (status, out, err) = match tokio::time::timeout(Duration::from_secs(45), task).await {
            Ok(Ok(v)) => v,
            _ => {
                let _ = child.kill().await;
                bail!("AWS request timed out; mutation outcome may be unknown; inspect original intent")
            }
        };
        ensure!(
            out.len() <= 4 * 1024 * 1024 && err.len() <= 65536,
            "AWS response limit exceeded; inspect original intent"
        );
        if !status.success() {
            // Only this exact response means absence. AccessDenied/expired login do not.
            let message = String::from_utf8_lossy(&err);
            if service=="s3api"&&operation=="get-bucket-tagging"&&message.trim()=="An error occurred (NoSuchBucket) when calling the GetBucketTagging operation: The specified bucket does not exist"{return Ok(None)}
            if service == "cloudformation" && operation == "describe-stacks" {
                let selector = input["StackName"]
                    .as_str()
                    .context("Stack selector missing")?;
                if message.trim()==format!("An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id {selector} does not exist"){return Ok(None)}
            }
            bail!("AWS request failed; mutation outcome may be unknown; output withheld to protect credentials")
        }
        if out.iter().all(u8::is_ascii_whitespace) {
            return Ok(Some(json!({})));
        }
        Ok(Some(serde_json::from_slice(&out).context(
            "Invalid AWS response; inspect original intent",
        )?))
    }
    async fn json(&self, s: &str, o: &str, input: Value) -> Result<Value> {
        self.call(s, o, input, None)
            .await?
            .context("Unexpected absent AWS response")
    }
    async fn identity(&self) -> Result<()> {
        ensure!(
            !self.profile.is_empty()
                && self.profile.len() <= 128
                && !self.profile.starts_with('-')
                && self
                    .profile
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-.@".contains(&b)),
            "Explicit valid AWS setup profile required"
        );
        let Target::Aws { account, .. } = &self.p.target else {
            bail!("AWS target required")
        };
        let identity = self.json("sts", "get-caller-identity", json!({})).await?;
        let prefix = format!(
            "arn:aws:sts::{account}:assumed-role/{}-setup/",
            self.p.prefix()
        );
        ensure!(identity["Account"]==*account&&identity["Arn"].as_str().is_some_and(|s|s.strip_prefix(&prefix).is_some_and(|session|!session.is_empty()&&!session.contains('/'))),"Exact generated setup role/account required; configure an AWS role profile using your existing login");
        Ok(())
    }
    async fn bucket_empty(&self) -> Result<()> {
        let Target::Aws { account, .. } = &self.p.target else {
            unreachable!()
        };
        let bucket = format!("{}-{account}", self.p.prefix());
        let common = json!({"Bucket":bucket,"ExpectedBucketOwner":account});
        let Some(tagged) = self
            .call("s3api", "get-bucket-tagging", common.clone(), None)
            .await?
        else {
            return Ok(());
        };
        ensure!(
            tagged["TagSet"].as_array().is_some_and(|a| a
                .iter()
                .any(|t| t["Key"] == "ows:deployment-id" && t["Value"] == self.p.deployment_id)),
            "Artifact bucket ownership mismatch"
        );
        let v = self
            .json("s3api", "get-bucket-versioning", common.clone())
            .await?;
        ensure!(
            v.get("Status").is_none(),
            "Versioned artifact bucket requires operator cleanup"
        );
        let mut input = common.clone();
        input["MaxKeys"] = json!(1);
        let objects = self.json("s3api", "list-objects-v2", input).await?;
        ensure!(objects["KeyCount"]==0&&objects["IsTruncated"]==false&&objects.get("Contents").is_none_or(|a|a.as_array().is_some_and(Vec::is_empty)),"Artifact bucket is not provably empty; remove owned artifacts through the artifact interface first");
        let mut input = common;
        input["MaxUploads"] = json!(1);
        let uploads = self.json("s3api", "list-multipart-uploads", input).await?;
        ensure!(
            uploads["IsTruncated"] == false
                && uploads
                    .get("Uploads")
                    .is_none_or(|a| a.as_array().is_some_and(Vec::is_empty)),
            "Outstanding multipart uploads require cleanup"
        );
        Ok(())
    }
    fn root_plan(&self) -> Result<PathBuf> {
        let j: Journal = serde_json::from_slice(&read_file(&self.root, "cloud-journal.json")?)?;
        Ok(j.plan)
    }
}
impl Provider for Aws {
    async fn authorize_creation(&mut self) -> Result<()> {
        let j: Journal = serde_json::from_slice(&read_file(&self.root, "cloud-journal.json")?)?;
        fresh_preflight(
            self.preflight
                .as_deref()
                .context("Fresh --preflight report required before a new cloud create")?,
            &self.p,
            &j,
        )
    }
    async fn stack(&mut self, selector: &str) -> Result<Option<Stack>> {
        let Some(value) = self
            .call(
                "cloudformation",
                "describe-stacks",
                json!({"StackName":selector}),
                None,
            )
            .await?
        else {
            return Ok(None);
        };
        let rows = value["Stacks"]
            .as_array()
            .context("Invalid stack response")?;
        ensure!(rows.len() == 1, "Ambiguous stack response");
        let s = &rows[0];
        let text = |k: &str| -> Result<String> {
            let v = s[k].as_str().context("Stack field missing")?;
            ensure!(v.len() <= 4096, "Oversized stack field");
            Ok(v.into())
        };
        let arn = text("StackId")?;
        let Target::Aws { account, region } = &self.p.target else {
            unreachable!()
        };
        let stack_name = text("StackName")?;
        ensure!(
            ["guard", "infra"]
                .iter()
                .any(|kind| stack_name == format!("{}-{kind}", self.p.prefix()))
                && arn.starts_with(&format!(
                    "arn:aws:cloudformation:{region}:{account}:stack/{stack_name}/"
                )),
            "Returned stack is outside the reviewed scope"
        );
        ensure!(
            !selector.starts_with("arn:") || selector == arn,
            "Original immutable stack identity changed"
        );
        let template = self
            .json(
                "cloudformation",
                "get-template",
                json!({"StackName":arn,"TemplateStage":"Original"}),
            )
            .await?;
        let template = match &template["TemplateBody"] {
            Value::String(v) => serde_json::from_str(v).context("Invalid stack template")?,
            Value::Object(_) => template["TemplateBody"].clone(),
            _ => bail!("Missing original template"),
        };
        let mut tokens = vec![];
        let mut next = None;
        let mut seen = std::collections::BTreeSet::new();
        for page in 0..20 {
            let mut q = json!({"StackName":arn});
            if let Some(t) = next.take() {
                q["NextToken"] = t;
            }
            let v = self
                .json("cloudformation", "describe-stack-events", q)
                .await?;
            let events = v["StackEvents"]
                .as_array()
                .context("Invalid stack events")?;
            ensure!(events.len() <= 1000, "Stack event inventory limit");
            for e in events {
                if e["StackId"] == arn
                    && e["PhysicalResourceId"] == arn
                    && e["ResourceType"] == "AWS::CloudFormation::Stack"
                {
                    if let Some(t) = e["ClientRequestToken"].as_str() {
                        ensure!(t.len() <= 128, "Invalid event token");
                        tokens.push(t.into());
                    }
                }
            }
            match v.get("NextToken") {
                None | Some(Value::Null) => break,
                Some(Value::String(t))
                    if !t.is_empty() && t.len() <= 1024 && seen.insert(t.clone()) && page < 19 =>
                {
                    next = Some(json!(t))
                }
                _ => bail!("Incomplete/repeated stack event pagination"),
            }
        }
        let mut tags = BTreeMap::new();
        for tag in s["Tags"].as_array().context("Stack tags missing")? {
            let k = tag["Key"].as_str().context("Invalid stack tag")?;
            let v = tag["Value"].as_str().context("Invalid stack tag")?;
            ensure!(
                tags.insert(k.into(), v.into()).is_none(),
                "Duplicate stack tag"
            );
        }
        let expected = template["Outputs"]
            .as_object()
            .context("Template outputs missing")?;
        let mut outputs = BTreeMap::new();
        if let Some(rows) = s.get("Outputs") {
            for r in rows.as_array().context("Invalid stack outputs")? {
                let k = r["OutputKey"].as_str().context("Invalid output key")?;
                let v = r["OutputValue"].as_str().context("Invalid output value")?;
                ensure!(
                    expected.contains_key(k)
                        && v.len() <= 4096
                        && outputs.insert(k.into(), v.into()).is_none(),
                    "Unrecognized/duplicate/oversized stack output"
                );
            }
        }
        Ok(Some(Stack {
            arn,
            name: text("StackName")?,
            status: text("StackStatus")?,
            role: text("RoleARN")?,
            template,
            tags,
            tokens,
            outputs,
        }))
    }
    async fn create(
        &mut self,
        name: &str,
        token: &str,
        template: &Value,
        tags: &BTreeMap<String, String>,
    ) -> Result<String> {
        let body = serde_json::to_string(template)?;
        ensure!(
            body.len() <= 51200,
            "CloudFormation inline template limit exceeded"
        );
        let v=self.json("cloudformation","create-stack",json!({"StackName":name,"ClientRequestToken":token,"TemplateBody":body,"RoleARN":self.p.bootstrap.as_ref().unwrap().execution_role_arn,"Tags":tags.iter().map(|(k,v)|json!({"Key":k,"Value":v})).collect::<Vec<_>>()})).await?;
        Ok(v["StackId"]
            .as_str()
            .context("Create returned no stack ARN; inspect original intent")?
            .into())
    }
    async fn delete(&mut self, arn: &str, token: &str) -> Result<()> {
        self.json("cloudformation","delete-stack",json!({"StackName":arn,"ClientRequestToken":token,"RoleARN":self.p.bootstrap.as_ref().unwrap().execution_role_arn})).await?;
        Ok(())
    }
    async fn guard_health(&mut self) -> Result<()> {
        ensure!(
            self.p.infrastructure.as_ref().unwrap().guard_code.sha256 == guard::archive_sha(),
            "Guard must match this CLI's locally qualified bundled package"
        );
        let Target::Aws { account, region } = &self.p.target else {
            unreachable!()
        };
        let function = format!(
            "arn:aws:lambda:{region}:{account}:function:{}-cleanup",
            self.p.prefix()
        );
        let config = self
            .json(
                "lambda",
                "get-function-configuration",
                json!({"FunctionName":function}),
            )
            .await?;
        let digest = base64::engine::general_purpose::STANDARD
            .decode(
                config["CodeSha256"]
                    .as_str()
                    .context("Guard digest missing")?,
            )
            .context("Invalid guard code digest")?;
        let input = self.p.infrastructure.as_ref().unwrap();
        ensure!(
            digest
                .iter()
                .map(|v| format!("{v:02x}"))
                .collect::<String>()
                == input.guard_code.sha256,
            "Deployed guard code digest mismatch"
        );
        let expected: Value =
            serde_json::from_slice(&read_file(&self.root_plan()?, "guard.cfn.json")?)?;
        ensure!(
            config["FunctionArn"] == function
                && config["Runtime"] == "nodejs22.x"
                && config["Handler"] == "index.handler"
                && config["State"] == "Active"
                && config["LastUpdateStatus"] == "Successful"
                && config["Role"]
                    == format!("arn:aws:iam::{account}:role/{}-cleanup", self.p.prefix())
                && config["Environment"]["Variables"]
                    == expected["Resources"]["Cleanup"]["Properties"]["Environment"]["Variables"],
            "Guard configuration/identity is not the reviewed deployment"
        );
        let rule = format!("{}-cleanup", self.p.prefix());
        let r = self
            .json("events", "describe-rule", json!({"Name":rule}))
            .await?;
        ensure!(
            r["State"] == "ENABLED"
                && r["ScheduleExpression"] == "rate(5 minutes)"
                && r["Arn"] == format!("arn:aws:events:{region}:{account}:rule/{rule}"),
            "Guard schedule missing/changed"
        );
        let targets = self
            .json(
                "events",
                "list-targets-by-rule",
                json!({"Rule":rule,"Limit":2}),
            )
            .await?;
        let rows = targets["Targets"]
            .as_array()
            .context("Guard targets unavailable")?;
        ensure!(
            targets.get("NextToken").is_none()
                && rows.len() == 1
                && rows[0]["Arn"] == function
                && rows[0]["RetryPolicy"]["MaximumRetryAttempts"] == 0
                && rows[0].get("Input").is_none()
                && rows[0].get("InputTransformer").is_none()
                && rows[0].get("InputPath").is_none(),
            "Guard target changed or incomplete"
        );
        let concurrency = self
            .json(
                "lambda",
                "get-function-concurrency",
                json!({"FunctionName":function}),
            )
            .await?;
        ensure!(
            concurrency["ReservedConcurrentExecutions"] == 1,
            "Guard reserved concurrency changed"
        );
        let path = self
            .root
            .join(format!(".guard-health-{}", uuid::Uuid::new_v4().simple()));
        let mut o = fs::OpenOptions::new();
        o.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            o.mode(0o600);
        }
        drop(o.open(&path)?);
        let result=async{
            let response=self.call("lambda","invoke",json!({"FunctionName":function,"InvocationType":"RequestResponse","LogType":"None","Payload":base64::engine::general_purpose::STANDARD.encode(br#"{"mode":"health"}"#)}),Some(&path)).await?.context("Missing Lambda response")?;
            ensure!(response["StatusCode"]==200&&response.get("FunctionError").is_none(),"Guard health invocation failed");
            let payload:Value=serde_json::from_slice(&read_file(&self.root,path.file_name().unwrap().to_str().unwrap())?)?;
            ensure!(payload["status"]=="healthy"&&payload["destructiveActions"]==0,"Guard health did not confirm a non-destructive response");Ok(())
        }.await;
        let _ = fs::remove_file(path);
        result
    }
}
fn fresh_preflight(path: &Path, p: &Plan, j: &Journal) -> Result<()> {
    let parent = path.parent().unwrap_or(Path::new("."));
    let name = path
        .file_name()
        .and_then(|s| s.to_str())
        .context("Invalid report path")?;
    let report: Value = serde_json::from_slice(&read_file(parent, name)?)?;
    let at = now()?;
    ensure!(
        report["schemaVersion"] == 1
            && report["deploymentId"] == p.deployment_id
            && report["bundleDigest"] == j.bundle_digest
            && report["target"] == serde_json::to_value(&p.target)?
            && report["mode"] == "read_only"
            && report["checksPassed"] == true,
        "Preflight does not match reviewed deployment"
    );
    let observed = report["observedAtUnix"]
        .as_u64()
        .context("Missing preflight observation time")?;
    let until = report["validUntilUnix"]
        .as_u64()
        .context("Missing preflight expiry")?;
    ensure!(
        observed <= at && at < until && until <= observed + 900 && until <= p.expires_at_unix,
        "Fresh preflight required before provisioning"
    );
    let checks = report["checks"]
        .as_array()
        .context("Preflight checks missing")?;
    ensure!(
        !checks.is_empty()
            && checks
                .iter()
                .filter(|v| v["required"] == true)
                .all(|v| v["status"] == "passed"),
        "Preflight contains blocked checks"
    );
    for required in [
        "operator_identity",
        "service_linked_roles",
        "availability_zones_and_offering",
        "eks_standard_support",
        "recommended_node_release",
        "addon_vpc-cni",
        "addon_kube-proxy",
        "addon_coredns",
        "addon_eks-pod-identity-agent",
        "addon_aws-ebs-csi-driver",
        "postgres_and_orderability",
        "quota_headroom",
        "published_cost_estimate",
        "guard_artifact_bytes",
        "policy_validation",
        "observation_freshness",
    ] {
        let matches: Vec<_> = checks.iter().filter(|v| v["name"] == required).collect();
        ensure!(
            matches.len() == 1
                && matches[0]["required"] == true
                && matches[0]["status"] == "passed",
            "Required preflight check missing/duplicated/blocked"
        );
    }
    Ok(())
}
pub(super) async fn run(command: &Command) -> Result<()> {
    if let Command::PlatformArtifact {
        plan,
        out,
        openshell_namespace,
        openshell_release,
    } = command
    {
        return platform_artifact::export(
            plan,
            out,
            openshell_namespace.as_deref(),
            openshell_release.as_deref(),
        );
    }
    if let Command::PublishImage {
        state,
        profile,
        role,
        source,
        allow_experimental_cloud,
        confirm_no_effect,
    } = command
    {
        return images::publish(
            state,
            profile,
            role,
            source,
            *allow_experimental_cloud,
            *confirm_no_effect,
        )
        .await;
    }
    if let Command::Installation {
        state,
        profile,
        config,
        out,
    } = command
    {
        return install::prepare(state, profile, config, out).await;
    }
    if let Command::Prepare { plan, out } = command {
        let (p, _) = inspect(plan)?;
        ensure!(
            p.infrastructure
                .as_ref()
                .is_some_and(|i| i.guard_code.sha256 == guard::archive_sha()),
            "Pin this CLI's packaged guard before preparing lifecycle execution"
        );
        let j = state::init(plan, out)?;
        return crate::output(
            json!({"deploymentId":j.deployment_id,"bundleDigest":j.bundle_digest,"phase":j.phase,"cloudCalls":false,"liveQualified":false}),
        );
    }
    let (root, profile) = match command {
        Command::Apply { state, profile, .. }
        | Command::Status { state, profile }
        | Command::Destroy { state, profile, .. } => (state, profile),
        _ => unreachable!(),
    };
    let (mut j, p, _lock) = state::load(root)?;
    match command {
        Command::Apply {
            allow_experimental_cloud,
            accept_plan,
            preflight,
            ..
        } => {
            ensure!(
                *allow_experimental_cloud,
                "Explicit --allow-experimental-cloud is required; no cloud calls made"
            );
            ensure!(*accept_plan == j.bundle_digest, "Accepted digest mismatch");
            if j.operations.is_empty() {
                fresh_preflight(
                    preflight
                        .as_deref()
                        .context("Fresh --preflight report required before a new cloud create")?,
                    &p,
                    &j,
                )?;
            }
        }
        Command::Destroy {
            allow_experimental_cloud,
            deployment_id,
            ..
        } => {
            ensure!(
                *allow_experimental_cloud,
                "Explicit --allow-experimental-cloud required; no cloud calls made"
            );
            ensure!(
                *deployment_id == j.deployment_id,
                "Deployment identity mismatch"
            );
        }
        _ => {}
    }
    let mut api = Aws {
        p: p.clone(),
        profile: profile.clone(),
        root: root.clone(),
        preflight: match command {
            Command::Apply { preflight, .. } => preflight.clone(),
            _ => None,
        },
    };
    api.identity().await?;
    match command {
        Command::Apply {
            confirm_no_effect, ..
        } => {
            if let Some(kind) = confirm_no_effect {
                state::confirm_no_effect(root, &mut j, &p, &mut api, kind).await?;
            }
            state::provision(root, &mut j, &p, &mut api, now()?).await?;
            crate::output(
                json!({"deploymentId":j.deployment_id,"phase":j.phase,"liveQualified":false,"serviceInstallation":"not_checked","guardRetained":true}),
            )
        }
        Command::Status { .. } => crate::output(state::observe(&j, &p, &mut api).await?),
        Command::Destroy {
            installation_state, ..
        } => {
            if !j.operations.contains_key("delete:infra") {
                if j.operations
                    .get("installation-handoff")
                    .is_some_and(|o| o.status != "prepared")
                {
                    let path=installation_state.as_ref().context("Installation was handed off; --installation-state with verified teardown is required")?;
                    let observed = state::observe(&j, &p, &mut api).await?;
                    let outputs =
                        serde_json::from_value(observed["observed"]["infra"]["outputs"].clone())?;
                    let c = install::read_cluster(&api, &outputs).await?;
                    local::verify_teardown(
                        path,
                        root,
                        profile,
                        &install::kubeconfig(&p, profile, &c),
                    )
                    .await?;
                }
                api.bucket_empty().await?;
            }
            state::destroy(root, &mut j, &p, &mut api).await?;
            crate::output(
                json!({"deploymentId":j.deployment_id,"phase":j.phase,"guardRetained":true,"bootstrapRetained":true,"allCloudResourcesAbsent":false,"liveQualified":false}),
            )
        }
        _ => unreachable!(),
    }
}
