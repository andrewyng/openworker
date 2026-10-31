//! Azure handoff to the existing, journaled Helm/OpenShell installer. Kubeconfig
//! contains an Entra exec plugin, never an admin certificate/static bearer token.
use super::api::Azure;
use super::*;
use base64::Engine;
use reqwest::Method;
#[derive(Subcommand)]
pub enum Installation {
    /// Observe owned AKS and export a reviewed private Helm handoff; no Kubernetes writes.
    Prepare {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Verify the original handoff against live ARM/control receipts; no Kubernetes writes.
    Verify {
        #[arg(long)]
        config: PathBuf,
    },
    /// Record verified shared-installer teardown before manual infrastructure destruction.
    Close {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        installation_state: PathBuf,
    },
}
#[derive(Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Binding {
    state: PathBuf,
    values_hash: String,
    kubeconfig_hash: String,
    context: String,
}
impl Binding {
    fn digest(&self) -> Result<String> {
        Ok(hash(&json_bytes(self)?))
    }
}
fn cluster_id(p: &Plan) -> String {
    graph::id(
        p,
        "Microsoft.ContainerService/managedClusters",
        &format!("{}-aks", graph::names(p).prefix),
    )
}
fn kubeconfig(p: &Plan, cluster: &Value, credentials: &Value) -> Result<Value> {
    let encoded = credentials["kubeconfigs"]
        .as_array()
        .filter(|v| v.len() == 1)
        .and_then(|v| v[0]["value"].as_str())
        .context("Exactly one Entra user kubeconfig required")?;
    ensure!(encoded.len() <= 128 * 1024, "Kubeconfig too large");
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .context("Invalid kubeconfig encoding")?;
    let raw: Value = serde_yaml::from_slice(&bytes).context("Invalid kubeconfig")?;
    let details = raw["clusters"]
        .as_array()
        .filter(|v| v.len() == 1)
        .map(|v| &v[0]["cluster"])
        .context("Exactly one cluster required")?;
    let endpoint = details["server"]
        .as_str()
        .context("Private endpoint missing")?;
    let parsed = reqwest::Url::parse(endpoint)?;
    ensure!(
        parsed.scheme() == "https"
            && parsed.host_str() == cluster["properties"]["privateFQDN"].as_str()
            && parsed.username().is_empty()
            && parsed.password().is_none()
            && parsed.query().is_none()
            && parsed.fragment().is_none()
            && parsed.path() == "/"
            && parsed.port().is_none(),
        "Credential endpoint differs from original private AKS API"
    );
    let ca = details["certificate-authority-data"]
        .as_str()
        .context("Embedded cluster CA required")?;
    let pem = base64::engine::general_purpose::STANDARD.decode(ca)?;
    ensure!(
        pem.len() < 16384 && String::from_utf8_lossy(&pem).contains("-----BEGIN CERTIFICATE-----"),
        "Invalid cluster CA"
    );
    let Target::Azure { tenant, .. } = &p.target else {
        unreachable!()
    };
    let name = graph::names(p).prefix;
    Ok(
        json!({"apiVersion":"v1","kind":"Config","clusters":[{"name":name,"cluster":{"server":endpoint,"certificate-authority-data":ca}}],"contexts":[{"name":name,"context":{"cluster":name,"user":name}}],"current-context":name,"users":[{"name":name,"user":{"exec":{"apiVersion":"client.authentication.k8s.io/v1beta1","interactiveMode":"Never","command":"kubelogin","args":["get-token","--login","azurecli","--tenant-id",tenant,"--server-id","6dae42f8-4368-4678-94ff-3960e28e3630"]}}}]}),
    )
}
fn values(p: &Plan, identity: &Value, v: &Value) -> Result<()> {
    let Target::Azure { tenant, .. } = &p.target else {
        unreachable!()
    };
    let n = graph::names(p);
    let repo = format!("{}.azurecr.io/", n.registry);
    for field in [&v["image"], &v["config"]["openshell"]["image"]] {
        let s = field
            .as_str()
            .context("Digest-pinned owned ACR image required")?;
        ensure!(
            s.starts_with(&repo)
                && s.rsplit_once("@sha256:").is_some_and(|(_, d)| d.len() == 64
                    && d.bytes()
                        .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))),
            "Image outside original ACR or not pinned"
        );
    }
    if v["managementUI"]["enabled"] == true {
        let image = v["managementUI"]["image"]
            .as_str()
            .context("Pinned UI image required")?;
        ensure!(
            image.starts_with(&repo)
                && image
                    .rsplit_once("@sha256:")
                    .is_some_and(|(_, d)| d.len() == 64
                        && d.bytes()
                            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))),
            "UI image outside original ACR or not pinned"
        );
    }
    ensure!(
        v["config"]["service"]["id"] == p.deployment_id
            && v["config"]["service"]["legacy_compatibility"] == false
            && v["config"]["database_loopback_plaintext"] == false
            && v["config"]["database_ca"] == "/run/service/pg-ca.crt",
        "Service identity or verified database TLS configuration differs"
    );
    let expected = json!({"kind":"azure","deployment":p.deployment_id,"account":n.storage,"container":"artifacts","tenant_id":tenant,"client_id":identity["properties"]["clientId"],"federated_token_file":"/var/run/secrets/azure/tokens/azure-identity-token"});
    ensure!(
        v["config"]["artifacts"] == expected,
        "Azure artifacts must match exact original container/tenant/identity/projection"
    );
    ensure!(
        v["apiReplicas"]
            .as_u64()
            .is_some_and(|n| (1..=2).contains(&n))
            && v["worker"]["replicas"]
                .as_u64()
                .is_some_and(|n| (1..=2).contains(&n))
            && v["worker"]["maxReplicas"]
                .as_u64()
                .is_some_and(|n| (1..=2).contains(&n)),
        "Keep the bounded two-node reference topology"
    );
    Ok(())
}
async fn observed(root: &Path, provisioning: bool) -> Result<(Plan, Azure, Value, Value)> {
    let (p, mut api, cluster) = recovery::installation_snapshot(root, provisioning).await?;
    ensure!(
        cluster["properties"]["apiServerAccessProfile"]["enablePrivateCluster"] == true
            && cluster["properties"]["disableLocalAccounts"] == true
            && cluster["properties"]["networkProfile"]["networkPolicy"] == "azure"
            && cluster["properties"]["securityProfile"]["workloadIdentity"]["enabled"] == true,
        "AKS private API, network policy and workload identity required"
    );
    ensure!(
        cluster["properties"]["kubernetesVersion"]
            == p.azure_infrastructure.as_ref().unwrap().kubernetes_version,
        "Original AKS version changed"
    );
    let ident = graph::id(
        &p,
        "Microsoft.ManagedIdentity/userAssignedIdentities",
        &format!("{}-artifacts", graph::names(&p).prefix),
    );
    let r = api
        .arm(
            Method::GET,
            &ident,
            "2023-01-31",
            None,
            &uuid::Uuid::new_v4().to_string(),
        )
        .await?;
    ensure!(r.status == 200, "Artifact identity unavailable");
    let identity = r.body.context("Artifact identity missing")?;
    ensure!(
        identity["properties"]["clientId"]
            .as_str()
            .is_some_and(|v| uuid::Uuid::parse_str(v).is_ok_and(|u| !u.is_nil())),
        "Artifact client ID missing"
    );
    let r = api
        .arm(
            Method::POST,
            &format!("{}/listClusterUserCredential", cluster_id(&p)),
            "2024-10-01",
            None,
            &uuid::Uuid::new_v4().to_string(),
        )
        .await?;
    ensure!(r.status==200,"Entra user kubeconfig unavailable; grant exact cluster user-credential action, never admin credentials");
    let kube = kubeconfig(
        &p,
        &cluster,
        &r.body.context("Credential response missing")?,
    )?;
    Ok((p, api, kube, identity))
}
pub(crate) async fn verify(
    binding: &Binding,
    path: &Path,
    context: &str,
    values_path: &Path,
    provisioning: bool,
) -> Result<()> {
    let kube_bytes = super::super::read_file(
        path.parent().unwrap(),
        path.file_name().unwrap().to_str().unwrap(),
    )?;
    let value_bytes = super::super::read_file(
        values_path.parent().unwrap(),
        values_path.file_name().unwrap().to_str().unwrap(),
    )?;
    ensure!(
        context == binding.context
            && hash(&kube_bytes) == binding.kubeconfig_hash
            && hash(&value_bytes) == binding.values_hash,
        "Azure installation inputs changed"
    );
    let (p, _, expected, identity) = observed(&binding.state, provisioning).await?;
    let actual: Value = serde_json::from_slice(&super::super::read_file(
        path.parent().unwrap(),
        path.file_name().unwrap().to_str().unwrap(),
    )?)?;
    ensure!(
        context == graph::names(&p).prefix && actual == expected,
        "Kubeconfig differs from original AKS endpoint/CA/Entra plugin"
    );
    let v: Value = serde_json::from_slice(&super::super::read_file(
        values_path.parent().unwrap(),
        values_path.file_name().unwrap().to_str().unwrap(),
    )?)?;
    values(&p, &identity, &v)
}
pub(crate) async fn intent(binding: &Binding) -> Result<()> {
    recovery::installation_mark(&binding.state, &binding.digest()?, false).await
}
pub(crate) fn database(binding: &Binding, url: &str) -> Result<()> {
    let p = recovery::local_plan(&binding.state)?;
    let n = graph::names(&p);
    let u = reqwest::Url::parse(url).context("Invalid database connection")?;
    ensure!(
        matches!(u.scheme(), "postgres" | "postgresql")
            && u.host_str()
                == Some(format!("{}-pg.postgres.database.azure.com", n.prefix).as_str())
            && u.port().is_none_or(|port| port == 5432)
            && u.username() == "owsadmin"
            && u.password().is_some()
            && u.path() == "/postgres"
            && u.query_pairs().count() == 1
            && u.query_pairs()
                .all(|(k, v)| k == "sslmode" && v == "verify-full")
            && u.fragment().is_none(),
        "Database must use original PostgreSQL host/database and verify-full TLS"
    );
    Ok(())
}
pub(crate) fn storage(classes: &Value) -> Result<()> {
    let defaults: Vec<_> = classes["items"]
        .as_array()
        .context("Storage classes missing")?
        .iter()
        .filter(|s| {
            s["metadata"]["annotations"]["storageclass.kubernetes.io/is-default-class"] == "true"
        })
        .collect();
    ensure!(
        defaults.len() == 1
            && defaults[0]["provisioner"] == "disk.csi.azure.com"
            && defaults[0]["reclaimPolicy"] == "Delete"
            && defaults[0]["volumeBindingMode"] == "WaitForFirstConsumer",
        "AKS requires one Azure Disk CSI default with Delete + delayed binding"
    );
    Ok(())
}
pub(crate) async fn install(command: &Installation) -> Result<()> {
    match command {
        Installation::Prepare { state, config, out } => {
            outside(out, state)?;
            let mut c: Value = serde_json::from_slice(&super::super::read_file(
                config.parent().unwrap(),
                config.file_name().unwrap().to_str().unwrap(),
            )?)?;
            ensure!(c["service"]["namespace"]=="ow-service"&&c["service"]["release"]=="ows"&&c["service"]["fixtureDatabase"]==false&&c.get("cloud").is_none()&&c.get("azure").is_none(),"Use a fresh managed-database installation config, namespace ow-service, release ows and no existing cloud binding");
            let vp = PathBuf::from(
                c["service"]["valuesFile"]
                    .as_str()
                    .context("Service values required")?,
            )
            .canonicalize()?;
            let v: Value = serde_json::from_slice(&super::super::read_file(
                vp.parent().unwrap(),
                vp.file_name().unwrap().to_str().unwrap(),
            )?)?;
            let (p, _, kube, identity) = observed(state, true).await?;
            values(&p, &identity, &v)?;
            let mut d = fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                d.mode(0o700);
            }
            d.create(out)
                .context("Use a fresh private handoff directory")?;
            let root = out.canonicalize()?;
            let kb = json_bytes(&kube)?;
            let binding = Binding {
                state: state.canonicalize()?,
                context: graph::names(&p).prefix,
                values_hash: hash(&fs::read(&vp)?),
                kubeconfig_hash: hash(&kb),
            };
            c["azure"] = serde_json::to_value(&binding)?;
            c["context"] = json!(binding.context);
            c["kubeconfig"] = json!(root.join("kubeconfig.json"));
            c["service"]["valuesFile"] = json!(vp);
            write(&root, "kubeconfig.json", &kb)?;
            write(&root, "cluster-install.json", &json_bytes(&c)?)?;
            write(&root, "AZURE-INSTALLATION.md", GUIDE.as_bytes())?;
            crate::output(
                json!({"deploymentId":p.deployment_id,"path":out,"bindingDigest":binding.digest()?,"kubernetesMutated":false,"liveQualified":false}),
            )
        }
        Installation::Verify { config } => {
            let c: Value = serde_json::from_slice(&super::super::read_file(
                config.parent().unwrap(),
                config.file_name().unwrap().to_str().unwrap(),
            )?)?;
            let binding: Binding = serde_json::from_value(c["azure"].clone())?;
            verify(
                &binding,
                Path::new(c["kubeconfig"].as_str().context("Kubeconfig required")?),
                c["context"].as_str().context("Context required")?,
                Path::new(
                    c["service"]["valuesFile"]
                        .as_str()
                        .context("Values required")?,
                ),
                true,
            )
            .await?;
            crate::output(
                json!({"armBindingVerified":true,"kubernetesMutated":false,"liveQualified":false}),
            )
        }
        Installation::Close {
            state,
            installation_state,
        } => {
            let local_plan: Value = serde_json::from_slice(&super::super::read_file(
                installation_state,
                "local-plan.json",
            )?)?;
            let journal: Value = serde_json::from_slice(&super::super::read_file(
                installation_state,
                "journal.json",
            )?)?;
            let binding: Binding = serde_json::from_value(local_plan["config"]["azure"].clone())?;
            ensure!(
                binding.state == state.canonicalize()?
                    && journal["phase"] == "destroyed"
                    && journal["planDigest"] == local_plan["digest"],
                "Original shared-installer teardown receipt required"
            );
            super::super::local::verify_azure_teardown(installation_state, &binding).await?;
            recovery::installation_mark(state, &binding.digest()?, true).await?;
            crate::output(json!({"installationClosed":true,"infrastructureMutated":false}))
        }
    }
}
fn write(root: &Path, name: &str, b: &[u8]) -> Result<()> {
    let mut o = fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(root.join(name))?;
    f.write_all(b)?;
    f.sync_all()?;
    Ok(())
}
const GUIDE: &str = r#"# Azure shared installation handoff

