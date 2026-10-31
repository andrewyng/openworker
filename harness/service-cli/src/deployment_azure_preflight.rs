//! Read-only Azure bootstrap observation. Never enables providers, exports access
//! tokens, switches the selected subscription, logs raw errors or installs extensions.
use super::*;
use std::{
    process::Stdio,
    time::{Duration, Instant},
};
use tokio::{io::AsyncReadExt, process::Command};
use zeroize::Zeroizing;
#[derive(Args)]
pub struct Preflight {
    #[arg(long)]
    plan: PathBuf,
    #[arg(long)]
    out: PathBuf,
    #[arg(long, default_value_t = 20)]
    timeout_seconds: u64,
    #[arg(long, default_value_t = 120)]
    overall_timeout_seconds: u64,
}
type Observed = std::result::Result<Value, &'static str>;
struct Az {
    subscription: String,
    timeout: Duration,
    deadline: Instant,
    calls: u32,
}
impl Az {
    async fn read(&mut self, command: &[&str], args: &[String]) -> Observed {
        if !matches!(
            command,
            ["cloud", "show"]
                | ["account", "show"]
                | ["group", "show"]
                | ["provider", "show"]
                | ["rest"]
        ) {
            return Err("operation_not_allowed");
        }
        if command == ["rest"]
            && !(args.len() == 4
                && args[0] == "--method"
                && args[1] == "get"
                && args[2] == "--url"
                && args[3].starts_with("https://management.azure.com/subscriptions/"))
        {
            return Err("operation_not_allowed");
        }
        let remaining = self.deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("timeout");
        }
        let mut cmd = Command::new("az");
        cmd.args(command)
            .args(args)
            .args([
                "--subscription",
                &self.subscription,
                "--only-show-errors",
                "--output",
                "json",
            ])
            .env("AZURE_CORE_ONLY_SHOW_ERRORS", "true")
            .env("AZURE_CORE_COLLECT_TELEMETRY", "false")
            .env("AZURE_EXTENSION_USE_DYNAMIC_INSTALL", "no")
            .env("AZURE_CORE_LOG_LEVEL", "error")
            .env("AZURE_CORE_NO_COLOR", "true")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let mut child = cmd.spawn().map_err(|_| "cli_unavailable")?;
        self.calls += 1;
        let stdout = child.stdout.take().ok_or("cli_unavailable")?;
        let stderr = child.stderr.take().ok_or("cli_unavailable")?;
        async fn capture<R: tokio::io::AsyncRead + Unpin>(
            r: R,
            max: u64,
        ) -> std::io::Result<Zeroizing<Vec<u8>>> {
            let mut b = Zeroizing::new(vec![]);
            r.take(max + 1).read_to_end(&mut b).await?;
            Ok(b)
        }
        let measured = tokio::time::timeout(self.timeout.min(remaining), async {
            tokio::try_join!(
                child.wait(),
                capture(stdout, 1024 * 1024),
                capture(stderr, 65536)
            )
        })
        .await;
        let (status, out, err) = match measured {
            Ok(Ok(v)) => v,
            _ => {
                let _ = child.kill().await;
                let _ = child.wait().await;
                return Err("timeout_or_io");
            }
        };
        if out.len() > 1024 * 1024 || err.len() > 65536 {
            return Err("output_limit");
        }
        if !status.success() {
            let message = String::from_utf8_lossy(&err);
            return Err(
                if message.contains("AuthorizationFailed") || message.contains("Forbidden") {
                    "access_denied"
                } else if message.contains("ResourceNotFound")
                    || message.contains("RoleDefinitionDoesNotExist")
                {
                    "not_found"
                } else if message.contains("az login") {
                    "login_required"
                } else {
                    "request_failed"
                },
            );
        }
        serde_json::from_slice(&out).map_err(|_| "invalid_response")
    }
}
fn check(rows: &mut Vec<Value>, name: &str, value: Observed, required: bool) -> bool {
    let success = value.is_ok();
    rows.push(match value {
        Ok(detail) => json!({"name":name,"status":"ok","required":required,"detail":detail}),
        Err(code) => json!({"name":name,"status":"blocked","required":required,"code":code}),
    });
    success
}
fn same_id(v: &Value, expected: &str) -> bool {
    v.as_str().is_some_and(|v| v.eq_ignore_ascii_case(expected))
}
fn expected_role(p: &Plan, value: Value) -> Observed {
    let expected = role(p);
    let a = &value["properties"];
    let b = &expected["properties"];
    let permissions = a["permissions"]
        .as_array()
        .filter(|v| v.len() == 1)
        .ok_or("role_permission_mismatch")?;
    let permission = &permissions[0];
    let scopes = a["assignableScopes"]
        .as_array()
        .filter(|s| s.len() == 1 && same_id(&s[0], &scope(p)))
        .ok_or("role_scope_mismatch")?;
    let mut actions: Vec<_> = permission["actions"]
        .as_array()
        .ok_or("role_permission_mismatch")?
        .iter()
        .map(|a| a.as_str().unwrap_or("").to_ascii_lowercase())
        .collect();
    actions.sort();
    let mut required: Vec<_> = ACTIONS.iter().map(|a| a.to_ascii_lowercase()).collect();
    required.sort();
    if value["name"] != guid(p, "setup-role")
        || a["roleName"] != b["roleName"]
        || a["type"] != "CustomRole"
        || actions != required
        || ["notActions", "dataActions", "notDataActions"]
            .iter()
            .any(|k| permission[*k].as_array().is_none_or(|v| !v.is_empty()))
    {
        return Err("role_permission_mismatch");
    }
    Ok(
        json!({"roleDefinitionId":definition_id(p),"assignableScopes":scopes,"exactMetadataActions":true,"dataActions":false,"roleAssignmentWrites":false}),
    )
}
fn expected_assignment(p: &Plan, value: Value) -> Observed {
    let expected = assignment(p);
    let a = &value["properties"];
    let e = &expected["properties"];
    if value["name"] != guid(p, "setup-assignment")
        || !same_id(&a["scope"], &scope(p))
        || !same_id(&a["principalId"], e["principalId"].as_str().unwrap())
        || a["principalType"] != e["principalType"]
        || !same_id(&a["roleDefinitionId"], &definition_id(p))
        || a.get("condition").is_some_and(|v| !v.is_null())
    {
        return Err("assignment_mismatch");
    }
    Ok(
        json!({"roleAssignmentGuid":guid(p,"setup-assignment"),"principalId":e["principalId"],"principalType":e["principalType"],"scope":scope(p),"unconditional":true}),
    )
}
fn fresh_directory(out: &Path, plan: &Path) -> Result<()> {
    let parent = out
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    ensure!(
        !parent.starts_with(plan.canonicalize()?),
        "Preflight output must be outside the immutable plan"
    );
    let mut dir = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        dir.mode(0o700);
    }
    dir.create(out).context("Use a fresh preflight directory")?;
    Ok(())
}
pub async fn preflight(o: &Preflight) -> Result<()> {
    ensure!(
        (1..=60).contains(&o.timeout_seconds) && (1..=600).contains(&o.overall_timeout_seconds),
        "Invalid preflight deadlines"
    );
    let (p, m) = inspect(&o.plan)?;
    let at = now()?;
    ensure!(
        p.created_at_unix <= at && at < p.expires_at_unix,
        "Plan expired or future-dated"
    );
    let i = p
        .azure_bootstrap
        .as_ref()
        .context("Export Azure bootstrap first")?;
    i.validate(&p)?;
    fresh_directory(&o.out, &o.plan)?;
    let Target::Azure {
        tenant,
        subscription,
        location,
    } = &p.target
    else {
        unreachable!()
    };
    let mut api = Az {
        subscription: subscription.clone(),
        timeout: Duration::from_secs(o.timeout_seconds),
        deadline: Instant::now() + Duration::from_secs(o.overall_timeout_seconds),
        calls: 0,
    };
    let mut checks = vec![];
    let overrides = [
        "AZURE_CORE_DISABLE_CONNECTION_VERIFICATION",
        "AZURE_CLOUD_NAME",
    ]
    .iter()
    .any(|k| {
        std::env::var(k)
            .is_ok_and(|v| !v.is_empty() && (*k != "AZURE_CLOUD_NAME" || v != "AzureCloud"))
    });
    let config = if overrides {
        Err("untrusted_cli_configuration")
    } else {
        api.read(&["cloud", "show"], &[]).await.and_then(|v| {
            if v["name"] == "AzureCloud"
                && v["endpoints"]["resourceManager"] == "https://management.azure.com/"
                && v["endpoints"]["activeDirectory"] == "https://login.microsoftonline.com/"
            {
                Ok(json!({"cloud":"AzureCloud","publicManagementEndpoint":true}))
            } else {
                Err("untrusted_cli_cloud")
            }
        })
    };
    let cloud_ok = check(&mut checks, "cli_cloud", config, true);
    let mut scope_ok = false;
    if cloud_ok {
        let account=api.read(&["account","show"],&[]).await.and_then(|v|{
            if v["id"]==*subscription&&v["tenantId"]==*tenant&&v["environmentName"]=="AzureCloud"&&v["state"]=="Enabled"{Ok(json!({"tenant":tenant,"subscription":subscription,"state":"Enabled","callerObjectIdentityVerified":false}))}else{Err("subscription_identity_mismatch")}
        });
        if check(&mut checks, "subscription", account, true) {
            scope_ok=check(&mut checks,"existing_resource_group",api.read(&["group","show"],&["--name".into(),i.resource_group.clone()]).await.and_then(|v|{
              if same_id(&v["id"],&scope(&p))&&v["properties"]["provisioningState"]=="Succeeded"{Ok(json!({"id":scope(&p),"ownership":"customer_provided","deploymentLocation":location}))}else{Err("resource_group_mismatch")}
            }),true);
        }
    }
    if scope_ok {
        check(
            &mut checks,
            "setup_role",
            api.read(
                &["rest"],
                &[
                    "--method".into(),
                    "get".into(),
                    "--url".into(),
                    role_url(&p),
                ],
            )
            .await
            .and_then(|v| expected_role(&p, v)),
            true,
        );
        check(
            &mut checks,
            "setup_assignment",
            api.read(
                &["rest"],
                &[
                    "--method".into(),
                    "get".into(),
                    "--url".into(),
                    assignment_url(&p),
                ],
            )
            .await
            .and_then(|v| expected_assignment(&p, v)),
            true,
        );
        for provider in [
            "Microsoft.ContainerService",
            "Microsoft.Compute",
            "Microsoft.Network",
            "Microsoft.DBforPostgreSQL",
            "Microsoft.ContainerRegistry",
            "Microsoft.Storage",
            "Microsoft.ManagedIdentity",
            "Microsoft.KeyVault",
        ] {
            check(
                &mut checks,
                &format!("provider:{provider}"),
                api.read(
                    &["provider", "show"],
                    &["--namespace".into(), provider.into()],
                )
                .await
                .and_then(|v| {
                    if v["namespace"] == provider && v["registrationState"] == "Registered" {
                        Ok(json!({"namespace":provider,"registered":true}))
                    } else {
                        Err("provider_not_registered")
                    }
                }),
                false,
            );
        }
    }
    let ready = checks
        .iter()
        .all(|v| v["required"] != true || v["status"] == "ok");
    let report = json!({"schemaVersion":1,"provider":"azure","deploymentId":p.deployment_id,"bundleDigest":m.bundle_digest,"observedAtUnix":now()?,"scope":p.target,"resourceGroupScope":scope(&p),"bootstrapReady":ready,"applyReady":false,"mutations":0,"cliCalls":api.calls,"checks":checks,"qualification":{"exactGrantOnly":true,"effectivePermissionsVerified":false,"setupCallerObjectIdentityVerified":false,"infrastructureReady":false,"workloadIdentityReady":false,"expiryGuardInstalled":false,"priceAndQuotaVerified":false},"remaining":["Authenticate the approved setup principal using the customer's login/federation flow","Resolve and grant resource-specific infrastructure actions","Qualify Azure Policy, deny assignments, locks, effective IAM and quotas","Implement Bicep/ARM lifecycle and independent cleanup","Configure AKS workload identity and exact container data grants","Install shared OpenShell/service Helm charts and run live qualification"]});
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut f = options.open(o.out.join("preflight.json"))?;
    f.write_all(&json_bytes(&report)?)?;
    f.sync_all()?;
    crate::output(report)?;
    ensure!(
        ready,
        "Azure bootstrap preflight is blocked; inspect the redacted report"
    );
    Ok(())
}
