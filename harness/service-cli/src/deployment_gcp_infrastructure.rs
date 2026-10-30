//! Offline G2a graph. No cloud client, Terraform execution, keys or implicit grants.
use super::*;
use std::net::Ipv4Addr;

#[derive(Args)]
pub struct Infrastructure {
    #[arg(long)]
    plan: PathBuf,
    #[arg(long)]
    config: PathBuf,
    #[arg(long)]
    out: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Inputs {
    pub zone: String,
    pub kubernetes_version: String,
    pub terraform_version: String,
    pub google_provider_version: String,
    pub node_machine_type: String,
    pub node_disk_gb: u16,
    pub postgres_version: String,
    pub postgres_tier: String,
    pub postgres_disk_gb: u16,
    pub subnet_cidr: String,
    pub pod_cidr: String,
    pub service_cidr: String,
    pub control_plane_cidr: String,
    pub sql_cidr: String,
}

fn cidr(s: &str, min: u8, max: u8) -> Result<(u32, u32)> {
    let (ip, bits) = s.split_once('/').context("Use an explicit IPv4 CIDR")?;
    let ip: Ipv4Addr = ip.parse()?;
    let bits: u8 = bits.parse()?;
    ensure!(
        ip.is_private() && (min..=max).contains(&bits),
        "Unsupported private CIDR size"
    );
    let mask = u32::MAX << (32 - bits);
    let start = u32::from(ip);
    ensure!(
        start & mask == start && s == format!("{ip}/{bits}"),
        "CIDR must be canonical and aligned"
    );
    let end = start | !mask;
    ensure!(
        Ipv4Addr::from(end).is_private(),
        "CIDR extends outside private address space"
    );
    Ok((start, end))
}

impl Inputs {
    pub fn validate(&self, p: &Plan) -> Result<()> {
        ensure!(
            matches!(p.target, Target::Gcp { .. }) && p.gcp_bootstrap.is_some(),
            "GCP bootstrap plan required"
        );
        let Target::Gcp { location, .. } = &p.target else {
            unreachable!()
        };
        let zone = self
            .zone
            .strip_prefix(&format!("{location}-"))
            .context("Zone must belong to plan region")?;
        ensure!(
            zone.len() == 1 && zone.bytes().all(|b| b.is_ascii_lowercase()),
            "Invalid zone"
        );
        let (base, build) = self
            .kubernetes_version
            .split_once("-gke.")
            .context("Pin a full GKE version")?;
        let parts: Vec<_> = base.split('.').collect();
        ensure!(
            parts.len() == 3
                && parts[0] == "1"
                && parts.iter().all(|v| !v.is_empty()
                    && v.len() <= 3
                    && v.bytes().all(|b| b.is_ascii_digit()))
                && !build.is_empty()
                && build.len() <= 10
                && build.bytes().all(|b| b.is_ascii_digit()),
            "Invalid pinned GKE version"
        );
        // Pins are a compatibility baseline, not a claim of regional availability.
        ensure!(
            self.terraform_version == "1.5.7" && self.google_provider_version == "6.44.0",
            "Unqualified Terraform/provider pin"
        );
        ensure!(
            ["e2-standard-2", "e2-standard-4"].contains(&self.node_machine_type.as_str()),
            "Unsupported bounded AMD64 node type"
        );
        ensure!(
            (30..=100).contains(&self.node_disk_gb) && (10..=100).contains(&self.postgres_disk_gb),
            "Disk size outside trial bounds"
        );
        ensure!(
            self.postgres_version == "POSTGRES_16"
                && ["db-f1-micro", "db-g1-small"].contains(&self.postgres_tier.as_str()),
            "Unsupported trial SQL configuration"
        );
        let ranges = [
            cidr(&self.subnet_cidr, 16, 24)?,
            cidr(&self.pod_cidr, 14, 22)?,
            cidr(&self.service_cidr, 20, 24)?,
            cidr(&self.control_plane_cidr, 28, 28)?,
            cidr(&self.sql_cidr, 16, 24)?,
        ];
        for (n, a) in ranges.iter().enumerate() {
            for b in &ranges[n + 1..] {
                ensure!(a.1 < b.0 || b.1 < a.0, "Private address ranges overlap");
            }
        }
        ensure!(
            p.expires_at_unix
                .checked_sub(p.created_at_unix)
                .is_some_and(|ttl| ttl <= 4 * 3600),
            "Initial GCP trial graph requires TTL at most four hours"
        );
        Ok(())
    }
}

pub fn export(o: &Infrastructure) -> Result<()> {
    let (mut p, _) = inspect(&o.plan)?;
    let at = now()?;
    ensure!(
        p.created_at_unix <= at && at < p.expires_at_unix,
        "Plan expired or future-dated"
    );
    ensure!(
        p.gcp_infrastructure.is_none(),
        "Infrastructure already exported"
    );
    let meta = fs::symlink_metadata(&o.config)?;
    ensure!(
        meta.is_file() && !meta.file_type().is_symlink() && meta.len() <= 65536,
        "Invalid infrastructure input file"
    );
    p.gcp_infrastructure = Some(serde_json::from_slice(&fs::read(&o.config)?)?);
    p.validate()?;
    let m = write_bundle(&o.out, &p)?;
    crate::output(result(&p, &m))
}

pub fn artifacts(p: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
    let i = p
        .gcp_infrastructure
        .as_ref()
        .context("Infrastructure inputs required")?;
    i.validate(p)?;
    let project = project(p);
    let a = accounts(p);
    let name = format!("ow-{}", &p.deployment_id[4..16]);
    let region = match &p.target {
        Target::Gcp { location, .. } => location,
        _ => unreachable!(),
    };
    let labels = json!({"openworker_deployment": &p.deployment_id, "openworker_expiry": p.expires_at_unix.to_string()});
    let mut r: BTreeMap<String, Value> = BTreeMap::new();
    r.insert("google_compute_network".into(), json!({"workload":{"name":format!("{name}-vpc"),"auto_create_subnetworks":false,"routing_mode":"REGIONAL"}}));
    r.insert("google_compute_subnetwork".into(), json!({"workload":{"name":format!("{name}-subnet"),"region":region,"network":"${google_compute_network.workload.id}","ip_cidr_range":i.subnet_cidr,"private_ip_google_access":true,"secondary_ip_range":[{"range_name":"pods","ip_cidr_range":i.pod_cidr},{"range_name":"services","ip_cidr_range":i.service_cidr}]}}));
    r.insert("google_compute_router".into(), json!({"workload":{"name":format!("{name}-router"),"region":region,"network":"${google_compute_network.workload.id}"}}));
    r.insert("google_compute_router_nat".into(), json!({"workload":{"name":format!("{name}-nat"),"region":region,"router":"${google_compute_router.workload.name}","nat_ip_allocate_option":"AUTO_ONLY","source_subnetwork_ip_ranges_to_nat":"LIST_OF_SUBNETWORKS","subnetwork":[{"name":"${google_compute_subnetwork.workload.id}","source_ip_ranges_to_nat":["ALL_IP_RANGES"]}]}}));
    let (sql_ip, sql_bits) = i.sql_cidr.split_once('/').unwrap();
    r.insert("google_compute_global_address".into(), json!({"sql":{"name":format!("{name}-sql-range"),"purpose":"VPC_PEERING","address_type":"INTERNAL","prefix_length":sql_bits.parse::<u8>()?,"address":sql_ip,"network":"${google_compute_network.workload.id}"}}));
    r.insert("google_service_networking_connection".into(), json!({"sql":{"network":"${google_compute_network.workload.id}","service":"servicenetworking.googleapis.com","reserved_peering_ranges":["${google_compute_global_address.sql.name}"]}}));
    r.insert("google_container_cluster".into(), json!({"workload":{
        "name":format!("{name}-gke"),"location":i.zone,"min_master_version":i.kubernetes_version,"remove_default_node_pool":true,"initial_node_count":1,"deletion_protection":false,"network":"${google_compute_network.workload.id}","subnetwork":"${google_compute_subnetwork.workload.id}","resource_labels":labels,
        "ip_allocation_policy":[{"cluster_secondary_range_name":"pods","services_secondary_range_name":"services"}],
        "private_cluster_config":[{"enable_private_nodes":true,"enable_private_endpoint":true,"master_ipv4_cidr_block":i.control_plane_cidr}],
        "workload_identity_config":[{"workload_pool":format!("{project}.svc.id.goog")}],
        "release_channel":[{"channel":"UNSPECIFIED"}],
        "network_policy":[{"enabled":true,"provider":"CALICO"}],
        "addons_config":[{"network_policy_config":[{"disabled":false}]}],
        "node_config":[{"machine_type":i.node_machine_type,"image_type":"UBUNTU_CONTAINERD","disk_size_gb":i.node_disk_gb,"disk_type":"pd-balanced","service_account":a["nodes"],"oauth_scopes":["https://www.googleapis.com/auth/cloud-platform"],"metadata":{"disable-legacy-endpoints":"true"}}],
        "depends_on":["google_compute_router_nat.workload"]
    }}));
    r.insert("google_container_node_pool".into(), json!({"workload":{
        "name":format!("{name}-nodes"),"location":i.zone,"cluster":"${google_container_cluster.workload.name}","version":i.kubernetes_version,"node_count":2,
        "management":[{"auto_repair":true,"auto_upgrade":false}],
        "node_config":[{"machine_type":i.node_machine_type,"image_type":"UBUNTU_CONTAINERD","disk_size_gb":i.node_disk_gb,"disk_type":"pd-balanced","service_account":a["nodes"],"oauth_scopes":["https://www.googleapis.com/auth/cloud-platform"],"metadata":{"disable-legacy-endpoints":"true"},"workload_metadata_config":[{"mode":"GKE_METADATA"}],"labels":labels}],
        "depends_on":["google_compute_router_nat.workload"]
    }}));
    r.insert("google_sql_database_instance".into(), json!({"workload":{
        "name":format!("{name}-sql"),"region":region,"database_version":i.postgres_version,"deletion_protection":false,
        "settings":[{"tier":i.postgres_tier,"edition":"ENTERPRISE","availability_type":"ZONAL","disk_type":"PD_SSD","disk_size":i.postgres_disk_gb,"disk_autoresize":false,"deletion_protection_enabled":false,"user_labels":labels,"ip_configuration":[{"ipv4_enabled":false,"private_network":"${google_compute_network.workload.id}","ssl_mode":"ENCRYPTED_ONLY"}],"database_flags":[{"name":"cloudsql.iam_authentication","value":"on"}]}],
        "depends_on":["google_service_networking_connection.sql"]
    }}));
    r.insert("google_sql_database".into(), json!({"workload":{"name":"openworker","instance":"${google_sql_database_instance.workload.name}"}}));
    r.insert("google_storage_bucket".into(), json!({"artifacts":{"name":format!("{project}-{name}-artifacts"),"location":region,"uniform_bucket_level_access":true,"public_access_prevention":"enforced","force_destroy":true,"labels":labels,"versioning":[{"enabled":true}]}}));
    r.insert("google_artifact_registry_repository".into(), json!({"images":{"repository_id":format!("{name}-images"),"location":region,"format":"DOCKER","labels":labels}}));
    let tf = json!({"terraform":{"required_version":format!("={}",i.terraform_version),"required_providers":{"google":{"source":"hashicorp/google","version":format!("={}",i.google_provider_version)}}},"provider":{"google":{"project":project,"region":region,"zone":i.zone}},"resource":r,
        "output":{"cluster_name":{"value":"${google_container_cluster.workload.name}"},"cluster_location":{"value":i.zone},"sql_connection_name":{"value":"${google_sql_database_instance.workload.connection_name}"},"sql_private_ip":{"value":"${google_sql_database_instance.workload.private_ip_address}"},"artifact_bucket":{"value":"${google_storage_bucket.artifacts.name}"},"image_repository":{"value":"${google_artifact_registry_repository.images.name}"}}});
    let resources: Vec<_> = r
        .iter()
        .flat_map(|(kind, v)| {
            v.as_object()
                .unwrap()
                .keys()
                .map(move |name| format!("{kind}.{name}"))
        })
        .collect();
    let graph = json!({"schemaVersion":1,"deploymentId":p.deployment_id,"target":p.target,"resourceCount":resources.len(),"terraformResources":resources,"controlPlaneAccess":"private-vpc-route-required","nodeCount":2,"transientDefaultPool":true,"nodeOs":"UBUNTU_CONTAINERD","autoScaling":false,"networkPolicyProvider":"CALICO","providerManagedDependents":["GKE-created Compute instances, disks, instance groups and firewall rules"],"trialDataDeletedOnDestroy":true,"provisioningReady":false,"permissionPolicyReady":false,"remaining":["provider operation/resource-specific IAM qualification","region version, quota, node OS and OpenShell compatibility","source and Terraform state storage generation receipts","private installer connectivity and PostgreSQL identity/secret setup","independent cleanup deployment, health and recovery","conservative fresh costs including NAT, transient node and storage"]});
    let cleanup = json!({"schemaVersion":1,"deploymentId":p.deployment_id,"expiryUnix":p.expires_at_unix,"outsideWorkloadCluster":true,"identity":a["cleanup"],"installed":false,"authorityReady":false,"target":"acknowledged create operation, resource name, createTime and revision receipts; no deployment UID is exposed","beforeCreate":["durable create intent and stable request UUID","immutable source generation and digest","independent cleanup receipt and health","fresh full preflight and cumulative allowance"],"destroy":["read pinned operation, createTime and revision receipts","observe active operation before deciding","DELETE underlying resources; never ABANDON or deleteState","retain Terraform source/state and cleanup receipts until absence confirmed","verify dependent resources and workload bucket versions are absent"],"unknownOutcome":"Persist uncertainty and read provider state. Never automatically replay a tool action after connection loss. Return unknown outcome to the caller/LLM so it can check status or read before deciding to retry.","notPartOfTerraformWorkload":["control-state/source bucket","cleanup scheduler and runner","bootstrap accounts and IAM grants"]});
    Ok(BTreeMap::from([
        ("gcp-main.tf.json".into(), json_bytes(&tf)?),
        ("gcp-provider.lock.hcl".into(), include_bytes!("../assets/gcp-google-6.44.0.lock.hcl").to_vec()),
        ("resource-graph.json".into(), json_bytes(&graph)?),
        ("gcp-cleanup-contract.json".into(), json_bytes(&cleanup)?),
        ("GCP-INFRASTRUCTURE.md".into(), b"# GCP private trial graph\n\nOffline export only; applyReady=false. No cloud or Terraform command was run.\nRead the exact pinned graph and gcp-cleanup-contract.json. This graph creates\na dedicated VPC/NAT, private zonal GKE Standard with two fixed AMD64 Ubuntu\nnodes, private Cloud SQL PostgreSQL, an artifact bucket and image registry.\nA temporary default node pool is created and removed during cluster setup.\nUbuntu availability does not qualify OpenShell kernel requirements.\n\nThe management endpoint requires a route from the installer into the VPC.\nNo public fallback or installer gateway is created. Database credentials/IAM\nusers, workload bucket identity, node grants and executor resource permissions\nstill require qualified setup. This is not a production HA/backup topology.\n\nTrial teardown intentionally deletes all workload artifacts and bucket versions.\nThe source/state bucket and independent cleanup deployment MUST be outside this\ngraph so destroying GKE cannot destroy their authority or recovery evidence.\nDo not add Terraform backend secrets, provisioners, arbitrary modules or passwords.\nCopy gcp-main.tf.json to main.tf.json and gcp-provider.lock.hcl to
.terraform.lock.hcl in a separate validation directory. Run only
init -backend=false -lockfile=readonly and validate during local qualification.\nA schema pass is not IAM, cost, effective-policy or live deployment qualification.\n".to_vec()),
    ]))
}
