//! Provider-independent lifecycle rules. Names are selectors, never ownership receipts.
//! Mutation outcome unknown => inspect original identity; do not replay automatically.
use super::*;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Operation {
    pub token: String,
    pub arn: Option<String>,
    pub status: String,
}
#[derive(Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Journal {
    pub schema_version: u16,
    pub deployment_id: String,
    pub bundle_digest: String,
    pub plan: PathBuf,
    pub phase: String,
    pub operations: BTreeMap<String, Operation>,
    pub recovery_history: Vec<(String, Operation)>,
    #[serde(default)]
    pub resources: BTreeMap<String, Value>,
}
#[derive(Clone)]
pub(super) struct Stack {
    pub arn: String,
    pub name: String,
    pub status: String,
    pub role: String,
    pub tags: BTreeMap<String, String>,
    pub template: Value,
    /// Only stack-level events whose StackId/PhysicalResourceId match the ARN.
    pub tokens: Vec<String>,
    pub outputs: BTreeMap<String, String>,
}
pub(super) trait Provider {
    async fn authorize_creation(&mut self) -> Result<()>;
    async fn stack(&mut self, selector: &str) -> Result<Option<Stack>>;
    async fn create(
        &mut self,
        name: &str,
        token: &str,
        template: &Value,
        tags: &BTreeMap<String, String>,
    ) -> Result<String>;
    async fn delete(&mut self, arn: &str, token: &str) -> Result<()>;
    async fn guard_health(&mut self) -> Result<()>;
}
pub(super) fn save(root: &Path, j: &Journal) -> Result<()> {
    let temporary = root.join(format!(".journal-{}", uuid::Uuid::new_v4().simple()));
    let mut options = fs::OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(&json_bytes(j)?)?;
    file.sync_all()?;
    fs::rename(temporary, root.join("cloud-journal.json"))?;
    #[cfg(unix)]
    fs::File::open(root)?.sync_all()?;
    Ok(())
}
pub(super) fn init(plan: &Path, root: &Path) -> Result<Journal> {
    let (p, m) = inspect(plan)?;
    ensure!(
        p.schema_version == 3 && p.infrastructure.is_some(),
        "Schema-3 AWS infrastructure plan required"
    );
    let parent = root
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    ensure!(
        !parent.starts_with(plan.canonicalize()?),
        "Lifecycle state must be outside the immutable plan"
    );
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder
        .create(root)
        .context("Lifecycle state directory must not exist")?;
    let j = Journal {
        schema_version: 1,
        deployment_id: p.deployment_id,
        bundle_digest: m.bundle_digest,
        plan: plan.canonicalize()?,
        phase: "prepared".into(),
        ..Default::default()
    };
    save(root, &j)?;
    Ok(j)
}
pub(super) fn load(root: &Path) -> Result<(Journal, Plan, fs::File)> {
    let meta = fs::symlink_metadata(root)?;
    ensure!(
        meta.is_dir() && !meta.file_type().is_symlink(),
        "Invalid lifecycle directory"
    );
    let path = root.join("cloud.lock");
    if let Ok(m) = fs::symlink_metadata(&path) {
        ensure!(
            m.is_file() && !m.file_type().is_symlink(),
            "Invalid lifecycle lock"
        );
    }
    let mut options = fs::OpenOptions::new();
    options.read(true).write(true).create(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let lock = options.open(path)?;
    lock.try_lock()
        .map_err(|_| anyhow::anyhow!("Another command holds the lifecycle lock"))?;
    let j: Journal = serde_json::from_slice(&read_file(root, "cloud-journal.json")?)?;
    let (p, m) = inspect(&j.plan)?;
    ensure!(
        j.schema_version == 1
            && p.schema_version == 3
            && j.deployment_id == p.deployment_id
            && j.bundle_digest == m.bundle_digest,
        "Lifecycle/plan identity mismatch"
    );
    Ok((j, p, lock))
}
fn name(p: &Plan, kind: &str) -> String {
    format!("{}-{kind}", p.prefix())
}
fn valid_arn(p: &Plan, kind: &str, arn: &str) -> bool {
    let Target::Aws { account, region } = &p.target else {
        return false;
    };
    arn.strip_prefix(&format!(
        "arn:aws:cloudformation:{region}:{account}:stack/{}/",
        name(p, kind)
    ))
    .is_some_and(|id| {
        !id.is_empty()
            && id.len() <= 128
            && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
    })
}
fn tags(p: &Plan, j: &Journal, token: &str) -> BTreeMap<String, String> {
    BTreeMap::from([
        ("ows:deployment-id".into(), p.deployment_id.clone()),
        ("ows:bundle-digest".into(), j.bundle_digest.clone()),
        ("ows:operation-id".into(), token.into()),
        ("ows:expires-at".into(), p.expires_at_unix.to_string()),
    ])
}
pub(super) fn verify(p: &Plan, j: &Journal, kind: &str, op: &Operation, s: &Stack) -> Result<()> {
    ensure!(
        valid_arn(p, kind, &s.arn) && s.name == name(p, kind),
        "Cloud stack scope mismatch"
    );
    ensure!(
        s.role
            == p.bootstrap
                .as_ref()
                .context("Executor missing")?
                .execution_role_arn,
        "Cloud stack executor changed"
    );
    ensure!(
        tags(p, j, &op.token)
            .iter()
            .all(|(k, v)| s.tags.get(k) == Some(v)),
        "Cloud stack ownership/request mismatch"
    );
    ensure!(
        op.arn.as_ref().is_none_or(|arn| arn == &s.arn),
        "Cloud stack immutable identity changed"
    );
    let expected: Value = serde_json::from_slice(&read_file(
        &j.plan,
        &format!(
            "{}.cfn.json",
            if kind == "infra" {
                "infrastructure"
            } else {
                "guard"
            }
        ),
    )?)?;
    ensure!(s.template == expected, "Cloud stack template drift");
    ensure!(
        s.tokens.contains(&op.token),
        "Original creation token not observed in stack events; inspect original operation"
    );
    Ok(())
}
fn begin(root: &Path, j: &mut Journal, key: &str, arn: Option<String>) -> Result<()> {
    ensure!(
        !j.operations.contains_key(key),
        "Operation already has an intent"
    );
    j.operations.insert(
        key.into(),
        Operation {
            token: format!("ows-{}", uuid::Uuid::new_v4().simple()),
            arn,
            status: "intent".into(),
        },
    );
    save(root, j)
}
/// One bounded observation/transition. Caller may invoke again; no hidden mutation retry.
pub(super) async fn provision<P: Provider>(
    root: &Path,
    j: &mut Journal,
    p: &Plan,
    api: &mut P,
    at: u64,
) -> Result<()> {
    ensure!(
        !["destroying", "infrastructure_deleted"].contains(&j.phase.as_str()),
        "Deployment teardown has begun"
    );
    for kind in ["guard", "infra"] {
        let key = format!("create:{kind}");
        let selector = j
            .operations
            .get(&key)
            .and_then(|o| o.arn.clone())
            .unwrap_or_else(|| name(p, kind));
        let observed = api.stack(&selector).await?;
        match observed {
            Some(s) => {
                let op = j
                    .operations
                    .get(&key)
                    .context("Refusing to adopt a preexisting stack")?;
                verify(p, j, kind, op, &s)?;
                let op = j.operations.get_mut(&key).unwrap();
                op.arn = Some(s.arn.clone());
                op.status = s.status.clone();
                save(root, j)?;
                match s.status.as_str(){
                    "CREATE_COMPLETE"=>{},
                    "CREATE_IN_PROGRESS"=>{j.phase=format!("{kind}_creating");save(root,j)?;return Ok(())},
                    _=>bail!("Cloud stack failed, changed or is being deleted; inspect original operation"),
                }
            }
            None => {
                ensure!(
                    p.created_at_unix <= at && at < p.expires_at_unix,
                    "Expiry prevents new provisioning; observation/teardown remain available"
                );
                ensure!(
                    !j.operations.contains_key(&key),
                    "Cloud create outcome unknown or original stack missing; no automatic replay"
                );
                if kind == "infra" {
                    api.guard_health().await?;
                }
                api.authorize_creation().await?;
                let template: Value = serde_json::from_slice(&read_file(
                    &j.plan,
                    if kind == "infra" {
                        "infrastructure.cfn.json"
                    } else {
                        "guard.cfn.json"
                    },
                )?)?;
                begin(root, j, &key, None)?;
                // Intent is durable before dispatch. A transport error retains this
                // token; the next process observes, never silently creates again.
                let token = j.operations[&key].token.clone();
                let arn = api
                    .create(&name(p, kind), &token, &template, &tags(p, j, &token))
                    .await?;
                ensure!(
                    valid_arn(p, kind, &arn),
                    "Create returned an invalid stack ARN; inspect original request"
                );
                j.operations.get_mut(&key).unwrap().arn = Some(arn);
                j.phase = format!("{kind}_creating");
                save(root, j)?;
                return Ok(());
            }
        }
        if kind == "guard" {
            api.guard_health().await?;
            j.phase = "guard_ready".into();
            save(root, j)?;
        }
    }
    // Infrastructure readiness is distinct from platform/service qualification.
    j.phase = "infrastructure_ready".into();
    save(root, j)
}
pub(super) async fn observe<P: Provider>(j: &Journal, p: &Plan, api: &mut P) -> Result<Value> {
    let mut rows = BTreeMap::new();
    for kind in ["guard", "infra"] {
        let key = format!("create:{kind}");
        let selector = j
            .operations
            .get(&key)
            .and_then(|o| o.arn.clone())
            .unwrap_or_else(|| name(p, kind));
        let s = api.stack(&selector).await?;
        if let Some(s) = s {
            let op = j
                .operations
                .get(&key)
                .context("Unbound preexisting stack observed")?;
            verify(p, j, kind, op, &s)?;
            rows.insert(kind,json!({"arn":s.arn,"status":s.status,"originalCreationTokenVerified":true,"outputs":s.outputs}));
        } else {
            rows.insert(
                kind,
                json!({"absent":true,"priorIntent":j.operations.contains_key(&key)}),
            );
        }
    }
    Ok(json!({"deploymentId":j.deployment_id,"phase":j.phase,"observed":rows,"readOnly":true}))
}
pub(super) async fn confirm_no_effect<P: Provider>(
    root: &Path,
    j: &mut Journal,
    p: &Plan,
    api: &mut P,
    kind: &str,
) -> Result<()> {
    ensure!(
        ["guard", "infra"].contains(&kind),
        "Only an unconfirmed create may be reset"
    );
    ensure!(
        j.recovery_history.len() < 16,
        "Recovery history limit reached"
    );
    let key = format!("create:{kind}");
    let old = j.operations.get(&key).context("No original intent")?;
    ensure!(
        old.status == "intent" && old.arn.is_none(),
        "A bound/observed operation cannot be reset"
    );
    ensure!(
        api.stack(&name(p, kind)).await?.is_none(),
        "Original stack exists; reconcile it instead"
    );
    j.recovery_history
        .push((key.clone(), j.operations.remove(&key).unwrap()));
    save(root, j)
}
/// Installation and data cleanup is a separate precondition, checked by caller.
/// Guard/bootstrap intentionally remain; they must outlive all owned infrastructure.
pub(super) async fn destroy<P: Provider>(
    root: &Path,
    j: &mut Journal,
    p: &Plan,
    api: &mut P,
) -> Result<()> {
    let key = "create:infra";
    let Some(mut op) = j.operations.get(key).cloned() else {
        bail!("Infrastructure has no original intent; inspect incomplete bootstrap")
    };
    if op.arn.is_none() {
        let s = api
            .stack(&name(p, "infra"))
            .await?
            .context("Infrastructure outcome unknown; original stack absent")?;
        verify(p, j, "infra", &op, &s)?;
        op.arn = Some(s.arn);
        j.operations.insert(key.into(), op.clone());
        save(root, j)?;
    }
    let arn = op.arn.as_ref().unwrap();
    let stack = api.stack(arn).await?;
    let delete_key = "delete:infra";
    if let Some(s) = stack {
        verify(p, j, "infra", &op, &s)?;
        if s.status == "DELETE_COMPLETE" {
            ensure!(
                j.operations.contains_key(delete_key),
                "Deletion occurred without recorded intent; inspect"
            );
            j.phase = "infrastructure_deleted".into();
            save(root, j)?;
            return Ok(());
        }
        if let Some(d) = j.operations.get(delete_key) {
            ensure!(d.arn.as_ref() == Some(arn), "Delete receipt changed");
            ensure!(
                s.status == "DELETE_IN_PROGRESS" && s.tokens.contains(&d.token),
                "Prior delete outcome unknown/failed; inspect instead of retrying"
            );
            return Ok(());
        }
        ensure!(
            !s.status.ends_with("IN_PROGRESS") && !s.status.starts_with("DELETE_"),
            "Stack is busy/failed deletion; inspect original operation"
        );
        begin(root, j, delete_key, Some(arn.clone()))?;
        j.phase = "destroying".into();
        save(root, j)?;
        api.delete(arn, &j.operations[delete_key].token).await?;
    } else {
        ensure!(
            j.operations.contains_key(delete_key),
            "Original stack absent without deletion intent; inspect"
        );
        j.phase = "infrastructure_deleted".into();
        save(root, j)?;
    }
    Ok(())
}