This private handoff binds original ARM ownership, private AKS endpoint/CA and
Entra user login to the same journaled Helm installer used on EKS/kind. It does
not contain admin certificates, bearer tokens, database passwords or model keys.

The installer needs a private network/DNS path to AKS, `kubelogin` with Azure CLI
login in the pinned tenant, `kubectl`, Helm, a reviewed platform manifest and the
pinned OpenShell 0.1.3 archive/images. Installing OpenShell/Agent Sandbox requires
reviewed cluster-scoped Kubernetes rights; namespace RBAC alone is insufficient.
No provider/plugin/component is installed implicitly by preparing this handoff.

Run `ows deploy cluster plan --config cluster-install.json --out NEW_STATE`, then
review its digest. Use shared `platform`, `apply --openshell-only`, `secrets`,
`apply`, `status` and `destroy` commands with that exact digest. The installer
checks live ARM/control ownership and a recent external heartbeat before new
mutations; retries retain existing Helm unknown-outcome safeguards.

Use `secrets --input /private/secrets.json`: database URL must target the original
private PostgreSQL host and postgres database, user owsadmin and verify-full TLS;
include its trusted CA in pg-ca.crt. API TLS/private key and model keys also travel
through private files/stdin to named Kubernetes Secrets. No secret belongs in
values, process arguments, the immutable deployment bundle or control Blob.
Credential retrieval from Key Vault is customer/installer-owned in this milestone.

