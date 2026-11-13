//! Adapter to the existing ownership-aware cloud and Kubernetes engines. The
//! coordinator never bypasses their fresh preflight, scope or receipt validation.
use super::*;
use config::Cloud;
use state::{Driver, Observation, Phase, Stage, Step};
#[path = "deployment_up_access.rs"]
mod access;
#[path = "deployment_up_azure_images.rs"]
mod azure_images;
#[path = "deployment_up_install.rs"]
mod install;
pub(super) fn validate_password_input(path: &Path) -> Result<()> {
    azure_images::private_password_file(path).map(|_| ())
}
pub(super) struct Native<'a> {
    pub root: PathBuf,
    pub review: &'a Review,
    pub package: package::Snapshot,
    pub bootstrap_approved: bool,
    pub database_password_file: Option<PathBuf>,
    pub bridge: std::cell::RefCell<Option<access::Bridge>>,
}
fn arg(path: &Path) -> Result<String> {
    Ok(path
        .to_str()
        .context("Deployment paths must be UTF-8")?
        .into())
}
fn text(v: &Value, key: &str) -> Result<String> {
    Ok(v[key]
        .as_str()
        .with_context(|| format!("Missing native receipt field {key}"))?
        .into())
}
fn strings(args: &[&str]) -> Vec<String> {
    args.iter().map(|s| s.to_string()).collect()
}
impl Native<'_> {
    fn environment(&self) -> Result<BTreeMap<String, String>> {
        let mut env = BTreeMap::new();
        if self.root.join("aws-config").is_file() {
            env.insert(
                "AWS_CONFIG_FILE".into(),
                arg(&self.root.join("aws-config"))?,
            );
        }
        env.insert("AWS_MAX_ATTEMPTS".into(), "1".into());
        env.insert("AWS_PAGER".into(), String::new());
        env.insert("AWS_CLI_AUTO_PROMPT".into(), "off".into());
        env.insert("AZURE_EXTENSION_USE_DYNAMIC_INSTALL".into(), "no".into());
        if self.root.join("tools").is_dir() {
            env.insert(
                "PATH".into(),
                format!(
                    "{}:{}",
                    self.root.join("tools").display(),
                    std::env::var("PATH").unwrap_or_default()
                ),
            );
        }
        Ok(env)
    }
    async fn command(&self, args: Vec<String>) -> Result<Value> {
        let bytes = super::super::local::exec_with_env(
            &std::env::current_exe()?,
            &args,
            None,
            &self.environment()?,
        )
        .await?;
        serde_json::from_slice(&bytes).context("Invalid native receipt")
    }
    fn provider(&self) -> &str {
        self.review.config.cloud.provider()
    }
    fn plan_root(&self) -> PathBuf {
        self.root.join("infrastructure")
    }
    fn cloud_state(&self) -> PathBuf {
        self.root.join("cloud")
    }
    fn install_state(&self) -> PathBuf {
        self.root.join("installation")
    }
    fn document(&self, path: &Path) -> Result<Value> {
        Ok(serde_json::from_slice(&fs::read(path)?)?)
    }
    fn plan(&self) -> Result<(super::super::Plan, super::super::Manifest)> {
        super::super::inspect(&self.plan_root())
    }
    fn aws_profile(&self) -> Result<String> {
        ensure!(self.provider() == "aws", "AWS profile required");
        Ok(format!("ow-{}", &self.review.deployment_id[4..16]))
    }
    fn native_plan(&self) -> Result<super::super::Plan> {
        let c = &self.review.config;
        let package = &self.package;
        let created = self.review.resolution.observed_at_unix;
        let mut value = json!({"schemaVersion":if self.provider()=="aws"{3}else{2},"deploymentId":self.review.deployment_id,"name":c.name,"createdAtUnix":created,"expiresAtUnix":created+u64::from(c.trial.as_ref().map(|t|t.expires_after_hours).unwrap_or(72))*3600,"autoExpire":c.trial.is_some(),"budget":{"authorizedUsd":c.budget.as_ref().map(|b|b.alert_usd).unwrap_or(0),"estimateCheckDisabled":c.budget.is_none(),"hardCap":false,"estimate":null},"topology":{"clusterMode":"new","nodeCount":c.nodes.count,"maxWorkerReplicas":2,"architecture":self.review.resolution.node.architecture,"access":"private","nodeAutoscaling":false}});
        let mut inputs = package.setup.infrastructure.clone();
        if let Some(version) = &c.advanced.kubernetes_version {
            inputs["kubernetesVersion"] = json!(version);
        }
        if let Some(cidr) = &c.advanced.network_cidr {
            ensure!(
                self.provider() == "aws",
                "Custom network CIDR is supported only by the AWS template"
            );
            inputs["vpcCidr"] = json!(cidr);
        }
        match &c.cloud {
            Cloud::Aws {
                account_id, region, ..
            } => {
                value["target"] = json!({"provider":"aws","account":account_id,"region":region});
                let operator = c
                    .advanced
                    .aws
                    .as_ref()
                    .and_then(|a| a.operator_arn.as_ref())
                    .or(package.setup.operator_arn.as_ref())
                    .context(
                        "Set advanced.aws.operatorArn to the approved installer IAM identity",
                    )?;
                let prefix = format!("ows-{}-{}", c.name, &self.review.deployment_id[4..16]);
                value["bootstrap"] = json!({"operatorArn":operator,"executionRoleArn":format!("arn:aws:iam::{account_id}:role/{prefix}-executor")});
                inputs["nodeInstanceType"] = json!(self.review.resolution.node.instance_type);
                inputs["acknowledgeExperimentDataDeletion"] = json!(c.trial.is_some());
                value["infrastructure"] = inputs;
            }
            Cloud::Azure {
                subscription_id,
                region,
            } => {
                value["target"] = json!({"provider":"azure","tenant":self.review.resolution.identity["tenantId"],"subscription":subscription_id,"location":region});
                let azure = c.advanced.azure.as_ref();
                if let Some(a) = azure {
                    if let Some(group) = &a.control_resource_group {
                        inputs["controlResourceGroup"] = json!(group);
                    }
                    if !a.admin_group_object_ids.is_empty() {
                        inputs["adminGroupObjectIds"] = json!(a.admin_group_object_ids);
                    }
                    if let Some(key) = &a.ssh_public_key_file {
                        inputs["sshPublicKey"] = json!(fs::read_to_string(key)?.trim());
                    }
                }
                value["azureBootstrap"] = json!({"resourceGroup":azure.and_then(|a| a.resource_group.as_ref()).or(package.setup.resource_group.as_ref()).context("Set advanced.azure.resourceGroup to the approved workload group")?,"setupObjectId":package.setup.setup_object_id.as_ref().context("Set advanced.azure.setupObjectId to the approved installer object ID")?,"setupPrincipalType":package.setup.setup_principal_type.as_deref().unwrap_or("ServicePrincipal")});
                inputs["nodeVmSize"] = json!(self.review.resolution.node.instance_type);
                value["azureInfrastructure"] = inputs;
            }
        }
        let p: super::super::Plan =
            serde_json::from_value(value).context("Invalid reviewed cloud setup inputs")?;
        p.validate()?;
        if self.provider() == "aws" {
            ensure!(
                super::super::guard::compatible(&p),
                "Setup profile must reference the guard archive packaged by this CLI release"
            );
        }
        Ok(p)
    }
    pub(super) fn artifact_digest(&self) -> Result<String> {
        let files = super::super::artifacts(&self.native_plan()?)?;
        let hashes: BTreeMap<_, _> = files.iter().map(|(n, b)| (n, hash(b))).collect();
        Ok(hash(&serde_json::to_vec(&hashes)?))
    }
    pub(super) async fn review_bundle(&self) -> Result<()> {
        self.bundle().await?;
        Ok(())
    }
    pub(super) async fn restore_access(&self) -> Result<()> {
        self.connect_private(&self.root.join("handoff/kubeconfig.json"))
            .await
    }
    pub(super) async fn verify_current(&self) -> Result<()> {
        self.connect_private(&self.root.join("handoff/kubeconfig.json"))
            .await?;
        self.verify().await?;
        Ok(())
    }
    async fn bundle(&self) -> Result<Value> {
        let expected = self.native_plan()?;
        ensure!(
            self.artifact_digest()? == self.review.native_bundle_digest,
            "Native cloud graph changed since approval"
        );
        if self.plan_root().exists() {
            let (p, m) = self.plan()?;
            ensure!(
                p == expected,
                "Original infrastructure bundle differs from review"
            );
            return Ok(json!({"bundleDigest":m.bundle_digest}));
        }
        let temporary = self
            .root
            .join(format!(".infrastructure-{}", uuid::Uuid::new_v4().simple()));
        super::super::write_bundle(&temporary, &expected)?;
        fs::rename(&temporary, self.plan_root())?;
        Ok(json!({"bundleDigest":self.plan()?.1.bundle_digest}))
    }
    async fn prepare_state(&self) -> Result<()> {
        let file = if self.provider() == "aws" {
            "cloud-journal.json"
        } else {
            "azure-state.json"
        };
        if self.cloud_state().join(file).exists() {
            return Ok(());
        }
        ensure!(
            !self.cloud_state().exists(),
            "Partial local lifecycle state; inspect before continuation"
        );
        let temp = self
            .root
            .join(format!(".cloud-{}", uuid::Uuid::new_v4().simple()));
        let mut args = strings(&["deploy", self.provider(), "lifecycle", "prepare", "--plan"]);
        args.push(arg(&self.plan_root())?);
        args.extend(["--out".into(), arg(&temp)?]);
        self.command(args).await?;
        fs::rename(temp, self.cloud_state())?;
        Ok(())
    }
    async fn cloud_status(&self) -> Result<Value> {
        let mut a = strings(&["deploy", self.provider(), "lifecycle", "status", "--state"]);
        a.push(arg(&self.cloud_state())?);
        if self.provider() == "aws" {
            a.extend(["--profile".into(), self.aws_profile()?]);
        }
        self.command(a).await
    }
    async fn cloud_apply(&self, stage: &str) -> Result<Value> {
        let (_, m) = self.plan()?;
        let report = self.root.join("preflight/preflight.json");
        let stale = if report.exists() {
            let r = self.document(&report)?;
            r["observedAtUnix"]
                .as_u64()
                .is_none_or(|at| now().is_ok_and(|n| at > n || n - at > 300))
        } else {
            true
        };
        if stale {
            let r = self.preflight().await?;
            ensure!(
                r["preflightReady"] == true || r["checksPassed"] == true,
                "Fresh cloud prerequisites are not ready; original requests are retained"
            );
        }
        let mut a = strings(&["deploy", self.provider(), "lifecycle", "apply", "--state"]);
        a.push(arg(&self.cloud_state())?);
        a.extend([
            if self.provider() == "aws" {
                "--accept-plan"
            } else {
                "--accept"
            }
            .into(),
            m.bundle_digest,
            "--allow-experimental-cloud".into(),
        ]);
        if self.provider() == "aws" {
            a.extend([
                "--profile".into(),
                self.aws_profile()?,
                "--preflight".into(),
                arg(&self.root.join("preflight/preflight.json"))?,
            ]);
        } else {
            a.extend(["--stage".into(), stage.into()]);
        }
        self.command(a).await
    }
    fn aws_config(&self) -> Result<()> {
        let Cloud::Aws {
            profile, region, ..
        } = &self.review.config.cloud
        else {
            return Ok(());
        };
        let (plan, _) = self.plan()?;
        let source = std::env::var_os("AWS_CONFIG_FILE")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".aws/config")
            });
        let mut content = if source.exists() {
            fs::read_to_string(source)?
        } else {
            String::new()
        };
        let name = self.aws_profile()?;
        ensure!(
            !content.contains(&format!("[profile {name}]")),
            "Generated setup profile collides with the original AWS configuration"
        );
        content.push_str(&format!("\n[profile {name}]\nrole_arn = arn:aws:iam::{}:role/{}-setup\nsource_profile = {profile}\nregion = {region}\nrole_session_name = {name}\n",self.review.config.cloud.account(),plan.prefix()));
        let path = self.root.join("aws-config");
        if path.exists() {
            ensure!(
                fs::read_to_string(&path)? == content,
                "AWS login configuration changed; inspect the pinned setup profile before resuming"
            );
            return Ok(());
        }
        use std::io::Write;
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        options.open(path)?.write_all(content.as_bytes())?;
        Ok(())
    }
    async fn aws_bootstrap(&self) -> Result<Observation> {
        if !self.bootstrap_approved {
            return Ok(Observation::Blocked(format!("Identity setup needs separate approval. Review {} and rerun with --accept-bootstrap {}. Normal setup never silently grants IAM.",self.plan_root().join("permission-review.json").display(),self.review.digest)));
        }
        let Cloud::Aws { profile, .. } = &self.review.config.cloud else {
            bail!("AWS configuration required")
        };
        let (_, manifest) = self.plan()?;
        let mut args = strings(&["deploy", "aws", "lifecycle", "bootstrap", "--plan"]);
        args.extend([
            arg(&self.plan_root())?,
            "--state".into(),
            arg(&self.root)?,
            "--profile".into(),
            profile.clone(),
            "--accept-plan".into(),
            manifest.bundle_digest,
            "--approve-identities".into(),
        ]);
        let receipt = self.command(args).await?;
        if receipt["phase"] == "ready" {
            self.aws_config()?;
            Ok(Observation::Complete(receipt))
        } else {
            Ok(Observation::Pending)
        }
    }
    async fn azure_bootstrap(&self, phase: &str) -> Result<Observation> {
        if !self.bootstrap_approved {
            return Ok(Observation::Blocked(format!("Review the exact Azure role/control payloads under {}, then approve with --accept-bootstrap {}. No implicit administrator grants are made.",self.plan_root().display(),self.review.digest)));
        }
        let (_, manifest) = self.plan()?;
        let mut a = strings(&["deploy", "azure", "installation", "bootstrap", "--plan"]);
        a.extend([
            arg(&self.plan_root())?,
            "--state".into(),
            arg(&self.cloud_state())?,
            "--phase".into(),
            phase.into(),
            "--accept-plan".into(),
            manifest.bundle_digest,
            "--approve-identities".into(),
        ]);
        let receipt = self.command(a).await?;
        Ok(if receipt["phase"] == "ready" {
            Observation::Complete(receipt)
        } else {
            Observation::Pending
        })
    }
    async fn preflight(&self) -> Result<Value> {
        let output = self
            .root
            .join(format!("preflight-{}", uuid::Uuid::new_v4().simple()));
        let mut args = strings(&["deploy", self.provider(), "preflight", "--plan"]);
        args.push(arg(&self.plan_root())?);
        args.extend([
            "--out".into(),
            arg(&output)?,
            "--timeout-seconds".into(),
            "60".into(),
            "--overall-timeout-seconds".into(),
            "180".into(),
        ]);
        if let Cloud::Aws { profile, .. } = &self.review.config.cloud {
            args.extend([
                "--profile".into(),
                profile.clone(),
                "--check-setup-role".into(),
            ]);
        }
        let result = self.command(args).await;
        let report = self.document(&output.join("preflight.json"))?;
        let (_, manifest) = self.plan()?;
        ensure!(
            report["deploymentId"] == self.review.deployment_id
                && report["bundleDigest"] == manifest.bundle_digest,
            "Preflight report is not bound to the original reviewed graph"
        );
        let _ = result;
        let dest = self.root.join("preflight");
        if dest.exists() {
            fs::rename(
                &dest,
                self.root.join(format!(
                    "preflight-previous-{}",
                    uuid::Uuid::new_v4().simple()
                )),
            )?;
        }
        fs::rename(output, &dest)?;
        Ok(report)
    }
    async fn installer(&self, verb: &str, extra: &[&str]) -> Result<Value> {
        let p = self.document(&self.install_state().join("local-plan.json"))?;
        let mut a = strings(&["deploy", "cluster", verb, "--state"]);
        a.push(arg(&self.install_state())?);
        if verb != "status" {
            a.extend(["--accept-plan".into(), text(&p, "digest")?]);
        }
        a.extend(extra.iter().map(|s| s.to_string()));
        self.command(a).await
    }
    fn ready_install(&self, stage: Stage) -> Result<Option<Value>> {
        let path = self.install_state().join("journal.json");
        if !path.exists() {
            return Ok(None);
        }
        let j = self.document(&path)?;
        let phase = j["phase"].as_str().unwrap_or("");
        let ready = match stage {
            Stage::Platform => j["operations"].as_object().is_some_and(|m| {
                m.iter()
                    .filter(|(k, _)| k.starts_with("platform:"))
                    .all(|(_, v)| v["phase"] == "complete")
                    && m.keys().any(|k| k.starts_with("platform:"))
            }),
            Stage::OpenShell => ["openshell_ready", "installed"].contains(&phase),
            Stage::Secrets => j["operations"].as_object().is_some_and(|m| {
                [
                    "secret:ow-service-database",
                    "secret:ow-service-tls",
                    "secret:ow-service-bootstrap",
                ]
                .iter()
                .all(|k| m.get(*k).is_some_and(|v| v["phase"] == "complete"))
            }),
            Stage::Service => phase == "installed",
            _ => false,
        };
        Ok(ready.then_some(json!({"phase":phase,"installer":self.install_state()})))
    }
}
impl Driver for Native<'_> {
    async fn observe(&mut self, stage: Stage, step: Option<&Step>) -> Result<Observation> {
        self.package.validate(&self.review.config)?;
        match stage {
            Stage::Bundle => {
                if self.plan_root().exists() {
                    let r = self.bundle().await?;
                    self.prepare_state().await?;
                    Ok(Observation::Complete(r))
                } else {
                    Ok(Observation::Absent)
                }
            }
            Stage::Preflight => Ok(Observation::Absent), // read-only; always obtain fresh observations
            Stage::Permissions
                if self.provider() == "aws"
                    && self.root.join("bootstrap-receipt.json").exists() =>
            {
                self.aws_bootstrap().await
            }
            Stage::Permissions => Ok(Observation::Absent),
            Stage::Control | Stage::IdentityBindings if self.provider() == "azure" => {
                Ok(Observation::Absent)
            }
            Stage::Images => Ok(Observation::Absent),
            Stage::PrivateAccess => Ok(Observation::Absent),
            Stage::Verify => Ok(Observation::Absent),
            Stage::Control if self.provider() == "aws" => Ok(Observation::Absent),
            Stage::IdentityBindings if self.provider() == "aws" => Ok(Observation::Complete(
                json!({"managedBy":"reviewed CloudFormation graph"}),
            )),
            Stage::Foundation | Stage::Cluster => {
                if !self.cloud_state().exists() {
                    return Ok(Observation::Absent);
                }
                let mut status = self.cloud_status().await?;
                if self.provider() == "aws" {
                    if status["observed"]["infra"]["status"] == "CREATE_COMPLETE" {
                        // Status is intentionally read-only. Reconcile the original
                        // create receipts before declaring this stage complete;
                        // installation requires the durable lifecycle phase too.
                        if status["phase"] != "infrastructure_ready" {
                            let reconciled = self.cloud_apply("foundation").await?;
                            ensure!(
                                reconciled["phase"] == "infrastructure_ready",
                                "Original AWS infrastructure receipts are not ready"
                            );
                            status = reconciled;
                        }
                        return Ok(Observation::Complete(status));
                    }
                    let journal = self.document(&self.cloud_state().join("cloud-journal.json"))?;
                    let uncertain = journal["operations"]
                        .as_object()
                        .is_some_and(|ops| ops.values().any(|o| o["status"] == "unknown"));
                    if uncertain {
                        return Ok(Observation::Unknown("Inspect original AWS lifecycle receipts; do not create a replacement stack".into()));
                    }
                    // Native provision reconciles existing create intents, refuses
                    // unknown effects and advances only dependencies already ready.
                    Ok(Observation::Absent)
                } else {
                    let name = if stage == Stage::Foundation {
                        "foundation"
                    } else {
                        "cluster"
                    };
                    let op = status["stages"]
                        .as_array()
                        .and_then(|a| a.iter().find(|v| v["stage"] == name))
                        .context("Azure stage receipt missing")?;
                    match op["phase"].as_str(){Some("active")=>Ok(Observation::Complete(status)),Some("prepared")=>Ok(Observation::Absent),Some("creating"|"unknown"|"intent")=>{
                        let(_,m)=self.plan()?;let mut a=strings(&["deploy","azure","lifecycle","reconcile","--state"]);a.extend([arg(&self.cloud_state())?,"--accept".into(),m.bundle_digest,"--allow-experimental-cloud".into()]);self.command(a).await?;Ok(Observation::Pending)
                    },_=>Ok(Observation::Blocked("Azure reported a failed deployment; inspect the original ARM operation before continuing".into()))}
                }
            }
            Stage::Platform | Stage::OpenShell | Stage::Secrets | Stage::Service => {
                if let Some(receipt) = self.ready_install(stage)? {
                    return Ok(Observation::Complete(receipt));
                }
                // All mutation retry decisions belong to the existing installer,
                // which checks namespace UIDs and persisted Helm/Secret intents.
                Ok(Observation::Absent)
            }
            _ => {
                if step.is_some_and(|s| s.phase == Phase::Intent || s.phase == Phase::Pending) {
                    Ok(Observation::Unknown(
                        "Read the original provider receipt before retrying this stage".into(),
                    ))
                } else {
                    Ok(Observation::Absent)
                }
            }
        }
    }
    async fn execute(&mut self, stage: Stage, _request: &str) -> Result<Observation> {
        match stage {
            Stage::Bundle => {
                let r = self.bundle().await?;
                self.prepare_state().await?;
                Ok(Observation::Complete(r))
            }
            Stage::Permissions if self.provider() == "aws" => self.aws_bootstrap().await,
            Stage::Control if self.provider() == "aws" => {
                let r = self.cloud_apply("foundation").await?;
                Ok(
                    if ["infra_creating", "infrastructure_ready"]
                        .contains(&r["phase"].as_str().unwrap_or(""))
                    {
                        Observation::Complete(r)
                    } else {
                        Observation::Pending
                    },
                )
            }
            Stage::Permissions if self.provider() == "azure" => self.azure_bootstrap("setup").await,
            Stage::Control if self.provider() == "azure" => self.azure_bootstrap("control").await,
            Stage::IdentityBindings if self.provider() == "azure" => {
                self.azure_bootstrap("foundation").await
            }
            Stage::Images => {
                if self.provider() == "azure" {
                    match self.azure_bootstrap("federation").await? {
                        Observation::Complete(_) => {}
                        other => return Ok(other),
                    }
                }
                self.images().await
            }
            Stage::PrivateAccess => self.handoff().await,
            Stage::Secrets => self.secrets().await,
            Stage::Verify => self.verify().await,
            Stage::Preflight => {
                let r = self.preflight().await?;
                if r["preflightReady"] == true || r["checksPassed"] == true {
                    Ok(Observation::Complete(r))
                } else {
                    Ok(Observation::Blocked(format!("Cloud prerequisites need attention. Review {} and rerun the same up command; no infrastructure request was submitted.",self.root.join("preflight/preflight.json").display())))
                }
            }
            Stage::Foundation => {
                let r = self.cloud_apply("foundation").await?;
                Ok(
                    if self.provider() == "aws" && r["phase"] == "infrastructure_ready" {
                        Observation::Complete(r)
                    } else {
                        Observation::Pending
                    },
                )
            }
            Stage::Cluster => {
                self.cloud_apply("cluster").await?;
                Ok(Observation::Pending)
            }
            Stage::Platform => {
                let r = self
                    .installer("platform", &["--allow-experimental-cloud"])
                    .await?;
                Ok(Observation::Complete(r))
            }
            Stage::OpenShell => {
                let r = self.installer("apply", &["--openshell-only"]).await?;
                Ok(Observation::Complete(r))
            }
            Stage::Service => {
                let r = self.installer("apply", &[]).await?;
                ensure!(r["phase"] == "installed", "Service installation not ready");
                Ok(Observation::Complete(r))
            }
            _ => Ok(Observation::Blocked(format!(
                "Stage {stage:?} needs account installation wiring before provisioning can proceed"
            ))),
        }
    }
}

