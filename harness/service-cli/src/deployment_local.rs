//! Local kind installer. No AWS/GCP/Azure API. Cluster prerequisites are external.
//! Namespace UIDs, Helm receipts and input hashes protect existing installations.
use super::*;
use std::{process::Stdio, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    process::Command as Process,
};
const OWNER: &str = "openworker.io/installer-id";
const CHART_SHA: &str = "5a82e27d6cf4089fa16d96c87d9fc0fe62f65fa05341465bc749ba5ab91abb33";
#[derive(Subcommand)]
pub enum Command {
    Plan {
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    Apply {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        accept_plan: String,
        #[arg(long)]
        openshell_only: bool,
        /// Only after independently proving an uncertain operation had no effect.
        #[arg(long)]
        confirm_no_effect: Option<String>,
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
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Config {
    schema_version: u16,
    kubeconfig: PathBuf,
    context: String,
    helm: PathBuf,
    service: Service,
    openshell: Shell,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Service {
    namespace: String,
    release: String,
    chart: PathBuf,
    values_file: PathBuf,
    fixture_database: bool,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(tag = "mode", rename_all = "lowercase", deny_unknown_fields)]
enum Shell {
    #[serde(rename_all = "camelCase")]
    Install {
        namespace: String,
        release: String,
        fullname: String,
        archive: PathBuf,
        values_file: PathBuf,
    },
    Existing {
        namespace: String,
        release: String,
        fullname: String,
    },
}
impl Shell {
    fn scope(&self) -> (&str, &str, &str) {
        match self {
            Self::Install {
                namespace,
                release,
                fullname,
                ..
            }
            | Self::Existing {
                namespace,
                release,
                fullname,
            } => (namespace, release, fullname),
        }
    }
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Plan {
    schema_version: u16,
    id: String,
    config: Config,
    hashes: BTreeMap<String, String>,
    cluster_uid: String,
    digest: String,
}
#[derive(Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Journal {
    schema_version: u16,
    plan_digest: String,
    phase: String,
    namespaces: BTreeMap<String, String>,
    operations: BTreeMap<String, Operation>,
    #[serde(default)]
    confirmed_no_effect: Vec<Operation>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Operation {
    status: String,
    request_id: String,
    receipt: Option<Value>,
}
struct Client<'a> {
    c: &'a Config,
}
async fn exec(bin: &Path, a: &[String], input: Option<Vec<u8>>) -> Result<Vec<u8>> {
    let mut child = Process::new(bin)
        .args(a)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .context("Local installer tool could not start")?;
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let mut stdin = child.stdin.take().unwrap();
    async fn bytes<R: tokio::io::AsyncRead + Unpin>(s: R, max: u64) -> std::io::Result<Vec<u8>> {
        let mut b = vec![];
        s.take(max + 1).read_to_end(&mut b).await?;
        Ok(b)
    }
    let operation = async {
        tokio::try_join!(
            child.wait(),
            bytes(stdout, 8 * 1024 * 1024),
            bytes(stderr, 65536),
            async move {
                if let Some(v) = input {
                    stdin.write_all(&v).await?;
                }
                drop(stdin);
                Ok(())
            }
        )
    };
    match tokio::time::timeout(Duration::from_secs(240), operation).await {
        Ok(Ok((status, out, err, _))) => {
            ensure!(
                out.len() <= 8 * 1024 * 1024 && err.len() <= 65536,
                "Local tool output limit exceeded; inspect original operation"
            );
            ensure!(status.success(),"Local tool failed; result may be unknown. Inspect scoped resources before retrying. Tool output withheld to protect credentials");
            Ok(out)
        }
        _ => {
            let _ = child.kill().await;
            bail!("Local tool timed out/failed; inspect original operation before retrying")
        }
    }
}
fn args(a: &[&str]) -> Vec<String> {
    a.iter().map(|s| s.to_string()).collect()
}
impl Client<'_> {
    async fn kube(&self, a: Vec<String>, input: Option<Vec<u8>>) -> Result<Vec<u8>> {
        let mut all = args(&[
            "--kubeconfig",
            self.c
                .kubeconfig
                .to_str()
                .context("Invalid kubeconfig path")?,
            "--context",
            &self.c.context,
        ]);
        all.extend(a);
        exec(Path::new("kubectl"), &all, input).await
    }
    async fn get(&self, kind: &str, name: &str, namespace: Option<&str>) -> Result<Option<Value>> {
        let mut a = args(&["get", kind, name, "--ignore-not-found=true", "-o", "json"]);
        if let Some(ns) = namespace {
            a.extend(args(&["-n", ns]));
        }
        let b = self.kube(a, None).await?;
        if b.iter().all(u8::is_ascii_whitespace) {
            return Ok(None);
        }
        Ok(Some(
            serde_json::from_slice(&b).context("Invalid cluster response")?,
        ))
    }
    async fn helm(&self, a: Vec<String>) -> Result<Vec<u8>> {
        let mut all = a;
        all.extend(args(&[
            "--kubeconfig",
            self.c.kubeconfig.to_str().unwrap(),
            "--kube-context",
            &self.c.context,
        ]));
        exec(&self.c.helm, &all, None).await
    }
    async fn release(&self, ns: &str, name: &str) -> Result<Option<Value>> {
        let rows: Value = serde_json::from_slice(
            &self
                .helm(args(&[
                    "list", "-n", ns, "--all", "--max", "256", "-o", "json",
                ]))
                .await?,
        )?;
        let rows = rows.as_array().context("Invalid Helm list")?;
        ensure!(rows.len() < 256, "Release list truncated");
        if !rows.iter().any(|v| v["name"] == name) {
            return Ok(None);
        }
        let status: Value = serde_json::from_slice(
            &self
                .helm(args(&["status", name, "-n", ns, "-o", "json"]))
                .await?,
        )?;
        let revision = status["version"]
            .as_u64()
            .context("Helm revision unavailable")?;
        let secret = self
            .get(
                "secret",
                &format!("sh.helm.release.v1.{name}.v{revision}"),
                Some(ns),
            )
            .await?
            .context("Helm release receipt missing")?;
        Ok(Some(
            json!({"name":name,"namespace":ns,"revision":revision,"status":status["info"]["status"],"description":status["info"]["description"],"uid":secret["metadata"]["uid"]}),
        ))
    }
    async fn cluster(&self) -> Result<String> {
        let ns = self
            .get("namespace", "kube-system", None)
            .await?
            .context("Cluster identity missing")?;
        Ok(ns["metadata"]["uid"]
            .as_str()
            .context("Cluster UID missing")?
            .to_string())
    }
}
fn read_json(p: &Path) -> Result<Value> {
    let parent = p
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    Ok(serde_json::from_slice(&super::read_file(
        parent,
        p.file_name()
            .and_then(|s| s.to_str())
            .context("Invalid file path")?,
    )?)?)
}
fn bounded_name(s: &str) -> bool {
    (3..=50).contains(&s.len())
        && s.as_bytes()[0].is_ascii_lowercase()
        && s.as_bytes().last().unwrap().is_ascii_alphanumeric()
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}
fn hash_path(path: &Path) -> Result<String> {
    let meta = fs::symlink_metadata(path)?;
    ensure!(
        !meta.file_type().is_symlink(),
        "Installer inputs cannot be links"
    );
    if meta.is_file() {
        ensure!(meta.len() <= 4 * 1024 * 1024, "Installer input too large");
        return Ok(hash(&fs::read(path)?));
    }
    ensure!(meta.is_dir(), "Invalid chart input");
    fn walk(root: &Path, p: &Path, files: &mut BTreeMap<String, String>) -> Result<()> {
        for (index, entry) in fs::read_dir(p)?.enumerate() {
            ensure!(index < 200, "Chart directory entry limit exceeded");
            let path = entry?.path();
            let m = fs::symlink_metadata(&path)?;
            ensure!(!m.file_type().is_symlink(), "Chart links forbidden");
            if m.is_dir() {
                ensure!(
                    path.components().count() - root.components().count() < 12,
                    "Chart nesting too deep"
                );
                walk(root, &path, files)?;
            } else {
                ensure!(m.is_file() && m.len() <= 1024 * 1024, "Invalid chart file");
                files.insert(
                    path.strip_prefix(root)?
                        .to_str()
                        .context("Invalid chart filename")?
                        .to_owned(),
                    hash(&fs::read(path)?),
                );
                ensure!(files.len() <= 200, "Chart file limit exceeded");
            }
        }
        Ok(())
    }
    let mut files = BTreeMap::new();
    walk(path, path, &mut files)?;
    ensure!(!files.is_empty(), "Empty chart");
    Ok(hash(&serde_json::to_vec(&files)?))
}
fn pinned(s: &str) -> bool {
    s.rsplit_once("@sha256:").is_some_and(|(name, h)| {
        !name.is_empty()
            && h.len() == 64
            && h.bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    })
}
fn inputs(c: &Config) -> Result<BTreeMap<String, String>> {
    ensure!(
        c.schema_version == 1 && c.context.starts_with("kind-") && c.kubeconfig.is_absolute(),
        "Explicit local kind kubeconfig/context required"
    );
    ensure!(
        c.service.fixture_database,
        "Local cleanup profile requires its dedicated PostgreSQL fixture"
    );
    let (ns, release, fullname) = c.openshell.scope();
    for name in [
        &c.service.namespace,
        &c.service.release,
        ns,
        release,
        fullname,
    ] {
        ensure!(bounded_name(name), "Invalid namespace/release name");
    }
    ensure!(
        c.service.namespace.starts_with("ow-service-m4-") && c.service.namespace != ns,
        "Dedicated local service namespace required"
    );
    let v = read_json(&c.service.values_file)?;
    ensure!(
        v["image"].as_str().is_some_and(|s| pinned(s)),
        "Service image must be digest pinned"
    );
    ensure!(
        v["config"]["service"]["id"]
            .as_str()
            .is_some_and(|s| s.starts_with("ows-")
                && s.len() <= 50
                && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')),
        "Invalid service identity"
    );
    let expected = format!("https://{fullname}.{ns}.svc.cluster.local:8080");
    ensure!(
        v["config"]["openshell"]["endpoint"] == expected,
        "Service and gateway endpoint differ"
    );
    let mut hashes = BTreeMap::from([
        ("service-chart".into(), hash_path(&c.service.chart)?),
        ("service-values".into(), hash_path(&c.service.values_file)?),
    ]);
    if let Shell::Install {
        archive,
        values_file,
        ..
    } = &c.openshell
    {
        let sha = hash_path(archive)?;
        ensure!(
            sha == CHART_SHA,
            "OpenShell archive differs from qualified 0.1.3 pin"
        );
        let v = read_json(values_file)?;
        ensure!(
            v["fullnameOverride"] == fullname
                && v["server"]["auth"]["allowUnauthenticatedUsers"] == false,
            "OpenShell must use the explicit name and authenticated gateway"
        );
        for pointer in [
            "/gateway/image/digest",
            "/sandboxRuntime/image/digest",
            "/supervisor/image/digest",
            "/sandbox/image/digest",
        ] {
            ensure!(
                v.pointer(pointer)
                    .and_then(Value::as_str)
                    .is_some_and(|s| pinned(&format!("image@{s}"))),
                "Trusted OpenShell images must be digest pinned"
            );
        }
        hashes.insert("openshell-chart".into(), sha);
        hashes.insert("openshell-values".into(), hash_path(values_file)?);
    }
    Ok(hashes)
}
fn digest(p: &Plan) -> Result<String> {
    Ok(hash(&serde_json::to_vec(&(
        &p.schema_version,
        &p.id,
        &p.config,
        &p.hashes,
        &p.cluster_uid,
    ))?))
}
fn save(root: &Path, name: &str, value: &impl Serialize) -> Result<()> {
    let path = root.join(format!(".{name}-{}", uuid::Uuid::new_v4().simple()));
    let mut o = fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(&path)?;
    f.write_all(&json_bytes(value)?)?;
    f.sync_all()?;
    fs::rename(path, root.join(name))?;
    #[cfg(unix)]
    fs::File::open(root)?.sync_all()?;
    Ok(())
}
fn load(root: &Path) -> Result<(Plan, Journal, fs::File)> {
    let m = fs::symlink_metadata(root)?;
    ensure!(
        m.is_dir() && !m.file_type().is_symlink(),
        "Invalid installer state directory"
    );
    let lock_path = root.join("installer.lock");
    if let Ok(meta) = fs::symlink_metadata(&lock_path) {
        ensure!(
            meta.is_file() && !meta.file_type().is_symlink(),
            "Invalid installer lock"
        );
    }
    let mut o = fs::OpenOptions::new();
    o.read(true).write(true).create(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let lock = o.open(lock_path)?;
    lock.try_lock()
        .map_err(|_| anyhow::anyhow!("Another installer process holds this journal"))?;
    let p: Plan = serde_json::from_slice(&super::read_file(root, "local-plan.json")?)?;
    ensure!(
        p.schema_version == 1 && p.digest == digest(&p)?,
        "Local plan digest mismatch"
    );
    let j: Journal = serde_json::from_slice(&super::read_file(root, "journal.json")?)?;
    ensure!(
        j.schema_version == 1 && j.plan_digest == p.digest,
        "Journal/plan mismatch"
    );
    Ok((p, j, lock))
}
fn begin(root: &Path, j: &mut Journal, step: &str) -> Result<()> {
    ensure!(
        !j.operations.contains_key(step),
        "Operation already has an intent"
    );
    j.operations.insert(
        step.into(),
        Operation {
            status: "intent".into(),
            request_id: uuid::Uuid::new_v4().to_string(),
            receipt: None,
        },
    );
    save(root, "journal.json", j)
}
fn complete(root: &Path, j: &mut Journal, step: &str, receipt: Value) -> Result<()> {
    let op = j
        .operations
        .get_mut(step)
        .context("Operation intent missing")?;
    op.status = "complete".into();
    op.receipt = Some(receipt);
    save(root, "journal.json", j)
}
async fn namespace(
    client: &Client<'_>,
    p: &Plan,
    root: &Path,
    j: &mut Journal,
    name: &str,
    service_id: &str,
) -> Result<()> {
    let step = format!("namespace:{name}");
    let existing = client.get("namespace", name, None).await?;
    if let Some(ns) = existing {
        ensure!(
            ns["metadata"]["labels"][OWNER] == p.id,
            "Namespace is not owned by this installer"
        );
        let uid = ns["metadata"]["uid"]
            .as_str()
            .context("Namespace UID missing")?;
        if let Some(expected) = j.namespaces.get(name) {
            ensure!(expected == uid, "Namespace identity changed");
        }
        ensure!(
            j.operations.contains_key(&step),
            "Refusing to adopt a namespace without an original intent"
        );
        j.namespaces.insert(name.into(), uid.into());
        complete(root, j, &step, json!({"uid":uid}))?;
        return Ok(());
    }
    ensure!(
        !j.operations.contains_key(&step),
        "Namespace outcome unknown or owned namespace missing; inspect before retrying"
    );
    begin(root, j, &step)?;
    let ns = json!({"apiVersion":"v1","kind":"Namespace","metadata":{"name":name,"labels":{OWNER:p.id,"openworker.io/qualification":service_id}}});
    client
        .kube(args(&["create", "-f", "-"]), Some(serde_json::to_vec(&ns)?))
        .await?;
    let ns = client
        .get("namespace", name, None)
        .await?
        .context("Namespace creation outcome unknown")?;
    ensure!(
        ns["metadata"]["labels"][OWNER] == p.id,
        "Namespace ownership mismatch"
    );
    let uid = ns["metadata"]["uid"]
        .as_str()
        .context("Namespace UID missing")?;
    j.namespaces.insert(name.into(), uid.into());
    complete(root, j, &step, json!({"uid":uid}))
}
async fn owned(client: &Client<'_>, p: &Plan, j: &Journal, name: &str) -> Result<Option<Value>> {
    let ns = client.get("namespace", name, None).await?;
    if let Some(v) = &ns {
        ensure!(
            v["metadata"]["labels"][OWNER] == p.id
                && j.namespaces.get(name).map(String::as_str) == v["metadata"]["uid"].as_str(),
            "Namespace ownership/UID mismatch"
        );
    }
    Ok(ns)
}
async fn install_release(
    client: &Client<'_>,
    p: &Plan,
    root: &Path,
    j: &mut Journal,
    key: &str,
    ns: &str,
    release: &str,
    chart: &Path,
    values: &Path,
) -> Result<()> {
    owned(client, p, j, ns)
        .await?
        .context("Owned namespace missing")?;
    if let Some(r) = client.release(ns, release).await? {
        let op = j
            .operations
            .get(key)
            .context("Refusing to adopt an existing Helm release")?;
        ensure!(
            r["description"] == format!("ows:{}:{}", p.id, op.request_id),
            "Helm release provenance mismatch"
        );
        if let Some(original) = &op.receipt {
            ensure!(
                original["uid"] == r["uid"] && original["revision"] == r["revision"],
                "Helm release identity/revision changed"
            );
        }
        ensure!(
            r["status"] == "deployed",
            "Helm release is pending/failed; inspect original release, no automatic reinstall"
        );
        complete(root, j, key, r)?;
        return Ok(());
    }
    ensure!(!j.operations.contains_key(key),"Helm operation outcome unknown: no release observed. Confirm no effect explicitly before reissuing");
    begin(root, j, key)?;
    let nonce = &j.operations[key].request_id;
    client
        .helm(args(&[
            "install",
            release,
            chart.to_str().context("Invalid chart path")?,
            "-n",
            ns,
            "-f",
            values.to_str().context("Invalid values path")?,
            "--description",
            &format!("ows:{}:{nonce}", p.id),
            "--wait",
            "--wait-for-jobs",
            "--timeout",
            "180s",
        ]))
        .await?;
    let r = client
        .release(ns, release)
        .await?
        .context("Helm installation outcome unknown")?;
    ensure!(
        r["status"] == "deployed" && r["description"] == format!("ows:{}:{nonce}", p.id),
        "Helm installation not confirmed"
    );
    complete(root, j, key, r)
}
async fn prerequisites(client: &Client<'_>) -> Result<()> {
    let crd = client
        .get("crd", "sandboxes.agents.x-k8s.io", None)
        .await?
        .context("Agent Sandbox CRD required")?;
    ensure!(
        crd["spec"]["versions"]
            .as_array()
            .is_some_and(|a| a.iter().any(|v| v["served"] == true
                && ["v1alpha1", "v1beta1"].contains(&v["name"].as_str().unwrap_or("")))),
        "Supported Agent Sandbox API required"
    );
    let controller = client
        .get(
            "deployment",
            "agent-sandbox-controller",
            Some("agent-sandbox-system"),
        )
        .await?
        .context("Local qualification requires the Agent Sandbox controller")?;
    ensure!(
        controller["status"]["availableReplicas"]
            .as_u64()
            .is_some_and(|n| n > 0),
        "Agent Sandbox controller must be available"
    );
    let classes: Value = serde_json::from_slice(
        &client
            .kube(args(&["get", "storageclasses", "-o", "json"]), None)
            .await?,
    )?;
    ensure!(
        classes["items"]
            .as_array()
            .is_some_and(|a| a.iter().any(|s| s["metadata"]["annotations"]
                ["storageclass.kubernetes.io/is-default-class"]
                == "true")),
        "Default storage provisioner required"
    );
    let ds: Value = serde_json::from_slice(
        &client
            .kube(args(&["get", "daemonsets", "-A", "-o", "json"]), None)
            .await?,
    )?;
    ensure!(
        ds["items"]
            .as_array()
            .is_some_and(|a| a.iter().any(|s| s["metadata"]["name"] == "calico-node"
                && s["status"]["desiredNumberScheduled"]
                    .as_u64()
                    .is_some_and(|n| n > 0 && s["status"]["numberReady"] == n))),
        "Local qualification requires ready Calico; functional enforcement must also be probed"
    );
    Ok(())
}
async fn ready_service(client: &Client<'_>, p: &Plan) -> Result<()> {
    for role in ["api", "worker", "controller", "platform"] {
        client
            .kube(
                args(&[
                    "-n",
                    &p.config.service.namespace,
                    "rollout",
                    "status",
                    &format!("deployment/{}-{role}", p.config.service.release),
                    "--timeout=180s",
                ]),
                None,
            )
            .await?;
    }
    Ok(())
}
async fn quiet_database(client: &Client<'_>, p: &Plan) -> Result<()> {
    let ns = &p.config.service.namespace;
    let exists = client.get("deployment", "postgres", Some(ns)).await?;
    if exists.is_none() {
        ensure!(
            client
                .release(ns, &p.config.service.release)
                .await?
                .is_none(),
            "Database unavailable for safe teardown"
        );
        return Ok(());
    }
    let v = read_json(&p.config.service.values_file)?;
    let schema = v["config"]["schema"]
        .as_str()
        .context("Schema unavailable")?;
    ensure!(
        schema.starts_with("ow_")
            && schema.len() <= 48
            && schema
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_'),
        "Invalid fixture schema"
    );
    let sql = |q: &str| {
        args(&[
            "-n",
            ns,
            "exec",
            "deployment/postgres",
            "--",
            "psql",
            "-U",
            "openworker",
            "-d",
            "openworker",
            "-Atc",
            q,
        ])
    };
    let exists=String::from_utf8(client.kube(sql(&format!("SELECT to_regclass('{schema}.sessions') IS NOT NULL, to_regclass('{schema}.environments') IS NOT NULL")),None).await?)?;
    if exists.trim() == "f|f" {
        return Ok(());
    }
    ensure!(
        exists.trim() == "t|t",
        "Partial schema requires manual cleanup review"
    );
    let q=format!("SELECT (SELECT count(*) FROM {schema}.environments WHERE data->>'phase' IS DISTINCT FROM 'deleted')+(SELECT count(*) FROM {schema}.sessions WHERE NOT COALESCE((data->>'closed')::boolean,FALSE))");
    let count = String::from_utf8(client.kube(sql(&q), None).await?)?;
    ensure!(
        count.trim() == "0",
        "Close sessions and delete their environments before teardown"
    );
    Ok(())
}
async fn delete_namespace(
    client: &Client<'_>,
    p: &Plan,
    root: &Path,
    j: &mut Journal,
    ns: &str,
) -> Result<()> {
    let key = format!("delete-namespace:{ns}");
    let existing = owned(client, p, j, ns).await?;
    if existing.is_none() {
        ensure!(
            j.operations.contains_key(&key),
            "Namespace absent without destruction intent"
        );
        return complete(root, j, &key, json!({"absent":true}));
    }
    if !j.operations.contains_key(&key) {
        begin(root, j, &key)?;
    }
    // The DELETE body carries a UID precondition: checking a name before delete
    // alone would allow a replacement namespace to be deleted in a race.
    let options =
        json!({"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":j.namespaces[ns]}});
    client
        .kube(
            args(&[
                "delete",
                "--raw",
                &format!("/api/v1/namespaces/{ns}"),
                "-f",
                "-",
            ]),
            Some(serde_json::to_vec(&options)?),
        )
        .await?;
    for _ in 0..180 {
        if owned(client, p, j, ns).await?.is_none() {
            return complete(root, j, &key, json!({"absent":true}));
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    bail!("Namespace deletion pending; resume the same journal")
}
async fn uninstall(
    client: &Client<'_>,
    p: &Plan,
    root: &Path,
    j: &mut Journal,
    key: &str,
    ns: &str,
    release: &str,
) -> Result<()> {
    owned(client, p, j, ns)
        .await?
        .context("Owned namespace missing before uninstall")?;
    let delete = format!("uninstall:{key}");
    if let Some(r) = client.release(ns, release).await? {
        let receipt = j
            .operations
            .get(key)
            .and_then(|o| o.receipt.as_ref())
            .context("Release identity was never confirmed")?;
        ensure!(
            r["uid"] == receipt["uid"] && r["revision"] == receipt["revision"],
            "Release identity changed before uninstall"
        );
        ensure!(
            !j.operations.contains_key(&delete),
            "Uninstall outcome unknown; inspect original release instead of replaying"
        );
        begin(root, j, &delete)?;
        client
            .helm(args(&[
                "uninstall",
                release,
                "-n",
                ns,
                "--wait",
                "--timeout",
                "180s",
            ]))
            .await?;
    } else if !j.operations.contains_key(&delete) {
        begin(root, j, &delete)?;
    }
    ensure!(
        client.release(ns, release).await?.is_none(),
        "Uninstall not confirmed"
    );
    complete(root, j, &delete, json!({"absent":true}))
}
pub(super) async fn run(command: &Command) -> Result<()> {
    if let Command::Plan { config, out } = command {
        let mut c: Config = serde_json::from_value(read_json(config)?)?;
        c.kubeconfig = c.kubeconfig.canonicalize()?;
        c.service.chart = c.service.chart.canonicalize()?;
        c.service.values_file = c.service.values_file.canonicalize()?;
        if let Shell::Install {
            archive,
            values_file,
            ..
        } = &mut c.openshell
        {
            *archive = archive.canonicalize()?;
            *values_file = values_file.canonicalize()?;
        }
        let hashes = inputs(&c)?;
        let client = Client { c: &c };
        let cluster_uid = client.cluster().await?;
        let mut p = Plan {
            schema_version: 1,
            id: format!("ows-local-{}", uuid::Uuid::new_v4().simple()),
            config: c,
            hashes,
            cluster_uid,
            digest: String::new(),
        };
        p.digest = digest(&p)?;
        let mut d = fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            d.mode(0o700);
        }
        d.create(out)
            .context("Local state directory must not exist")?;
        save(out, "local-plan.json", &p)?;
        save(
            out,
            "journal.json",
            &Journal {
                schema_version: 1,
                plan_digest: p.digest.clone(),
                phase: "planned".into(),
                ..Default::default()
            },
        )?;
        return crate::output(
            json!({"deploymentId":p.id,"planDigest":p.digest,"state":"planned","scope":"local-kind"}),
        );
    }
    let state = match command {
        Command::Apply { state, .. }
        | Command::Status { state }
        | Command::Destroy { state, .. } => state,
        _ => unreachable!(),
    };
    let (p, mut j, _lock) = load(state)?;
    let client = Client { c: &p.config };
    ensure!(
        client.cluster().await? == p.cluster_uid,
        "Cluster identity changed"
    );
    if let Command::Status { .. } = command {
        let mut observed = BTreeMap::new();
        for name in j.namespaces.keys() {
            observed.insert(name.clone(), owned(&client,&p,&j,name).await?.map(|v|json!({"uid":v["metadata"]["uid"],"terminating":v["metadata"].get("deletionTimestamp").is_some()})));
        }
        let (ns, release, _) = p.config.openshell.scope();
        let gateway = client.release(ns, release).await?;
        let service = client
            .release(&p.config.service.namespace, &p.config.service.release)
            .await?;
        return crate::output(
            json!({"deploymentId":p.id,"phase":j.phase,"operations":j.operations,"confirmedNoEffect":j.confirmed_no_effect,"observedNamespaces":observed,"observedGateway":gateway,"observedService":service,"clusterIdentityVerified":true,"readOnly":true}),
        );
    }
    ensure!(
        inputs(&p.config)? == p.hashes,
        "Installer inputs changed; regenerate and review a new plan"
    );
    if let Command::Apply {
        accept_plan,
        openshell_only,
        confirm_no_effect,
        ..
    } = command
    {
        ensure!(accept_plan == &p.digest, "Accepted plan digest mismatch");
        ensure!(
            !["destroying", "destroyed"].contains(&j.phase.as_str()),
            "Deployment is being/has been destroyed"
        );
        prerequisites(&client).await?;
        if let Some(step) = confirm_no_effect {
            let old = j
                .operations
                .get(step)
                .context("No such uncertain operation")?;
            ensure!(
                old.status == "intent" && step.ends_with("-release"),
                "Only an unconfirmed Helm install may be reset"
            );
            let (ns, release, _) = if step == "openshell-release" {
                p.config.openshell.scope()
            } else if step == "service-release" {
                (
                    &p.config.service.namespace[..],
                    &p.config.service.release[..],
                    "",
                )
            } else {
                bail!("Invalid recovery step")
            };
            ensure!(
                client.release(ns, release).await?.is_none(),
                "A release exists; reconcile it instead"
            );
            ensure!(
                j.confirmed_no_effect.len() < 32,
                "Recovery history limit reached"
            );
            j.confirmed_no_effect
                .push(j.operations.remove(step).unwrap());
            save(state, "journal.json", &j)?;
        }
        let v = read_json(&p.config.service.values_file)?;
        let service_id = v["config"]["service"]["id"].as_str().unwrap();
        namespace(
            &client,
            &p,
            state,
            &mut j,
            &p.config.service.namespace,
            service_id,
        )
        .await?;
        let (ns, release, _) = p.config.openshell.scope();
        match &p.config.openshell {
            Shell::Install {
                archive,
                values_file,
                ..
            } => {
                namespace(&client, &p, state, &mut j, ns, service_id).await?;
                install_release(
                    &client,
                    &p,
                    state,
                    &mut j,
                    "openshell-release",
                    ns,
                    release,
                    archive,
                    values_file,
                )
                .await?;
            }
            Shell::Existing { .. } => {
                let r = client
                    .release(ns, release)
                    .await?
                    .context("Existing gateway release absent")?;
                ensure!(
                    r["status"] == "deployed",
                    "Existing gateway is not deployed"
                );
                if let Some(op) = j.operations.get("external-openshell") {
                    ensure!(
                        op.receipt.as_ref() == Some(&r),
                        "Existing gateway changed during installation"
                    );
                } else {
                    begin(state, &mut j, "external-openshell")?;
                    complete(state, &mut j, "external-openshell", r)?;
                }
            }
        }
        j.phase = "openshell_ready".into();
        save(state, "journal.json", &j)?;
        if !openshell_only {
            for key in ["database", "openshell", "apiTLS", "bootstrap", "probe"] {
                let name = v["secrets"][key].as_str().unwrap_or(match key {
                    "database" => "ow-service-database",
                    "openshell" => "ow-service-openshell",
                    "apiTLS" => "ow-service-tls",
                    "bootstrap" => "ow-service-bootstrap",
                    _ => "ow-service-probe",
                });
                client.get("secret",name,Some(&p.config.service.namespace)).await?.context("Required service Secret absent; stage credentials/fixtures before service installation")?;
            }
            install_release(
                &client,
                &p,
                state,
                &mut j,
                "service-release",
                &p.config.service.namespace,
                &p.config.service.release,
                &p.config.service.chart,
                &p.config.service.values_file,
            )
            .await?;
            ready_service(&client, &p).await?;
            j.phase = "installed".into();
            save(state, "journal.json", &j)?;
        }
        return crate::output(
            json!({"deploymentId":p.id,"phase":j.phase,"functionalSandboxQualification":"required_separately"}),
        );
    }
    if let Command::Destroy { deployment_id, .. } = command {
        ensure!(deployment_id == &p.id, "Deployment identity mismatch");
        if j.phase == "destroyed" {
            return crate::output(json!({"deploymentId":p.id,"phase":"destroyed"}));
        }
        if let Some(_) = owned(&client, &p, &j, &p.config.service.namespace).await? {
            quiet_database(&client, &p).await?;
            if let Some(_) = client
                .get(
                    "deployment",
                    &format!("{}-api", p.config.service.release),
                    Some(&p.config.service.namespace),
                )
                .await?
            {
                client
                    .kube(
                        args(&[
                            "-n",
                            &p.config.service.namespace,
                            "scale",
                            &format!("deployment/{}-api", p.config.service.release),
                            "--replicas=0",
                        ]),
                        None,
                    )
                    .await?;
                client
                    .kube(
                        args(&[
                            "-n",
                            &p.config.service.namespace,
                            "wait",
                            "--for=delete",
                            "pod",
                            "-l",
                            &format!("app={}-api", p.config.service.release),
                            "--timeout=90s",
                        ]),
                        None,
                    )
                    .await?;
                quiet_database(&client, &p).await?;
            }
            j.phase = "destroying".into();
            save(state, "journal.json", &j)?;
            uninstall(
                &client,
                &p,
                state,
                &mut j,
                "service-release",
                &p.config.service.namespace,
                &p.config.service.release,
            )
            .await?;
            delete_namespace(&client, &p, state, &mut j, &p.config.service.namespace).await?;
        } else {
            ensure!(
                j.operations
                    .contains_key(&format!("delete-namespace:{}", p.config.service.namespace)),
                "Service namespace unexpectedly missing"
            );
        }
        if let Shell::Install {
            namespace: ns,
            release,
            ..
        } = &p.config.openshell
        {
            if owned(&client, &p, &j, ns).await?.is_some() {
                let sandboxes: Value = serde_json::from_slice(
                    &client
                        .kube(
                            args(&["get", "sandboxes.agents.x-k8s.io", "-n", ns, "-o", "json"]),
                            None,
                        )
                        .await?,
                )?;
                ensure!(
                    sandboxes["items"].as_array().is_some_and(Vec::is_empty),
                    "Owned OpenShell still has sandboxes; remove them through the service first"
                );
                uninstall(&client, &p, state, &mut j, "openshell-release", ns, release).await?;
                delete_namespace(&client, &p, state, &mut j, ns).await?;
            }
        }
        j.phase = "destroyed".into();
        save(state, "journal.json", &j)?;
        return crate::output(
            json!({"deploymentId":p.id,"phase":j.phase,"existingGatewayPreserved":matches!(p.config.openshell,Shell::Existing{..})}),
        );
    }
    unreachable!()
}
