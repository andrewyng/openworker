//! One-file front end for the existing identity/journal-aware kind installer.
//! This does not bootstrap Kubernetes or claim qualification on arbitrary clusters.
use super::super::up::config::Secret;
use super::features::{ManagementUI, Observability};
use super::*;
#[derive(Subcommand)]
pub(crate) enum Command {
    /// Resolve one YAML into a bound installer plan; no cluster mutations.
    Plan {
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        state: PathBuf,
    },
    /// Apply the exact reviewed local-kind plan. Kubernetes/OpenShell/DB prerequisites must be ready.
    Up {
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        accept_plan: String,
    },
    Status {
        #[arg(long)]
        state: PathBuf,
    },
    Destroy {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        deployment_id: String,
    },
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Deployment {
    api_version: String,
    name: String,
    #[serde(default, rename = "managementUI")]
    management_ui: ManagementUI,
    #[serde(default)]
    observability: Observability,
    #[serde(default)]
    models: BTreeMap<String, Secret>,
    advanced: Advanced,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Advanced {
    kubernetes: Kubernetes,
    service: Images,
    openshell: OpenShell,
    database: Database,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Kubernetes {
    kubeconfig: PathBuf,
    context: String,
    namespace: String,
    #[serde(default = "helm")]
    helm: PathBuf,
}
fn helm() -> PathBuf {
    "helm".into()
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Images {
    chart: PathBuf,
    image: String,
    runner_image: String,
    ui_image: Option<String>,
    model: Option<String>,
    #[serde(default)]
    scripted: bool,
}
#[derive(Deserialize, Serialize)]
#[serde(tag = "mode", rename_all = "lowercase", deny_unknown_fields)]
enum OpenShell {
    Existing {
        namespace: String,
        release: String,
        fullname: String,
    },
    #[serde(rename_all = "camelCase")]
    Install {
        namespace: String,
        release: String,
        fullname: String,
        archive: PathBuf,
        values: Value,
    },
}
impl OpenShell {
    fn scope(&self) -> (&str, &str, &str) {
        match self {
            Self::Existing {
                namespace,
                release,
                fullname,
            }
            | Self::Install {
                namespace,
                release,
                fullname,
                ..
            } => (namespace, release, fullname),
        }
    }
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Database {
    url: Secret,
    ca_file: PathBuf,
}
fn load_deployment(path: &Path) -> Result<Deployment> {
    let bytes = fs::read(path)?;
    ensure!(bytes.len() <= 65536, "Deployment YAML exceeds 64 KiB");
    let mut c: Deployment = serde_yaml::from_slice(&bytes).map_err(|_| {
        anyhow::anyhow!("Invalid local-k8s YAML schema; use env/file credential references")
    })?;
    ensure!(
        c.api_version == "ows/v1alpha1" && super::super::name_ok(&c.name),
        "Invalid deployment version/name"
    );
    let k = &c.advanced.kubernetes;
    ensure!(k.context.starts_with("kind-") && k.namespace.starts_with("ow-service-m4-") && bounded_name(&k.namespace),"local-k8s is qualified only for an explicit kind-* context and dedicated ow-service-m4-* namespace; remote-k8s remains roadmap");
    c.management_ui.validate()?;
    c.observability.validate()?;
    ensure!(
        pinned(&c.advanced.service.image) && pinned(&c.advanced.service.runner_image),
        "Pin service and runner images by digest"
    );
    if c.management_ui.enabled {
        ensure!(
            c.advanced.service.ui_image.as_deref().is_some_and(pinned),
            "Pin the enabled management UI image by digest"
        );
    }
    let base = path
        .canonicalize()?
        .parent()
        .context("Deployment directory missing")?
        .to_owned();
    let resolve = |p: &mut PathBuf| {
        if p.is_relative() {
            *p = base.join(&*p)
        }
    };
    resolve(&mut c.advanced.kubernetes.kubeconfig);
    resolve(&mut c.advanced.service.chart);
    resolve(&mut c.advanced.database.ca_file);
    if c.advanced.kubernetes.helm.components().count() > 1 {
        resolve(&mut c.advanced.kubernetes.helm);
    }
    if let OpenShell::Install { archive, .. } = &mut c.advanced.openshell {
        resolve(archive)
    }
    for (name, secret) in c.models.iter_mut().chain(std::iter::once((
        &"database".to_owned(),
        &mut c.advanced.database.url,
    ))) {
        ensure!(
            name == "database" || ["anthropic", "openai"].contains(&name.as_str()),
            "Only OpenAI/Anthropic deployment credential wiring is supported currently"
        );
        ensure!(
            secret.env.is_some() != secret.file.is_some(),
            "Use exactly one env/file reference per credential"
        );
        if let Some(env) = &secret.env {
            ensure!(
                !env.is_empty()
                    && env.len() <= 128
                    && env
                        .bytes()
                        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
                "Invalid credential variable name"
            );
        }
        if let Some(p) = &mut secret.file {
            resolve(p)
        }
    }
    Ok(c)
}
fn exact(root: &Path, name: &str, value: &Value) -> Result<()> {
    let path = root.join(name);
    if path.exists() {
        ensure!(
            read_json(&path)? == *value,
            "Saved deployment inputs changed; preserve the original plan and journal"
        );
    } else {
        super::super::up::write_private(&path, value)?;
    }
    Ok(())
}
fn secret(s: &Secret) -> Result<zeroize::Zeroizing<String>> {
    let value = if let Some(env) = &s.env {
        std::env::var(env).context("Credential environment variable unavailable")?
    } else {
        fs::read_to_string(s.file.as_ref().context("Credential reference missing")?)?
    };
    ensure!(
        !value.trim().is_empty() && value.len() <= 32768 && !value.trim().contains(['\n', '\r']),
        "Invalid credential format"
    );
    Ok(zeroize::Zeroizing::new(value.trim().to_string()))
}
fn render(c: &Deployment, root: &Path) -> Result<()> {
    let k = &c.advanced.kubernetes;
    let (shell_ns, _, fullname) = c.advanced.openshell.scope();
    let service_id = if root.join("service-values.json").exists() {
        read_json(&root.join("service-values.json"))?["config"]["service"]["id"]
            .as_str()
            .context("Original service identity missing")?
            .to_string()
    } else {
        format!("ows-{}", &uuid::Uuid::new_v4().simple().to_string()[..12])
    };
    let provider = if c.models.contains_key("anthropic") {
        "anthropic"
    } else {
        "openai"
    };
    let connections:Vec<_>=c.models.keys().enumerate().map(|(i,p)|json!({"id":if i==0{"primary"}else{p},"provider":p,"identity":format!("{service_id}-{p}"),"key_env":if p=="anthropic"{"ANTHROPIC_API_KEY"}else{"OPENAI_API_KEY"}})).collect();
    let mut values = json!({"image":c.advanced.service.image,"secrets":{"model":if c.models.is_empty(){""}else{"ow-service-model"}},"worker":{"replicas":1,"ownership":"manual","maxReplicas":2,"slots":2,"drainSeconds":30},"terminationGracePeriodSeconds":90,"config":{"service":{"id":service_id,"namespace":"local","agent":"installed-tools","legacy_compatibility":false},"database_env":"OW_PG_URL","schema":format!("ow_{}",c.name.replace('-',"_")),"database_loopback_plaintext":false,"database_ca":"/run/service/pg-ca.crt","state_root":"/state","bundle":"/opt/openworker/bundle","runner":"/opt/openworker/openworker","model":c.advanced.service.model.as_deref().unwrap_or("unconfigured"),"scripted":c.advanced.service.scripted,"model_provider":provider,"model_connections":connections,"api_token_file":"/bootstrap/admin-token","actor":"deployment-admin","openshell":{"identity":service_id,"endpoint":format!("https://{fullname}.{shell_ns}.svc.cluster.local:8080"),"tls_server_name":format!("{fullname}.{shell_ns}.svc.cluster.local"),"certificates":"/openshell","workspace":"default","image":c.advanced.service.runner_image}}});
    let mut ui = c.management_ui.clone();
    // The generated self-signed API certificate is also the public trust anchor.
    if ui.ca_secret.is_none() {
        ui.ca_secret = Some("ow-service-tls".into());
        ui.ca_key = Some("tls.crt".into());
    }
    features::configure_ui(
        &mut values,
        &ui,
        c.advanced.service.ui_image.as_deref(),
        "ows",
        &k.namespace,
    )?;
    if c.observability.enabled {
        values["config"]["observability"] =
            json!({"schema":format!("ow_observability_{}",c.name.replace('-',"_"))});
    }
    features::configure_observability(&mut values, &c.observability)?;
    exact(root, "service-values.json", &values)?;
    let shell = match &c.advanced.openshell {
        OpenShell::Existing {
            namespace,
            release,
            fullname,
        } => json!({"mode":"existing","namespace":namespace,"release":release,"fullname":fullname}),
        OpenShell::Install {
            namespace,
            release,
            fullname,
            archive,
            values,
        } => {
            exact(root, "openshell-values.json", values)?;
            json!({"mode":"install","namespace":namespace,"release":release,"fullname":fullname,"archive":archive,"valuesFile":root.join("openshell-values.json")})
        }
    };
    let installer = json!({"schemaVersion":1,"kubeconfig":k.kubeconfig,"context":k.context,"helm":k.helm,"service":{"namespace":k.namespace,"release":"ows","chart":c.advanced.service.chart,"valuesFile":root.join("service-values.json"),"fixtureDatabase":true},"openshell":shell});
    exact(root, "installer.json", &installer)?;
    exact(root, "deployment-input.json", &serde_json::to_value(c)?)
}
async fn credentials(c: &Deployment, root: &Path) -> Result<PathBuf> {
    let cert = root.join("api.crt");
    let key = root.join("api.key");
    if !cert.exists() || !key.exists() {
        ensure!(
            !cert.exists() && !key.exists(),
            "Partial certificate generation requires inspection"
        );
        exec(Path::new("openssl"),&args(&["req","-x509","-newkey","rsa:3072","-nodes","-days","30","-subj","/CN=localhost","-addext","basicConstraints=critical,CA:TRUE","-addext","keyUsage=critical,digitalSignature,keyCertSign,cRLSign","-addext","extendedKeyUsage=serverAuth","-addext",&format!("subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:ows-api.{}.svc,DNS:ows-api.{}.svc.cluster.local",c.advanced.kubernetes.namespace,c.advanced.kubernetes.namespace),"-keyout",key.to_str().context("Invalid key path")?,"-out",cert.to_str().context("Invalid certificate path")?]),None).await?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&key, fs::Permissions::from_mode(0o600))?;
    }
    let models: serde_json::Map<String, Value> = c
        .models
        .iter()
        .map(|(p, s)| Ok((p.clone(), json!(secret(s)?.as_str()))))
        .collect::<Result<_>>()?;
    let input = json!({"database":{"database-url":secret(&c.advanced.database.url)?.as_str(),"pg-ca.crt":fs::read_to_string(&c.advanced.database.ca_file)?},"apiTLS":{"tls.crt":fs::read_to_string(cert)?,"tls.key":fs::read_to_string(key)?},"model":if models.is_empty(){Value::Null}else{json!(models)}});
    let path = root.join("installation-secrets.json");
    super::super::up::write_private(&path, &input)?;
    Ok(path)
}
pub(crate) async fn run(command: &Command) -> Result<()> {
    let (config, root, accept) = match command {
        Command::Plan { config, state } => (Some(config), state, None),
        Command::Up {
            config,
            state,
            accept_plan,
        } => (Some(config), state, Some(accept_plan)),
        Command::Status { state } => (None, state, None),
        Command::Destroy {
            state,
            deployment_id,
        } => {
            return super::run(&super::Command::Destroy {
                state: state.join("installation"),
                deployment_id: deployment_id.clone(),
            })
            .await
        }
    };
    if let Command::Status { .. } = command {
        return super::run(&super::Command::Status {
            state: root.join("installation"),
        })
        .await;
    }
    let c = load_deployment(config.unwrap())?;
    let _lock = super::super::up::operation_lock(root, "configuration.lock")?;
    let root = root.canonicalize()?;
    render(&c, &root)?;
    let state = root.join("installation");
    if !state.exists() {
        super::run(&super::Command::Plan {
            config: root.join("installer.json"),
            out: state.clone(),
        })
        .await?;
    }
    let plan: Plan = read_json(&state.join("local-plan.json"))
        .and_then(|v| serde_json::from_value(v).map_err(Into::into))?;
    if matches!(command, Command::Plan { .. }) {
        return crate::output(
            json!({"mode":"local-k8s","phase":"review","state":root,"planDigest":plan.digest,"deploymentId":plan.id,"clusterAlreadyExists":true,"mutations":0}),
        );
    }
    let accept = accept.unwrap();
    ensure!(
        accept == &plan.digest,
        "Approval digest differs from original installer plan"
    );
    super::run(&super::Command::Apply {
        state: state.clone(),
        accept_plan: accept.clone(),
        openshell_only: true,
        confirm_no_effect: None,
        reconcile_ready_release: None,
    })
    .await?;
    let input = credentials(&c, &root).await?;
    super::run(&super::Command::Secrets {
        state: state.clone(),
        input,
        accept_plan: accept.clone(),
    })
    .await?;
    super::run(&super::Command::Apply {
        state: state.clone(),
        accept_plan: accept.clone(),
        openshell_only: false,
        confirm_no_effect: None,
        reconcile_ready_release: None,
    })
    .await?;
    features::ui(&features::UICommand::Status {
        target: features::Target { state },
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn one_yaml_reference_renders_private_origin_and_installer_files_without_secrets_or_effects() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../deploy/service/examples/local-k8s/deployment.yaml");
        let c = load_deployment(&path).unwrap();
        let d = tempfile::tempdir().unwrap();
        render(&c, d.path()).unwrap();
        let v = read_json(&d.path().join("service-values.json")).unwrap();
        assert_eq!(v["managementUI"]["publicOrigin"], "https://localhost:8444");
        assert_eq!(v["config"]["observability"]["retention_days"], 14);
        assert!(!d.path().join("installation-secrets.json").exists());
        let _: Config =
            serde_json::from_value(read_json(&d.path().join("installer.json")).unwrap()).unwrap();
        render(&c, d.path()).unwrap();
        let mut changed = c;
        changed.management_ui.port = Some(9000);
        assert!(render(&changed, d.path()).is_err());
    }
}