#[cfg(test)]
mod contract_tests {
    use super::*;
    fn fixture(cloud: &str) -> (Review, package::Snapshot) {
        let aws = cloud == "aws";
        let account = if aws {
            "123456789012"
        } else {
            "11111111-2222-4333-8444-555555555555"
        };
        let mut infrastructure: Value = if aws {
            serde_json::from_str(include_str!("../examples/aws-infrastructure.json")).unwrap()
        } else {
            json!({"kubernetesVersion":"1.33.4","nodeVmSize":"Standard_D4s_v5","sshPublicKey":"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","adminGroupObjectIds":["11111111-1111-4111-8111-111111111111"],"controlResourceGroup":"example-control","guardImage":format!("ghcr.io/test/ows@sha256:{}","a".repeat(64)),"postgresPassword":{"keyVaultId":format!("/subscriptions/{account}/resourceGroups/example-secrets/providers/Microsoft.KeyVault/vaults/example-vault"),"secretName":"postgres","secretVersion":"a".repeat(32)}})
        };
        if aws {
            infrastructure["guardCode"]["sha256"] =
                json!(super::super::super::guard::archive_sha());
            infrastructure["operatorAccess"] =
                json!({"mode":"ssm","amiId":"ami-0123456789abcdef0"});
        }
        let target = if aws {
            json!({"provider":"aws","accountId":account,"region":"us-west-2"})
        } else {
            json!({"provider":"azure","subscriptionId":account,"region":"westus2"})
        };
        let size = if aws { "t4g.xlarge" } else { "Standard_D4s_v5" };
        let arch = if aws { "arm64" } else { "amd64" };
        let config:Config=serde_json::from_value(json!({"name":"example","cloud":target,"nodes":{"count":3,"instanceType":size},"budget":{"limitUSD":1000}})).unwrap();
        let pin = json!({"file":"/fixture","sha256":"a".repeat(64)});
        let package:package::Snapshot=serde_json::from_value(json!({"digest":"fixture","setup":{"schemaVersion":1,"provider":cloud,"account":account,"region":config.cloud.region(),"infrastructure":infrastructure,"operatorArn":"arn:aws:iam::123456789012:role/operator","setupObjectId":"bbbbbbbb-cccc-4ddd-8eee-ffffffffffff","setupPrincipalType":"User","resourceGroup":"example-workload","release":"/release.json"},"release":{"schemaVersion":1,"version":"test","architecture":arch,"images":{"harness":format!("ghcr.io/test/harness@sha256:{}","a".repeat(64)),"runner":format!("ghcr.io/test/runner@sha256:{}","b".repeat(64))},"serviceChart":"/chart","serviceChartSha256":"c".repeat(64),"openshellArchive":pin,"openshellValues":pin,"platformManifest":pin,"databaseCa":pin}})).unwrap();
        let review=Review{schema_version:1,config_digest:config.digest().unwrap(),config,deployment_id:"ows-0123456789abcdef0123456789abcdef".into(),package_digest:"fixture".into(),native_bundle_digest:String::new(),digest:String::new(),resolution:serde_json::from_value(json!({"node":{"instanceType":size,"cpu":4,"memoryGiB":16,"architecture":arch,"eligible":true,"hourlyUsd":0.2},"price":{"currency":"USD","workerHourlyUsd":0.6,"totalHourlyUsd":null,"knownHourlySubtotalUsd":0.6,"components":{},"variableAllowanceUsd":25,"plannedHours":730,"unpricedComponents":["network"],"hardCap":false},"observedAtUnix":1000,"identity":{"tenantId":"11111111-2222-4333-8444-555555555555"}})).unwrap()};
        (review, package)
    }
    #[test]
    fn both_native_graphs_preserve_fixed_capacity_and_persistent_policy() {
        for cloud in ["aws", "azure"] {
            let (review, package) = fixture(cloud);
            let native = Native {
                root: PathBuf::from("/fixture"),
                review: &review,
                package,
                bootstrap_approved: false,
                database_password_file: None,
                bridge: Default::default(),
            };
            let plan = native.native_plan().unwrap();
            assert_eq!(plan.topology.node_count, 3);
            assert_eq!(plan.auto_expire, Some(false));
            assert!(!plan.expires_workload());
            let files = super::super::super::artifacts(&plan).unwrap();
            if cloud == "aws" {
                let graph: Value =
                    serde_json::from_slice(&files["infrastructure.cfn.json"]).unwrap();
                let group = graph["Resources"]
                    .as_object()
                    .unwrap()
                    .values()
                    .find(|v| v["Type"] == "AWS::EKS::Nodegroup")
                    .unwrap();
                assert_eq!(
                    group["Properties"]["ScalingConfig"],
                    json!({"DesiredSize":3,"MinSize":3,"MaxSize":3})
                );
                assert_eq!(group["Properties"]["InstanceTypes"], json!(["t4g.xlarge"]));
                let setup: Value = serde_json::from_slice(&files["setup-policy.json"]).unwrap();
                assert!(setup["Statement"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|s| s["Sid"] == "OwnManagementSessionOnly"
                        && s["Condition"]["StringEquals"]
                            ["ssm:resourceTag/aws:ssmmessages:session-id"]
                            == "${aws:userid}"));
            } else {
                let graph: Value =
                    serde_json::from_slice(&files["azure-cluster.arm.json"]).unwrap();
                let cluster = graph["resources"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|v| v["type"] == "Microsoft.ContainerService/managedClusters")
                    .unwrap();
                assert_eq!(cluster["properties"]["agentPoolProfiles"][0]["count"], 3);
                let grants: Value = serde_json::from_slice(&files["azure-up-grants.json"]).unwrap();
                for g in grants["grants"].as_array().unwrap() {
                    assert!(g["id"].as_str().unwrap().contains("/resourceGroups/"));
                    assert!(!g.to_string().contains("roleAssignments/write"));
                    if g["label"].as_str().unwrap().ends_with("-grant") {
                        assert_eq!(
                            g["body"]["properties"]["principalId"],
                            "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
                        );
                    }
                }
            }
        }
    }
}
