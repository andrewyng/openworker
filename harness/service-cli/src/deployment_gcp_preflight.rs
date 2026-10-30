//! Read-only bootstrap observation. No token export, API enablement or IAM writes.
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
    /// Optional private creation journal: pin all five account UIDs before recovery.
    #[arg(long)]
    bootstrap_journal: Option<PathBuf>,
    #[arg(long, default_value_t = 20)]
    timeout_seconds: u64,
    #[arg(long, default_value_t = 300)]
    overall_timeout_seconds: u64,
    /// Explicit prior spend declaration, not a verified billing measurement.
    #[arg(long, default_value_t = 0)]
    spent_usd: u32,
    #[arg(long, default_value_t = 25)]
    variable_allowance_usd: u32,
}
type Observed = std::result::Result<Value, &'static str>;
struct Gcloud {
    project: String,
    account: String,
    timeout: Duration,
    deadline: Instant,
    calls: u32,
}
impl Gcloud {
    async fn read(&mut self, command: &[&str], args: &[String]) -> Observed {
        // No caller-provided command, URL, shell or generic flag forwarding.
        if !matches!(
            command,
            ["config", "list"]
                | ["auth", "list"]
                | ["projects", "describe"]
                | ["billing", "projects", "describe"]
                | ["services", "list"]
                | ["projects", "get-iam-policy"]
                | ["iam", "service-accounts", "describe"]
                | ["iam", "service-accounts", "get-iam-policy"]
                | ["compute", "regions", "describe"]
                | ["container", "get-server-config"]
        ) {
            return Err("operation_not_allowed");
        }
        let remaining = self.deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("timeout");
        }
        let mut cmd = Command::new("gcloud");
        cmd.args(command)
            .args(args)
            .args([
                "--project",
                &self.project,
                "--billing-project",
                &self.project,
                "--account",
                &self.account,
                "--format=json",
                "--quiet",
                "--verbosity=error",
            ])
            .env("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")
            .env("CLOUDSDK_CORE_LOG_HTTP", "false")
            .env("CLOUDSDK_CORE_HTTP_TIMEOUT", "10")
            .env("CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK", "true")
            .env_remove("CLOUDSDK_AUTH_ACCESS_TOKEN")
            .env_remove("CLOUDSDK_AUTH_ACCESS_TOKEN_FILE")
            .env_remove("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE")
            .env_remove("GOOGLE_APPLICATION_CREDENTIALS")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        // Endpoint and impersonation overrides are rejected, not silently used.
        for (key, _) in std::env::vars_os() {
            if key
                .to_string_lossy()
                .starts_with("CLOUDSDK_API_ENDPOINT_OVERRIDES_")
            {
                return Err("endpoint_override");
            }
        }
        for key in [
            "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
            "CLOUDSDK_CORE_UNIVERSE_DOMAIN",
        ] {
            if let Ok(v) = std::env::var(key) {
                if !(v.is_empty() || key.ends_with("UNIVERSE_DOMAIN") && v == "googleapis.com") {
                    return Err("implicit_credential_or_universe_override");
                }
            }
        }
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
        let result = tokio::time::timeout(self.timeout.min(remaining), async {
            tokio::try_join!(
                child.wait(),
                capture(stdout, 4 * 1024 * 1024),
                capture(stderr, 65536)
            )
        })
        .await;
        let (status, out, err) = match result {
            Ok(Ok(v)) => v,
            _ => {
                let _ = child.kill().await;
                let _ = child.wait().await;
                return Err("timeout_or_io");
            }
        };
        if out.len() > 4 * 1024 * 1024 || err.len() > 65536 {
            return Err("output_limit");
        }
        if !status.success() {
            let e = String::from_utf8_lossy(&err);
            return Err(if e.contains("SERVICE_DISABLED") {
                "api_disabled"
            } else if e.contains("PERMISSION_DENIED") || e.contains("does not have permission") {
                "access_denied"
            } else if e.contains("NOT_FOUND") {
                "not_found"
            } else {
                "request_failed"
            });
        }
        serde_json::from_slice(&out).map_err(|_| "invalid_response")
    }
}
fn check(rows: &mut Vec<Value>, name: &str, result: Observed, required: bool) -> bool {
    let ok = result.is_ok();
    rows.push(match result {
        Ok(detail) => json!({"name":name,"status":"ok","required":required,"detail":detail}),
        Err(code) => json!({"name":name,"status":"blocked","required":required,"code":code}),
    });
    ok
}
fn array(v: &Value) -> std::result::Result<&Vec<Value>, &'static str> {
    v.as_array()
        .filter(|v| v.len() <= 1000)
        .ok_or("invalid_or_truncated_response")
}
fn bindings(v: &Value, role: &str, member: &str) -> bool {
    v["bindings"]
        .as_array()
        .map(|bs| {
            bs.iter().any(|b| {
                b["role"] == role
                    && b.get("condition").is_none()
                    && b["members"]
                        .as_array()
                        .map(|ms| ms.iter().any(|m| m == member))
                        .unwrap_or(false)
            })
        })
        .unwrap_or(false)
}
fn safe_project_policy(v: &Value, a: &BTreeMap<String, String>) -> bool {
    let Some(bs) = v["bindings"].as_array() else {
        return false;
    };
    bs.iter().all(|b| {
        a.iter().all(|(kind, email)| {
            let applies = b["members"]
                .as_array()
                .map(|ms| ms.iter().any(|m| m == &format!("serviceAccount:{email}")))
                .unwrap_or(false);
            !applies
                || (b.get("condition").is_none()
                    && match kind.as_str() {
                        "setup" => {
                            b["role"] == "roles/config.admin"
                                || b["role"] == "roles/serviceusage.serviceUsageConsumer"
                        }
                        "executor" => b["role"] == "roles/config.agent",
                        _ => false,
                    })
        })
    })
}
fn account_policy(v: &Value, role: &str, expected: &[String]) -> bool {
    // Extra trust or conditional bindings require explicit review, not guessing
    // whether an inherited/conditional permission happens to be effective now.
    let Some(bs) = v["bindings"].as_array() else {
        return false;
    };
    bs.iter().all(|b| {
        b["role"] == role
            && b.get("condition").is_none()
            && b["members"]
                .as_array()
                .map(|ms| {
                    ms.iter().all(|m| {
                        m.as_str()
                            .map(|s| expected.iter().any(|e| e == s))
                            .unwrap_or(false)
                    })
                })
                .unwrap_or(false)
    }) && expected.iter().all(|m| bindings(v, role, m))
}
fn fresh_directory(out: &Path, plan: &Path) -> Result<()> {
    let parent = out
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    ensure!(
        !parent.starts_with(plan.canonicalize()?),
        "Preflight output must be outside the plan"
    );
    let mut d = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        d.mode(0o700);
    }
    d.create(out)
        .context("Use a fresh preflight output directory")?;
    Ok(())
}
pub async fn preflight(o: &Preflight) -> Result<()> {
    ensure!(
        (1..=60).contains(&o.timeout_seconds) && (1..=600).contains(&o.overall_timeout_seconds),
        "Invalid preflight deadlines"
    );
    let (p, m) = inspect(&o.plan)?;
    let i = p
        .gcp_bootstrap
        .as_ref()
        .context("Export GCP bootstrap first")?;
    let at = now()?;
    ensure!(
        p.created_at_unix <= at && at < p.expires_at_unix,
        "Plan expired or future-dated"
    );
    let committed = o
        .spent_usd
        .checked_add(o.variable_allowance_usd)
        .context("Budget overflow")?;
    ensure!(
        committed < p.budget.authorized_usd,
        "Prior spend plus reserve consumes authorized budget"
    );
    let target = project(&p).to_owned();
    let a = accounts(&p);
    let receipts = o
        .bootstrap_journal
        .as_ref()
        .map(|root| -> Result<BTreeMap<String, String>> {
            let meta = fs::symlink_metadata(root)?;
            ensure!(
                meta.is_dir() && !meta.file_type().is_symlink(),
                "Bootstrap journal must be a directory, not a link"
            );
            a.iter()
                .map(|(role, email)| {
                    let v: Value =
                        serde_json::from_slice(&read_file(root, &format!("{role}.json"))?)?;
                    let uid = v["uniqueId"]
                        .as_str()
                        .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
                        .context("Missing journal account UID")?;
                    ensure!(
                        v["email"] == *email && v["projectId"] == target,
                        "Journal receipt scope mismatch"
                    );
                    Ok((role.clone(), uid.to_owned()))
                })
                .collect()
        })
        .transpose()?;
    fresh_directory(&o.out, &o.plan)?;
    let mut api = Gcloud {
        project: target.clone(),
        account: i.email().into(),
        timeout: Duration::from_secs(o.timeout_seconds),
        deadline: Instant::now() + Duration::from_secs(o.overall_timeout_seconds),
        calls: 0,
    };
    let mut rows = vec![];
    let configuration = api.read(&["config", "list"], &[]).await.and_then(|v| {
        if !v.is_object()
            || ["api_endpoint_overrides", "auth", "core"]
                .iter()
                .any(|k| v.get(k).map(|s| !s.is_object()).unwrap_or(false))
        {
            return Err("invalid_cli_configuration");
        }
        let override_present = v["api_endpoint_overrides"]
            .as_object()
            .map(|m| !m.is_empty())
            .unwrap_or(false);
        let auth = [
            "impersonate_service_account",
            "credential_file_override",
            "access_token_file",
        ]
        .iter()
        .any(|k| {
            v["auth"][k]
                .as_str()
                .map(|s| !s.is_empty())
                .unwrap_or(false)
        });
        let universe = v["core"]["universe_domain"]
            .as_str()
            .unwrap_or("googleapis.com");
        if !v.is_object() || override_present || auth || universe != "googleapis.com" {
            Err("untrusted_cli_configuration")
        } else {
            Ok(json!({"publicGoogleApis":true,"implicitImpersonation":false}))
        }
    });
    let config_ok = check(&mut rows, "cli_configuration", configuration, true);
    let mut scope_ok = false;
    if config_ok {
        let identity=api.read(&["auth","list"],&["--filter=status:ACTIVE".into()]).await.and_then(|v|{let xs=array(&v)?;if xs.len()==1&&xs[0]["account"]==i.email()&&xs[0]["status"]=="ACTIVE"{Ok(json!({"expectedOperator":i.operator_member,"source":"configured account plus authenticated project read; no token export"}))}else{Err("identity_mismatch")}});
        if check(&mut rows, "operator_identity", identity, true) {
            let scope = api
                .read(&["projects", "describe"], std::slice::from_ref(&target))
                .await
                .and_then(|v| {
                    if v["projectId"] == target
                        && v["projectNumber"] == i.project_number
                        && v["lifecycleState"] == "ACTIVE"
                    {
                        Ok(json!({"project":target,"projectNumber":i.project_number,"active":true}))
                    } else {
                        Err("project_identity_mismatch")
                    }
                });
            scope_ok = check(&mut rows, "project_identity", scope, true);
        }
    }
    if scope_ok {
        check(
            &mut rows,
            "billing_link",
            api.read(
                &["billing", "projects", "describe"],
                std::slice::from_ref(&target),
            )
            .await
            .and_then(|v| {
                if v["projectId"] == target
                    && v["billingEnabled"] == true
                    && v["billingAccountName"]
                        .as_str()
                        .map(|s| s.starts_with("billingAccounts/"))
                        .unwrap_or(false)
                {
                    Ok(json!({"enabled":true,"accountIdOmitted":true}))
                } else {
                    Err("billing_not_enabled_or_mismatched")
                }
            }),
            true,
        );
        let services = api
            .read(
                &["services", "list"],
                &["--enabled".into(), "--limit=1001".into()],
            )
            .await
            .and_then(|v| {
                let xs = array(&v)?;
                let mut names = vec![];
                for x in xs {
                    names.push(
                        x["config"]["name"]
                            .as_str()
                            .ok_or("invalid_response")?
                            .to_owned(),
                    );
                }
                Ok(json!(names))
            });
        let enabled = services
            .as_ref()
            .ok()
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        check(
            &mut rows,
            "enabled_api_inventory",
            services.map(|v| json!({"count":v.as_array().unwrap().len()})),
            true,
        );
        let missing: Vec<_> = BOOTSTRAP_APIS
            .iter()
            .filter(|s| !enabled.iter().any(|v| v == **s))
            .copied()
            .collect();
        let iam_ready = enabled.iter().any(|v| v == "iam.googleapis.com");
        rows.push(json!({"name":"bootstrap_apis","status":if missing.is_empty(){"ok"}else{"blocked"},"required":true,"missing":missing}));
        let infra_missing: Vec<_> = INFRASTRUCTURE_APIS
            .iter()
            .filter(|s| !enabled.iter().any(|v| v == **s))
            .copied()
            .collect();
        rows.push(json!({"name":"infrastructure_apis","status":if infra_missing.is_empty(){"ok"}else{"blocked"},"required":false,"missing":infra_missing}));
        let policy = api
            .read(
                &["projects", "get-iam-policy"],
                std::slice::from_ref(&target),
            )
            .await;
        let policy_ok = policy
            .as_ref()
            .map(|v| {
                safe_project_policy(v, &a)
                    && bindings(
                        v,
                        "roles/config.admin",
                        &format!("serviceAccount:{}", a["setup"]),
                    )
                    && bindings(
                        v,
                        "roles/config.agent",
                        &format!("serviceAccount:{}", a["executor"]),
                    )
                    && bindings(
                        v,
                        "roles/serviceusage.serviceUsageConsumer",
                        &format!("serviceAccount:{}", a["setup"]),
                    )
            })
            .unwrap_or(false);
        check(&mut rows,"project_bootstrap_bindings",policy.and_then(|_|if policy_ok{Ok(json!({"reviewedDirectBindings":true,"notEffectivePermissionProof":true,"projectWideImpersonation":false}))}else{Err("missing_or_excess_bootstrap_bindings")}),true);
        if iam_ready {
            // Google-managed identities are not readable through customer IAM Get
            // permissions. Verify their exact executor trust below; initialization
            // is explicit in the bootstrap's Service Usage receipt, not an excuse
            // to request broader IAM read privileges on Google's internal project.
            for (role, email) in &a {
                let receipt_uid = receipts.as_ref().and_then(|r| r.get(role));
                check(
                    &mut rows,
                    &format!("identity_{role}"),
                    api.read(
                        &["iam", "service-accounts", "describe"],
                        std::slice::from_ref(email),
                    )
                    .await
                    .and_then(|v| {
                        if v["email"] == *email
                            && v["projectId"] == target
                            && v["disabled"] != true
                            && receipt_uid.map(|uid|v["uniqueId"]==*uid).unwrap_or(true)
                            && v["uniqueId"]
                                .as_str()
                                .map(|s| !s.is_empty())
                                .unwrap_or(false)
                        {
                            Ok(json!({"email":email,"uniqueId":v["uniqueId"],"journalReceiptMatched":receipt_uid.map(|_|true)}))
                        } else {
                            Err("identity_missing_disabled_or_mismatched")
                        }
                    }),
                    true,
                );
            }
            for (role, grant, members) in [
                (
                    "setup",
                    "roles/iam.serviceAccountTokenCreator",
                    vec![i.operator_member.clone()],
                ),
                (
                    "executor",
                    "roles/iam.serviceAccountUser",
                    vec![
                        format!("serviceAccount:{}", a["setup"]),
                        format!(
                            "serviceAccount:service-{}@gcp-sa-config.iam.gserviceaccount.com",
                            i.project_number
                        ),
                    ],
                ),
            ] {
                check(&mut rows,&format!("trust_{role}"),api.read(&["iam","service-accounts","get-iam-policy"],std::slice::from_ref(&a[role])).await.and_then(|v|if account_policy(&v,grant,&members){Ok(json!({"exactDirectBindings":true,"notEffectivePermissionProof":true}))}else{Err("missing_conditional_or_excess_trust")}),true);
            }
        } else {
            check(
                &mut rows,
                "bootstrap_identities",
                Err("iam_api_disabled"),
                true,
            );
        }
        if enabled.iter().any(|v| v == "compute.googleapis.com") {
            let Target::Gcp { location, .. } = &p.target else {
                unreachable!()
            };
            check(&mut rows,"regional_quota_observation",api.read(&["compute","regions","describe"],std::slice::from_ref(location)).await.and_then(|v|{
                if v["name"]!=*location{return Err("region_mismatch");}
                let qs=v["quotas"].as_array().ok_or("invalid_response")?;
                let q=qs.iter().find(|v|v["metric"]=="CPUS").ok_or("missing_cpu_quota")?;
                let limit=q["limit"].as_f64().filter(|v|v.is_finite()&&*v>=0.).ok_or("invalid_quota")?;
                let used=q["usage"].as_f64().filter(|v|v.is_finite()&&*v>=0.&&*v<=limit).ok_or("invalid_quota")?;
                Ok(json!({"cpuLimit":limit,"cpuUsage":used,"cpuRemaining":limit-used,"notCapacityReservation":true,"topologyQuotaCheckPending":true}))
            }),false);
        }
        if enabled.iter().any(|v| v == "container.googleapis.com") {
            let Target::Gcp { location, .. } = &p.target else {
                unreachable!()
            };
            check(&mut rows,"gke_version_observation",api.read(&["container","get-server-config"],&[format!("--region={location}")]).await.and_then(|v|{let xs=v["validMasterVersions"].as_array().filter(|v|!v.is_empty()).ok_or("invalid_gke_response")?;Ok(json!({"availableVersionCount":xs.len(),"pinnedVersionAndNodeOsPending":true}))}),false);
        }
    }
    let ready = scope_ok
        && rows
            .iter()
            .filter(|r| r["required"] == true)
            .all(|r| r["status"] == "ok");
    let report = json!({"schemaVersion":1,"provider":"gcp","scope":"bootstrap_preflight_only","deploymentId":p.deployment_id,"bundleDigest":m.bundle_digest,"target":p.target,"observedAtUnix":at,"validUntilUnix":(now()?+300).min(p.expires_at_unix),"bootstrapReady":ready,"applyReady":false,"gcloudInvocations":api.calls,"checks":rows,"budget":{"authorizedUsd":p.budget.authorized_usd,"declaredSpentUsd":o.spent_usd,"variableAllowanceUsd":o.variable_allowance_usd,"availableForEstimateUsd":p.budget.authorized_usd-committed,"verifiedActualSpend":null,"estimate":null,"hardCap":false},"deploymentGates":["resolved Terraform graph and resource-specific executor permissions","effective organization policies and private cluster-management connectivity","pinned GKE/node OS, OpenShell and image compatibility","full topology quotas and conservative price estimate","independent expiry/cleanup implementation and qualification","cloud lifecycle implementation"],"cloudMutations":0,"limits":["CLI invocations are not HTTP request counts; gcloud can retry reads internally","direct IAM bindings are not effective-permission or organization-policy proof","no setup-role token or model credential is obtained or persisted"]});
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
    crate::output(
        json!({"bootstrapReady":ready,"applyReady":false,"cloudMutations":0,"report":o.out.join("preflight.json"),"gcloudInvocations":api.calls}),
    )?;
    ensure!(
        ready,
        "GCP bootstrap checks blocked; inspect the private preflight report"
    );
    Ok(())
}
