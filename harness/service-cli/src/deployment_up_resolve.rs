//! Read-only machine resolution. Freeze an exact eligible choice before effects.
use super::*;
use config::{Cloud, Config};
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Node {
    pub instance_type: String,
    pub cpu: u32,
    #[serde(rename = "memoryGiB")]
    pub memory_gi_b: u32,
    pub architecture: String,
    pub eligible: bool,
    pub hourly_usd: Option<f64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Price {
    pub currency: String,
    pub worker_hourly_usd: Option<f64>,
    pub total_hourly_usd: Option<f64>,
    pub known_hourly_subtotal_usd: Option<f64>,
    pub components: BTreeMap<String, pricing::Line>,
    pub variable_allowance_usd: u32,
    pub planned_hours: u32,
    pub unpriced_components: Vec<String>,
    pub hard_cap: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Resolution {
    pub node: Node,
    pub price: Price,
    pub observed_at_unix: u64,
    pub identity: Value,
}
impl Resolution {
    pub fn validate(&self, c: &Config) -> Result<()> {
        ensure!(
            eligible(c, &self.node),
            "Resolved node no longer matches the reviewed configuration"
        );
        ensure!(
            self.price.currency == "USD" && !self.price.hard_cap,
            "Invalid price currency or hard-cap claim"
        );
        for n in [
            self.node.hourly_usd,
            self.price.worker_hourly_usd,
            self.price.total_hourly_usd,
        ]
        .into_iter()
        .flatten()
        {
            ensure!(
                n.is_finite() && n > 0.0,
                "Unknown/invalid prices must not be represented as zero"
            );
        }
        ensure!(
            self.observed_at_unix > 0,
            "Missing resolution observation time"
        );
        Ok(())
    }
}
fn eligible(c: &Config, n: &Node) -> bool {
    let architecture = if c.cloud.provider() == "aws" {
        "arm64"
    } else {
        "amd64"
    };
    n.eligible
        && n.architecture == architecture
        && n.cpu >= 2
        && n.memory_gi_b >= 8
        && u32::from(c.nodes.count) * n.cpu >= 4
        && u32::from(c.nodes.count) * n.memory_gi_b >= 16
        && match (&c.nodes.instance_type, &c.nodes.requirements) {
            (Some(size), None) => size == &n.instance_type,
            (None, Some(r)) => {
                r.cpu.min <= n.cpu
                    && n.cpu <= r.cpu.max
                    && r.memory_gi_b.min <= n.memory_gi_b
                    && n.memory_gi_b <= r.memory_gi_b.max
            }
            _ => false,
        }
}
pub(super) fn choose(c: &Config, nodes: Vec<Node>) -> Result<Node> {
    let mut candidates: Vec<_> = nodes.into_iter().filter(|n| eligible(c, n)).collect();
    ensure!(!candidates.is_empty(),"No reviewed node size satisfies your requirements and regional availability. Change the range/type or request quota; no larger fallback was selected.");
    // Do not label an unknown price cheapest. Deterministic resource ordering is
    // an explicit fallback and the review marks the estimate incomplete.
    candidates.sort_by(|a, b| {
        match (a.hourly_usd, b.hourly_usd) {
            (Some(a), Some(b)) => a.total_cmp(&b),
            (Some(_), None) => std::cmp::Ordering::Less,
            (None, Some(_)) => std::cmp::Ordering::Greater,
            _ => a.cpu.cmp(&b.cpu).then(a.memory_gi_b.cmp(&b.memory_gi_b)),
        }
        .then(a.instance_type.cmp(&b.instance_type))
    });
    Ok(candidates.remove(0))
}
pub(super) async fn read(c: &Config, command: &[&str], args: Vec<String>) -> Result<Value> {
    let (bin, mut all) = match &c.cloud {
        Cloud::Aws {
            region, profile, ..
        } => {
            ensure!(
                matches!(
                    command,
                    ["sts", "get-caller-identity"]
                        | ["ec2", "describe-instance-types"]
                        | ["ec2", "describe-instance-type-offerings"]
                        | ["ec2", "describe-availability-zones"]
                        | ["pricing", "get-products"]
                        | ["eks", "describe-cluster-versions"]
                        | ["eks", "describe-addon-versions"]
                        | ["ssm", "get-parameter"]
                        | ["rds", "describe-db-engine-versions"]
                ),
                "Unapproved read operation"
            );
            let r = if command == ["pricing", "get-products"] {
                "us-east-1"
            } else {
                region
            };
            (
                "aws",
                [
                    command.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
                    vec![
                        "--profile".into(),
                        profile.clone(),
                        "--region".into(),
                        r.into(),
                        "--output".into(),
                        "json".into(),
                        "--no-cli-pager".into(),
                    ],
                ]
                .concat(),
            )
        }
        Cloud::Azure {
            subscription_id, ..
        } => {
            ensure!(
                matches!(
                    command,
                    ["account", "show"]
                        | ["cloud", "show"]
                        | ["vm", "list-skus"]
                        | ["vm", "list-usage"]
                        | ["aks", "get-versions"]
                        | ["ad", "signed-in-user", "show"]
                ),
                "Unapproved read operation"
            );
            let mut v = command.iter().map(|s| s.to_string()).collect::<Vec<_>>();
            if command != ["cloud", "show"] && command != ["ad", "signed-in-user", "show"] {
                v.extend(["--subscription".into(), subscription_id.clone()]);
            }
            v.extend([
                "--output".into(),
                "json".into(),
                "--only-show-errors".into(),
            ]);
            ("az", v)
        }
    };
    all.extend(args);
    let raw=super::super::local::exec(Path::new(bin),&all,None).await.context("Cloud prerequisite read failed. Check CLI login, target permissions and connectivity; no provisioning occurred.")?;
    serde_json::from_slice(&raw).context("Cloud returned an invalid prerequisite response")
}
pub(super) async fn identity(c: &Config) -> Result<Value> {
    match &c.cloud {
        Cloud::Aws { account_id, .. } => {
            let v = read(c, &["sts", "get-caller-identity"], vec![]).await?;
            ensure!(
                v["Account"] == *account_id
                    && v["Arn"].as_str().is_some_and(
                        |s| s.starts_with("arn:aws:") && s.contains(&format!(":{account_id}:"))
                    ),
                "AWS login differs from the configured account"
            );
            Ok(json!({"accountId":account_id,"principal":v["Arn"]}))
        }
        Cloud::Azure {
            subscription_id, ..
        } => {
            let cloud = read(c, &["cloud", "show"], vec![]).await?;
            ensure!(
                cloud["name"] == "AzureCloud",
                "Only Azure public cloud is qualified by this release"
            );
            let v = read(c, &["account", "show"], vec![]).await?;
            ensure!(
                v["id"] == *subscription_id && v["state"] == "Enabled",
                "Azure login/subscription is not the configured enabled target"
            );
            let tenant = v["tenantId"].as_str().context("Azure tenant missing")?;
            ensure!(
                uuid::Uuid::parse_str(tenant).is_ok_and(|id| !id.is_nil()),
                "Invalid Azure tenant"
            );
            if let Some(wanted) = c.advanced.azure.as_ref().and_then(|a| a.tenant_id.as_ref()) {
                ensure!(wanted == tenant, "Azure tenant mismatch");
            }
            Ok(json!({"subscriptionId":subscription_id,"tenantId":tenant}))
        }
    }
}
fn capability<'a>(v: &'a Value, name: &str) -> Option<&'a str> {
    v["capabilities"]
        .as_array()?
        .iter()
        .find(|r| r["name"] == name)?["value"]
        .as_str()
}
pub(super) fn azure_nodes(c: &Config, v: &Value) -> Result<Vec<Node>> {
    let rows = v.as_array().context("Azure SKU inventory malformed")?;
    let mut out = vec![];
    for (name, cpu, mem) in [
        ("Standard_D4s_v5", 4, 16),
        ("Standard_D8s_v5", 8, 32),
        ("Standard_D4as_v7", 4, 16),
    ] {
        let Some(row) = rows
            .iter()
            .find(|r| r["name"] == name && r["resourceType"] == "virtualMachines")
        else {
            continue;
        };
        let locations = row["locations"]
            .as_array()
            .context("Azure SKU locations missing")?;
        let restrictions = row["restrictions"]
            .as_array()
            .context("Azure SKU restrictions missing")?;
        let restricted = restrictions.iter().any(|r| r["type"] != "Zone");
        let capable = capability(row, "vCPUs").and_then(|s| s.parse::<u32>().ok()) == Some(cpu)
            && capability(row, "MemoryGB").and_then(|s| s.parse::<u32>().ok()) == Some(mem)
            && capability(row, "CpuArchitectureType") == Some("x64");
        out.push(Node {
            instance_type: name.into(),
            cpu,
            memory_gi_b: mem,
            architecture: "amd64".into(),
            eligible: !restricted && capable && locations.iter().any(|r| r == c.cloud.region()),
            hourly_usd: None,
        });
    }
    Ok(out)
}
pub(super) fn aws_nodes(types: &Value, offers: &Value) -> Result<Vec<Node>> {
    let rows = types["InstanceTypes"]
        .as_array()
        .context("AWS type inventory malformed")?;
    let offers = offers["InstanceTypeOfferings"]
        .as_array()
        .context("AWS offerings malformed")?;
    let mut out = vec![];
    for name in ["t4g.large", "t4g.xlarge", "t4g.2xlarge"] {
        let Some(r) = rows.iter().find(|r| r["InstanceType"] == name) else {
            continue;
        };
        let (cpu, mem) = super::super::infrastructure::worker_shape(name).unwrap();
        let ok = r["VCpuInfo"]["DefaultVCpus"] == cpu
            && r["MemoryInfo"]["SizeInMiB"] == mem * 1024
            && r["ProcessorInfo"]["SupportedArchitectures"]
                .as_array()
                .is_some_and(|a| a.iter().any(|s| s == "arm64"));
        out.push(Node {
            instance_type: name.into(),
            cpu,
            memory_gi_b: mem,
            architecture: "arm64".into(),
            eligible: ok && offers.iter().any(|r| r["InstanceType"] == name),
            hourly_usd: None,
        });
    }
    Ok(out)
}
pub(super) async fn live(c: &Config, infrastructure: &Value) -> Result<Resolution> {
    eprintln!("Checking cloud identity, supported node sizes and quota…");
    let identity = identity(c).await?;
    let mut candidates = match &c.cloud {
        Cloud::Azure { region, .. } => azure_nodes(
            c,
            &read(
                c,
                &["vm", "list-skus"],
                vec![
                    "--location".into(),
                    region.clone(),
                    "--resource-type".into(),
                    "virtualMachines".into(),
                    "--size".into(),
                    "Standard_D".into(),
                    "--all".into(),
                ],
            )
            .await?,
        )?,
        Cloud::Aws { .. } => {
            let types = read(
                c,
                &["ec2", "describe-instance-types"],
                vec![
                    "--instance-types".into(),
                    "t4g.large".into(),
                    "t4g.xlarge".into(),
                    "t4g.2xlarge".into(),
                ],
            )
            .await?;
            let offers = read(
                c,
                &["ec2", "describe-instance-type-offerings"],
                vec![
                    "--location-type".into(),
                    "region".into(),
                    "--filters".into(),
                    "Name=instance-type,Values=t4g.large,t4g.xlarge,t4g.2xlarge".into(),
                ],
            )
            .await?;
            aws_nodes(&types, &offers)?
        }
    };
    if c.cloud.provider() == "azure" {
        let usage = read(
            c,
            &["vm", "list-usage"],
            vec!["--location".into(), c.cloud.region().into()],
        )
        .await?;
        let management = infrastructure
            .get("managementAccess")
            .is_some_and(|v| !v.is_null());
        for node in &mut candidates {
            let family = match node.instance_type.as_str() {
                "Standard_D4s_v5" | "Standard_D8s_v5" => "standardDSv5Family",
                _ => "standardDasv7Family",
            };
            node.eligible &= (!management || node.instance_type == "Standard_D4as_v7")
                && quota_available(
                    &usage,
                    "cores",
                    u64::from(c.nodes.count) * u64::from(node.cpu) + if management { 2 } else { 0 },
                )
                && quota_available(
                    &usage,
                    family,
                    u64::from(c.nodes.count) * u64::from(node.cpu) + if management { 2 } else { 0 },
                );
        }
    }
    eprintln!("Reading price catalogs; unavailable rates will remain marked unknown…");
    pricing::price_nodes(c, &mut candidates).await;
    let node = choose(c, candidates)?;
    let price = pricing::estimate(c, &node, infrastructure).await;
    Ok(Resolution {
        node,
        price,
        observed_at_unix: now()?,
        identity,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> Config {
        serde_json::from_value(json!({"name":"example","cloud":{"provider":"azure","subscriptionId":"11111111-2222-4333-8444-555555555555","region":"westus2"},"nodes":{"count":2,"requirements":{"cpu":{"min":4,"max":8},"memoryGiB":{"min":16,"max":32}}},"budget":{"alertUSD":100}})).unwrap()
    }
    fn node(name: &str, cpu: u32, price: Option<f64>) -> Node {
        Node {
            instance_type: name.into(),
            cpu,
            memory_gi_b: cpu * 4,
            architecture: "amd64".into(),
            eligible: true,
            hourly_usd: price,
        }
    }
    #[test]
    fn resolution_obeys_bounds_and_never_claims_unknown_is_free() {
        let c = config();
        assert_eq!(
            choose(
                &c,
                vec![node("small", 4, None), node("known", 8, Some(0.2))]
            )
            .unwrap()
            .instance_type,
            "known"
        );
        assert!(choose(&c, vec![node("too-big", 16, Some(0.1))]).is_err());
        let mut bad = node("wrong-arch", 4, None);
        bad.architecture = "arm64".into();
        assert!(choose(&c, vec![bad]).is_err());
    }
    #[test]
    fn subscription_restrictions_and_unverified_capabilities_exclude_node() {
        let c = config();
        let sku = json!([{"name":"Standard_D4s_v5","resourceType":"virtualMachines","locations":["westus2"],"restrictions":[{"type":"Location"}],"capabilities":[{"name":"vCPUs","value":"4"},{"name":"MemoryGB","value":"16"},{"name":"CpuArchitectureType","value":"x64"}]}]);
        assert!(choose(&c, azure_nodes(&c, &sku).unwrap()).is_err());
    }
}

fn quota_available(v: &Value, name: &str, needed: u64) -> bool {
    let Some(rows) = v.as_array() else {
        return false;
    };
    let rows: Vec<_> = rows
        .iter()
        .filter(|r| {
            r["name"]["value"]
                .as_str()
                .is_some_and(|n| n.eq_ignore_ascii_case(name))
        })
        .collect();
    if rows.len() != 1 {
        return false;
    }
    let count = |v: &Value| v.as_u64().or_else(|| v.as_str()?.parse().ok());
    match (count(&rows[0]["limit"]), count(&rows[0]["currentValue"])) {
        (Some(limit), Some(used)) => limit.checked_sub(used).is_some_and(|n| n >= needed),
        _ => false,
    }
}

#[cfg(test)]
mod quota_tests {
    use super::*;
    #[test]
    fn quota_requires_unambiguous_remaining_capacity() {
        let row = json!({"name":{"value":"standardDasv7Family"},"limit":12,"currentValue":2});
        assert!(quota_available(
            &json!([row.clone()]),
            "StandardDasv7Family",
            10
        ));
        assert!(!quota_available(
            &json!([row.clone()]),
            "StandardDasv7Family",
            11
        ));
        assert!(!quota_available(
            &json!([row.clone(), row]),
            "StandardDasv7Family",
            1
        ));
        assert!(!quota_available(&json!([]), "cores", 1));
    }
}
