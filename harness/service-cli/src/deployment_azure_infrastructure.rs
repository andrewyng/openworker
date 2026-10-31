//! Reviewed ARM exports. Privileged bindings are deliberately separate from the
//! executor graph: normal deployment authority cannot grant itself Azure roles.
use super::*;

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
pub(crate) struct Inputs {
    pub(crate) kubernetes_version: String,
    pub(crate) node_vm_size: String,
    pub(crate) ssh_public_key: String,
    pub(crate) admin_group_object_ids: Vec<String>,
    pub(crate) control_resource_group: String,
    pub(crate) guard_image: String,
    pub(crate) postgres_password: SecretReference,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct SecretReference {
    key_vault_id: String,
    secret_name: String,
    secret_version: String,
}
fn uuid(s: &str) -> bool {
    uuid::Uuid::parse_str(s).is_ok_and(|u| !u.is_nil() && u.to_string() == s)
}
fn identifier(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 63
        && s.bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}
impl Inputs {
    pub(crate) fn validate(&self, p: &Plan) -> Result<()> {
        use base64::Engine;
        let identity = p
            .azure_bootstrap
            .as_ref()
            .context("Azure bootstrap required")?;
        ensure!(
            matches!(p.target, Target::Azure { .. })
                && p.schema_version == 2
                && p.gcp_infrastructure.is_none(),
            "Azure graph requires Azure identity-only base"
        );
        let version: Vec<_> = self.kubernetes_version.split('.').collect();
        ensure!(
            version.len() == 3
                && version[0] == "1"
                && version.iter().all(|s| !s.is_empty()
                    && s.len() <= 3
                    && s.bytes().all(|c| c.is_ascii_digit())),
            "Pin an exact Kubernetes 1.x.y version; regional availability requires live preflight"
        );
        ensure!(
            ["Standard_D4s_v5", "Standard_D8s_v5"].contains(&self.node_vm_size.as_str()),
            "Use an explicitly bounded AMD64 node SKU; OpenShell compatibility remains a live gate"
        );
        let key: Vec<_> = self.ssh_public_key.split_whitespace().collect();
        ensure!(
            key.len() == 2
                && ["ssh-ed25519", "ssh-rsa"].contains(&key[0])
                && (32..=2048).contains(&key[1].len())
                && base64::engine::general_purpose::STANDARD
                    .decode(key[1])
                    .is_ok(),
            "Provide an SSH public key only, without comments"
        );
        ensure!(
            !self.admin_group_object_ids.is_empty()
                && self.admin_group_object_ids.len() <= 4
                && self.admin_group_object_ids.iter().all(|s| uuid(s)),
            "Use 1..4 exact Entra administrator group object IDs"
        );
        let distinct: std::collections::BTreeSet<_> = self.admin_group_object_ids.iter().collect();
        ensure!(
            distinct.len() == self.admin_group_object_ids.len(),
            "Duplicate admin groups"
        );
        ensure!(
            identifier(&self.control_resource_group)
                && self.control_resource_group != identity.resource_group,
            "Cleanup requires a distinct existing dedicated control resource group"
        );
        let (registry, digest) = self
            .guard_image
            .split_once("@sha256:")
            .context("Pin cleanup image by SHA256")?;
        ensure!(
            registry.contains('/')
                && !registry.starts_with('/')
                && registry.len() <= 200
                && registry
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"./_-:".contains(&b))
                && digest.len() == 64
                && digest
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "Invalid pinned cleanup image"
        );
        let Target::Azure { subscription, .. } = &p.target else {
            unreachable!()
        };
        let prefix = format!("/subscriptions/{subscription}/resourceGroups/");
        let rest = self
            .postgres_password
            .key_vault_id
            .strip_prefix(&prefix)
            .context("Key Vault must be in the target subscription")?;
        let (rg, vault) = rest
            .split_once("/providers/Microsoft.KeyVault/vaults/")
            .context("Exact existing Key Vault resource ID required")?;
        ensure!(
            identifier(rg)
                && identifier(vault)
                && identifier(&self.postgres_password.secret_name)
                && self.postgres_password.secret_version.len() == 32
                && self
                    .postgres_password
                    .secret_version
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit()),
            "Pin Key Vault secret name and version; plaintext secrets are forbidden"
        );
        ensure!(
            !self
                .guard_image
                .contains(&format!("{}.azurecr.io", names(p).registry)),
            "Cleanup image must survive deletion of the workload registry"
        );
        Ok(())
    }
}
pub(crate) struct Names {
    pub(crate) prefix: String,
    pub(crate) storage: String,
    pub(crate) registry: String,
}
pub(crate) fn names(p: &Plan) -> Names {
    let id = &hash(p.deployment_id.as_bytes())[..18];
    Names {
        prefix: format!("ow-{}", &id[..12]),
        storage: format!("ow{id}"),
        registry: format!("ow{id}acr"),
    }
}
pub(crate) fn id(p: &Plan, kind: &str, name: &str) -> String {
    format!("{}/providers/{kind}/{name}", scope(p))
}
pub(crate) fn control_scope(p: &Plan) -> String {
    let Target::Azure { subscription, .. } = &p.target else {
        unreachable!()
    };
    format!(
        "/subscriptions/{subscription}/resourceGroups/{}",
        p.azure_infrastructure
            .as_ref()
            .unwrap()
            .control_resource_group
    )
}
pub(crate) fn infrastructure(o: &Infrastructure) -> Result<()> {
    let (mut p, _) = inspect(&o.plan)?;
    ensure!(
        p.azure_infrastructure.is_none() && now()? < p.expires_at_unix,
        "Use an unexpired bootstrap bundle without infrastructure"
    );
    p.azure_infrastructure = Some(serde_json::from_slice(
        &fs::read(&o.config).context("Read infrastructure config")?,
    )?);
    p.validate()?;
    let m = write_bundle(&o.out, &p)?;
    crate::output(result(&p, &m))
}
fn template(resources: Vec<Value>, outputs: Value) -> Value {
    json!({"$schema":"https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#","contentVersion":"1.0.0.0","parameters":{"ownershipNonce":{"type":"string"},"bundleDigest":{"type":"string"}},"resources":resources,"outputs":outputs})
}
fn tags(p: &Plan) -> Value {
    json!({"ows-deployment":p.deployment_id,"ows-expiry":p.expires_at_unix.to_string(),"ows-nonce":"[parameters('ownershipNonce')]","ows-bundle":"[parameters('bundleDigest')]"})
}
fn resource(p: &Plan, typ: &str, api: &str, name: &str, properties: Value) -> Value {
    let Target::Azure { location, .. } = &p.target else {
        unreachable!()
    };
    json!({"type":typ,"apiVersion":api,"name":name,"location":location,"tags":tags(p),"properties":properties})
}
fn mi(p: &Plan, purpose: &str) -> String {
    id(
        p,
        "Microsoft.ManagedIdentity/userAssignedIdentities",
        &format!("{}-{purpose}", names(p).prefix),
    )
}
fn principal(resource_id: &str) -> String {
    format!("[reference('{resource_id}', '2023-01-31').principalId]")
}
fn assignment_resource(
    p: &Plan,
    purpose: &str,
    scope: &str,
    principal_id: Value,
    role_id: &str,
) -> Value {
    json!({"type":"Microsoft.Authorization/roleAssignments","apiVersion":"2022-04-01","name":guid(p,purpose),"scope":scope,"properties":{"principalId":principal_id,"principalType":"ServicePrincipal","roleDefinitionId":role_id}})
}
/// Exact top-level workload IDs in dependency-safe deletion order. Nested children
/// disappear with parents; the customer RG and AKS-generated node RG are not owned.
pub(crate) fn inventory(p: &Plan) -> Vec<Value> {
    let n = names(p);
    [
      ("cluster","Microsoft.ContainerService/managedClusters",format!("{}-aks",n.prefix),"2024-10-01","/systemData/createdAt"),
      ("foundation","Microsoft.DBforPostgreSQL/flexibleServers",format!("{}-pg",n.prefix),"2024-08-01","/systemData/createdAt"),
      ("foundation","Microsoft.Network/privateEndpoints",format!("{}-blob",n.prefix),"2024-05-01","/systemData/createdAt"),
      ("foundation","Microsoft.Storage/storageAccounts",n.storage,"2023-05-01","/properties/creationTime"),
      ("foundation","Microsoft.ContainerRegistry/registries",n.registry,"2023-07-01","/properties/creationDate"),
      ("foundation","Microsoft.Network/privateDnsZones",format!("{}.postgres.database.azure.com",n.prefix),"2020-06-01","/systemData/createdAt"),
      ("foundation","Microsoft.Network/privateDnsZones","privatelink.blob.core.windows.net".into(),"2020-06-01","/systemData/createdAt"),
      ("foundation","Microsoft.Network/virtualNetworks",format!("{}-vnet",n.prefix),"2024-05-01","/properties/resourceGuid"),
      ("foundation","Microsoft.ManagedIdentity/userAssignedIdentities",format!("{}-artifacts",n.prefix),"2023-01-31","/properties/principalId"),
      ("foundation","Microsoft.ManagedIdentity/userAssignedIdentities",format!("{}-kubelet",n.prefix),"2023-01-31","/properties/principalId"),
      ("foundation","Microsoft.ManagedIdentity/userAssignedIdentities",format!("{}-cluster",n.prefix),"2023-01-31","/properties/principalId"),
    ].into_iter().map(|(stage,kind,name,api,pointer)|json!({"stage":stage,"id":id(p,kind,&name),"apiVersion":api,"incarnationPointer":pointer})).collect()
}
pub(crate) fn artifacts(p: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
    let c = p
        .azure_infrastructure
        .as_ref()
        .context("Infrastructure required")?;
    c.validate(p)?;
    let n = names(p);
    let Target::Azure {
        tenant,
        subscription,
        location,
    } = &p.target
    else {
        unreachable!()
    };
    let vnet = id(
        p,
        "Microsoft.Network/virtualNetworks",
        &format!("{}-vnet", n.prefix),
    );
    let aks_subnet = format!("{vnet}/subnets/aks");
    let pg_subnet = format!("{vnet}/subnets/postgres");
    let pg_zone = id(
        p,
        "Microsoft.Network/privateDnsZones",
        &format!("{}.postgres.database.azure.com", n.prefix),
    );
    let blob_zone = id(
        p,
        "Microsoft.Network/privateDnsZones",
        "privatelink.blob.core.windows.net",
    );
    let storage = id(p, "Microsoft.Storage/storageAccounts", &n.storage);
    let registry = id(p, "Microsoft.ContainerRegistry/registries", &n.registry);
    let cluster = mi(p, "cluster");
    let kubelet = mi(p, "kubelet");
    let artifacts = mi(p, "artifacts");
    let mut foundation = vec![resource(
        p,
        "Microsoft.Network/virtualNetworks",
        "2024-05-01",
        &format!("{}-vnet", n.prefix),
        json!({"addressSpace":{"addressPrefixes":["10.72.0.0/16"]},"subnets":[{"name":"aks","properties":{"addressPrefix":"10.72.0.0/22"}},{"name":"postgres","properties":{"addressPrefix":"10.72.4.0/24","delegations":[{"name":"postgres","properties":{"serviceName":"Microsoft.DBforPostgreSQL/flexibleServers"}}]}},{"name":"private-endpoints","properties":{"addressPrefix":"10.72.5.0/24","privateEndpointNetworkPolicies":"Disabled"}}]}),
    )];
    for purpose in ["cluster", "kubelet", "artifacts"] {
        foundation.push(resource(
            p,
            "Microsoft.ManagedIdentity/userAssignedIdentities",
            "2023-01-31",
            &format!("{}-{purpose}", n.prefix),
            json!({}),
        ));
    }
    for (zone, zone_id) in [
        (
            format!("{}.postgres.database.azure.com", n.prefix),
            &pg_zone,
        ),
        ("privatelink.blob.core.windows.net".into(), &blob_zone),
    ] {
        let mut r = resource(
            p,
            "Microsoft.Network/privateDnsZones",
            "2020-06-01",
            &zone,
            json!({}),
        );
        r["location"] = json!("global");
        foundation.push(r);
        foundation.push(json!({"type":"Microsoft.Network/privateDnsZones/virtualNetworkLinks","apiVersion":"2020-06-01","name":format!("{zone}/workload"),"location":"global","dependsOn":[zone_id,&vnet],"properties":{"virtualNetwork":{"id":vnet},"registrationEnabled":false}}));
    }
    let mut st = resource(
        p,
        "Microsoft.Storage/storageAccounts",
        "2023-05-01",
        &n.storage,
        json!({"allowBlobPublicAccess":false,"allowSharedKeyAccess":false,"minimumTlsVersion":"TLS1_2","supportsHttpsTrafficOnly":true,"publicNetworkAccess":"Disabled","networkAcls":{"defaultAction":"Deny","bypass":"None"}}),
    );
    st["sku"] = json!({"name":"Standard_LRS"});
    st["kind"] = json!("StorageV2");
    foundation.push(st);
    foundation.push(json!({"type":"Microsoft.Storage/storageAccounts/blobServices/containers","apiVersion":"2023-05-01","name":format!("{}/default/artifacts",n.storage),"dependsOn":[&storage],"properties":{"publicAccess":"None"}}));
    let mut pe = resource(
        p,
        "Microsoft.Network/privateEndpoints",
        "2024-05-01",
        &format!("{}-blob", n.prefix),
        json!({"subnet":{"id":format!("{vnet}/subnets/private-endpoints")},"privateLinkServiceConnections":[{"name":"blob","properties":{"privateLinkServiceId":storage,"groupIds":["blob"]}}]}),
    );
    pe["dependsOn"] = json!([vnet, storage]);
    foundation.push(pe);
    foundation.push(json!({"type":"Microsoft.Network/privateEndpoints/privateDnsZoneGroups","apiVersion":"2024-05-01","name":format!("{}-blob/default",n.prefix),"dependsOn":[id(p,"Microsoft.Network/privateEndpoints",&format!("{}-blob",n.prefix)),blob_zone],"properties":{"privateDnsZoneConfigs":[{"name":"blob","properties":{"privateDnsZoneId":blob_zone}}]}}));
    let mut acr = resource(
        p,
        "Microsoft.ContainerRegistry/registries",
        "2023-07-01",
        &n.registry,
        json!({"adminUserEnabled":false,"anonymousPullEnabled":false,"publicNetworkAccess":"Enabled"}),
    );
    acr["sku"] = json!({"name":"Basic"});
    foundation.push(acr);
    let mut pg = resource(
        p,
        "Microsoft.DBforPostgreSQL/flexibleServers",
        "2024-08-01",
        &format!("{}-pg", n.prefix),
        json!({"version":"16","administratorLogin":"owsadmin","administratorLoginPassword":"[parameters('postgresPassword')]","storage":{"storageSizeGB":32},"backup":{"backupRetentionDays":7,"geoRedundantBackup":"Disabled"},"highAvailability":{"mode":"Disabled"},"network":{"delegatedSubnetResourceId":pg_subnet,"privateDnsZoneArmResourceId":pg_zone,"publicNetworkAccess":"Disabled"}}),
    );
    pg["sku"] = json!({"name":"Standard_B1ms","tier":"Burstable"});
    pg["dependsOn"] = json!([vnet, pg_zone]);
    foundation.push(pg);
    let mut ft = template(foundation, json!({}));
    ft["parameters"]["postgresPassword"] = json!({"type":"secureString"});
    let mut aks = resource(
        p,
        "Microsoft.ContainerService/managedClusters",
        "2024-10-01",
        &format!("{}-aks", n.prefix),
        json!({"dnsPrefix":n.prefix,"kubernetesVersion":c.kubernetes_version,"enableRBAC":true,"disableLocalAccounts":true,"aadProfile":{"managed":true,"enableAzureRBAC":false,"adminGroupObjectIDs":c.admin_group_object_ids,"tenantID":tenant},"apiServerAccessProfile":{"enablePrivateCluster":true,"enablePrivateClusterPublicFQDN":false,"privateDNSZone":"System"},"oidcIssuerProfile":{"enabled":true},"securityProfile":{"workloadIdentity":{"enabled":true}},"linuxProfile":{"adminUsername":"owsadmin","ssh":{"publicKeys":[{"keyData":c.ssh_public_key}]}},"agentPoolProfiles":[{"name":"system","count":2,"vmSize":c.node_vm_size,"osDiskSizeGB":64,"osType":"Linux","osSKU":"Ubuntu","mode":"System","type":"VirtualMachineScaleSets","enableAutoScaling":false,"vnetSubnetID":aks_subnet}],"networkProfile":{"networkPlugin":"azure","networkPolicy":"azure","serviceCidr":"10.73.0.0/24","dnsServiceIP":"10.73.0.10","loadBalancerSku":"standard","outboundType":"loadBalancer"},"identityProfile":{"kubeletidentity":{"resourceId":kubelet,"clientId":format!("[reference('{kubelet}', '2023-01-31').clientId]"),"objectId":principal(&kubelet)}}}),
    );
    aks["identity"] = json!({"type":"UserAssigned","userAssignedIdentities":{cluster.clone():{}}});
    aks["sku"] = json!({"name":"Base","tier":"Free"});
    let ct = template(
        vec![aks],
        json!({"oidcIssuer":{"type":"string","value":format!("[reference('{}', '2024-10-01').oidcIssuerProfile.issuerURL]",id(p,"Microsoft.ContainerService/managedClusters",&format!("{}-aks",n.prefix)))}}),
    );
    let builtin = |s: &str| {
        format!(
            "/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/{s}"
        )
    };
    let bindings = template(
        vec![
            assignment_resource(
                p,
                "cluster-network",
                &vnet,
                json!(principal(&cluster)),
                &builtin("4d97b98b-1d4f-4787-a291-c67834d212e7"),
            ),
            assignment_resource(
                p,
                "cluster-kubelet",
                &kubelet,
                json!(principal(&cluster)),
                &builtin("f1a07417-d97a-45cb-824c-7a7467783830"),
            ),
            assignment_resource(
                p,
                "kubelet-pull",
                &registry,
                json!(principal(&kubelet)),
                &builtin("7f951dda-4ed3-4680-a7ca-43fe172d538d"),
            ),
            assignment_resource(
                p,
                "artifact-data",
                &format!("{storage}/blobServices/default/containers/artifacts"),
                json!(principal(&artifacts)),
                &builtin("ba92f5b4-2d11-453d-a403-e96b0029c9fe"),
            ),
        ],
        json!({}),
    );
    let aks_id = id(
        p,
        "Microsoft.ContainerService/managedClusters",
        &format!("{}-aks", n.prefix),
    );
    let fic = template(
        vec![
            json!({"type":"Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials","apiVersion":"2023-01-31","name":format!("{}-artifacts/ows-artifacts",n.prefix),"properties":{"issuer":format!("[reference('{aks_id}', '2024-10-01').oidcIssuerProfile.issuerURL]"),"subject":"system:serviceaccount:ow-service:ows-artifacts","audiences":["api://AzureADTokenExchange"]}}),
        ],
        json!({}),
    );
    let mut actions = std::collections::BTreeSet::<String>::new();
    for r in inventory(p) {
        let resource_id = r["id"].as_str().unwrap();
        let kind = resource_id
            .split("/providers/")
            .nth(1)
            .unwrap()
            .split('/')
            .take(2)
            .collect::<Vec<_>>()
            .join("/");
        for verb in ["read", "write", "delete"] {
            actions.insert(format!("{kind}/{verb}"));
        }
    }
    for action in [
        "Microsoft.Network/virtualNetworks/subnets/read",
        "Microsoft.Network/virtualNetworks/subnets/join/action",
        "Microsoft.Network/privateDnsZones/virtualNetworkLinks/read",
        "Microsoft.Network/privateDnsZones/virtualNetworkLinks/write",
        "Microsoft.Network/privateDnsZones/virtualNetworkLinks/delete",
        "Microsoft.Network/privateDnsZones/join/action",
        "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/read",
        "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/write",
        "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/delete",
        "Microsoft.Storage/storageAccounts/blobServices/containers/read",
        "Microsoft.Storage/storageAccounts/blobServices/containers/write",
        "Microsoft.Storage/storageAccounts/blobServices/containers/delete",
        "Microsoft.Resources/deployments/read",
        "Microsoft.Resources/deployments/write",
        "Microsoft.Resources/deployments/operations/read",
        "Microsoft.Resources/deployments/operationstatuses/read",
    ] {
        actions.insert(action.into());
    }
    // assign/action is granted only on these exact managed identities by a reviewed administrator.
    let executor_role = json!({"properties":{"roleName":format!("{}-executor",n.prefix),"type":"CustomRole","assignableScopes":[scope(p)],"permissions":[{"actions":actions,"notActions":[],"dataActions":[],"notDataActions":[]}]}});
    let mut assignment_actions = vec![];
    for resource_id in [&cluster, &kubelet] {
        assignment_actions.push(json!({"scope":resource_id,"actions":["Microsoft.ManagedIdentity/userAssignedIdentities/assign/action"],"recipientObjectId":p.azure_bootstrap.as_ref().unwrap().setup_object_id}));
    }
    let review=format!("# Azure ARM graph (experimental, live-unqualified)\n\nTarget `{subscription}` / `{location}`. Workload scope `{}`. Independent control scope `{}`. Both groups already exist and remain customer owned.\n\nOrder: bootstrap independent control and verify heartbeat; submit foundation; have a bootstrap administrator apply resource-specific bindings; submit cluster; have administrator apply federation. Normal executor role has no RBAC writes. Exported identity-assignment and Key Vault deploy grants are separate customer-reviewed prerequisites.\n\nTwo bounded AMD64 Ubuntu nodes; private AKS management API, PostgreSQL and Blob. ACR Basic remains authenticated public HTTPS (no admin credentials). Existing private network access and DNS are needed by the installer. Fixed network ranges must not overlap the customer's network. Admin group memberships/Entra privileges are customer owned.\n\nThe PostgreSQL secret is a pinned Key Vault reference, never plaintext. Existing vault requires template-deployment enabled and exact vault `Microsoft.KeyVault/vaults/deploy/action` for the deployment caller. ARM resolves the secret; no value is exported.\n\nResource tags and names are bookkeeping, not ownership authority. Cleanup uses exact IDs + persisted nonce and immutable creation receipts; missing receipts stop deletion. The AKS provider manages its node RG; we never delete arbitrary `MC_*` groups. No customer RG delete, wildcard role assignment, account key/SAS, Entra app creation or broad subscription grant is exported.\n\nThis export is not a production readiness certificate. Region/version/SKU/quota/price, Azure Policy/locks/effective IAM, actual API schema validation, private routes, cleanup and AKS/OpenShell runtime compatibility need a separately authorized live trial. No paid resource is created by export. Budget alerts/TTL are not a hard cost cap.\n",scope(p),control_scope(p));
    Ok(BTreeMap::from([
        ("azure-foundation.arm.json".into(), json_bytes(&ft)?),
        ("azure-cluster.arm.json".into(), json_bytes(&ct)?),
        ("azure-bindings.arm.json".into(), json_bytes(&bindings)?),
        ("azure-federation.arm.json".into(), json_bytes(&fic)?),
        (
            "azure-foundation.parameters.json".into(),
            json_bytes(
                &json!({"$schema":"https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#","contentVersion":"1.0.0.0","parameters":{"postgresPassword":{"reference":{"keyVault":{"id":c.postgres_password.key_vault_id},"secretName":c.postgres_password.secret_name,"secretVersion":c.postgres_password.secret_version}}}}),
            )?,
        ),
        (
            "azure-executor-role.json".into(),
            json_bytes(&executor_role)?,
        ),
        (
            "azure-executor-prerequisites.json".into(),
            json_bytes(
                &json!({"principalObjectId":p.azure_bootstrap.as_ref().unwrap().setup_object_id,"identityAssignments":assignment_actions,"keyVaultDeploy":{"scope":c.postgres_password.key_vault_id,"actions":["Microsoft.KeyVault/vaults/deploy/action"]},"bindingsRequireBootstrapAdmin":true,"automaticGrant":false}),
            )?,
        ),
        (
            "azure-inventory.json".into(),
            json_bytes(
                &json!({"schemaVersion":1,"resources":inventory(p),"resourceGroupOwned":false,"controlScope":control_scope(p),"applyReady":false}),
            )?,
        ),
        ("AZURE-INFRASTRUCTURE.md".into(), review.into_bytes()),
    ]))
}
