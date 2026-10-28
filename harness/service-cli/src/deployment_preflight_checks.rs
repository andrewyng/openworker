use super::*;
use serde::Deserialize;
fn invalid() -> Failure {
    Failure::InvalidResponse
}
fn strings<'a>(v: &'a Value, field: &str) -> Observed<&'a str> {
    v.get(field).and_then(Value::as_str).ok_or_else(invalid)
}
fn finite(v: &Value) -> Observed<f64> {
    v.as_f64()
        .filter(|n| n.is_finite() && *n >= 0.0)
        .ok_or_else(invalid)
}
fn array<'a>(v: &'a Value, field: &str) -> Observed<&'a Vec<Value>> {
    v.get(field).and_then(Value::as_array).ok_or_else(invalid)
}
fn target(p: &Plan) -> (&str, &str) {
    match &p.target {
        Target::Aws { account, region } => (account, region),
        _ => unreachable!(),
    }
}
fn expect(b: bool) -> Observed<()> {
    if b {
        Ok(())
    } else {
        Err(invalid())
    }
}
pub(super) async fn all(
    aws: &mut Aws,
    p: &Plan,
    plan_root: &Path,
    out: &Path,
    allowance: u32,
    check_role: bool,
    results: &mut Vec<Check>,
) {
    let i = p.infrastructure.as_ref().unwrap();
    record(results, "service_linked_roles", service_roles(aws, p).await);
    record(
        results,
        "availability_zones_and_offering",
        zones(aws, p).await,
    );
    record(results, "eks_standard_support", eks_version(aws, p).await);
    record(
        results,
        "recommended_node_release",
        node_release(aws, p).await,
    );
    for (name, pin) in [
        ("vpc-cni", &i.addons.vpc_cni),
        ("kube-proxy", &i.addons.kube_proxy),
        ("coredns", &i.addons.core_dns),
        ("eks-pod-identity-agent", &i.addons.pod_identity),
        ("aws-ebs-csi-driver", &i.addons.ebs_csi),
    ] {
        record(
            results,
            &format!("addon_{name}"),
            addon(aws, p, name, pin).await,
        );
    }
    record(results, "postgres_and_orderability", database(aws, p).await);
    record(results, "quota_headroom", quotas(aws, p).await);
    record(
        results,
        "published_cost_estimate",
        cost(aws, p, allowance).await,
    );
    record(results, "guard_artifact_bytes", guard(aws, p, out).await);
    record(
        results,
        "policy_validation",
        policies(aws, p, plan_root).await,
    );
    if check_role {
        record(results, "setup_role_assumption", assume(aws, p).await);
    } else {
        results.push(Check {
            name: "setup_role_assumption".into(),
            status: "not_requested",
            required: false,
            detail: json!({"afterIdentityBootstrap":true,"use":"--check-setup-role"}),
        });
    }
}
async fn service_roles(aws: &mut Aws, p: &Plan) -> Observed<Value> {
    let (account, _) = target(p);
    let mut checked = vec![];
    for (name, service) in [
        ("AWSServiceRoleForAmazonEKS", "eks.amazonaws.com"),
        (
            "AWSServiceRoleForAmazonEKSNodegroup",
            "eks-nodegroup.amazonaws.com",
        ),
        ("AWSServiceRoleForAutoScaling", "autoscaling.amazonaws.com"),
        ("AWSServiceRoleForRDS", "rds.amazonaws.com"),
    ] {
        let v = aws
            .json("iam", "get-role", args(&["--role-name", name]))
            .await?;
        expect(
            v["Role"]["Arn"]
                == format!("arn:aws:iam::{account}:role/aws-service-role/{service}/{name}"),
        )?;
        checked.push(name);
    }
    Ok(json!({"existingRoles":checked,"missingRolesRequireCustomerBootstrap":true}))
}
async fn zones(aws: &mut Aws, p: &Plan) -> Observed<Value> {
    let i = p.infrastructure.as_ref().unwrap();
    let mut a = args(&["--zone-names"]);
    a.extend(i.availability_zones.iter().cloned());
    let v = aws.json("ec2", "describe-availability-zones", a).await?;
    let rows = array(&v, "AvailabilityZones")?;
    for zone in &i.availability_zones {
        expect(rows.iter().any(|z| {
            z["ZoneName"] == *zone
                && z["State"] == "available"
                && ["opt-in-not-required", "opted-in"]
                    .contains(&z["OptInStatus"].as_str().unwrap_or(""))
        }))?;
    }
    let filter = format!("Name=location,Values={}", i.availability_zones.join(","));
    let offers = aws
        .pages(
            "ec2",
            "describe-instance-type-offerings",
            args(&[
                "--location-type",
                "availability-zone",
                "--filters",
                "Name=instance-type,Values=t4g.large",
                &filter,
            ]),
            "InstanceTypeOfferings",
        )
        .await?;
    for zone in &i.availability_zones {
        expect(
            offers
                .iter()
                .any(|z| z["InstanceType"] == "t4g.large" && z["Location"] == *zone),
        )?;
    }
    Ok(
        json!({"zones":i.availability_zones,"instanceType":"t4g.large","offered":true,"capacityReserved":false}),
    )
}
async fn eks_version(aws: &mut Aws, p: &Plan) -> Observed<Value> {
    let i = p.infrastructure.as_ref().unwrap();
    let rows = aws
        .pages(
            "eks",
            "describe-cluster-versions",
            args(&["--cluster-versions", &i.kubernetes_version]),
            "clusterVersions",
        )
        .await?;
    let selected = rows
        .iter()
        .find(|v| v["clusterVersion"] == i.kubernetes_version)
        .ok_or_else(invalid)?;
    expect(selected["versionStatus"] == "STANDARD_SUPPORT")?;
    Ok(json!({"kubernetesVersion":i.kubernetes_version,"status":"STANDARD_SUPPORT"}))
}
async fn node_release(aws: &mut Aws, p: &Plan) -> Observed<Value> {
    let i = p.infrastructure.as_ref().unwrap();
    let parameter = format!(
        "/aws/service/eks/optimized-ami/{}/amazon-linux-2023/arm64/standard/recommended",
        i.kubernetes_version
    );
    let result = aws
        .json("ssm", "get-parameter", args(&["--name", &parameter]))
        .await?;
    let value: Value =
        serde_json::from_str(strings(&result["Parameter"], "Value")?).map_err(|_| invalid())?;
    let recommended = strings(&value, "release_version")?;
    let release = recommended
        .strip_prefix(&format!("{}.", i.kubernetes_version))
        .and_then(|s| s.split_once('-'));
    expect(
        release
            .map(|(patch, date)| {
                !patch.is_empty()
                    && patch.len() <= 3
                    && date.len() == 8
                    && patch
                        .bytes()
                        .chain(date.bytes())
                        .all(|b| b.is_ascii_digit())
            })
            .unwrap_or(false),
    )?;
    if recommended != i.node_release_version {
        return Ok(
            json!({"blockingReason":"node_pin_not_current_recommendation","pinnedRelease":i.node_release_version,"recommendedRelease":recommended,"action":"review and regenerate the plan; no substitution performed"}),
        );
    }
    let ami = strings(&value, "image_id")?;
    expect(
        ami.starts_with("ami-")
            && ami.len() <= 32
            && ami[4..].bytes().all(|b| b.is_ascii_hexdigit()),
    )?;
    let v = aws
        .json(
            "ec2",
            "describe-images",
            args(&["--image-ids", ami, "--owners", "amazon"]),
        )
        .await?;
    let images = array(&v, "Images")?;
    expect(
        images.len() == 1
            && images[0]["ImageId"] == ami
            && images[0]["State"] == "available"
            && images[0]["Architecture"] == "arm64"
            && images[0]["ImageOwnerAlias"] == "amazon",
    )?;
    Ok(
        json!({"release":i.node_release_version,"imageId":ami,"architecture":"arm64","validation":"current_recommended_release_only"}),
    )
}
async fn addon(aws: &mut Aws, p: &Plan, name: &str, pin: &str) -> Observed<Value> {
    let i = p.infrastructure.as_ref().unwrap();
    let rows = aws
        .pages(
            "eks",
            "describe-addon-versions",
            args(&[
                "--addon-name",
                name,
                "--kubernetes-version",
                &i.kubernetes_version,
            ]),
            "addons",
        )
        .await?;
    let valid = rows
        .iter()
        .filter(|a| a["addonName"] == name)
        .filter_map(|a| a["addonVersions"].as_array())
        .flatten()
        .any(|v| {
            v["addonVersion"] == pin
                && v["architecture"]
                    .as_array()
                    .map(|a| a.iter().any(|s| s == "arm64"))
                    .unwrap_or(false)
                && v["compatibilities"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .any(|x| x["clusterVersion"] == i.kubernetes_version)
                    })
                    .unwrap_or(false)
        });
    if !valid {
        let available: Vec<_> = rows
            .iter()
            .filter(|a| a["addonName"] == name)
            .filter_map(|a| a["addonVersions"].as_array())
            .flatten()
            .filter(|v| {
                v["architecture"]
                    .as_array()
                    .map(|a| a.iter().any(|s| s == "arm64"))
                    .unwrap_or(false)
                    && v["compatibilities"]
                        .as_array()
                        .map(|a| {
                            a.iter()
                                .any(|x| x["clusterVersion"] == i.kubernetes_version)
                        })
                        .unwrap_or(false)
            })
            .filter_map(|v| v["addonVersion"].as_str())
            .filter(|v| infrastructure::addon_version(v))
            .take(10)
            .collect();
        return Ok(
            json!({"blockingReason":"addon_pin_unavailable","addon":name,"pinnedVersion":pin,"availableCompatibleVersions":available,"action":"review and regenerate the plan; no substitution performed"}),
        );
    }
    Ok(
        json!({"addon":name,"version":pin,"architecture":"arm64","kubernetesVersion":i.kubernetes_version}),
    )
}
async fn database(aws: &mut Aws, p: &Plan) -> Observed<Value> {
    let i = p.infrastructure.as_ref().unwrap();
    let versions = aws
        .pages(
            "rds",
            "describe-db-engine-versions",
            args(&[
                "--engine",
                "postgres",
                "--engine-version",
                &i.postgres_version,
            ]),
            "DBEngineVersions",
        )
        .await?;
    expect(versions.iter().any(|v| {
        v["Engine"] == "postgres"
            && v["EngineVersion"] == i.postgres_version
            && v["DBParameterGroupFamily"] == "postgres16"
            && v["Status"] == "available"
    }))?;
    let rows = aws
        .pages(
            "rds",
            "describe-orderable-db-instance-options",
            args(&[
                "--engine",
                "postgres",
                "--engine-version",
                &i.postgres_version,
                "--db-instance-class",
                "db.t4g.micro",
                "--vpc",
            ]),
            "OrderableDBInstanceOptions",
        )
        .await?;
    let available = i.availability_zones.iter().all(|zone| {
        rows.iter().any(|r| {
            r["EngineVersion"] == i.postgres_version
                && r["DBInstanceClass"] == "db.t4g.micro"
                && r["Vpc"] == true
                && r["StorageType"] == "gp3"
                && r["SupportsStorageEncryption"] == true
                && r["AvailabilityZones"]
                    .as_array()
                    .map(|a| a.iter().any(|v| v["Name"] == *zone))
                    .unwrap_or(false)
        })
    });
    expect(available)?;
    Ok(
        json!({"postgresVersion":i.postgres_version,"instanceClass":"db.t4g.micro","storage":"gp3","encryptionSupported":true}),
    )
}
async fn quota(aws: &mut Aws, service: &str, code: &str) -> Observed<f64> {
    let v = aws
        .json(
            "service-quotas",
            "get-service-quota",
            args(&["--service-code", service, "--quota-code", code]),
        )
        .await?;
    expect(v["Quota"]["ServiceCode"] == service && v["Quota"]["QuotaCode"] == code)?;
    finite(&v["Quota"]["Value"])
}
async fn quotas(aws: &mut Aws, p: &Plan) -> Observed<Value> {
    let limit = quota(aws, "ec2", "L-1216C47A").await?;
    let reservations = aws
        .pages(
            "ec2",
            "describe-instances",
            args(&[
                "--filters",
                "Name=instance-state-name,Values=pending,running",
            ]),
            "Reservations",
        )
        .await?;
    let mut types: BTreeMap<String, u32> = BTreeMap::new();
    for reservation in reservations {
        for v in array(&reservation, "Instances")? {
            expect(["pending", "running"].contains(&v["State"]["Name"].as_str().unwrap_or("")))?;
            if v.get("InstanceLifecycle").is_some() {
                continue;
            } // Spot/capacity-block instances do not consume this quota.
            let tenancy = strings(&v["Placement"], "Tenancy")?;
            if tenancy == "host" {
                continue;
            }
            expect(["default", "dedicated"].contains(&tenancy))?;
            *types
                .entry(strings(v, "InstanceType")?.to_owned())
                .or_default() += 1;
        }
    }
    let account = target(p).0;
    let reservations = aws
        .pages(
            "ec2",
            "describe-capacity-reservations",
            args(&[
                "--filters",
                "Name=state,Values=assessing,scheduled,pending,active,delayed",
                &format!("Name=owner-id,Values={account}"),
            ]),
            "CapacityReservations",
        )
        .await?;
    let mut unused_reserved_instances = 0u64;
    for r in reservations {
        let state = strings(&r, "State")?;
        expect(
            r["OwnerId"] == account
                && ["assessing", "scheduled", "pending", "active", "delayed"].contains(&state),
        )?;
        let total = r["TotalInstanceCount"].as_u64().ok_or_else(invalid)?;
        let available = if state == "active" {
            r["AvailableInstanceCount"].as_u64().ok_or_else(invalid)?
        } else {
            total
        };
        expect(available <= total)?;
        let count = u32::try_from(available).map_err(|_| invalid())?;
        let entry = types
            .entry(strings(&r, "InstanceType")?.into())
            .or_default();
        *entry = entry.checked_add(count).ok_or_else(invalid)?;
        unused_reserved_instances = unused_reserved_instances
            .checked_add(available)
            .ok_or_else(invalid)?;
    }
    let mut used = 0.0;
    for chunk in types.keys().collect::<Vec<_>>().chunks(100) {
        let mut a = args(&["--instance-types"]);
        a.extend(chunk.iter().map(|v| (*v).clone()));
        let v = aws.json("ec2", "describe-instance-types", a).await?;
        let rows = array(&v, "InstanceTypes")?;
        for name in chunk {
            let r = rows
                .iter()
                .find(|v| v["InstanceType"] == ***name)
                .ok_or_else(invalid)?;
            used += finite(&r["VCpuInfo"]["DefaultVCpus"])? * f64::from(types[*name]);
        }
    }
    // Counting every non-host On-Demand family is conservative: some families
    // consume separate quotas. Never understate usage by assuming their count zero.
    expect(limit >= used + 4.0)?;
    let cluster_limit = quota(aws, "eks", "L-1194D53C").await?;
    let clusters = aws
        .pages("eks", "list-clusters", vec![], "clusters")
        .await?;
    expect(cluster_limit >= clusters.len() as f64 + 1.0)?;
    let vpc_limit = quota(aws, "vpc", "L-F678F1CE").await?;
    let vpcs = aws.pages("ec2", "describe-vpcs", vec![], "Vpcs").await?;
    expect(vpc_limit >= vpcs.len() as f64 + 1.0)?;
    let eip_limit = quota(aws, "ec2", "L-0263D0A3").await?;
    let eips = aws.json("ec2", "describe-addresses", vec![]).await?;
    expect(eip_limit >= array(&eips, "Addresses")?.len() as f64 + 1.0)?;
    let nat_limit = quota(aws, "vpc", "L-FE5A380F").await?;
    let nats = aws
        .pages(
            "ec2",
            "describe-nat-gateways",
            args(&["--filter", "Name=state,Values=pending,available"]),
            "NatGateways",
        )
        .await?;
    // Regional usage is >= usage in the chosen zone; this may reject conservatively.
    expect(nat_limit >= nats.len() as f64 + 1.0)?;
    let attrs = aws
        .json("rds", "describe-account-attributes", vec![])
        .await?;
    let db = array(&attrs, "AccountQuotas")?
        .iter()
        .find(|q| q["AccountQuotaName"] == "DBInstances")
        .ok_or_else(invalid)?;
    let db_used = finite(&db["Used"])?;
    let db_max = finite(&db["Max"])?;
    expect(db_used + 1.0 <= db_max)?;
    let _ = p;
    Ok(
        json!({"requestedStandardVCpus":4,"regionalStandardVCpuLimit":limit,"conservativeOnDemandVCpuUsage":used,"conservativeReservedInstanceSlots":unused_reserved_instances,"eksClusterLimit":cluster_limit,"observedClusters":clusters.len(),"vpcLimit":vpc_limit,"observedVpcs":vpcs.len(),"elasticIpLimit":eip_limit,"observedElasticIps":array(&eips,"Addresses")?.len(),"natPerZoneLimit":nat_limit,"conservativeRegionalNatUsage":nats.len(),"rdsInstanceLimit":db_max,"rdsInstanceUsage":db_used,"capacityReserved":false,"usageMayChangeAfterObservation":true}),
    )
}
#[derive(Clone, Copy)]
enum Product {
    Node,
    Database,
    Cluster,
    Nat,
    Ip,
    Ebs,
    RdsStorage,
}
async fn price(aws: &mut Aws, p: &Plan, kind: Product) -> Observed<Value> {
    let region = target(p).1;
    let (service, filters, unit) = match kind {
        Product::Node => (
            "AmazonEC2",
            vec![
                ("regionCode", region),
                ("instanceType", "t4g.large"),
                ("operatingSystem", "Linux"),
                ("tenancy", "Shared"),
                ("preInstalledSw", "NA"),
                ("capacitystatus", "Used"),
            ],
            "Hrs",
        ),
        Product::Database => (
            "AmazonRDS",
            vec![
                ("regionCode", region),
                ("instanceType", "db.t4g.micro"),
                ("databaseEngine", "PostgreSQL"),
                ("deploymentOption", "Single-AZ"),
            ],
            "Hrs",
        ),
        Product::Cluster => (
            "AmazonEKS",
            vec![("regionCode", region), ("operation", "CreateOperation")],
            "Hrs",
        ),
        Product::Nat => (
            "AmazonEC2",
            vec![("regionCode", region), ("productFamily", "NAT Gateway")],
            "Hrs",
        ),
        Product::Ip => ("AmazonVPC", vec![("regionCode", region)], "Hrs"),
        Product::Ebs => (
            "AmazonEC2",
            vec![
                ("regionCode", region),
                ("productFamily", "Storage"),
                ("volumeApiName", "gp3"),
            ],
            "GB-Mo",
        ),
        Product::RdsStorage => (
            "AmazonRDS",
            vec![
                ("regionCode", region),
                ("productFamily", "Database Storage"),
                ("databaseEngine", "PostgreSQL"),
                ("deploymentOption", "Single-AZ"),
            ],
            "GB-Mo",
        ),
    };
    let filter: Vec<_> = filters
        .iter()
        .map(|(k, v)| json!({"Type":"TERM_MATCH","Field":k,"Value":v}))
        .collect();
    let mut rows = vec![];
    let mut next = None;
    let mut seen = std::collections::BTreeSet::new();
    let mut finished = false;
    for _ in 0..20 {
        let mut a = args(&[
            "--service-code",
            service,
            "--filters",
            &serde_json::to_string(&filter).map_err(|_| invalid())?,
        ]);
        if let Some(t) = next.take() {
            a.extend([
                "--cli-input-json".into(),
                json!({"NextToken":t}).to_string(),
            ]);
        }
        let data = aws
            .bytes("pricing", "get-products", a, Some("us-east-1"))
            .await?;
        let v: Value = serde_json::from_slice(&data).map_err(|_| invalid())?;
        let page = array(&v, "PriceList")?;
        if page.len() + rows.len() > 10000 {
            return Err(Failure::OutputLimit);
        }
        rows.extend(page.iter().cloned());
        match v.get("NextToken") {
            None | Some(Value::Null) => {
                finished = true;
                break;
            }
            Some(Value::String(t)) if t.is_empty() => {
                finished = true;
                break;
            }
            Some(Value::String(t)) if t.len() <= 8192 && seen.insert(t.clone()) => {
                next = Some(t.clone())
            }
            _ => return Err(invalid()),
        }
    }
    expect(finished)?;
    let mut selected = vec![];
    for row in rows {
        let product: Value =
            serde_json::from_str(row.as_str().ok_or_else(invalid)?).map_err(|_| invalid())?;
        let attrs = &product["product"]["attributes"];
        if attrs["regionCode"] != region {
            continue;
        }
        let usage = attrs["usagetype"].as_str().unwrap_or("");
        match kind {
            Product::Node if !usage.ends_with("-BoxUsage:t4g.large") => continue,
            Product::Database if !usage.ends_with("-InstanceUsage:db.t4g.micro") => continue,
            Product::Nat if !usage.ends_with("-NatGateway-Hours") => continue,
            Product::Cluster if !usage.ends_with("AmazonEKS-Hours:perCluster") => continue,
            Product::Ip if !usage.ends_with("PublicIPv4:InUseAddress") => continue,
            Product::RdsStorage
                if !attrs["volumeType"]
                    .as_str()
                    .unwrap_or("")
                    .to_lowercase()
                    .contains("gp3") =>
            {
                continue
            }
            _ => {}
        }
        let terms = product["terms"]["OnDemand"]
            .as_object()
            .ok_or_else(invalid)?;
        for term in terms.values() {
            for d in term["priceDimensions"]
                .as_object()
                .ok_or_else(invalid)?
                .values()
            {
                let source_unit = strings(d, "unit")?;
                let compatible_unit = if unit == "Hrs" {
                    ["hrs", "hours", "hour"].contains(&source_unit.to_ascii_lowercase().as_str())
                } else {
                    source_unit == unit
                };
                if !compatible_unit || d["beginRange"] != "0" || d["endRange"] != "Inf" {
                    continue;
                }
                let rate = strings(&d["pricePerUnit"], "USD")?
                    .parse::<f64>()
                    .map_err(|_| invalid())?;
                if !rate.is_finite() || rate <= 0.0 || rate > 10000.0 {
                    return Err(invalid());
                }
                selected.push(json!({"sku":strings(&product["product"],"sku")?,"usd":rate,"unit":unit,"sourceUnit":source_unit,"pricingRegion":"us-east-1"}));
            }
        }
    }
    // Missing/ambiguous/zero prices must never become a zero-cost estimate.
    expect(selected.len() == 1)?;
    Ok(selected.remove(0))
}
async fn cost(aws: &mut Aws, p: &Plan, allowance: u32) -> Observed<Value> {
    let mut components = BTreeMap::new();
    for (name, kind, quantity) in [
        ("workerInstances", Product::Node, 2.0),
        ("databaseInstance", Product::Database, 1.0),
        ("eksStandardCluster", Product::Cluster, 1.0),
        ("natGateway", Product::Nat, 1.0),
        ("natPublicIpv4", Product::Ip, 1.0),
        ("nodeGp3Storage", Product::Ebs, 60.0),
        ("databaseGp3Storage", Product::RdsStorage, 20.0),
    ] {
        let mut line = match price(aws, p, kind).await {
            Ok(line) => line,
            Err(e) => {
                return Ok(
                    json!({"blockingReason":"price_lookup_failed","component":name,"reason":e.code(),"estimateAvailable":false}),
                )
            }
        };
        line["quantity"] = json!(quantity);
        components.insert(name, line);
    }
    let hours = (p.expires_at_unix - p.created_at_unix) as f64 / 3600.0;
    let mut fixed = 0.0;
    for line in components.values() {
        let rate = finite(&line["usd"])?;
        let quantity = finite(&line["quantity"])?;
        fixed += rate * quantity * hours / if line["unit"] == "GB-Mo" { 730.0 } else { 1.0 };
    }
    expect(fixed.is_finite())?;
    let fixed = (fixed * 100.0).ceil() / 100.0;
    let estimate = fixed + f64::from(allowance);
    let mut detail = json!({"rateSource":"AWS Price List GetProducts OnDemand USD","components":components,"plannedHours":hours,"fixedCostEstimateUsd":fixed,"variableAllowanceUsd":allowance,"planningEstimateUsd":estimate,"authorizedBudgetUsd":p.budget.authorized_usd,"withinAuthorizedBudget":estimate<=f64::from(p.budget.authorized_usd),"hardCap":false,"monthHoursForProration":730,"excludedVariableCharges":["NAT processed bytes","data transfer","T4g CPU credits","S3/ECR storage and requests","guard, logs and control-plane requests","OpenShell workspace volume usage"],"allowanceIsNotAnUpperBound":true});
    if estimate > f64::from(p.budget.authorized_usd) {
        detail["blockingReason"] = json!("budget_exceeded");
    }
    Ok(detail)
}
async fn guard(aws: &mut Aws, p: &Plan, out: &Path) -> Observed<Value> {
    let code = &p.infrastructure.as_ref().unwrap().guard_code;
    let (account, region) = target(p);
    let location = aws
        .json(
            "s3api",
            "get-bucket-location",
            args(&["--bucket", &code.bucket, "--expected-bucket-owner", account]),
        )
        .await?;
    let actual = match location.get("LocationConstraint") {
        Some(Value::Null) => "us-east-1",
        Some(Value::String(v)) if v == "EU" => "eu-west-1",
        Some(Value::String(v)) => v.as_str(),
        _ => return Err(invalid()),
    };
    expect(actual == region)?;
    let a = args(&[
        "--bucket",
        &code.bucket,
        "--key",
        &code.key,
        "--version-id",
        &code.version_id,
        "--expected-bucket-owner",
        account,
    ]);
    let head = aws.json("s3api", "head-object", a.clone()).await?;
    let size = head["ContentLength"].as_u64().ok_or_else(invalid)?;
    expect(size > 0 && size <= 8 * 1024 * 1024 && head["VersionId"] == code.version_id)?;
    let path = out.join(format!(".guard-{}", uuid::Uuid::new_v4().simple()));
    let scratch = Scratch(path);
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    opts.open(&scratch.0).map_err(|_| invalid())?;
    let mut get = a;
    get.extend([
        "--range".into(),
        format!("bytes=0-{}", size - 1),
        scratch.0.to_str().ok_or_else(invalid)?.into(),
    ]);
    let downloaded = aws.json("s3api", "get-object", get).await?;
    expect(downloaded["VersionId"] == code.version_id)?;
    let meta = fs::symlink_metadata(&scratch.0).map_err(|_| invalid())?;
    expect(meta.is_file() && !meta.file_type().is_symlink() && meta.len() == size)?;
    let mut bytes = Zeroizing::new(vec![]);
    fs::File::open(&scratch.0)
        .map_err(|_| invalid())?
        .take(8 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| invalid())?;
    expect(hash(&bytes) == code.sha256)?;
    Ok(
        json!({"versionId":code.version_id,"bytes":size,"sha256Verified":true,"executionOrUnpackingPerformed":false,"behaviorQualified":false}),
    )
}
async fn policies(aws: &mut Aws, p: &Plan, root: &Path) -> Observed<Value> {
    let mut names = vec![
        "setup-policy.json".to_string(),
        "workload-policy.json".into(),
        "cleanup-policy.json".into(),
    ];
    let all = artifacts(p).map_err(|_| invalid())?;
    names.extend(
        all.keys()
            .filter(|n| n.starts_with("execution-policy-"))
            .cloned(),
    );
    let mut findings = vec![];
    let mut blocked = false;
    for name in &names {
        let file = format!(
            "file://{}",
            root.canonicalize()
                .map_err(|_| invalid())?
                .join(name)
                .display()
        );
        let rows = aws
            .pages(
                "accessanalyzer",
                "validate-policy",
                args(&[
                    "--policy-type",
                    "IDENTITY_POLICY",
                    "--policy-document",
                    &file,
                ]),
                "findings",
            )
            .await?;
        for f in rows {
            let kind = strings(&f, "findingType")?;
            expect(["ERROR", "SECURITY_WARNING", "WARNING", "SUGGESTION"].contains(&kind))?;
            let issue = strings(&f, "issueCode")?;
            expect(
                !issue.is_empty()
                    && issue.len() <= 128
                    && issue
                        .bytes()
                        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
            )?;
            if ["ERROR", "SECURITY_WARNING"].contains(&kind) {
                blocked = true;
            }
            findings.push(json!({"policy":name,"type":kind,"issueCode":issue}));
            if findings.len() > 1000 {
                return Err(Failure::OutputLimit);
            }
        }
    }
    let mut detail = json!({"policiesValidated": names, "validator": "AWS Access Analyzer ValidatePolicy", "effectivePermissionSimulation": false,"findings":findings});
    if blocked {
        detail["blockingReason"] = json!("policy_review_required");
    }
    Ok(detail)
}