The artifact backend uses AKS OIDC federation to the dedicated managed identity,
exact ow-service/ows-artifacts subject, private Blob endpoint and native Rust
Azure adapter. API/worker alone receive the projected token. Verify real webhook,
role assignment, issuer, private DNS, CSI provisioning, network deny probes and
OpenShell runtime on AKS before claiming deployment qualification.

After `deploy cluster destroy` confirms teardown, run `deploy azure installation
close` with both original state paths before manual infrastructure destruction.
Expiry remains an explicitly agreed hard workload lifetime even if agents are
active. Audit control resources remain for explicit customer removal.
"#;

#[cfg(test)]
mod tests {
    use super::*;
    fn credentials() -> (Plan, Value, Value) {
        let p = recovery::fixture_plan();
        let cluster = json!({"properties":{"privateFQDN":"aks.privatelink.westus2.azmk8s.io"}});
        let kube = json!({"clusters":[{"cluster":{"server":"https://aks.privatelink.westus2.azmk8s.io:443","certificate-authority-data":base64::engine::general_purpose::STANDARD.encode("-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----")}}],"users":[{"user":{"token":"this-is-never-copied"}}]});
        let response = json!({"kubeconfigs":[{"value":base64::engine::general_purpose::STANDARD.encode(serde_json::to_vec(&kube).unwrap())}]});
        (p, cluster, response)
    }
    fn input(p: &Plan) -> (Value, Value) {
        let identity = json!({"properties":{"clientId":"12345678-1234-4234-8234-123456789abc"}});
        let Target::Azure { tenant, .. } = &p.target else {
            unreachable!()
        };
        let n = graph::names(p);
        let v = json!({"image":format!("{}.azurecr.io/harness@sha256:{}",n.registry,"a".repeat(64)),"apiReplicas":1,"worker":{"replicas":1,"maxReplicas":2},"config":{"service":{"id":p.deployment_id,"legacy_compatibility":false},"database_ca":"/run/service/pg-ca.crt","database_loopback_plaintext":false,"openshell":{"image":format!("{}.azurecr.io/runner@sha256:{}",n.registry,"a".repeat(64))},"artifacts":{"kind":"azure","deployment":p.deployment_id,"account":n.storage,"container":"artifacts","tenant_id":tenant,"client_id":identity["properties"]["clientId"],"federated_token_file":"/var/run/secrets/azure/tokens/azure-identity-token"}}});
        (identity, v)
    }
    #[test]
    fn handoff_uses_private_original_ca_and_entra_plugin_without_copying_credentials() {
        let (p, cluster, response) = credentials();
        let kube = kubeconfig(&p, &cluster, &response).unwrap();
        let text = kube.to_string();
        assert!(!text.contains("this-is-never-copied"));
        assert!(!text.contains("client-key-data"));
        assert_eq!(kube["users"][0]["user"]["exec"]["command"], "kubelogin");
        assert!(text.contains("azurecli"));
        let mut changed = cluster;
        changed["properties"]["privateFQDN"] = json!("attacker.invalid");
        assert!(kubeconfig(&p, &changed, &response).is_err());
        let mut duplicate = response.clone();
        duplicate["kubeconfigs"]
            .as_array_mut()
            .unwrap()
            .push(response["kubeconfigs"][0].clone());
        assert!(kubeconfig(&p, &json!({}), &duplicate).is_err());
    }
    #[test]
    fn installation_rejects_other_registry_container_identity_or_plaintext_database() {
        let p = recovery::fixture_plan();
        let (identity, v) = input(&p);
        values(&p, &identity, &v).unwrap();
        for (field, value) in [
            ("account", json!("foreignaccount")),
            ("container", json!("other")),
            ("client_id", json!("00000000-0000-0000-0000-000000000000")),
            ("endpoint", json!("http://insecure.invalid")),
        ] {
            let mut bad = v.clone();
            bad["config"]["artifacts"][field] = value;
            assert!(values(&p, &identity, &bad).is_err());
        }
        let mut bad = v.clone();
        bad["config"]["database_loopback_plaintext"] = json!(true);
        assert!(values(&p, &identity, &bad).is_err());
        let mut bad = v;
        bad["image"] = json!(format!(
            "foreign.azurecr.io/harness@sha256:{}",
            "a".repeat(64)
        ));
        assert!(values(&p, &identity, &bad).is_err());
    }
    #[test]
    fn database_secret_must_target_original_host_and_verified_tls() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("state");
        let p = recovery::fixture_state_dir(&root);
        let binding = Binding {
            state: root,
            values_hash: "".into(),
            kubeconfig_hash: "".into(),
            context: "".into(),
        };
        let url=format!("postgres://owsadmin:fixture-secret@{}-pg.postgres.database.azure.com/postgres?sslmode=verify-full",graph::names(&p).prefix);
        database(&binding, &url).unwrap();
        for bad in [
            url.replace("verify-full", "disable"),
            url.replace(".postgres.database.azure.com", ".attacker.invalid"),
            format!("{url}&sslmode=disable"),
            url.replace("owsadmin:", "other:"),
        ] {
            assert!(database(&binding, &bad).is_err());
        }
    }
    #[test]
    fn aks_storage_requires_exact_csi_and_safe_volume_lifecycle() {
        let good = json!({"items":[{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}},"provisioner":"disk.csi.azure.com","reclaimPolicy":"Delete","volumeBindingMode":"WaitForFirstConsumer"}]});
        storage(&good).unwrap();
        for (field, value) in [
            ("provisioner", "evil.csi"),
            ("reclaimPolicy", "Retain"),
            ("volumeBindingMode", "Immediate"),
        ] {
            let mut bad = good.clone();
            bad["items"][0][field] = json!(value);
            assert!(storage(&bad).is_err());
        }
        let mut multiple = good.clone();
        multiple["items"]
            .as_array_mut()
            .unwrap()
            .push(good["items"][0].clone());
        assert!(storage(&multiple).is_err());
    }
}
