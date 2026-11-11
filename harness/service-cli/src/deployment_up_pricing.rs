//! Public catalog observations, not a bill or spending cap. Missing and ambiguous
//! prices stay unknown; variable allowances are visible and are never upper bounds.
use super::*;
use config::{Cloud, Config};
use resolve::Node;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Line {
    pub hourly_usd: f64,
    pub quantity: f64,
    pub source: String,
    pub meter: String,
}
fn rate(v: &Value) -> Option<f64> {
    let n = v.as_f64().or_else(|| v.as_str()?.parse().ok())?;
    (n.is_finite() && n > 0.0).then_some(n)
}
fn unique(rows: Vec<(String, f64)>) -> Option<(String, f64)> {
    (rows.len() == 1).then(|| rows[0].clone())
}
pub(super) fn aws_rate(value: &Value, unit: &str) -> Option<(String, f64)> {
    let mut rates = vec![];
    for row in value["PriceList"].as_array()? {
        let p: Value = if let Some(s) = row.as_str() {
            serde_json::from_str(s).ok()?
        } else {
            row.clone()
        };
        for term in p["terms"]["OnDemand"].as_object()?.values() {
            for (id, d) in term["priceDimensions"].as_object()? {
                if d["unit"] == unit && d["beginRange"] == "0" && d["endRange"] == "Inf" {
                    rates.push((id.clone(), rate(&d["pricePerUnit"]["USD"])?));
                }
            }
        }
    }
    unique(rates)
}
async fn aws(
    c: &Config,
    service: &str,
    filters: &[(&str, &str)],
    unit: &str,
) -> Result<Option<(String, f64)>> {
    let filters: Vec<_> = filters
        .iter()
        .map(|(field, value)| json!({"Type":"TERM_MATCH","Field":field,"Value":value}))
        .collect();
    let value = resolve::read(
        c,
        &["pricing", "get-products"],
        vec![
            "--service-code".into(),
            service.into(),
            "--filters".into(),
            serde_json::to_string(&filters)?,
        ],
    )
    .await?;
    Ok(aws_rate(&value, unit))
}
async fn retail(c: &Config, service: &str, extra: &str) -> Result<Vec<Value>> {
    retail_region(c.cloud.region(), service, extra).await
}
async fn retail_region(region: &str, service: &str, extra: &str) -> Result<Vec<Value>> {
    use futures_util::StreamExt;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let mut url = reqwest::Url::parse("https://prices.azure.com/api/retail/prices")?;
    url.query_pairs_mut().append_pair("$filter",&format!("serviceName eq '{service}' and armRegionName eq '{}' and priceType eq 'Consumption'{extra}",region));
    let mut seen = std::collections::BTreeSet::new();
    let mut rows = vec![];
    for _ in 0..16 {
        ensure!(
            url.scheme() == "https"
                && url.host_str() == Some("prices.azure.com")
                && url.username().is_empty()
                && url.password().is_none()
                && url.port().is_none_or(|p| p == 443)
                && url.path() == "/api/retail/prices"
                && seen.insert(url.to_string()),
            "Unexpected/repeated Azure price page"
        );
        let response = client.get(url.clone()).send().await?;
        ensure!(
            response.status().is_success(),
            "Azure price catalog unavailable"
        );
        let mut bytes = vec![];
        let mut body = response.bytes_stream();
        while let Some(chunk) = body.next().await {
            bytes.extend(chunk?);
            ensure!(
                bytes.len() <= 8 * 1024 * 1024,
                "Azure price response too large"
            );
        }
        let value: Value = serde_json::from_slice(&bytes)?;
        rows.extend(
            value["Items"]
                .as_array()
                .context("Azure price items missing")?
                .iter()
                .cloned(),
        );
        ensure!(rows.len() <= 16000, "Azure price inventory too large");
        match value["NextPageLink"].as_str().filter(|s| !s.is_empty()) {
            Some(next) => url = reqwest::Url::parse(next)?,
            None => return Ok(rows),
        }
    }
    bail!("Azure price pagination incomplete")
}
pub(super) fn azure_rate(
    rows: &[Value],
    region: &str,
    unit: &str,
    select: impl Fn(&Value) -> bool,
) -> Option<(String, f64)> {
    let at = now().ok()? as i64;
    let mut rates = vec![];
    for row in rows {
        if row["armRegionName"] != region
            || row["currencyCode"] != "USD"
            || row["type"] != "Consumption"
            || row["unitOfMeasure"] != unit
            || row["tierMinimumUnits"].as_f64() != Some(0.0)
            || row["isPrimaryMeterRegion"] == false
            || !select(row)
        {
            continue;
        }
        let started = chrono::DateTime::parse_from_rfc3339(row["effectiveStartDate"].as_str()?)
            .ok()?
            .timestamp();
        if started > at {
            continue;
        }
        rates.push((row["meterId"].as_str()?.into(), rate(&row["retailPrice"])?));
    }
    unique(rates)
}
pub(super) async fn price_nodes(c: &Config, nodes: &mut [Node]) {
    for node in nodes.iter_mut().filter(|n| n.eligible) {
        let result = match &c.cloud {
            Cloud::Aws { region, .. } => {
                aws(
                    c,
                    "AmazonEC2",
                    &[
                        ("regionCode", region),
                        ("instanceType", &node.instance_type),
                        ("operatingSystem", "Linux"),
                        ("tenancy", "Shared"),
                        ("preInstalledSw", "NA"),
                        ("capacitystatus", "Used"),
                    ],
                    "Hrs",
                )
                .await
            }
            Cloud::Azure { region, .. } => {
                let rows = retail(
                    c,
                    "Virtual Machines",
                    &format!(" and armSkuName eq '{}'", node.instance_type),
                )
                .await;
                rows.map(|rows| {
                    azure_rate(&rows, region, "1 Hour", |r| {
                        r["armSkuName"] == node.instance_type
                            && !r["productName"].as_str().unwrap_or("").contains("Windows")
                            && !r["meterName"].as_str().unwrap_or("").contains("Spot")
                            && !r["meterName"]
                                .as_str()
                                .unwrap_or("")
                                .contains("Low Priority")
                    })
                })
            }
        };
        node.hourly_usd = result.ok().flatten().map(|(_, rate)| rate);
    }
}
pub(super) async fn estimate(c: &Config, node: &Node, infrastructure: &Value) -> resolve::Price {
    let mut components = BTreeMap::new();
    let mut missing = vec![];
    let mut add =
        |name: &str, quote: Option<(String, f64)>, quantity: f64, divisor: f64, source: &str| {
            if let Some((meter, value)) = quote {
                components.insert(
                    name.to_string(),
                    Line {
                        hourly_usd: value / divisor,
                        quantity,
                        source: source.into(),
                        meter,
                    },
                );
            } else {
                missing.push(name.into());
            }
        };
    add(
        "workerInstances",
        node.hourly_usd.map(|v| (node.instance_type.clone(), v)),
        f64::from(c.nodes.count),
        1.0,
        "provider retail catalog",
    );
    match &c.cloud {
        Cloud::Aws { region, .. } => {
            for (name, service, filters, unit, quantity, divisor) in [
                (
                    "database",
                    "AmazonRDS",
                    vec![
                        ("regionCode", region.as_str()),
                        ("instanceType", "db.t4g.micro"),
                        ("databaseEngine", "PostgreSQL"),
                        ("deploymentOption", "Single-AZ"),
                    ],
                    "Hrs",
                    1.0,
                    1.0,
                ),
                (
                    "cluster",
                    "AmazonEKS",
                    vec![
                        ("regionCode", region.as_str()),
                        ("operation", "CreateOperation"),
                    ],
                    "Hrs",
                    1.0,
                    1.0,
                ),
                (
                    "nodeDisks",
                    "AmazonEC2",
                    vec![
                        ("regionCode", region.as_str()),
                        ("productFamily", "Storage"),
                        ("volumeApiName", "gp3"),
                    ],
                    "GB-Mo",
                    30.0 * f64::from(c.nodes.count),
                    730.0,
                ),
            ] {
                add(
                    name,
                    aws(c, service, &filters, unit).await.ok().flatten(),
                    quantity,
                    divisor,
                    "AWS Price List OnDemand USD",
                );
            }
            for (name, service, filters, unit, quantity, divisor) in [
                (
                    "natGateway",
                    "AmazonEC2",
                    vec![
                        ("regionCode", region.as_str()),
                        ("productFamily", "NAT Gateway"),
                    ],
                    "Hrs",
                    1.0,
                    1.0,
                ),
                (
                    "publicIPv4",
                    "AmazonVPC",
                    vec![
                        ("regionCode", region.as_str()),
                        ("group", "VPCPublicIPv4Address"),
                    ],
                    "Hrs",
                    1.0,
                    1.0,
                ),
                (
                    "databaseDisks",
                    "AmazonRDS",
                    vec![
                        ("regionCode", region.as_str()),
                        ("productFamily", "Database Storage"),
                        ("databaseEngine", "PostgreSQL"),
                        ("deploymentOption", "Single-AZ"),
                        ("volumeType", "General Purpose-GP3"),
                    ],
                    "GB-Mo",
                    20.0,
                    730.0,
                ),
            ] {
                add(
                    name,
                    aws(c, service, &filters, unit).await.ok().flatten(),
                    quantity,
                    divisor,
                    "AWS Price List OnDemand USD",
                );
            }
            if infrastructure
                .get("operatorAccess")
                .is_some_and(|v| !v.is_null())
            {
                add(
                    "managementInstance",
                    aws(
                        c,
                        "AmazonEC2",
                        &[
                            ("regionCode", region),
                            ("instanceType", "t4g.micro"),
                            ("operatingSystem", "Linux"),
                            ("tenancy", "Shared"),
                            ("preInstalledSw", "NA"),
                            ("capacitystatus", "Used"),
                        ],
                        "Hrs",
                    )
                    .await
                    .ok()
                    .flatten(),
                    1.0,
                    1.0,
                    "AWS Price List OnDemand USD",
                );
                add(
                    "managementDisk",
                    aws(
                        c,
                        "AmazonEC2",
                        &[
                            ("regionCode", region),
                            ("productFamily", "Storage"),
                            ("volumeApiName", "gp3"),
                        ],
                        "GB-Mo",
                    )
                    .await
                    .ok()
                    .flatten(),
                    8.0,
                    730.0,
                    "AWS Price List OnDemand USD",
                );
            }
        }
        Cloud::Azure { region, .. } => {
            let pg = retail(c, "Azure Database for PostgreSQL", "")
                .await
                .unwrap_or_default();
            add(
                "database",
                azure_rate(&pg, region, "1 Hour", |r| {
                    r["productName"]=="Azure Database for PostgreSQL Flexible Server Burstable BS Series Compute"&&r["skuName"]=="B1MS"
                }),
                1.0,
                1.0,
                "Azure Retail Prices USD",
            );
            add(
                "databaseStorage",
                azure_rate(&pg, region, "1 GB/Month", |r| {
                    r["productName"] == "Azure Database for PostgreSQL Flex Server Storage"
                        && r["skuName"] == "Storage"
                }),
                32.0,
                730.0,
                "Azure Retail Prices USD",
            );
            let registry = retail(c, "Container Registry", "")
                .await
                .unwrap_or_default();
            add(
                "registry",
                azure_rate(&registry, region, "1/Day", |r| {
                    r["meterName"] == "Basic Registry Unit"
                }),
                1.0,
                24.0,
                "Azure Retail Prices USD",
            );
            let network = retail(c, "Virtual Network", "").await.unwrap_or_default();
            add(
                "outboundIPv4",
                azure_rate(&network, region, "1 Hour", |r| {
                    r["productName"] == "IP Addresses"
                        && r["meterName"] == "Standard IPv4 Static Public IP"
                }),
                1.0,
                1.0,
                "Azure Retail Prices USD",
            );
            let disk = retail(
                c,
                "Storage",
                " and productName eq 'Premium SSD Managed Disks'",
            )
            .await
            .unwrap_or_default();
            add(
                "nodeDisks",
                azure_rate(&disk, region, "1/Month", |r| {
                    r["meterName"] == "P6 LRS Disk"
                }),
                f64::from(c.nodes.count),
                730.0,
                "Azure Retail Prices USD; Premium SSD 64 GiB assumption",
            );
            let lb = retail_region("Global", "Load Balancer", "")
                .await
                .unwrap_or_default();
            add(
                "loadBalancer",
                azure_rate(&lb, "Global", "1 Hour", |r| {
                    r["meterName"] == "Standard Included LB Rules and Outbound Rules"
                }),
                1.0,
                1.0,
                "Azure Retail Prices USD",
            );
            let private = retail_region(
                "Global",
                "Virtual Network",
                " and productName eq 'Virtual Network Private Link'",
            )
            .await
            .unwrap_or_default();
            add(
                "blobPrivateEndpoint",
                azure_rate(&private, "Global", "1 Hour", |r| {
                    r["meterName"] == "Standard Private Endpoint"
                }),
                1.0,
                1.0,
                "Azure Retail Prices USD",
            );
            if infrastructure
                .get("managementAccess")
                .is_some_and(|v| !v.is_null())
            {
                let vm = retail(
                    c,
                    "Virtual Machines",
                    " and armSkuName eq 'Standard_D2as_v7'",
                )
                .await
                .unwrap_or_default();
                add(
                    "managementInstance",
                    azure_rate(&vm, region, "1 Hour", |r| {
                        !r["productName"].as_str().unwrap_or("").contains("Windows")
                            && !r["meterName"].as_str().unwrap_or("").contains("Spot")
                            && !r["meterName"]
                                .as_str()
                                .unwrap_or("")
                                .contains("Low Priority")
                    }),
                    1.0,
                    1.0,
                    "Azure Retail Prices USD",
                );
                add(
                    "managementDisk",
                    azure_rate(&disk, region, "1/Month", |r| {
                        r["meterName"] == "P4 LRS Disk"
                    }),
                    1.0,
                    730.0,
                    "Azure Retail Prices USD; 32 GiB assumption",
                );
                add(
                    "managementIPv4",
                    azure_rate(&network, region, "1 Hour", |r| {
                        r["productName"] == "IP Addresses"
                            && r["meterName"] == "Standard IPv4 Static Public IP"
                    }),
                    1.0,
                    1.0,
                    "Azure Retail Prices USD",
                );
            }
        }
    }
    let subtotal: f64 = components.values().map(|l| l.hourly_usd * l.quantity).sum();
    resolve::Price {
        currency: "USD".into(),
        worker_hourly_usd: node.hourly_usd.map(|v| v * f64::from(c.nodes.count)),
        total_hourly_usd: if missing.is_empty() {
            Some(subtotal)
        } else {
            None
        },
        known_hourly_subtotal_usd: if components.is_empty() {
            None
        } else {
            Some(subtotal)
        },
        components,
        unpriced_components: missing,
        variable_allowance_usd: 25,
        planned_hours: c
            .trial
            .as_ref()
            .map(|t| u32::from(t.expires_after_hours))
            .unwrap_or(730),
        hard_cap: false,
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ambiguous_or_missing_price_is_not_zero() {
        assert!(aws_rate(&json!({"PriceList":[]}), "Hrs").is_none());
        let row = json!({"product":{"sku":"x"},"terms":{"OnDemand":{"a":{"priceDimensions":{"m":{"unit":"Hrs","beginRange":"0","endRange":"Inf","pricePerUnit":{"USD":"0.2"}}}}}}});
        assert_eq!(
            aws_rate(&json!({"PriceList":[row.clone()]}), "Hrs")
                .unwrap()
                .1,
            0.2
        );
        assert!(aws_rate(&json!({"PriceList":[row.clone(),row]}), "Hrs").is_none());
    }
}
