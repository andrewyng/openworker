//! Read-only Azure bootstrap and selected compute observation. Never enables providers, exports access
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
                | ["vm", "list-skus"]
                | ["vm", "list-usage"]
                | ["rest"]
        ) {
            return Err("operation_not_allowed");
        }
        if command == ["rest"]
            && !((args.len() == 4 || (args.len() == 6 && args[4] == "--query"))
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
        cmd.args(command).args(args);
        // `az cloud show` reads CLI-wide cloud configuration and rejects
        // --subscription. Every resource-scoped read must still pin the target.
        if command != ["cloud", "show"] {
            cmd.args(["--subscription", &self.subscription]);
        }
        cmd.args(["--only-show-errors", "--output", "json"])
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
                    || message.contains("ResourceGroupNotFound")
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
fn selected_sku(value: Value, size: &str, location: &str) -> Observed {
    let (family, cores) = graph::node_sku(size).ok_or("unsupported_node_sku")?;
    let rows = value
        .as_array()
        .filter(|v| v.len() == 1)
        .ok_or("sku_not_found_or_ambiguous")?;
    let sku = &rows[0];
    if sku["name"] != size
        || sku["resourceType"] != "virtualMachines"
        || !same_id(&sku["family"], family)
        || !sku["locations"]
            .as_array()
            .is_some_and(|v| v.iter().any(|l| same_id(l, location)))
    {
        return Err("sku_identity_mismatch");
    }
    let capabilities = sku["capabilities"]
        .as_array()
        .ok_or("sku_capabilities_missing")?;
    let cap = |name: &str| -> Option<&str> {
        let mut found = capabilities.iter().filter(|c| c["name"] == name);
        let value = found.next()?["value"].as_str()?;
        found.next().is_none().then_some(value)
    };
    if cap("CpuArchitectureType") != Some("x64")
        || cap("vCPUs").and_then(|v| v.parse::<u64>().ok()) != Some(cores)
        || !cap("MemoryGB")
            .and_then(|v| v.parse::<f64>().ok())
            .is_some_and(|v| v.is_finite() && v >= (cores * 4) as f64)
        || cap("PremiumIO") != Some("True")
    {
        return Err("sku_capability_mismatch");
    }
    let restrictions = sku["restrictions"]
        .as_array()
        .ok_or("sku_restrictions_missing")?;
    // The reference template selects no availability zones. Keep zone-only
    // restrictions visible; location/unknown restrictions must block deployment.
    if restrictions.iter().any(|r| r["type"] != "Zone") {
        return Err("sku_location_restricted");
    }
    Ok(
        json!({"name":size,"family":family,"vcpusPerNode":cores,"architecture":"amd64","zoneRestrictions":restrictions,"zonalDeployment":false,"allocationGuaranteed":false,"aksRuntimeQualified":false}),
    )
}
fn quota_headroom(value: Value, family: &str, needed: u64) -> Observed {
    fn count(v: &Value) -> Option<u64> {
        v.as_u64().or_else(|| v.as_str()?.parse().ok())
    }
    let rows = value.as_array().ok_or("quota_invalid_response")?;
    let mut limits = vec![];
    for name in ["cores", family] {
        let mut found = rows.iter().filter(|r| same_id(&r["name"]["value"], name));
        let row = found.next().ok_or("quota_missing")?;
        if found.next().is_some() {
            return Err("quota_ambiguous");
        }
        let used = count(&row["currentValue"]).ok_or("quota_invalid_response")?;
        let limit = count(&row["limit"]).ok_or("quota_invalid_response")?;
        let available = limit.checked_sub(used).ok_or("quota_exceeded")?;
        limits.push(json!({"name":name,"used":used,"limit":limit,"available":available}));
        if available < needed {
            return Err("insufficient_compute_quota");
        }
    }
    Ok(
        json!({"requiredVcpus":needed,"limits":limits,"scope":"initial_node_pool_only","managementCapacityIncluded":false,"surgeCapacityIncluded":false,"capacityReserved":false}),
    )
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
        // SKU enumeration may take over a minute even with --size: Azure only
        // supports server-side filtering by location. Retain an explicit bound
        // and the independent overall deadline instead of retrying the read.
        (1..=180).contains(&o.timeout_seconds) && (1..=600).contains(&o.overall_timeout_seconds),
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
                && matches!(
                    v["endpoints"]["activeDirectory"].as_str(),
                    Some(
                        "https://login.microsoftonline.com" | "https://login.microsoftonline.com/"
                    )
                )
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
    let bootstrap_ready = checks
        .iter()
        .all(|v| v["required"] != true || v["status"] == "ok");
    let mut sku_verified = false;
    let mut quota_verified = false;
    if scope_ok {
        if let Some(config) = &p.azure_infrastructure {
            let size = &config.node_vm_size;
            // Use the documented server-side location filter. `az vm list-skus`
            // can enumerate the entire subscription before filtering locally.
            let mut url = reqwest::Url::parse(&format!("https://management.azure.com/subscriptions/{subscription}/providers/Microsoft.Compute/skus"))?;
            url.query_pairs_mut()
                .append_pair("api-version", "2021-07-01")
                .append_pair("$filter", &format!("location eq '{location}'"));
            let selection = if config.management_access.is_some() {
                format!("(name=='{size}' || name=='Standard_D2as_v7')")
            } else {
                format!("name=='{size}'")
            };
            let query = format!(
                "{{skus:value[?resourceType=='virtualMachines' && {selection}],nextLink:nextLink}}"
            );
            let catalog = api
                .read(
                    &["rest"],
                    &[
                        "--method".into(),
                        "get".into(),
                        "--url".into(),
                        url.to_string(),
                        "--query".into(),
                        query,
                    ],
                )
                .await;
            let select = |value: Value, name: &str| -> Observed {
                if !value["nextLink"].is_null() {
                    return Err("sku_catalog_truncated");
                }
                let rows = value["skus"].as_array().ok_or("sku_invalid_response")?;
                selected_sku(
                    Value::Array(rows.iter().filter(|r| r["name"] == name).cloned().collect()),
                    name,
                    location,
                )
            };
            sku_verified = check(
                &mut checks,
                "selected_node_sku",
                catalog.clone().and_then(|v| select(v, size)),
                true,
            );
            if config.management_access.is_some() {
                check(
                    &mut checks,
                    "management_sku",
                    catalog.and_then(|v| select(v, "Standard_D2as_v7")),
                    true,
                );
            }
            let (family, cores) = graph::node_sku(size).context("Unsupported node SKU")?;
            quota_verified = check(
                &mut checks,
                "selected_node_quota",
                api.read(
                    &["vm", "list-usage"],
                    &["--location".into(), location.clone()],
                )
                .await
                .and_then(|v| {
                    quota_headroom(
                        v,
                        family,
                        cores * u64::from(p.topology.node_count)
                            + if config.management_access.is_some() {
                                2
                            } else {
                                0
                            },
                    )
                    .map(|mut detail| {
                        if config.management_access.is_some() {
                            detail["scope"] = json!("initial_node_pool_and_management");
                            detail["managementVcpusIncluded"] = json!(2);
                            detail["managementCapacityIncluded"] = json!(true);
                        }
                        detail
                    })
                }),
                true,
            );
        }
    }
    let ready = checks
        .iter()
        .all(|v| v["required"] != true || v["status"] == "ok");
    let report = json!({"schemaVersion":1,"provider":"azure","deploymentId":p.deployment_id,"bundleDigest":m.bundle_digest,"observedAtUnix":now()?,"scope":p.target,"resourceGroupScope":scope(&p),"bootstrapReady":bootstrap_ready,"preflightReady":ready,"applyReady":false,"mutations":0,"cliCalls":api.calls,"checks":checks,"qualification":{"exactGrantOnly":true,"effectivePermissionsVerified":false,"setupCallerObjectIdentityVerified":false,"infrastructureReady":false,"workloadIdentityReady":false,"expiryGuardInstalled":false,"priceAndQuotaVerified":false,"selectedSkuVerified":sku_verified,"initialNodeQuotaVerified":quota_verified},"remaining":["Authenticate the approved setup principal using the customer's login/federation flow","Resolve and grant resource-specific infrastructure actions","Qualify Azure Policy, deny assignments, locks, effective IAM and quotas","Qualify ARM lifecycle and independent cleanup in the target subscription","Configure AKS workload identity and exact container data grants","Install shared OpenShell/service Helm charts and run live qualification"]});
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
        "Azure preflight is blocked; inspect the redacted report"
    );
    Ok(())
}

