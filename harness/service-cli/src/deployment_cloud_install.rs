//! Cloud outputs -> reviewed Kubernetes handoff. No implicit ingress or credential export.
use super::*;
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Binding {
    pub state: PathBuf,
    pub profile: String,
}

fn image(value: &Value, repository: &str) -> Result<()> {
    let text = value
        .as_str()
        .context("Digest-pinned cloud image required")?;
    let suffix = text
        .strip_prefix(&format!("{repository}@sha256:"))
        .context("Image must belong to the original stack's repository")?;
    ensure!(
        suffix.len() == 64
            && suffix
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "Invalid image digest"
    );
    Ok(())
}
pub(super) async fn snapshot(
    binding: &Binding,
) -> Result<(
    Journal,
    Plan,
    Aws,
    Value,
    BTreeMap<String, String>,
    fs::File,
)> {
    let (j, p, lock) = state::load(&binding.state)?;
    ensure!(
        j.phase == "infrastructure_ready",
        "Original infrastructure must be ready before installation"
    );
    let mut api = Aws {
        p: p.clone(),
        profile: binding.profile.clone(),
        root: binding.state.clone(),
        preflight: None,
    };
    api.identity().await?;
    let observed = state::observe(&j, &p, &mut api).await?;
    api.guard_health().await?;
    ensure!(
        observed["observed"]["infra"]["status"] == "CREATE_COMPLETE",
        "Original infrastructure is not ready"
    );
    let outputs: BTreeMap<String, String> =
        serde_json::from_value(observed["observed"]["infra"]["outputs"].clone())?;
    let cluster = read_cluster(&api, &outputs).await?;
    Ok((j, p, api, cluster, outputs, lock))
}
pub(super) async fn read_cluster(api: &Aws, outputs: &BTreeMap<String, String>) -> Result<Value> {
    let Target::Aws { account, region } = &api.p.target else {
        unreachable!()
    };
    let name = api.p.prefix();
    ensure!(
        outputs.get("Cluster") == Some(&name)
            && outputs.get("ExpectedClusterArn")
                == Some(&format!("arn:aws:eks:{region}:{account}:cluster/{name}")),
        "Original stack's cluster output mismatch"
    );
    let data = api
        .json("eks", "describe-cluster", json!({"name":name}))
        .await?;
    let c = &data["cluster"];
    let endpoint = c["endpoint"].as_str().context("Cluster endpoint missing")?;
    ensure!(
        endpoint.starts_with("https://")
            && !endpoint[8..].is_empty()
            && !endpoint[8..].contains(['/', '?', '#', '@'])
            && !endpoint.chars().any(char::is_whitespace),
        "Invalid private cluster management endpoint"
    );
    ensure!(
        c["arn"] == format!("arn:aws:eks:{region}:{account}:cluster/{name}")
            && c["name"] == name
            && c["status"] == "ACTIVE"
            && c["version"] == api.p.infrastructure.as_ref().unwrap().kubernetes_version
            && c["resourcesVpcConfig"]["endpointPublicAccess"] == false
            && c["resourcesVpcConfig"]["endpointPrivateAccess"] == true
            && c["resourcesVpcConfig"]["vpcId"].as_str() == outputs.get("Vpc").map(String::as_str),
        "Cluster identity, version, private access or VPC changed"
    );
    let pem = base64::engine::general_purpose::STANDARD
        .decode(
            c["certificateAuthority"]["data"]
                .as_str()
                .context("Cluster CA missing")?,
        )
        .context("Invalid cluster CA")?;
    ensure!(
        pem.len() < 16384 && String::from_utf8_lossy(&pem).contains("-----BEGIN CERTIFICATE-----"),
        "Invalid cluster CA"
    );
    Ok(c.clone())
}
pub(super) fn kubeconfig(p: &Plan, profile: &str, cluster: &Value) -> Value {
    let Target::Aws { region, .. } = &p.target else {
        unreachable!()
    };
    let name = p.prefix();
    json!({"apiVersion":"v1","kind":"Config","clusters":[{"name":name,"cluster":{"server":cluster["endpoint"],"certificate-authority-data":cluster["certificateAuthority"]["data"]}}],
        "contexts":[{"name":name,"context":{"cluster":name,"user":name}}],"current-context":name,
        "users":[{"name":name,"user":{"exec":{"apiVersion":"client.authentication.k8s.io/v1beta1","command":"aws","interactiveMode":"Never","args":["eks","get-token","--cluster-name",name,"--region",region,"--profile",profile]}}}]})
}
pub(crate) async fn verify_installation(
    binding: &Binding,
    path: &Path,
    context: &str,
    values: &Path,
    provisioning: bool,
) -> Result<()> {
    let (_j, p, _api, c, outputs, _lock) = snapshot(binding).await?;
    if provisioning {
        ensure!(
            p.created_at_unix <= now()? && now()? < p.expires_at_unix,
            "Expiry blocks new Kubernetes provisioning; observation/teardown remain available"
        );
    }
    let content: Value = serde_json::from_slice(&read_file(
        path.parent().unwrap(),
        path.file_name().unwrap().to_str().unwrap(),
    )?)?;
    ensure!(
        context == p.prefix() && content == kubeconfig(&p, &binding.profile, &c),
        "Kubeconfig must match the original private cluster endpoint, CA and setup profile"
    );
    let values: Value = serde_json::from_slice(&read_file(
        values.parent().unwrap(),
        values.file_name().unwrap().to_str().unwrap(),
    )?)?;
    verify_values(&p, &outputs, &values)
}
fn verify_values(p: &Plan, outputs: &BTreeMap<String, String>, v: &Value) -> Result<()> {
    let Target::Aws { account, region } = &p.target else {
        unreachable!()
    };
    let registry = format!("{account}.dkr.ecr.{region}.amazonaws.com/{}", p.prefix());
    for (key, suffix) in [
        ("HarnessRepository", "harness"),
        ("RunnerRepository", "runner"),
        ("UiRepository", "service-ui"),
    ] {
        ensure!(
            outputs.get(key) == Some(&format!("{registry}/{suffix}")),
            "Original image repository mismatch"
        );
    }
    image(&v["image"], &outputs["HarnessRepository"])?;
    image(
        &v["config"]["openshell"]["image"],
        &outputs["RunnerRepository"],
    )?;
    if v["managementUI"]["enabled"] == true {
        image(&v["managementUI"]["image"], &outputs["UiRepository"])?;
    }
    ensure!(
        v["config"]["service"]["id"] == p.deployment_id
            && v["config"]["service"]["legacy_compatibility"] == false
            && v["config"]["database_loopback_plaintext"] == false
            && v["config"]["database_ca"] == "/run/service/pg-ca.crt"
            && v["config"]["artifacts"]["kind"] == "s3"
            && v["config"]["artifacts"]["deployment"] == p.deployment_id
            && v["config"]["artifacts"]["bucket"].as_str()
                == outputs.get("ArtifactBucket").map(String::as_str)
            && v["config"]["artifacts"]["region"] == *region
            && v["config"]["artifacts"]
                .get("endpoint")
                .is_none_or(Value::is_null),
        "Service identity/database verification/artifact scope differs from original deployment"
    );
    ensure!(
        outputs.get("ArtifactBucket") == Some(&format!("{}-{account}", p.prefix()))
            && outputs.get("ArtifactPrefix") == Some(&format!("{}/", p.deployment_id)),
        "Original artifact scope mismatch"
    );
    Ok(())
}
/// Persist before any Kubernetes installer mutation. A read-only handoff alone
/// must not strand empty infrastructure behind a nonexistent teardown receipt.
pub(crate) fn installation_intent(binding: &Binding) -> Result<()> {
    let (mut journal, plan, _lock) = state::load(&binding.state)?;
    ensure!(
        journal.phase == "infrastructure_ready"
            && !journal.operations.contains_key("delete:infra")
            && now()? < plan.expires_at_unix,
        "Cloud infrastructure is no longer open for installation"
    );
    let op = journal
        .operations
        .get_mut("installation-handoff")
        .context("Original installation handoff missing")?;
    if op.status != "mutation-intent" {
        ensure!(op.status == "prepared", "Unexpected installation state");
        op.status = "mutation-intent".into();
        state::save(&binding.state, &journal)?;
    }
    Ok(())
}
pub(super) async fn prepare(root: &Path, profile: &str, config: &Path, out: &Path) -> Result<()> {
    let binding = Binding {
        state: root.canonicalize()?,
        profile: profile.into(),
    };
    let (mut j, p, _api, c, outputs, _lock) = snapshot(&binding).await?;
    ensure!(
        now()? < p.expires_at_unix,
        "Expiry blocks new installation handoffs"
    );
    let parent = config
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut config: Value = serde_json::from_slice(&read_file(
        parent,
        config.file_name().unwrap().to_str().unwrap(),
    )?)?;
    ensure!(
        config["service"]["namespace"] == "ow-service"
            && config["service"]["release"] == "ows"
            && config["service"]["fixtureDatabase"] == false,
        "Cloud service uses namespace ow-service, release ows and a managed database"
    );
    let values = PathBuf::from(
        config["service"]["valuesFile"]
            .as_str()
            .context("Service values file required")?,
    )
    .canonicalize()?;
    let v: Value = serde_json::from_slice(&read_file(
        values.parent().unwrap(),
        values.file_name().unwrap().to_str().unwrap(),
    )?)?;
    verify_values(&p, &outputs, &v)?;
    let parent = out
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    ensure!(
        !parent.starts_with(&j.plan) && !parent.starts_with(root.canonicalize()?),
        "Installation handoff must be separate from immutable plan/lifecycle state"
    );
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder
        .create(out)
        .context("Fresh installation handoff directory required")?;
    let target = out.canonicalize()?;
    if !j.operations.contains_key("installation-handoff") {
        j.operations.insert(
            "installation-handoff".into(),
            state::Operation {
                token: uuid::Uuid::new_v4().to_string(),
                arn: j.operations["create:infra"].arn.clone(),
                status: "prepared".into(),
            },
        );
        state::save(root, &j)?;
    }
    config["kubeconfig"] = json!(target.join("kubeconfig.json"));
    config["context"] = json!(p.prefix());
    config["cloud"] = serde_json::to_value(binding)?;
    config["service"]["valuesFile"] = json!(values);
    let receipt = json!({"deploymentId":p.deployment_id,"bundleDigest":j.bundle_digest,"infraArn":j.operations["create:infra"].arn,"clusterArn":c["arn"],"privateEndpoint":c["endpoint"],"certificateSha256":hash(&base64::engine::general_purpose::STANDARD.decode(c["certificateAuthority"]["data"].as_str().unwrap())?),"outputs":outputs,"serviceValuesSha256":hash(&json_bytes(&v)?),"credentialsIncluded":false,"networkPathRequired":true});
    for (name, value) in [
        ("kubeconfig.json", kubeconfig(&p, profile, &c)),
        ("installer-config.json", config),
        ("cloud-handoff.json", receipt),
    ] {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(target.join(name))?;
        file.write_all(&json_bytes(&value)?)?;
        file.sync_all()?;
    }
    crate::output(
        json!({"installationConfig":target.join("installer-config.json"),"clusterManagementApi":"private","credentialsIncluded":false,"installed":false}),
    )
}
pub(crate) async fn verify_network(binding: &Binding) -> Result<()> {
    let (_j, p, api, _c, _outputs, _lock) = snapshot(binding).await?;
    let addon = api
        .json(
            "eks",
            "describe-addon",
            json!({"clusterName":p.prefix(),"addonName":"vpc-cni"}),
        )
        .await?;
    let config: Value = serde_json::from_str(
        addon["addon"]["configurationValues"]
            .as_str()
            .context("CNI configuration missing")?,
    )?;
    ensure!(addon["addon"]["status"]=="ACTIVE"&&addon["addon"]["addonVersion"]==p.infrastructure.as_ref().unwrap().addons.vpc_cni&&config["enableNetworkPolicy"]=="true"&&config["env"]["NETWORK_POLICY_ENFORCING_MODE"]=="strict","Strict network policy enforcement must be installed/verified before admitting service/sandbox pods");
    Ok(())
}
pub(crate) async fn database_url(binding: &Binding) -> Result<String> {
    let (_j, p, api, _c, outputs, _lock) = snapshot(binding).await?;
    let Target::Aws { account, region } = &p.target else {
        unreachable!()
    };
    let arn = outputs
        .get("DatabaseSecretArn")
        .context("Original database secret ARN missing")?;
    ensure!(
        arn.starts_with(&format!(
            "arn:aws:secretsmanager:{region}:{account}:secret:{}-database-",
            p.prefix()
        )),
        "Database secret scope mismatch"
    );
    let raw = api
        .json(
            "secretsmanager",
            "get-secret-value",
            json!({"SecretId":arn}),
        )
        .await?;
    ensure!(raw["ARN"] == *arn, "Database secret identity changed");
    let secret: Value = serde_json::from_str(
        raw["SecretString"]
            .as_str()
            .context("Database secret unavailable")?,
    )
    .map_err(|_| anyhow::anyhow!("Invalid private database secret"))?;
    let host = outputs
        .get("DatabaseEndpoint")
        .context("Database endpoint missing")?;
    ensure!(
        secret["host"] == *host
            && secret["port"] == 5432
            && secret["username"] == "openworker"
            && secret["dbname"] == "openworker"
            && host.ends_with(&format!(".{region}.rds.amazonaws.com"))
            && !host.chars().any(char::is_whitespace),
        "Managed database identity differs from original stack"
    );
    let password = secret["password"]
        .as_str()
        .context("Database password unavailable")?;
    ensure!(
        !password.is_empty() && password.len() <= 1024,
        "Invalid private database secret"
    );
    Ok(managed_database_url(host, password))
}
fn managed_database_url(host: &str, password: &str) -> String {
    let encode = |s: &str| {
        s.bytes()
            .map(|b| {
                if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
                    (b as char).to_string()
                } else {
                    format!("%{b:02X}")
                }
            })
            .collect::<String>()
    };
    // rust-postgres accepts require, not libpq's verify-full spelling. The
    // service's Verified TLS connector independently verifies CA + hostname;
    // installation values require it and the RDS CA bundle, without opt-outs.
    format!(
        "postgresql://openworker:{}@{host}:5432/openworker?sslmode=require",
        encode(password)
    )
}
pub(crate) fn install_scope(binding: &Binding) -> Result<(String, String)> {
    let (_j, p, _lock) = state::load(&binding.state)?;
    Ok((p.deployment_id, p.infrastructure.unwrap().vpc_cidr))
}
pub(crate) async fn enable_strict_network(binding: &Binding) -> Result<bool> {
    let (mut j, p, api, _c, _outputs, _lock) = snapshot(binding).await?;
    let addon = api
        .json(
            "eks",
            "describe-addon",
            json!({"clusterName":p.prefix(),"addonName":"vpc-cni"}),
        )
        .await?;
    let Target::Aws { account, region } = &p.target else {
        unreachable!()
    };
    let arn = addon["addon"]["addonArn"]
        .as_str()
        .context("CNI immutable identity missing")?
        .to_string();
    ensure!(
        arn.starts_with(&format!(
            "arn:aws:eks:{region}:{account}:addon/{}/vpc-cni/",
            p.prefix()
        )) && addon["addon"]["addonVersion"] == p.infrastructure.as_ref().unwrap().addons.vpc_cni,
        "CNI identity/version mismatch"
    );
    let current: Value = serde_json::from_str(
        addon["addon"]["configurationValues"]
            .as_str()
            .context("CNI configuration missing")?,
    )?;
    let desired =
        json!({"enableNetworkPolicy":"true","env":{"NETWORK_POLICY_ENFORCING_MODE":"strict"}});
    let key = "eks-strict-network";
    if let Some(op) = j.operations.get(key) {
        ensure!(
            op.arn.as_ref() == Some(&arn),
            "CNI immutable identity changed"
        );
    }
    if current == desired && addon["addon"]["status"] == "ACTIVE" {
        if let Some(op) = j.operations.get_mut(key) {
            op.status = "complete".into();
            state::save(&binding.state, &j)?;
        }
        return Ok(true);
    }
    if addon["addon"]["status"] == "UPDATING" && j.operations.contains_key(key) {
        return Ok(false);
    }
    ensure!(
        !j.operations.contains_key(key),
        "CNI update outcome unknown/failed; inspect original intent instead of replaying"
    );
    ensure!(
        p.created_at_unix <= now()? && now()? < p.expires_at_unix,
        "Expiry prevents new platform mutations"
    );
    ensure!(
        addon["addon"]["status"] == "ACTIVE"
            && current
                == json!({"enableNetworkPolicy":"true","env":{"NETWORK_POLICY_ENFORCING_MODE":"standard"}}),
        "CNI drift/health requires operator review"
    );
    let token = uuid::Uuid::new_v4().to_string();
    j.operations.insert(
        key.into(),
        state::Operation {
            token: token.clone(),
            arn: Some(arn),
            status: "intent".into(),
        },
    );
    state::save(&binding.state, &j)?;
    let r=api.json("eks","update-addon",json!({"clusterName":p.prefix(),"addonName":"vpc-cni","addonVersion":p.infrastructure.as_ref().unwrap().addons.vpc_cni,"configurationValues":serde_json::to_string(&desired)?,"resolveConflicts":"NONE","clientRequestToken":token})).await?;
    let id = r["update"]["id"]
        .as_str()
        .context("CNI update response unknown; inspect original request")?;
    ensure!(id.len() <= 128 && !id.is_empty(), "Invalid CNI update ID");
    j.operations.get_mut(key).unwrap().status = format!("dispatched:{id}");
    state::save(&binding.state, &j)?;
    Ok(false)
}

#[cfg(test)]
mod database_url_tests {
    use super::*;
    #[test]
    fn managed_url_is_accepted_by_actual_driver_and_preserves_password() {
        let host = "trial.fixture.us-west-2.rds.amazonaws.com";
        let password = "test:@ /?#%+;&=雪";
        let url = managed_database_url(host, password);
        let parsed: postgres::Config = url
            .parse()
            .expect("actual driver must accept emitted configuration");
        assert_eq!(parsed.get_password(), Some(password.as_bytes()));
        assert_eq!(parsed.get_ssl_mode(), postgres::config::SslMode::Require);
        assert_eq!(
            parsed.get_hosts(),
            &[postgres::config::Host::Tcp(host.into())]
        );
        assert_eq!(parsed.get_dbname(), Some("openworker"));
    }
}