#[derive(Deserialize)]
#[serde(rename_all = "PascalCase")]
struct AssumeResponse {
    credentials: TemporaryCredentials,
}
#[derive(Deserialize)]
#[serde(rename_all = "PascalCase")]
struct TemporaryCredentials {
    access_key_id: String,
    secret_access_key: String,
    session_token: String,
}
impl Drop for TemporaryCredentials {
    fn drop(&mut self) {
        self.access_key_id.zeroize();
        self.secret_access_key.zeroize();
        self.session_token.zeroize();
    }
}
async fn assume(aws: &mut Aws, p: &Plan) -> Observed<Value> {
    let account = target(p).0;
    let arn = format!("arn:aws:iam::{account}:role/{}-setup", p.prefix());
    let session = format!("ows-preflight-{}", &p.deployment_id[4..16]);
    let policy=json!({"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"sts:GetCallerIdentity","Resource":"*"}]}).to_string();
    let bytes = aws
        .bytes(
            "sts",
            "assume-role",
            args(&[
                "--role-arn",
                &arn,
                "--role-session-name",
                &session,
                "--duration-seconds",
                "900",
                "--policy",
                &policy,
            ]),
            None,
        )
        .await?;
    let mut response: AssumeResponse = serde_json::from_slice(&bytes).map_err(|_| invalid())?;
    expect(
        !response.credentials.access_key_id.is_empty()
            && response.credentials.access_key_id.len() <= 256
            && !response.credentials.secret_access_key.is_empty()
            && response.credentials.secret_access_key.len() <= 256
            && !response.credentials.session_token.is_empty()
            && response.credentials.session_token.len() <= 65536,
    )?;
    aws.credentials = Some(Credentials {
        access_key: std::mem::take(&mut response.credentials.access_key_id),
        secret_key: std::mem::take(&mut response.credentials.secret_access_key),
        token: std::mem::take(&mut response.credentials.session_token),
    });
    let identity = aws.json("sts", "get-caller-identity", vec![]).await;
    aws.credentials = None;
    expect(valid_identity(p, &identity?, true))?;
    Ok(
        json!({"setupRole":arn,"temporarySessionSeconds":900,"sessionPermissions":"GetCallerIdentity only","credentialsPersisted":false,"deploymentPermissionsVerified":false}),
    )
}