#[cfg(test)]
mod compute_tests {
    use super::*;
    #[test]
    fn quota_requires_both_unique_well_formed_limits_and_available_headroom() {
        let rows = json!([
            {"name":{"value":"cores"},"currentValue":"0","limit":"12"},
            {"name":{"value":"standarddasv7family"},"currentValue":2,"limit":10}
        ]);
        assert!(quota_headroom(rows.clone(), "StandardDasv7Family", 8).is_ok());
        assert!(quota_headroom(rows.clone(), "StandardDasv7Family", 9).is_err());
        for field in ["currentValue", "limit"] {
            for invalid in [json!(-1), json!(null), json!("bad"), json!(1.5)] {
                let mut changed = rows.clone();
                changed[1][field] = invalid;
                assert!(quota_headroom(changed, "StandardDasv7Family", 8).is_err());
            }
        }
        assert!(quota_headroom(json!([rows[0]]), "StandardDasv7Family", 8).is_err());
        assert!(
            quota_headroom(json!([rows[0], rows[1], rows[1]]), "StandardDasv7Family", 8).is_err()
        );
    }
    #[test]
    fn sku_observation_matches_exact_size_region_family_and_architecture() {
        let sku = json!([{"name":"Standard_D4as_v7","resourceType":"virtualMachines",
            "family":"StandardDasv7Family","locations":["westus2"],"restrictions":[],
            "capabilities":[{"name":"CpuArchitectureType","value":"x64"},
                {"name":"vCPUs","value":"4"},{"name":"MemoryGB","value":"16"},
                {"name":"PremiumIO","value":"True"}]}]);
        assert!(selected_sku(sku.clone(), "Standard_D4as_v7", "westus2").is_ok());
        for (field, value) in [
            ("family", json!("unrelated")),
            ("locations", json!(["eastus"])),
            ("restrictions", json!(null)),
            ("capabilities", json!([])),
        ] {
            let mut changed = sku.clone();
            changed[0][field] = value;
            assert!(selected_sku(changed, "Standard_D4as_v7", "westus2").is_err());
        }
        assert!(selected_sku(json!([sku[0], sku[0]]), "Standard_D4as_v7", "westus2").is_err());
        let mut zonal = sku.clone();
        zonal[0]["restrictions"] = json!([{"type":"Zone","restrictionInfo":{"zones":["1"]}}]);
        assert!(
            !selected_sku(zonal, "Standard_D4as_v7", "westus2").unwrap()["zoneRestrictions"]
                .as_array()
                .unwrap()
                .is_empty()
        );
    }
}
